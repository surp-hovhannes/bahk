function waitForNextEngagementPoll(signal) {
    return new Promise((resolve, reject) => {
        function abort() {
            clearTimeout(timer);
            reject(signal.reason);
        }
        const timer = setTimeout(() => {
            signal.removeEventListener('abort', abort);
            resolve();
        }, 2000);
        signal.addEventListener('abort', abort, { once: true });
    });
}

// Apply one deadline to the entire request, including the initial fetch.
async function waitForEngagementSnapshot(url, callerSignal, pollSeconds) {
    callerSignal?.throwIfAborted();
    const controller = new AbortController();
    const { signal } = controller;
    const abort = () => controller.abort(callerSignal.reason);
    callerSignal?.addEventListener('abort', abort, { once: true });
    const deadline = setTimeout(() => {
        controller.abort(new Error('Analytics are still being prepared'));
    }, pollSeconds * 1000);

    try {
        while (true) {
            signal.throwIfAborted();
            const response = await fetch(url, { signal });
            if (!response.ok) throw new Error('Analytics temporarily unavailable');
            const data = await response.json();
            signal.throwIfAborted();
            if (response.status !== 202) return data;
            await waitForNextEngagementPoll(signal);
        }
    } finally {
        clearTimeout(deadline);
        callerSignal?.removeEventListener('abort', abort);
    }
}
