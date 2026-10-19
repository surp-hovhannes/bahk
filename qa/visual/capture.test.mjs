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
