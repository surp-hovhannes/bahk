// Validates a visual QA plan. Plans are data only: no scripts, no selectors
// that run code, and no credentials. Targets supply logins themselves.
import { PlanError } from './util.mjs';

export const VIEWPORT_PRESETS = {
  mobile: { width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true },
  tablet: { width: 820, height: 1180, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
  laptop: { width: 1280, height: 800, deviceScaleFactor: 2 },
  desktop: { width: 1440, height: 900, deviceScaleFactor: 2 },
};

export const TARGET_KINDS = ['preview', 'url', 'tarkman-admin-local', 'ci-artifact', 'ios-sim'];
const STEP_KINDS = ['click', 'fill', 'press', 'select', 'hover', 'waitFor', 'waitMs', 'scroll', 'goto', 'check', 'uncheck'];
const SLUG = /^[a-z0-9][a-z0-9-]{0,60}$/;

function fail(message) {
  throw new PlanError(`invalid plan: ${message}`);
}

function viewport(v, i) {
  if (typeof v === 'string') {
    if (!VIEWPORT_PRESETS[v]) fail(`viewports[${i}] "${v}" is not one of ${Object.keys(VIEWPORT_PRESETS).join(', ')}`);
    return { name: v, ...VIEWPORT_PRESETS[v] };
  }
  if (!v || !SLUG.test(v.name || '')) fail(`viewports[${i}] needs a slug name`);
  for (const key of ['width', 'height']) {
    if (!Number.isInteger(v[key]) || v[key] < 200 || v[key] > 4000) fail(`viewports[${i}].${key} must be 200-4000`);
  }
  return {
    name: v.name, width: v.width, height: v.height,
    deviceScaleFactor: v.deviceScaleFactor ?? 2, isMobile: Boolean(v.isMobile), hasTouch: Boolean(v.hasTouch ?? v.isMobile),
  };
}

function step(s, where) {
  if (!s || typeof s !== 'object' || Array.isArray(s)) fail(`${where} must be an object`);
  const keys = Object.keys(s);
  if (keys.length !== 1 || !STEP_KINDS.includes(keys[0])) fail(`${where} must have exactly one of ${STEP_KINDS.join(', ')}`);
  const [kind] = keys;
  const value = s[kind];
  const str = (x) => typeof x === 'string' && x.length > 0 && x.length <= 300;
  switch (kind) {
    case 'fill': case 'press': case 'select':
      if (!Array.isArray(value) || value.length !== 2 || !str(value[0]) || typeof value[1] !== 'string') fail(`${where}.${kind} must be [selector, text]`);
      break;
    case 'waitMs':
      if (!Number.isInteger(value) || value < 0 || value > 5000) fail(`${where}.waitMs must be 0-5000`);
      break;
    case 'goto':
      if (!str(value) || !value.startsWith('/')) fail(`${where}.goto must be a path starting with /`);
      break;
    case 'scroll':
      if (!str(value)) fail(`${where}.scroll must be "bottom", "top" or a selector`);
      break;
    default:
      if (!str(value)) fail(`${where}.${kind} must be a selector string`);
  }
  return { kind, value };
}

export function validatePlan(plan) {
  if (!plan || typeof plan !== 'object') fail('not an object');
  if (!SLUG.test(plan.name || '')) fail('name must be a short slug');
  if (!plan.target || !TARGET_KINDS.includes(plan.target.kind)) fail(`target.kind must be one of ${TARGET_KINDS.join(', ')}`);
  const kind = plan.target.kind;
  if (kind !== 'url' && !/^[\w.-]+\/[\w.-]+$/.test(plan.repo || '')) fail('repo must be owner/name');
  if (['preview', 'ci-artifact', 'tarkman-admin-local', 'ios-sim'].includes(kind) && !Number.isInteger(plan.pr)) fail('pr must be a pull request number');
  if (plan.expectSha !== undefined && !/^[0-9a-f]{7,40}$/.test(plan.expectSha)) fail('expectSha must be 7-40 hex characters');
  if (kind === 'url' && !/^https?:\/\//.test(plan.target.baseUrl || '')) fail('target.baseUrl is required for kind url');
  if (kind === 'ios-sim') {
    if (!Array.isArray(plan.flows) || plan.flows.length === 0 || plan.flows.length > 20) fail('ios-sim plans need 1-20 flows (smoke, core, regression, all, or repo-relative .yaml paths)');
    for (const f of plan.flows) {
      if (typeof f !== 'string' || !(/^(smoke|core|regression|all)$/.test(f) || (/^[\w./-]+\.ya?ml$/.test(f) && !f.includes('..')))) fail(`flow "${f}" must be a level or a repo-relative .yaml path`);
    }
    return { ...plan, viewports: [], scenarios: [] };
  }
  const viewports = (plan.viewports && plan.viewports.length ? plan.viewports : ['mobile', 'desktop']).map(viewport);
  if (!Array.isArray(plan.scenarios) || plan.scenarios.length === 0 || plan.scenarios.length > 40) fail('scenarios must list 1-40 scenarios');
  const names = new Set();
  const scenarios = plan.scenarios.map((s, i) => {
    if (!SLUG.test(s?.name || '')) fail(`scenarios[${i}] needs a slug name`);
    if (names.has(s.name)) fail(`duplicate scenario name ${s.name}`);
    names.add(s.name);
    if (typeof s.path !== 'string' || !s.path.startsWith('/')) fail(`scenarios[${i}].path must start with /`);
    const steps = (s.steps || []).map((st, j) => step(st, `scenarios[${i}].steps[${j}]`));
    if (steps.length > 30) fail(`scenarios[${i}] has more than 30 steps`);
    const only = s.viewports ? s.viewports.map(String) : null;
    return {
      name: s.name, path: s.path, steps, viewports: only,
      fullPage: s.fullPage !== false, mask: (s.mask || []).slice(0, 20).map(String),
      waitFor: s.waitFor ? String(s.waitFor) : null, colorScheme: s.colorScheme === 'dark' ? 'dark' : 'light',
      expectStatus: Number.isInteger(s.expectStatus) ? s.expectStatus : 200,
      // Characters of visible text that prove the page finished loading; 0 disables the check.
      minText: Number.isInteger(s.minText) && s.minText >= 0 ? s.minText : 20,
    };
  });
  return { ...plan, viewports, scenarios };
}
