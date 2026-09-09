# Public API calendar endpoint (#499)

## Goal

Provide one anonymous, read-only `/api/v1/calendar/` request for a church and explicit date without triggering persistence, passage retrieval, LLM work, or task dispatch.

## Contract

- Required: `church_id`, ISO `date`.
- Optional: `lang` (`en`/`hy`), IANA `tz` (default `UTC`; validates and participates in cache identity but does not shift the explicit date).
- Response keys: `date`, `church`, `readings`, `fast`, `feasts`, `partial_failures`.
- Readings are stored citations only.
- Fast selection is deterministic: lowest ID if malformed data associates multiple active Fasts.
- Feasts use the shared legacy-name/pending-observance-ID read-only lookup and always serialize as an array.
- Only the explicit pending `FeastDataUnavailable` condition becomes a component failure; unexpected/database failures remain whole-request 503 responses.

## Safety and operations

- Route remains behind `PUBLIC_API_RESOURCES_ENABLED` and the #498 deployment-readiness gate.
- Shared admission control runs before cache lookup.
- Cache identity includes route, church, date, effective language, and timezone; unknown parameters do not fragment it.
- Traffic metrics classify the endpoint as `calendar`.
- `/docs/` remains Coming soon until #500 and production readiness are complete.

## Verification

- Golden response, localization, cross-church isolation, empty/one/two Feast states, deterministic Fast selection, partial failure, eager validation, read-only SQL/query budgets, JSON method/content negotiation, cache isolation/hits/quota, and telemetry tests.
- Ruff, Python syntax, `git diff --check`, independent reviewer, and Crabbox full CI before PR publication.
