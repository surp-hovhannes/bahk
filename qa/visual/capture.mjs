// Drives Chromium through a plan's scenarios and records evidence.
import fs from 'node:fs';
import path from 'node:path';
import { nowIso, safeUrl, settings, sha256File } from './util.mjs';

const NAV_TIMEOUT = 45_000;
const STEP_TIMEOUT = 15_000;
const READY_TIMEOUT = Number(process.env.VQA_READY_TIMEOUT_MS || 20_000);

// QA browsing must not report to production telemetry (the bahk preview build
// sends errors to the production Sentry project). These hosts are aborted.
const TELEMETRY_HOSTS = [
  /(^|\.)sentry\.io$/, /(^|\.)google-analytics\.com$/, /(^|\.)googletagmanager\.com$/, /(^|\.)segment\.(io|com)$/,
  /(^|\.)mixpanel\.com$/, /(^|\.)posthog\.com$/, /(^|\.)amplitude\.com$/, /(^|\.)nr-data\.net$/, /(^|\.)newrelic\.com$/,
  /(^|\.)clarity\.ms$/, /(^|\.)hotjar\.(com|io)$/, /(^|\.)doubleclick\.net$/,
];

function isTelemetry(url) {
  try {
    const host = new URL(url).hostname;
    return TELEMETRY_HOSTS.some((re) => re.test(host));
  } catch {
    return false;
  }
}

// Share of the image covered by its single most common colour, measured on a
// downscaled copy in a blank helper page. Splash and loading screens are
// ~99.9% one colour; sparse but real pages (a 404, an empty list) are not.
const BLANK_FRACTION = 0.995;

async function dominantColourFraction(helper, png) {
  return helper.evaluate(async (b64) => {
    const img = new Image();
    img.src = `data:image/png;base64,${b64}`;
    await img.decode();
    const w = 200;
    const h = Math.max(1, Math.round((img.height / img.width) * w));
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0, w, h);
    const data = ctx.getImageData(0, 0, w, h).data;
    const counts = new Map();
    let best = 0;
    for (let i = 0; i < data.length; i += 4) {
      const key = ((data[i] >> 3) << 10) | ((data[i + 1] >> 3) << 5) | (data[i + 2] >> 3);
      const n = (counts.get(key) || 0) + 1;
      counts.set(key, n);
      if (n > best) best = n;
    }
    return best / (w * h);
  }, png.toString('base64'));
}

async function waitForContent(page, minText) {
  if (!minText) return true;
  try {
    await page.waitForFunction((n) => (document.body?.innerText || '').trim().length >= n, minText, { timeout: READY_TIMEOUT });
    return true;
  } catch {
    return false;
  }
}

async function loadPlaywright() {
  // On this Mac browsers live on T7; on a CI runner Playwright's default path is used.
  if (!process.env.PLAYWRIGHT_BROWSERS_PATH && fs.existsSync(settings().browsersPath)) {
    process.env.PLAYWRIGHT_BROWSERS_PATH = settings().browsersPath;
  }
  const mod = await import('playwright');
  return mod.chromium;
}

async function runStep(page, baseUrl, { kind, value }) {
  const opts = { timeout: STEP_TIMEOUT };
  switch (kind) {
    case 'click': return page.locator(value).first().click(opts);
    case 'hover': return page.locator(value).first().hover(opts);
    case 'check': return page.locator(value).first().check(opts);
    case 'uncheck': return page.locator(value).first().uncheck(opts);
    case 'fill': return page.locator(value[0]).first().fill(value[1], opts);
    case 'press': return page.locator(value[0]).first().press(value[1], opts);
    case 'select': return page.locator(value[0]).first().selectOption(value[1], opts);
    case 'waitFor': return page.locator(value).first().waitFor({ state: 'visible', timeout: STEP_TIMEOUT });
    case 'waitMs': return page.waitForTimeout(value);
    case 'goto': return page.goto(`${baseUrl}${value}`, { waitUntil: 'load', timeout: NAV_TIMEOUT });
    case 'scroll':
      if (value === 'bottom') return page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      if (value === 'top') return page.evaluate(() => window.scrollTo(0, 0));
      return page.locator(value).first().scrollIntoViewIfNeeded(opts);
    default: throw new Error(`unknown step ${kind}`);
  }
}

// Logs in through the app's own form with credentials the target created for
// this run. Credentials come from the environment, never from the plan.
async function login(context, baseUrl, spec, creds) {
  const page = await context.newPage();
  const loginPath = spec.loginPath || (spec.kind === 'devise' ? '/users/sign_in' : '/admin/login/');
  await page.goto(`${baseUrl}${loginPath}`, { waitUntil: 'load', timeout: NAV_TIMEOUT });
  const userSel = spec.usernameField || (spec.kind === 'devise' ? 'input[name="user[email]"]' : 'input[name="username"]');
  const passSel = spec.passwordField || (spec.kind === 'devise' ? 'input[name="user[password]"]' : 'input[name="password"]');
  await page.locator(userSel).first().fill(creds.username, { timeout: STEP_TIMEOUT });
  await page.locator(passSel).first().fill(creds.password, { timeout: STEP_TIMEOUT });
  await Promise.all([
    page.waitForLoadState('load', { timeout: NAV_TIMEOUT }),
    page.locator(spec.submit || 'form [type="submit"]').first().click({ timeout: STEP_TIMEOUT }),
  ]);
  await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
  const stillOnLogin = new URL(page.url()).pathname === new URL(`${baseUrl}${loginPath}`).pathname;
  await page.close();
  if (stillOnLogin) throw new Error(`login did not leave ${loginPath}; check the login form selectors or the per-run user`);
}

export async function capture({ plan, baseUrl, outDir, loginSpec = null, creds = null, headers = null }) {
  const chromium = await loadPlaywright();
  const browser = await chromium.launch({ headless: true });
  const browserVersion = browser.version();
  const helperContext = await browser.newContext();
  const helper = await helperContext.newPage();
  const results = [];
  const shotsDir = path.join(outDir, 'screens');
  fs.mkdirSync(shotsDir, { recursive: true });
  try {
    for (const vp of plan.viewports) {
      const context = await browser.newContext({
        viewport: { width: vp.width, height: vp.height },
        deviceScaleFactor: vp.deviceScaleFactor,
        isMobile: vp.isMobile,
        hasTouch: vp.hasTouch,
        locale: 'en-US',
        timezoneId: 'America/Los_Angeles',
        reducedMotion: 'reduce',
        ...(headers ? { extraHTTPHeaders: headers } : {}),
      });
      let blockedTelemetry = 0;
      await context.route((url) => isTelemetry(url.href), (route) => {
        blockedTelemetry += 1;
        return route.abort('blockedbyclient');
      });
      try {
        if (loginSpec) await login(context, baseUrl, loginSpec, creds);
      } catch (err) {
        for (const s of plan.scenarios) {
          if (!s.viewports || s.viewports.includes(vp.name)) results.push({ scenario: s.name, viewport: vp.name, status: 'error', error: `login failed: ${err.message}`.slice(0, 500) });
        }
        await context.close();
        continue;
      }
      for (const s of plan.scenarios) {
        if (s.viewports && !s.viewports.includes(vp.name)) continue;
        const page = await context.newPage();
        await page.emulateMedia({ colorScheme: s.colorScheme, reducedMotion: 'reduce' });
        const consoleErrors = [];
        const failedRequests = [];
        page.on('console', (m) => { if (m.type() === 'error' && consoleErrors.length < 20) consoleErrors.push(m.text().slice(0, 300)); });
        page.on('pageerror', (e) => { if (consoleErrors.length < 20) consoleErrors.push(`pageerror: ${String(e.message).slice(0, 300)}`); });
        page.on('requestfailed', (r) => { if (!isTelemetry(r.url()) && failedRequests.length < 20) failedRequests.push({ url: safeUrl(r.url()), failure: r.failure()?.errorText ?? 'failed' }); });
        page.on('response', (r) => { if (r.status() >= 400 && failedRequests.length < 20) failedRequests.push({ url: safeUrl(r.url()), status: r.status() }); });
        const blockedBefore = blockedTelemetry;
        const entry = { scenario: s.name, viewport: vp.name, url: safeUrl(`${baseUrl}${s.path}`), startedAt: nowIso() };
        try {
          const response = await page.goto(`${baseUrl}${s.path}`, { waitUntil: 'load', timeout: NAV_TIMEOUT });
          entry.httpStatus = response?.status() ?? null;
          await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
          for (const st of s.steps) await runStep(page, baseUrl, st);
          if (s.waitFor) await page.locator(s.waitFor).first().waitFor({ state: 'visible', timeout: STEP_TIMEOUT });
          const hasContent = await waitForContent(page, s.minText);
          await page.evaluate(() => document.fonts?.ready).catch(() => {});
          const file = path.join(shotsDir, `${s.name}__${vp.name}.png`);
          // Retake while the page still looks like a splash screen, up to the ready timeout.
          const until = Date.now() + READY_TIMEOUT;
          let fraction = 1;
          for (;;) {
            const png = await page.screenshot({
              path: file, fullPage: s.fullPage, animations: 'disabled', caret: 'hide',
              mask: s.mask.map((sel) => page.locator(sel)),
            });
            fraction = await dominantColourFraction(helper, png);
            if (fraction < BLANK_FRACTION || Date.now() >= until) break;
            await page.waitForTimeout(2000);
          }
          entry.finalUrl = safeUrl(page.url());
          entry.screenshot = path.relative(outDir, file);
          entry.sha256 = sha256File(file);
          entry.dominantColourShare = Math.round(fraction * 1000) / 1000;
          const leftSite = new URL(page.url()).origin !== new URL(baseUrl).origin;
          entry.textLength = await page.evaluate(() => (document.body?.innerText || '').trim().length).catch(() => null);
          if (leftSite) {
            // A login wall or deployment protection page is a real page, but not the app.
            entry.status = 'redirected';
            entry.error = `ended on ${entry.finalUrl}, outside ${new URL(baseUrl).origin} (login wall or deployment protection)`;
          } else if (!hasContent || fraction >= BLANK_FRACTION) {
            entry.status = 'blank';
            entry.error = !hasContent
              ? `page showed fewer than ${s.minText} characters of text after ${READY_TIMEOUT / 1000}s (splash, loading or empty screen)`
              : `${(fraction * 100).toFixed(1)}% of the screenshot is one colour after ${READY_TIMEOUT / 1000}s (splash or loading screen covering the page)`;
          } else {
            entry.status = entry.httpStatus === s.expectStatus ? 'ok' : 'unexpected-status';
          }
        } catch (err) {
          entry.status = 'error';
          entry.error = String(err.message).split('\n')[0].slice(0, 500);
          try {
            const file = path.join(shotsDir, `${s.name}__${vp.name}__error.png`);
            await page.screenshot({ path: file, fullPage: false });
            entry.screenshot = path.relative(outDir, file);
            entry.sha256 = sha256File(file);
          } catch { /* page unusable */ }
        }
        entry.consoleErrors = consoleErrors.filter((t) => !/status of 4\d\d \(\)$/.test(t) || failedRequests.length > 0);
        entry.failedRequests = failedRequests;
        entry.blockedTelemetryRequests = blockedTelemetry - blockedBefore;
        results.push(entry);
        await page.close();
      }
      await context.close();
    }
  } finally {
    await browser.close();
  }
  return { results, browser: { name: 'chromium', version: browserVersion } };
}
