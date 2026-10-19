import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { capture } from './capture.mjs';
import { validatePlan } from './plan.mjs';

test('checks the final document status after goto, click and redirects', async () => {
  const server = http.createServer((req, res) => {
    if (req.url === '/redirect') {
      res.writeHead(302, { Location: '/500' });
      return res.end();
    }
    res.writeHead(req.url === '/404' ? 404 : req.url === '/500' ? 500 : 200,
      { 'Content-Type': 'text/html' });
    res.end(`<body style="background:linear-gradient(90deg,white,steelblue)">
      <h1>Visual QA navigation regression page with sufficient visible content</h1>
      <a href="/500">Broken detail</a>
      ${req.url === "/" ? '<iframe src="/404"></iframe>' : ""}</body>`);
  });
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vqa-navigation-'));
  try {
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const plan = validatePlan({
      name: 'navigation', target: { kind: 'url', baseUrl: 'http://localhost' },
      viewports: [{ name: 'test', width: 400, height: 300, deviceScaleFactor: 1 }],
      scenarios: [
        { name: 'initial', path: '/' },
        { name: 'goto-404', path: '/', steps: [{ goto: '/404' }] },
        { name: 'click-500', path: '/', steps: [{ click: 'a' }] },
        { name: 'redirect-500', path: '/', steps: [{ goto: '/redirect' }] },
        { name: 'expected-404', path: '/', steps: [{ goto: '/404' }], expectStatus: 404 },
      ],
    });
    const { results } = await capture({ plan,
      baseUrl: `http://127.0.0.1:${server.address().port}`, outDir });
    assert.deepEqual(results.map(({ httpStatus, status }) => [httpStatus, status]), [
      [200, 'ok'], [404, 'unexpected-status'], [500, 'unexpected-status'],
      [500, 'unexpected-status'], [404, 'ok'],
    ]);
  } finally {
    await new Promise((resolve) => server.close(resolve));
    fs.rmSync(outDir, { recursive: true, force: true });
  }
});

test('masks sensitive content in fallback screenshots after readiness failure', async () => {
  const server = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end('<body style="margin:0;background:white"><div id="secret" style="width:100px;height:100px;background:blue">private</div></body>');
  });
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vqa-mask-'));
  let browser;
  try {
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const plan = validatePlan({
      name: 'mask', target: { kind: 'url', baseUrl: 'http://localhost' },
      viewports: [{ name: 'test', width: 400, height: 300, deviceScaleFactor: 1 }],
      scenarios: [{ name: 'failure', path: '/', mask: ['#secret'], waitFor: '[' }],
    });
    const { results: [result] } = await capture({ plan,
      baseUrl: `http://127.0.0.1:${server.address().port}`, outDir });
    assert.equal(result.status, 'error');
    assert.match(result.screenshot, /__error\.png$/);
    const { chromium } = await import('playwright');
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    const png = fs.readFileSync(path.join(outDir, result.screenshot));
    const pixel = await page.evaluate(async (base64) => {
      const image = new Image();
      image.src = `data:image/png;base64,${base64}`;
      await image.decode();
      const canvas = document.createElement('canvas');
      canvas.width = image.width;
      canvas.height = image.height;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(image, 0, 0);
      return Array.from(ctx.getImageData(50, 50, 1, 1).data);
    }, png.toString('base64'));
    assert.deepEqual(pixel, [255, 0, 255, 255], 'sensitive region must have the opaque Playwright mask');
  } finally {
    if (browser) await browser.close();
    await new Promise((resolve) => server.close(resolve));
    fs.rmSync(outDir, { recursive: true, force: true });
  }
});
