// Poll only while the server is preparing this window.
async function waitForEngagementSnapshot(url, signal) {
    for (let attempt = 0; attempt < 35; attempt++) {
        const response = await fetch(url, { signal });
        if (response.status === 202) {
            await new Promise(resolve => setTimeout(resolve, 2000));
            continue;
        }
        if (!response.ok) throw new Error('Analytics temporarily unavailable');
        return response.json();
    }
    throw new Error('Analytics are still being prepared');
}
