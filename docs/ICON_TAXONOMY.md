# Icon taxonomy automation

This additive pipeline prepares private, versioned icon evidence and deterministic
matching. Icons need no feast association. The installed `armenian-lectionary`
library remains the only calendar authority. Only `evaluate_icon_taxonomy
--calendar` enumerates calendar names in this feature; its output is a test
artifact, never runtime reference data. Existing calendar services are unchanged.

All new provider dispatch, request adaptation and routing are disabled by default.
The production matcher remains the existing GPT-4.1-mini baseline. Existing
control/v1/v2 experiment profiles and their prompts are unchanged. No existing
icon selection is rewritten. No live pilot, backfill, deployment or model calls
were performed to implement this feature.

## Storage and evidence

Migration `icons.0019` adds original filenames, independent content digests and
generations, vocabulary/provenance records, analyses, observations, assertions,
projections, an outbox, reservation ledgers and a request-interpretation cache.
It changes no existing image paths, tags, titles, churches or selected icons.
Filename preservation is automatic in `Icon.save`, including API, admin and the
shared `ingest_icon` import boundary. Unicode basenames are retained, directory
components/control characters removed, and length capped at 255. A replacement
filename is captured only when the image is actually saved. Positional
`save(..., update_fields)` is supported. Legacy recovery only recognizes the
known timestamp/UUID storage pattern and labels its stem as lossy; unknown paths
stay blank. A filename is a metadata claim, never visual proof.

The vocabulary is seeded from explicit qualified metadata grammar and small
sourced iconographic definitions, independently of the calendar. `John the
Baptist`, `Gregory of Narek`, and saint/person-context labels with multiple name
components can introduce identities without a saint registry. Arbitrary
multiword phrases and bare `Saint Peter` cannot. Qualifiers are retained;
transliteration guesses, similar spellings and unknown aliases are unresolved.
Exact sourced multilingual aliases are supported. An engine-supplied bilingual
commemoration can be used for that request without persisting a calendar alias.
The ordinary metadata grammar is intentionally conservative, not an exhaustive
natural-language name recognizer.

Themes include prayer, charity, repentance, teaching, healing, mercy, service,
gratitude, humility, trust, hope, hospitality, love, peace and mourning. Explicit
scene definitions currently cover the Last Supper, Washing of Feet, Return of
the Prodigal Son, Ascension and Annunciation. Their software source definitions
and biblical references are in `taxonomy_vocabulary.py`. These are meanings and
sourced scene/theme relations, not feast records. Extending these general rules
requires a rule/prompt version change; do not add identity aliases based on model
suggestions. Source definitions/relations may be imported as ordinary ORM
vocabulary records with exact provenance. No manual per-icon content review or
saint-by-saint registration workflow exists.

Stage one sends only a private, EXIF-oriented RGB image derivative (at most
1536×1536; no public URL or metadata). It records bounded observations,
inscriptions, depiction and figure count. Stage two compares metadata/canonical
meanings with that independent evidence, referencing allowed IDs. It cannot
promote confidence, repetition or reassurance into proof. Strict local schema
and reference validation treats all model content as untrusted data. Existing
OpenAI SDK 3.3.1 is reused with Responses, structured outputs, Luna reasoning
`none`, a 4096-output-token bound and SDK retries disabled.

Fixed rules publish per-attribute supported/unknown/contradicted states. Unique
qualified metadata produces metadata-supported suggestions. Exact readable
qualified inscriptions may corroborate them; known inscriptions also produce
image-derived suggestions when source metadata is generic. Unknown/ambiguous
inscriptions are not invented identities. Explicit scene observation combinations
and sourced relations support event/theme suggestions without requiring
inscriptions on every narrative image. Automatic event eligibility still needs
corroborated event **and** participant evidence. Complete pairs accept separate
member tags; partial members are suggestions. An exact sourced open-group ID
can match without an invented member list. Disputed identity does not erase
independent themes.

An analysis stores code/schema/model versions and dependency fingerprints for
meanings used by assertions, their aliases, ambiguous-alias competitors and
relevant relations. Unrelated new subjects do not invalidate completed analyses
or consume new calls. Changing a used definition/alias/relation, introducing a
collision, or changing a code/release version makes relevant projections
ineligible immediately on query/assignment. Reconciliation queues their new
revision. Stage-one observations remain reusable by church, actual image bytes,
model, prompt, schema and image-processing version. Completed analyses/assertions
are retained for audit; mutable execution fields are only written while pending.
No private traces are exposed by public serializers.

## Ingestion and consistency

Use `icons.services.ingestion.ingest_icon(icon=..., image=..., title=...,
church=..., tags=[...])` for imports. API writes wrap creation/replacement and
final tags in one transaction; admin finalizes after `save_related`. Model writes,
forward/reverse tag edits, through-row edits and tag renames invalidate the active
projection and update a durable desired revision. Rollback discards work and
wakeups. Duplicate-upload behavior is retained with an inner savepoint for the
409 lookup. Prayer imports consume icons; they are not image ingestion.

Supported image saves lock the icon row and advance its durable byte digest and
content generation, including explicitly saved same-path replacements. Search
and assignment use database generations and dependency guards only: they never
download or resize catalogue images. The worker reads real bytes before stages
and before publication outside database locks, then rechecks the supported-write
generation, metadata, dependency versions and lease under compatible locks.
Provider calls never run inside a database transaction.

Direct external storage writes and SQL/bulk updates are **not** supported
transactional ingestion APIs. S3 and the database cannot be made one atomic
transaction. Such writes must invoke ingestion/reconciliation before matching;
bounded backfill reconciliation reads actual bytes and advances generations.
There is no claim that an unannounced S3 overwrite is atomically observable by a
DB-only search before that reconciliation. Storage writes that fail/roll back can
leave orphan files, as with ordinary Django file storage; taxonomy publication
remains guarded by database state and valid bytes.

Outbox workers use leases, conditional claims and unique versioned analyses.
Observation leases prevent duplicate simultaneous image calls. Transient errors
receive at most three attempts with 30/60-second backoff; expired leases recover
after ten minutes. Timeouts retain unknown usage reservations. Completed stages
are reused. Schema/invalid-image/permanent errors are terminal unavailable,
without a human queue. Semantic uncertainty completes normally and is not retried
until inputs or policy change. Budget-blocked work stays frozen until explicitly
resumed. The explicit dispatcher recovers lost broker messages; duplicate delivery
cannot publish twice.

## Operations (future authorized rollout only)

Run these commands in the configured application runtime. Implementation tests
inject providers and never use application credentials for live calls.

```sh
python manage.py migrate icons
python manage.py backfill_icon_taxonomy --dry-run --church 1 --limit 100
python manage.py backfill_icon_taxonomy --church 1 --limit 100 --after-id 0
```

The second command only enqueues/reconciles. Use the reported `next_after_id` as
`--after-id` to resume. `--church` is optional for catalogue maintenance. Storage
reads are bounded by record limit, 25 MiB compressed image size, 40 million pixels,
and the derivative limit. Missing/unreadable images become unavailable. Ordinary
bulk metadata writers must reconcile; do not use bulk updates as ingestion.

Before any billable rollout, authorize explicit cumulative calls, tokens and USD
caps and set:

- `ICON_TAXONOMY_DISPATCH_ENABLED=true`
- `ICON_TAXONOMY_BUDGET=<unique authorized budget name>`
- `ICON_TAXONOMY_MAX_USD_PER_MILLION_TOKENS=<conservative maximum input/output rate>`
- Optional `ICON_TAXONOMY_MODEL` (default `gpt-5.6-luna`) and
  `ICON_TAXONOMY_RELEASE` (default `catalogue-v1`).

There are no implicit prices or budgets. A zero/unconfigured price prevents
reservations. Reservations use UTF-8 payload bytes plus 8192 tokens for prompt,
schema and bounded output, plus 65,536 for a <=1536px image. USD reservation uses
the configured conservative maximum token price. Confirm those conservative
bounds/prices for the selected model before rollout. Reservations happen
atomically immediately before **each actual wire attempt**, including stage two,
adapter calls, retries and timeouts. Counters are cumulative and never refunded;
actual usage and returned model are recorded separately. These deliberately high
bounds can stop a pilot well before its nominal call count. No spend forecast or
real-world model-quality claim was established offline.

Explicit provisioning and direct dispatch, without installing a broker or scheduler:

```sh
# Replace all CAP_* values with separately authorized positive numbers.
python manage.py dispatch_icon_taxonomy --inline --church 1 --limit 20 \
  --budget approved-pilot --max-calls CAP_CALLS --max-tokens CAP_TOKENS \
  --max-spend CAP_USD --concurrency 1
```

Supplying all caps explicitly creates that named budget. Existing counters/caps
are never reset or enlarged implicitly; differing caps are rejected. Omitting
caps uses an existing enabled budget. `--concurrency` is bounded to 1–4 for direct
execution. For broker delivery omit `--inline`; workers still require deployment
dispatch configuration. `backfill_icon_taxonomy --dispatch` accepts the same
budget/concurrency options and record/church/checkpoint bounds. To resume after
a separately authorized new budget is configured, use
`backfill_icon_taxonomy --resume-budget-blocked` (optionally `--dispatch`).

For recurring recovery, explicitly set `ICON_TAXONOMY_BEAT_ENABLED=true` on the
**existing** Celery beat deployment. This adds only the one-minute
`icons.tasks.dispatch_icon_taxonomy` entry to the existing managed schedule; it
does not create/start a scheduler or overwrite other entries. Upload commits
also wake that dispatcher with a three-second delay after the two-second
debounce. The periodic task recovers broker failures and expired leases, and
queues stale dependency/code versions. Without beat, an explicitly configured
external scheduler must invoke `dispatch_icon_taxonomy`, or an operator must run
it. Do not assume a one-shot upload wake is a recovery scheduler.

## Matching, evaluation and rollback

`ICON_MATCH_ROUTER_MODE` defaults to `baseline`; supported alternatives are
`shadow`, `taxonomy_suggestions`, and `taxonomy`. Deployment settings may set
`ICON_MATCH_ROUTER_SCOPES` with keys such as `feast:1`, `content:1` or
`content:None` for per-church/consumer rollout. Public search keeps its optional
church filter; feast/prayer consumers require their church. The allowed caller
catalogue never expands. Invalid modes and failed taxonomy gates never fall
through to baseline assignment.

Ranking is deterministic: exact event, complete subject/group portrait, partial
member, then explicitly sourced related/thematic suggestions; coverage, evidence
and stable title/ID break ties. Limits are maxima. Event intent remains structured
when the requested event has no catalogue concept (for example Beheading of a
known qualified subject), so supported portrait fallback still works. An
unclassified/stale catalogue or an available exact event blocks automatic
missing-event portrait fallback. Identity constraints, negation, unknown
participants and nonidentity requests remain distinct. Unknown specific names
cannot turn into themes just because their words contain a theme alias.

Automatic selection rechecks target church/title/tags/empty selection and locked
icon generation/projection/dependencies. Manual selections remain untouched.
Taxonomy public responses are not cached across revisions; baseline caching is
unchanged. Metadata evidence stays suggestion-only under the initial strict gate.
`taxonomy_suggestions` disables all automatic assignments.

`ICON_TAXONOMY_REQUEST_ADAPTER_ENABLED` defaults to false. If explicitly enabled,
a single strict, budgeted call may map unknown **thematic** wording to existing
allowed theme IDs. It cannot create identity, choose icons or assign anything.
Cache keys include original request/context, church scope, theme definitions,
model/release and adapter version. Unknown spans are retained by the deterministic
request; adapter-derived suggestions never become autoeligible. Failure/deadline
falls back to controlled lexical themes or unknown. The endpoint's 20-second
budget is passed through to the adapter's remaining timeout. Deadline checks also
bound deterministic iteration. Public shadow requests skip synchronous shadow
work to preserve baseline latency; background consumers and evaluation supply
shadow diagnostics without an adapter call.

```sh
python manage.py evaluate_icon_taxonomy --church 1 --request 'mercy' \
  --request 'Beheading of Saint John the Baptist' --output /tmp/taxonomy.json
python manage.py evaluate_icon_taxonomy --church 1 --calendar \
  --output /tmp/calendar-coverage.json
python manage.py evaluate_icon_taxonomy --church 1 --fixtures frozen-requests.json \
  --output /tmp/regression-coverage.json
```

Fixture rows accept `text` or `primary_text`, `kind`, and `context_terms`; each
original row/label is retained. Use the original frozen 28-case artifact without
relabeling it. Existing paired/control/v1/v2 replay commands remain unchanged.
Calendar evaluation reads both engine labels throughout its installed supported
range, emits every distinct pair, and reports unresolved parsing separately from
candidate/portrait availability, automatic eligibility and unclassified records.
Non-festal thematic requests are evaluated independently. Evaluation never calls
the provider or changes calendar/taxonomy data. Reports measure evidence
consistency and retrieval coverage, **not** real-world saint-identification
accuracy. There is no live model-as-judge or human content review queue.

Rollback is configuration-only: set router mode/scopes back to baseline, disable
request adaptation, set dispatch and beat flags false, and disable the authorized
budget. In-flight calls cannot be unbilled, but no new real provider call passes
the dispatch gate after it is disabled. Existing selections and additive audit
data remain. Prefer retaining migration 0019 during rollback; reversing it drops
only the newly added evidence tables/fields and requires a separate backup/data
retention decision. Do not reverse unrelated migrations.

No UI change is required beyond the image-field filename help text. A future
optional UI could display evidence level and aggregate unavailable/unknown
counts; it must not turn uncertainty into a content-review assignment.

## Validation and limitations

`icons.tests.test_taxonomy` uses `TransactionTestCase`, filesystem fixtures,
mocked provider/SDK calls and atomic budget contention. It covers ingestion,
rollback/broker failure, leases, retries, stale edits, content generations,
per-analysis dependencies, aliases, pairs/open groups, event absence/precedence,
non-festal observation-only evidence, scope, commands, adapters and baseline
routing. Existing icon/service/consumer/profile regressions remain applicable.

The local shared virtualenv may contain lectionary 2.0 despite the retained source
pin `<2.0.0`; do not mutate it to hide unrelated calendar failures. Migration
inspection also encounters a pre-existing test-only inner `OfflineS3Storage`
serialization issue and unrelated `hub.LLMPrompt.model` choice drift. Use a
throwaway settings override disabling that test storage and compare only icons
migration state; no unrelated schema change belongs in this work.

Offline fixtures do not establish visual accuracy, Armenian inscription recall,
production latency, real provider pricing or full calendar availability. Generic
appearance does not prove saint identity. Unrecognized grammar/source aliases
remain unresolved, and new iconographic scene/theme meanings need sourced
software definitions. Budget caps, strict corroboration and lack of a real-world
pilot can leave many icons suggestion-only or unavailable. Full Crabbox CI,
independent final review and any separately authorized rollout/publishing belong
to the parent workflow.

Implementation validation on 2026-09-05 used the requested shared Python runtime
with `AWS_CONFIG_FILE=/dev/null`, `AWS_SHARED_CREDENTIALS_FILE=/dev/null`, and
`AWS_EC2_METADATA_DISABLED=true`:

```sh
python manage.py test icons.tests \
  hub.tests.test_icon_match_service hub.tests.test_icon_match_consumers \
  hub.tests.test_icon_match_profiles hub.tests.test_feast_icon_matching \
  hub.tests.test_feast_match_icon_api hub.tests.test_feast_assign_icon_api \
  hub.tests.test_feast_admin_icon_actions hub.tests.test_icon_match_evaluation \
  hub.tests.test_icon_match_paired_evaluation --noinput --settings=tests.test_settings
```

283 targeted tests passed, including 51 taxonomy invariant tests. Changed-file
Ruff lint and `git diff --check` passed. `makemigrations icons --check --dry-run`
reported no changes with the throwaway storage-settings workaround above.
The corresponding full-project drift check reports only the existing
`hub.LLMPrompt.model` choice change; no hub migration was kept. Source dependency
pin `armenian-lectionary>=1.3.0,<2.0.0` and baseline/profile files are unchanged.

Round-three corrections retain whole original title/tag/filename sources alongside
resolved claims and unresolved spans in comparison and private assertion evidence.
Metadata is rejected rather than truncated above 64 source entries, 16,000 raw
source bytes or 48,000 parsed-source bytes. Bare event types, explicit scene/event
labels and unfamiliar action/scene descriptors block portrait fallback while
unresolved; ordinary search tags alone do not. Model reassurance cannot override
that guard. Arbitrary unmarked prose can still be semantically unknown; preserving
it for comparison does not claim exhaustive deterministic scene recognition.

Alias dependency fingerprints now include every lookup term from parsing,
including separator-normalized filename stems and unresolved conjunction/event
components. New aliases affecting those terms invalidate only dependent analyses;
unrelated vocabulary additions remain inert. Rule and comparison-prompt versions
advance for these corrections while the independent observation cache remains
reusable. An event with required participants receives exact-event ranking only
with their complete supported coverage. Scene-only evidence remains a related
suggestion and cannot suppress a fully covered portrait merely by carrying an
event label. Supported events with no required participants can still be exact
event suggestions under the unchanged strict automatic-assignment policy.
