const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(
    path.join(__dirname, '../../static/admin/js/engagement-snapshot.js'), 'utf8'
);

function polling(fetch, Controller = AbortController) {
    const timers = new Map();
    const wait = vm.runInNewContext(source + '\nwaitForEngagementSnapshot', {
        AbortController: Controller,
        DOMException,
        fetch,
        setTimeout(callback, delay) {
            const timer = Symbol();
            timers.set(timer, { callback, delay });
            return timer;
        },
        clearTimeout(timer) {
            timers.delete(timer);
        },
    });
    function fire(delay) {
        const [timer, entry] = [...timers].find(([, entry]) => entry.delay === delay);
        timers.delete(timer);
        entry.callback();
    }
    return { wait, timers, fire };
}

function stalled(signal) {
    signal?.throwIfAborted();
    return new Promise((_, reject) => {
        signal.addEventListener('abort', () => reject(signal.reason), { once: true });
    });
}

const ready = { status: 200, ok: true, json: async () => ({ events_in_period: 3 }) };
const pending = { status: 202, ok: true, json: async () => ({ poll_seconds: 134 }) };

test('polling works with abort signals lacking throwIfAborted and reason', async () => {
    class LegacyAbortController extends AbortController {
        constructor() {
            super();
            Object.defineProperties(this.signal, {
                throwIfAborted: { value: undefined },
                reason: { value: undefined },
            });
        }
    }

    const caller = new LegacyAbortController();
    let calls = 0;
    const { wait, timers, fire } = polling(async () => calls++ === 0 ? pending : ready, LegacyAbortController);
    const result = wait('/analytics', caller.signal, 134);
    await new Promise(setImmediate);
    fire(2000);
    assert.deepEqual(await result, { events_in_period: 3 });
    assert.equal(timers.size, 0);

    const delayed = polling(async () => pending, LegacyAbortController);
    const cancelled = assert.rejects(delayed.wait('/analytics', caller.signal, 134), { name: 'AbortError' });
    await new Promise(setImmediate);
    caller.abort();
    await cancelled;
    await assert.rejects(wait('/analytics', caller.signal, 134), { name: 'AbortError' });
    assert.equal(calls, 2);
    assert.equal(delayed.timers.size, 0);
    assert.equal(timers.size, 0);
});

test('deadline aborts a stalled initial fetch or response body', async () => {
    for (const status of [null, 200, 202]) {
        let signal;
        const { wait, timers, fire } = polling((_, fetchOptions) => {
            signal = fetchOptions.signal;
            return status === null ? stalled(signal) : Promise.resolve({
                status, ok: true, json: () => stalled(signal),
            });
        });
        const result = wait('/analytics', undefined, 134);
        const rejected = assert.rejects(result, /still being prepared/);
        await new Promise(setImmediate);
        fire(134000);
        await rejected;
        assert.equal(signal.aborted, true);
        assert.equal(timers.size, 0);
    }
});

test('deadline and caller cancellation both interrupt the polling delay', async () => {
    for (const cancelByCaller of [false, true]) {
        const caller = new AbortController();
        let calls = 0;
        const { wait, timers, fire } = polling(async () => {
            calls += 1;
            return pending;
        });
        const result = wait('/analytics', caller.signal, 134);
        const rejected = assert.rejects(result, cancelByCaller ? /superseded/ : /still being prepared/);
        await new Promise(setImmediate);
        assert.equal(timers.size, 2);
        if (cancelByCaller) caller.abort(new Error('superseded'));
        else fire(134000);
        await rejected;
        assert.equal(calls, 1);
        assert.equal(timers.size, 0);
    }
});

test('polling returns ready data and clears the deadline', async () => {
    let calls = 0;
    const { wait, timers, fire } = polling(async () => calls++ === 0 ? pending : ready);
    const result = wait('/analytics', undefined, 134);
    await new Promise(setImmediate);
    fire(2000);
    assert.deepEqual(await result, { events_in_period: 3 });
    assert.equal(timers.size, 0);
});

test('caller cancellation aborts a fetch and skips fetch for an already aborted caller', async () => {
    const caller = new AbortController();
    let calls = 0;
    const { wait, timers } = polling((_, { signal }) => {
        calls += 1;
        return stalled(signal);
    });
    const result = wait('/analytics', caller.signal, 134);
    const rejected = assert.rejects(result, /superseded/);
    caller.abort(new Error('superseded'));
    await rejected;
    await assert.rejects(wait('/analytics', caller.signal, 134), /superseded/);
    assert.equal(calls, 1);
    assert.equal(timers.size, 0);
});

test('HTTP failure clears the deadline', async () => {
    const { wait, timers } = polling(async () => ({ status: 503, ok: false }));
    await assert.rejects(wait('/analytics', undefined, 134), /temporarily unavailable/);
    assert.equal(timers.size, 0);
});
