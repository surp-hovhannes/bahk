# Control/Luna paired icon evaluation

`evaluate_icon_matching_paired` is a read-only, evaluation-only comparison of two
explicit profiles (default: `control-v1` versus `luna-v1`):

| Profile | Pinned model | Reasoning | Positive maximum per batch | Ranking |
| --- | --- | --- | --- | --- |
| `control-v1` | `gpt-4.1-mini` | Omitted, as before | 8 | Existing control ordering |
| `luna-v1` | `gpt-5.6-luna` | `none` | 4 | Event alternatives use confidence/relevance before relation |
| `luna-v2` | `gpt-5.6-luna` | `none` | 4 | Same as Luna v1 |

Use `--profiles luna-v1 luna-v2` for the prompt-only comparison, or
`--profiles control-v1 luna-v2` for a bundled profile comparison. Exactly two
distinct registered IDs are required; unknown or duplicate selections fail before
provider construction or billing. The Python evaluator accepts `profiles=(LUNA,
LUNA_V2)` or the corresponding two ID strings. Omitting selection retains the
original control/v1 pair and alternating arm order. Reports include
`selected_profile_ids`; case IDs and input digests do not depend on profile selection.
Replay responses must be bound to the selected version's profile hash; v1 recordings
cannot be relabeled v2.

Luna v2 changes only assessment/verification prompts and their fictional examples.
Both stages exclude positively different identities before ranking, distinguish
missing details from contradictory metadata, and inspect all title/tags before a
portrait claim. Useful correct-member partial groups, related scenes and themes
remain suggestions; an empty list is valid when no candidate survives. Independent
verification removes unsupported hypotheses. Reasons stay short and grounded in
existing structured fields. The request analysis prompt, model, reasoning, limit of
four, ranking and recommendation-policy hash remain identical to Luna v1.
These instructions are a precision hypothesis, not deterministic semantic enforcement
or evidence of live improvement. Existing source quotes, schema and safeguards are
unchanged; control/v1 comparisons remain bundled effects.

The production default, existing single evaluator, endpoint selection, settings,
assignment tasks, prayer imports and caches are unchanged. Profiles are explicit,
immutable inputs to the shared matcher. No Django settings or global prompt maps
are swapped between arms. Both arms use the same source validators, repair/retry
limits, deadlines, exact-event summaries and assignment gates. No assignment task
or database lookup/write is called by this command.

Luna asks for full-catalogue assessment with **at most** four strong positives per
batch, not a quota. Request `kind` is routing metadata, not text to quote; empty
context is valid. Full synthetic examples cover qualifiers, composite subjects,
event-only evidence without indices, themes, conflicts and portrait fallback.
An explicit audit of all original title/tag metadata precedes any portrait claim.
This remains a model judgment, not deterministic semantic conflict detection or
a whitelist of names/events. Existing quote validation remains strict.

For event requests, Luna orders nonconflicting candidates first, then exact events,
confidence, relevance, covered-subject count, relation and stable ID. This key is
used for the verification shortlist and final recommendations. A strong related
event may outrank a weak portrait without changing its relation or making it
assignable. Other requests retain control ranking. Incomplete catalogue assessment
and the catalogue-wide exact-event summary still prevent unsafe portrait assignment.

## Offline use (default)

Use the same catalogue/request JSON arrays as `evaluate_icon_matching`. Catalogue
records contain `id`, `title`, and `tags` (or `tag_list`). Requests may be strings
(feast requests), or objects with `kind`, `primary_text`/`title`/`name`/`text`,
`context_terms`/`tags`, and optional `max_results`.

All selected requests (after `--limit`) are validated, canonicalized and serialized
for their digests before any provider is initialized or called. A malformed later
item rejects the entire run with an indexed command error and leaves any existing
output report intact; earlier cases cannot consume a live budget first. Items must
be strings or objects with nonempty primary text, kind `feast`/`content`, at most
32 context strings and integer `max_results` from 1 to 100. Omitted context defaults
to an empty array; explicit null, strings or objects are not valid context arrays.
Canonical requests must fit the matcher's 16000-byte limit and serialize as UTF-8.
The existing aliases and defaults remain: string requests use `feast`, objects
without `kind` use `content`, and omitted `max_results` is 10.

```bash
python manage.py evaluate_icon_matching_paired \
  --catalogue-json /tmp/synthetic-catalogue.json \
  --requests-json /tmp/synthetic-requests.json \
  --output-json /tmp/paired-offline.json \
  --limit 10 --arm-timeout 180
```

Without recordings, this writes both unavailable outcomes for every case and
never initializes a live provider. It also supplies stable case IDs for preparing
replays. Missing replay entries are retained as arm failures, not dropped pairs.

```bash
python manage.py evaluate_icon_matching_paired \
  --catalogue-json /tmp/synthetic-catalogue.json \
  --requests-json /tmp/synthetic-requests.json \
  --responses-json /tmp/paired-responses.json \
  --output-json /tmp/paired-replay.json
```

Paired recordings are a JSON object keyed by `case_id`, then `control-v1` and
`luna-v1` by default (or the selected two profile IDs), each holding an ordered
list of wire response entries. Each entry needs:

- `stage`: `analyze`, `assess`, or `verify`.
- `payload_hash`: SHA-256 of the canonical **wire** payload, including the original
  request, catalogue order, positive limit, analysis, candidates and repair
  feedback when present. Use `digest(provider_payload(payload))`.
- `schema_hash`: `digest(wire_schema(schema))`.
- `profile_hash`: `profile.metadata()["profile_hash"]`, binding model, reasoning,
  prompt content/version, recommendation policy source/version and positive limit.
- `response`: the complete strict-schema wire response. Candidate evidence uses
  `source: "title"` or `source: "tag"`, as the live adapter does.
- Optional `model` and `usage`: actual returned model identifier and provider usage
  object. Unknown values must stay absent/null.

`replay_binding(stage, payload, schema, profile)` constructs the four required
binding fields. `digest` uses UTF-8 JSON, Unicode preserved, sorted object keys and
compact separators; arrays retain order. The synthetic `RecordingFixture` in
`hub/tests/test_icon_match_paired_evaluation.py` demonstrates full bound entries.
The existing single evaluator's older replay format is unchanged.

Case IDs bind request and catalogue, but are not sufficient replay provenance.
Every entry is checked against all binding fields **before** exposing its response,
model or usage. A changed prompt, policy, schema, verification shortlist, rationale
or repair payload rejects stale recordings. Do not relabel old traces by replacing
hashes to make them fit. Binding verifies declared provenance, not authenticity of
user-supplied recordings. Historical replay usage is labeled `recorded_response`;
replay makes zero new wire calls.

Instead of `response`, recorded failures can carry a sanitized `error` object:

```json
{"category": "http", "status": 429}
```

Supported categories are `timeout`, `http` and `invalid_response`. HTTP statuses
are limited to 400, 401, 403, 404, 408, 422, 429, 500, 502, 503 and 504. Shared
orchestration retries transient statuses/timeouts only; terminal errors do not
retry. Each retry/repair needs its own bound entry. HTTP replay uses zero backoff
rather than reproducing historical waiting. Never include raw exception bodies,
headers, credentials or keys. Invalid categories fail closed. A binding mismatch
is reported separately in `replay_diagnostics`.

## Future billable opt-in (not run for this implementation)

Both `--live` and an explicit positive `--maximum-wire-calls` are required:

```bash
python manage.py evaluate_icon_matching_paired \
  --catalogue-json /tmp/approved-catalogue.json \
  --requests-json /tmp/approved-requests.json \
  --output-json /tmp/paired-live.json \
  --live --maximum-wire-calls 20 --limit 4 --arm-timeout 180
```

This example authorizes up to **20 wire calls**, not 20 cases or pairs. The shared
budget is consumed at provider dispatch across both arms and every retry/repair;
SDK retries are disabled. Existing per-arm call/deadline limits also apply.
Exhaustion records `partial_budget` for an arm that dispatched work and
`skipped_budget` for an undispatched arm. All later pairs/arms remain in the report.
`--live` and `--responses-json` are mutually exclusive. Unsupported model or
parameter combinations fail visibly through arm outcomes; no substitution occurs.

## Reading the report

The same canonical catalogue/request and case order run through independent
provider instances. Which arm runs first alternates by case and is recorded.
Each arm records configured/returned models, profile/prompt/policy hashes and
versions, reasoning, limits, input/catalogue digests, ranked IDs and evidence,
assignment eligibility, diagnostics, elapsed time and actual wire-call count.
The nested outcome retains its legacy catalogue digest algorithm; the paired
input/catalogue digests use the canonical `digest` function above.

Usage is saved before parsing or validation, including malformed/truncated
responses. Per-call usage retains cached/reasoning token details. Totals sum only
provider top-level prompt/completion/total counts, never their detail subsets.
Unavailable usage is null, not zero; any unknown call usage makes the corresponding
aggregate total unknown. No cost estimate is made. Transport failures may have
unknown usage despite consuming a wire-call slot.

Summaries show completion, recommendation availability, eligibility and top-ID
agreement with explicit denominators. Only completed arms count as legitimate
no-match. Failed/skipped arms are retained and are not accuracy observations.
Replay/offline latency is not evidence of live latency. Full-catalogue assessment
is validated model attestation, not proof of semantic recall; positives are bounded,
not exhaustive retrieval.

There are no reviewed accuracy labels in this iteration and **no live evidence
for the new profile**. This compares bundled model/prompt/policy changes, not the
model alone, and establishes no winning profile or measured improvement. Full CI
and independent review are handled by the parent workflow before any future run.
