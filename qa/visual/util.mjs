// Shared helpers for the visual QA runner.
import { spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

export function settings() {
  const env = process.env;
  return {
    ghBin: env.VQA_GH_BIN || 'gh',
    t7: env.VQA_T7 || '/Volumes/T7',
    home: env.VQA_HOME || '/Volumes/T7/vqa',
    browsersPath: env.PLAYWRIGHT_BROWSERS_PATH || '/Volumes/T7/vqa/ms-playwright',
  };
}

export class PlanError extends Error {}
export class SetupError extends Error {}

export function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, {
    encoding: 'utf8',
    timeout: opts.timeout ?? 60_000,
    env: opts.env ?? process.env,
    cwd: opts.cwd,
    input: opts.input,
    maxBuffer: 64 * 1024 * 1024,
  });
  return { code: r.status, out: r.stdout ?? '', err: r.stderr ?? '', error: r.error?.code ?? null };
}

export function gh(args, opts = {}) {
  const r = run(settings().ghBin, args, opts);
  if (r.code !== 0) throw new SetupError(`gh ${args.slice(0, 3).join(' ')} failed: ${(r.err || r.out || r.error || '').trim().slice(0, 300)}`);
  return r.out;
}

export function ghJson(args, opts = {}) {
  return JSON.parse(gh(args, opts));
}

export function sha256File(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

export function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`);
  fs.renameSync(tmp, file);
}

export function loadRepos() {
  return JSON.parse(fs.readFileSync(path.join(ROOT, 'repos.json'), 'utf8'));
}

// Reads a secret from the environment or ~/.openclaw/.env. Never logged or
// written into manifests.
export function readSecret(name) {
  if (process.env[name]) return process.env[name].trim();
  const file = process.env.VQA_ENV_FILE || path.join(process.env.HOME || '', '.openclaw', '.env');
  if (!fs.existsSync(file)) return null;
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const m = line.match(new RegExp(`^\\s*(?:export\\s+)?${name}\\s*=\\s*(.*)\\s*$`));
    if (m) return m[1].replace(/^(['"])(.*)\1$/, '$2').trim() || null;
  }
  return null;
}

export const nowIso = () => new Date().toISOString();
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Strip query strings and fragments: they can carry signed tokens.
export function safeUrl(raw) {
  try {
    const u = new URL(raw);
    return `${u.origin}${u.pathname}`;
  } catch {
    return String(raw).split(/[?#]/)[0].slice(0, 300);
  }
}
