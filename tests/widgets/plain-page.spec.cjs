const { test, expect } = require("@playwright/test");
const fs = require("node:fs");
const path = require("node:path");
const fixture = require("./calendar.json");

const screenshotDir = process.env.WIDGET_SCREENSHOT_DIR || "/tmp/bahk-widget-screenshots";
const widgets = ["bahk-fasting-guidance", "bahk-daily-readings"];

for (const [device, viewport] of Object.entries({
  desktop: { width: 1440, height: 1000 },
  mobile: { width: 390, height: 844 },
})) {
  for (const state of ["loaded", "loading", "error"]) {
    test(`plain HTML embed E2E: ${device}, ${state}, mocked public API`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.clock.install({ time: new Date("2026-03-01T12:00:00Z") });
      const errors = [];
      const requests = [];
      page.on("pageerror", (error) => errors.push(error.message));
      let release;
      let recovering = false;
      const gate = new Promise((resolve) => { release = resolve; });
      await page.route("https://api.fastandpray.app/api/v1/calendar/**", async (route) => {
        requests.push(route.request());
        if (state === "loading") await gate;
        if (state === "error" && !recovering) {
          await route.fulfill({ status: 503, headers: { "Retry-After": "5" },
            json: { code: "service_unavailable", message: "Fixture unavailable", details: { retry_after: 5 } },
          });
        } else {
          await route.fulfill({ json: fixture });
        }
      });
      await page.goto("/docs/examples/widgets-plain.html");
      await page.locator("#data-source").evaluate((element) => {
        element.textContent = "DATA SOURCE: MOCKED public-v1 API fixture. No live API data.";
      });
      await page.locator("#scenario").evaluate((element, state) => {
        element.textContent = `E2E screenshot: ${state} state · March 1, 2026 · UTC.`;
      }, state);

      if (state === "loaded") {
        await expect(page.locator("bahk-fasting-guidance h3")).toHaveText("Great Fast");
        await expect(page.locator("bahk-daily-readings li")).toHaveText([
          "Matthew 5:1–12", "John 3:16", "Genesis 1:31–2:3",
        ]);
      }
      for (const tag of widgets) {
        const status = page.locator(`${tag} [role=status]`);
        if (state === "loading") {
          await expect(status).toHaveText("Loading…");
          await expect(page.locator(`${tag} [aria-busy]`)).toHaveAttribute("aria-busy", "true");
        } else if (state === "error") {
          await expect(status).toHaveText("Unable to load this widget.");
          await expect(page.locator(tag)).toContainText("HTTP 503");
          await expect(page.locator(`${tag} button`)).toBeDisabled();
          await expect(page.locator(`${tag} [aria-busy]`)).toHaveAttribute("aria-busy", "false");
        } else {
          await expect(status).toHaveText("Example Church");
          await expect(page.locator(`${tag} [aria-busy]`)).toHaveAttribute("aria-busy", "false");
        }
        // Strong, conflicting host selectors do not enter either widget.
        await expect(page.locator(`${tag} section`)).toHaveCSS("background-color", "rgb(255, 255, 255)");
        await expect(page.locator(`${tag} section`)).toHaveCSS("border-top-style", "solid");
        await expect(page.locator(`${tag} h2`)).toHaveCSS("color", "rgb(128, 39, 62)");
        await expect(page.locator(`${tag} footer a svg`)).toHaveAttribute("aria-hidden", "true");
        expect(await page.locator(`${tag} h2`).evaluate((element) => getComputedStyle(element).fontFamily))
          .not.toContain("Georgia");
        if (state === "error") {
          await expect(page.locator(`${tag} button`)).toHaveCSS("background-color", "rgb(243, 232, 235)");
        }
      }
      // Widget section/heading/button styles do not escape into the host page.
      await expect(page.locator("#host-control")).toHaveCSS("background-color", "rgb(234, 241, 251)");
      await expect(page.locator("#host-control")).toHaveCSS("border-top-style", "dashed");
      await expect(page.locator("#host-control h2")).toHaveCSS("color", "rgb(124, 25, 71)");
      await expect(page.locator("#host-control p")).toHaveCSS("font-family", "Georgia, serif");
      await expect(page.locator("#host-control button")).toHaveCSS("background-color", "rgb(124, 25, 71)");
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      expect(requests).toHaveLength(1);
      expect(requests[0].headers()).not.toHaveProperty("authorization");
      expect(requests[0].headers()).not.toHaveProperty("cookie");

      fs.mkdirSync(screenshotDir, { recursive: true });
      await page.screenshot({ path: path.join(screenshotDir, `${device}-${state}-mocked.png`), fullPage: true });

      if (state === "loading") release();
      if (state === "error") {
        recovering = true;
        await page.clock.fastForward(6500);
        for (const tag of widgets) {
          await expect(page.locator(`${tag} button`)).toBeEnabled();
          await page.locator(`${tag} button`).click();
        }
      }
      // Both loading and error states recover to real rendered widget content.
      await expect(page.locator("bahk-fasting-guidance h3")).toHaveText("Great Fast");
      await expect(page.locator("bahk-daily-readings li")).toHaveCount(3);
      expect(errors).toEqual([]);
    });
  }
}
