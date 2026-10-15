const { test, expect } = require("@playwright/test");
const fixture = require("./calendar.json");
const script = "/hub/static/widgets/bahk-widgets.js";
const calendar = "**/api/v1/calendar/**";

async function mount(page, attributes = 'church-id="1" date="2026-03-01" time-zone="UTC"', tags) {
  await page.goto("/blank");
  await page.addScriptTag({ url: script });
  await page.evaluate(({ attributes, tags }) => {
    // Test-only markup; widget content is always rendered with textContent.
    document.querySelector("main").innerHTML = (tags || ["bahk-fasting-guidance", "bahk-daily-readings"])
      .map((tag) => `<${tag} ${attributes}></${tag}>`).join("");
  }, { attributes, tags });
}

async function respond(page, change = (data) => data) {
  await page.route(calendar, (route) => route.fulfill({ json: change(structuredClone(fixture)) }));
}

test("renders both documented resources and shares a single anonymous request", async ({ page }) => {
  const requests = [];
  await page.route(calendar, async (route) => {
    requests.push(route.request());
    await route.fulfill({ json: fixture });
  });
  await mount(page);
  await expect(page.locator("bahk-fasting-guidance h3")).toHaveText("Great Fast");
  await expect(page.locator("bahk-daily-readings li")).toHaveText(["Matthew 5:1–12", "John 3:16", "Genesis 1:31–2:3"]);
  const request = requests.find((entry) => entry.url().includes("tz=UTC"));
  expect(new URL(request.url()).searchParams.get("date")).toBe("2026-03-01");
  expect(requests.filter((entry) => entry.url() === request.url())).toHaveLength(1);
  expect(request.headers()).not.toHaveProperty("authorization");
  expect(request.headers()).not.toHaveProperty("cookie");
  await expect(page.locator("bahk-fasting-guidance [aria-busy]")).toHaveAttribute("aria-busy", "false");
  await expect(page.locator("bahk-fasting-guidance [role=status]")).toHaveAttribute("aria-live", "polite");
});

test("has a loading state while the API is pending", async ({ page }) => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route(calendar, async (route) => { await gate; await route.fulfill({ json: fixture }); });
  await mount(page);
  await expect(page.locator("bahk-fasting-guidance [role=status]")).toHaveText("Loading…");
  await expect(page.locator("bahk-daily-readings [aria-busy]")).toHaveAttribute("aria-busy", "true");
  release();
  await expect(page.locator("bahk-daily-readings li")).toHaveCount(3);
});

test("empty stored data does not claim that fasting is unnecessary", async ({ page }) => {
  await respond(page, (data) => ({ ...data, fast: null, readings: [] }));
  await mount(page);
  await expect(page.locator("bahk-fasting-guidance")).toContainText("No fasting guidance is recorded for this date.");
  await expect(page.locator("bahk-daily-readings")).toContainText("No readings are recorded for this date.");
});

test("handles nullable text and unrelated feast partial failures", async ({ page }) => {
  await respond(page, (data) => {
    data.fast.name = null;
    data.fast.description = null;
    data.fast.learn_more_url = null;
    data.readings[0].book = null;
    data.partial_failures = [{ component: "feasts", code: "data_unavailable" }];
    return data;
  });
  await mount(page);
  await expect(page.locator("bahk-fasting-guidance h3")).toHaveText("Current fast");
  await expect(page.locator("bahk-fasting-guidance")).toContainText("No fasting instructions are recorded for this fast.");
  await expect(page.locator("bahk-daily-readings li").first()).toHaveText("Unknown book 5:1–12");
});

test("renders hostile API text literally and rejects script URLs", async ({ page }) => {
  await respond(page, (data) => {
    data.church.name = '<img src=x onerror="window.compromised=true">';
    data.fast.name = "<script>window.compromised=true</script>";
    data.fast.description = '<svg onload="window.compromised=true">';
    data.fast.learn_more_url = "javascript:window.compromised=true";
    data.readings[0].book = "<b>Matthew</b>";
    return data;
  });
  await mount(page);
  await expect(page.locator("bahk-fasting-guidance h3")).toHaveText("<script>window.compromised=true</script>");
  await expect(page.locator("bahk-daily-readings li").first()).toContainText("<b>Matthew</b>");
  await expect(page.locator("bahk-fasting-guidance a")).toHaveCount(1); // Attribution only.
  expect(await page.evaluate(() => window.compromised)).toBeUndefined();
  // The only SVG is the static logo in the attribution footer; API text adds no elements.
  await expect(page.locator("bahk-fasting-guidance svg")).toHaveCount(1);
  await expect(page.locator("bahk-fasting-guidance footer a svg")).toHaveCount(1);
  for (const tag of ["bahk-fasting-guidance", "bahk-daily-readings"]) {
    await expect(page.locator(`${tag} .content`).locator("img, svg, script")).toHaveCount(0);
  }
});

for (const attributes of [
  'church-id="0"', 'church-id="abc"', 'church-id="9223372036854775808"',
  'church-id="1" date="2026-02-30"', 'church-id="1" date=""',
  'church-id="1" time-zone="Invalid/Zone"', 'church-id="1" language="fr"',
  'church-id="1" api-host="https://user:secret@example.org"',
  'church-id="1" api-host="https://example.org/api/v1/"',
]) {
  test(`rejects invalid configuration: ${attributes}`, async ({ page }) => {
    await respond(page);
    await mount(page, attributes);
    await expect(page.locator("bahk-fasting-guidance [role=status]")).toHaveText("Unable to load this widget.");
    await expect(page.locator("bahk-daily-readings button")).toBeEnabled();
  });
}

test("uses local date in the selected timezone and rolls over at midnight", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-03-01T07:59:30Z") });
  const dates = [];
  await page.route(calendar, (route) => {
    const date = new URL(route.request().url()).searchParams.get("date");
    dates.push(date);
    return route.fulfill({ json: { ...fixture, date } });
  });
  await mount(page, 'church-id="1" time-zone="America/Los_Angeles"');
  await expect(page.locator("bahk-fasting-guidance time")).toHaveAttribute("datetime", "2026-02-28");
  await page.clock.fastForward(60000);
  await expect(page.locator("bahk-fasting-guidance time")).toHaveAttribute("datetime", "2026-03-01");
  await expect(page.locator("bahk-daily-readings li")).toHaveCount(3);
  expect(dates).toContain("2026-03-01");
  const count = dates.length;
  await page.clock.fastForward(3600000);
  expect(dates).toHaveLength(count); // No requests for an unchanged date.
});

test("an explicit date remains fixed and Armenian data is marked with its language", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-03-01T23:59:30Z") });
  await respond(page, (data) => {
    data.fast.name = "Մեծ պահք";
    data.readings[0].book = "Մատթեոս";
    return data;
  });
  await mount(page, 'church-id="1" date="2026-03-01" time-zone="UTC" language="hy"');
  await expect(page.locator("bahk-fasting-guidance h3")).toHaveAttribute("lang", "hy");
  await page.clock.fastForward(120000);
  await expect(page.locator("bahk-fasting-guidance time")).toHaveAttribute("datetime", "2026-03-01");
});

test("throttling honors Retry-After and manual retry recovers", async ({ page }) => {
  await page.clock.install();
  let throttled = true;
  await page.route(calendar, (route) => route.fulfill(throttled
    ? { status: 429, headers: { "Retry-After": "120" }, json: { code: "throttled", message: "Wait", details: { retry_after: 120 } } }
    : { json: fixture }));
  await mount(page);
  const button = page.locator("bahk-fasting-guidance button");
  await expect(button).toBeDisabled();
  await page.clock.fastForward(60000);
  await expect(button).toBeDisabled();
  throttled = false;
  await page.clock.fastForward(62000);
  await expect(button).toBeEnabled();
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("bahk-fasting-guidance h3")).toHaveText("Great Fast");
});

test("disabled routes and malformed responses have useful errors", async ({ page }) => {
  await page.route(calendar, (route) => route.fulfill({ status: 404, json: { code: "not_found", message: "Not found", details: {} } }));
  await mount(page);
  await expect(page.locator("bahk-fasting-guidance")).toContainText("whether public v1 resources are enabled");
  await page.unroute(calendar);
  await respond(page, (data) => ({ ...data, date: "2025-01-01" }));
  await page.locator("bahk-fasting-guidance button").click();
  await expect(page.locator("bahk-fasting-guidance")).toContainText("unexpected calendar response");
});

test("service-unavailable retry hints work when Retry-After is not exposed", async ({ page }) => {
  await page.clock.install();
  await page.route(calendar, (route) => route.fulfill({ status: 503,
    json: { code: "service_unavailable", message: "Unavailable", details: { retry_after: 180 } },
  }));
  await mount(page);
  const button = page.locator("bahk-daily-readings button");
  await expect(button).toBeDisabled();
  await page.clock.fastForward(120000);
  await expect(button).toBeDisabled();
  await page.clock.fastForward(62000);
  await expect(button).toBeEnabled();
});

for (const tag of ["bahk-fasting-guidance", "bahk-daily-readings"]) {
  test(`${tag} can be embedded on its own`, async ({ page }) => {
    await respond(page);
    await mount(page, 'church-id="1" date="2026-03-01"', [tag]);
    await expect(page.locator(`${tag} [aria-busy]`)).toHaveAttribute("aria-busy", "false");
    await expect(page.locator(`${tag} button`)).toHaveCount(0);
  });
}

test("a timeout provides a retry instead of an endless loading state", async ({ page }) => {
  await page.clock.install();
  await page.route(calendar, () => {});
  await mount(page);
  await page.clock.fastForward(11000);
  await expect(page.locator("bahk-fasting-guidance")).toContainText("Could not reach the public API");
});

test("browser enforces real cross-origin CORS and no cookies are sent", async ({ page, context }) => {
  await context.addCookies([{ name: "sessionid", value: "private", domain: "127.0.0.1", path: "/" }]);
  const requests = [];
  page.on("request", (request) => { if (request.url().includes(":8766/")) requests.push(request); });
  await mount(page, 'church-id="1" date="2026-03-01" api-host="http://127.0.0.1:8766"');
  await expect(page.locator("bahk-daily-readings li")).toHaveCount(3);
  expect(await requests[0].headerValue("cookie")).toBeNull();
  await page.locator("bahk-daily-readings").evaluate((element) => element.setAttribute("church-id", "2"));
  await expect(page.locator("bahk-daily-readings")).toContainText("API's CORS settings");
});

test("attribute changes cannot render an older in-flight response; duplicate script loads are safe", async ({ page }) => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route(calendar, async (route) => {
    const date = new URL(route.request().url()).searchParams.get("date");
    if (date === "2026-03-01") await gate;
    await route.fulfill({ json: { ...fixture, date, fast: { ...fixture.fast, name: date } } });
  });
  await mount(page);
  await page.locator("bahk-fasting-guidance").evaluate((element) => element.setAttribute("date", "2026-03-02"));
  await expect(page.locator("bahk-fasting-guidance h3")).toHaveText("2026-03-02");
  release();
  await page.addScriptTag({ url: script });
  await expect(page.locator("bahk-fasting-guidance h3")).toHaveText("2026-03-02");
});

test("demo is readable at mobile width and supports form updates", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.route(calendar, (route) => route.fulfill({ json: {
    ...fixture, date: new URL(route.request().url()).searchParams.get("date"),
  } }));
  await page.goto("/docs/examples/widgets.html");
  await expect(page.locator("bahk-fasting-guidance h3")).toHaveText("Great Fast");
  await page.locator('[name="date"]').fill("2026-03-01");
  await page.locator('[name="zone"]').fill("UTC");
  await page.getByRole("button", { name: "Update widgets" }).click();
  await expect(page.locator("bahk-daily-readings time")).toHaveAttribute("datetime", "2026-03-01");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/bahk-widget-mobile.png", fullPage: true });
});
