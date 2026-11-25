// Poll only while the server is preparing this window.
async function waitForEngagementSnapshot(url, signal) {
    const started = performance.now();
    let deadline;
    while (true) {
        const response = await fetch(url, { signal });
        if (response.status !== 202) {
            if (!response.ok) throw new Error('Analytics temporarily unavailable');
            return response.json();
        }
        if (deadline === undefined) {
            const { poll_seconds } = await response.json();
            if (!Number.isFinite(poll_seconds) || poll_seconds <= 0) {
                throw new Error('Analytics temporarily unavailable');
            }
            deadline = started + poll_seconds * 1000;
        }
        if (performance.now() >= deadline) {
            throw new Error('Analytics are still being prepared');
        }
        await new Promise(resolve => setTimeout(resolve, 2000));
    }
}
