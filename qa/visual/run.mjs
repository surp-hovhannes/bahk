#!/usr/bin/env node
// CI entry point for admin visual QA (copied into a repo as qa/visual/run.mjs
// together with plan.mjs, capture.mjs and util.mjs from tools/visual-qa/lib).
// Reads everything from the environment so workflow inputs never reach a shell:
//   VQA_PLAN       plan JSON (scenarios + viewports), from the workflow input
//   VQA_BASE_URL   the app started by the workflow, e.g. http://127.0.0.1:8000
//   VQA_USER / VQA_PASSWORD   the one-run admin created by the workflow
//   VQA_LOGIN_KIND django-admin | devise
//   VQA_SHA        the commit under test (github.sha)
//   VQA_OUT        output directory (uploaded as the artifact)
import path from 'node:path';
import { capture } from './capture.mjs';
import { validatePlan } from './plan.mjs';
import { nowIso, writeJson } from './util.mjs';

const env = process.env;
const outDir = path.resolve(env.VQA_OUT || 'qa-results');
const manifest = { schema: 1, startedAt: nowIso(), target: null, results: [], summary: null };
try {
  const plan = validatePlan({ ...JSON.parse(env.VQA_PLAN || '{}'), target: { kind: 'ci-artifact' } });
  manifest.plan = { name: plan.name, repo: plan.repo ?? null, pr: plan.pr ?? null, target: 'ci-artifact' };
  const baseUrl = env.VQA_BASE_URL;
  const loginSpec = { kind: env.VQA_LOGIN_KIND || 'django-admin' };
  const out = await capture({ plan, baseUrl, outDir, loginSpec, creds: { username: env.VQA_USER, password: env.VQA_PASSWORD } });
  manifest.target = { kind: 'ci-artifact', baseUrl, sha: env.VQA_SHA || null, shaVerified: Boolean(env.VQA_SHA), shaSource: 'GitHub Actions run checked out this commit', runId: env.GITHUB_RUN_ID || null };
  manifest.results = out.results;
  manifest.tool = { browser: out.browser };
} catch (err) {
  manifest.setupError = String(err.message).slice(0, 1000);
}
const failed = manifest.results.filter((r) => r.status !== 'ok');
manifest.finishedAt = nowIso();
manifest.summary = { total: manifest.results.length, ok: manifest.results.length - failed.length, failed: failed.length, setupError: Boolean(manifest.setupError) };
writeJson(path.join(outDir, 'manifest.json'), manifest);
console.log(JSON.stringify(manifest.summary));
// The artifact is the result; the step fails only when nothing could be captured.
process.exitCode = manifest.setupError ? 1 : 0;
