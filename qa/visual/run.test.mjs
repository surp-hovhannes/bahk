import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

// Exercise the real entry point and manifest, with capture isolated from browsers.
test('verifies the requested commit before capturing evidence', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vqa-sha-'));
  const sha = '1234567890abcdef1234567890abcdef12345678';
  try {
    for (const name of ['run.mjs', 'plan.mjs', 'util.mjs']) {
      fs.copyFileSync(new URL(name, import.meta.url), path.join(dir, name));
    }
    fs.writeFileSync(path.join(dir, 'capture.mjs'), `export async function capture() {
      return { results: [{ status: 'ok' }], browser: { name: 'stub' } };
    }`);
    for (const [expected, actual, ok] of [
      [sha, sha, true], [sha.slice(0, 7), sha, true],
      ['abcdef0', sha, false], [sha, '', false],
      [sha, sha.slice(0, 7), false],
    ]) {
      const result = spawnSync(process.execPath, [path.join(dir, 'run.mjs')], {
        env: { ...process.env, VQA_OUT: dir, VQA_SHA: actual,
          VQA_PLAN: JSON.stringify({ name: 'sha-test', repo: 'owner/repo', pr: 0,
            expectSha: expected, scenarios: [{ name: 'index', path: '/' }] }) },
        encoding: 'utf8',
      });
      const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json')));
      assert.equal(result.status, ok ? 0 : 1, result.stderr);
      assert.equal(manifest.summary.setupError, !ok);
      if (ok) {
        assert.equal(manifest.target.shaVerified, true);
        assert.equal(manifest.target.sha, sha);
      } else {
        assert.match(manifest.setupError, /expected commit/);
        assert.equal(manifest.target, null);
        assert.deepEqual(manifest.results, []);
      }
    }
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
