# Independent icon catalogue backfill

This pipeline enriches private, versioned catalogue evidence. Non-festal icons are
first-class: icons need no feast association. The installed `armenian-lectionary`
library remains the sole calendar authority; ingestion never enumerates a calendar
or builds a parallel feast registry. The dependency retains Andy's
`armenian-lectionary>=1.3.0,<2.0.0` pin.

Runtime matching remains main's existing implementation. No feast/prayer
assignments, image selections, titles or tags change. Matcher routing, request
adaptation and matching evaluation commands belong to the later matching PR,
with independent matching tests. There is no manual content-review queue.

## Storage and ingestion

Additive `icons.0019` preserves the source PR's exact schema: original filenames,
image content digests/generations, vocabulary/provenance, analyses, observations,
assertions, projections, durable work and cumulative reservation ledgers. The
unused request-interpretation cache table and dormant adapter schema/prompt are
retained for migration compatibility; their execution consumer is not included.
No existing image paths or catalogue content are rewritten by the migration.

`Icon.save` preserves uploaded Unicode basenames (directory/control characters
removed, length capped at 255), including API/admin and positional `update_fields`.
Only an actually saved replacement image changes the captured filename. Legacy
backfill recovers only the recognized timestamp/UUID storage stem and marks it
`recovered_storage_stem`, which is lossy; unknown paths remain blank. Filenames
are metadata claims, never visual proof.

Imports should call `icons.services.ingestion.ingest_icon(icon=..., image=...,
title=..., church=..., tags=[...])`. API writes commit the image and final tags
together; admin finalizes after `save_related`. Model saves, tag rename and
forward/reverse/through-row tag mutations invalidate projections and update a
durable revision. Transaction rollback discards outbox changes and wakeups.
Supported image saves advance a content digest/generation, including same-path
replacement. Provider calls and storage reads occur outside database locks;
publication rechecks metadata, byte digest, generation, dependencies and lease.

SQL/bulk updates and external storage overwrites are not transactional ingestion
APIs. Reconcile them explicitly before relying on new evidence. External storage
and the database are not a single transaction; rolled-back writes can leave orphan
files as in ordinary Django storage. Backfill may read private storage to compare
actual bytes, bounded by batch size, 25 MiB compressed images, 40 million pixels
and an EXIF-oriented RGB derivative at most 1536×1536. Missing/unreadable images
become unavailable during processing.

## Evidence and consistency

Qualified source metadata grammar and small sourced iconographic definitions seed
the vocabulary independently of feast dates. Qualifiers and exact sourced
multilingual aliases are preserved; arbitrary phrases, bare ambiguous names and
transliteration guesses do not become identities. Scene/theme definitions and
references live in `icons/services/taxonomy_vocabulary.py`. Extending general
meanings requires sourced definitions and appropriate rule/prompt version changes,
not model-suggested identity aliases or saint-by-saint registration.

Stage one receives only the private image derivative, never public URLs or source
metadata. Stage two compares whole title/tag/filename sources, including unresolved
spans, with independent observations and allowed concept IDs. The Responses SDK
uses strict schemas, bounded output and no SDK retries. The icons-local
`taxonomy_schema.py` validates the strict schema subset without importing matching.
Fixed acceptance rules publish supported/unknown/contradicted attributes; model
confidence or metadata repetition cannot create proof. Qualified readable
inscriptions can corroborate identity, while visible activities and sourced scene
relations support non-festal themes even for generic metadata. Identity disputes
do not erase independently supported themes. Unresolved scene claims block a
supported portrait attribute; ordinary search tags alone do not.

Analyses preserve versions and fingerprints of used meanings, aliases, collisions,
relations and unresolved lookup terms, including normalized filename components.
Unrelated vocabulary additions reuse completed evidence. Changed dependencies or
versions are detected by reconciliation and by worker stale guards; consumers of
private projections must check them rather than treating row existence as currency.
Observation reuse is scoped by church, bytes, model, prompt, schema and processor.
Completed analyses/assertions remain for audit; public serializers expose no traces.

Work and observation leases prevent duplicate publication and simultaneous image
calls. Transient failures have at most three attempts with 30/60-second backoff;
expired leases recover after ten minutes. Observation contention defers without
spending a work attempt. Completed stages are reused. Schema, invalid-image and
permanent errors are terminal `unavailable`; uncertainty completes normally until
inputs/policy change. Budget-blocked work stays frozen until explicitly resumed.

## Commands and checkpoints

Use the configured application runtime. These are operational examples for a
separately authorized rollout, not instructions to run against production now.
Built-in discovery:

```sh
python manage.py help backfill_icon_taxonomy
python manage.py help dispatch_icon_taxonomy
```

Read-only preview (all churches by default):

```sh
python manage.py backfill_icon_taxonomy --dry-run --limit 100 --after-id 0
```

Dry-run writes nothing, creates no budget and calls no provider, even with
`--dispatch`; it can read private storage. Enqueue/reconcile a bounded batch:

```sh
python manage.py backfill_icon_taxonomy --limit 100 --after-id 0
# Set NEXT_AFTER_ID from the preceding response.
python manage.py backfill_icon_taxonomy --limit 100 --after-id "$NEXT_AFTER_ID"
```

**Without `--dispatch`, backfill makes no direct provider calls, but enqueue can
wake enabled background workers and become billable.** Omit `--church` for all
churches or append `--church "$CHURCH_ID"` with the intended church PK. Backfill
scans ascending icon PKs after the exclusive `--after-id`, up to the positive
`--limit` (default 100). JSON `rows` records pre-reconciliation state/reason;
`next_after_id` is the last scanned PK, unchanged for an empty batch. Repeat with
that checkpoint until `rows` is empty. `states` contains inline results only;
enqueue returns an empty states list, not a promise that background work is idle.
Use a new scan from zero when revisiting previously scanned budget-blocked rows.

## Guarded dispatch and cumulative budgets

Dispatch is off by default. Before an authorized billable run, configure:

- `ICON_TAXONOMY_DISPATCH_ENABLED=true` and application provider credentials.
- `ICON_TAXONOMY_BUDGET` or `--budget`: an explicitly provisioned, enabled name.
- `ICON_TAXONOMY_MAX_USD_PER_MILLION_TOKENS`: a positive finite conservative maximum
  of input/output token prices; zero (the default) prevents reservations.
- Optional `ICON_TAXONOMY_MODEL` (default `gpt-5.6-luna`) and
  `ICON_TAXONOMY_RELEASE` (default `catalogue-v1`); `ICON_TAXONOMY_TIMEOUT` is 60s
  by default, and the provider also bounds supplied timeouts at 120s.

Use an existing enabled budget for synchronous processing:

```sh
python manage.py backfill_icon_taxonomy --dispatch --budget "$BUDGET_NAME" --limit 20
python manage.py dispatch_icon_taxonomy --inline --budget "$BUDGET_NAME" --limit 20
```

Provisioning example: set every variable below to an explicitly authorized name
and positive cap before use; the placeholders deliberately specify no pilot caps.

```sh
python manage.py dispatch_icon_taxonomy --inline --budget "$BUDGET_NAME" \
  --max-calls "$CAP_CALLS" --max-tokens "$CAP_TOKENS" --max-spend "$CAP_USD" \
  --limit 20 --concurrency 1
```

All three caps are required together to create a budget. Existing differing caps
are rejected; existing counters/caps are never implicitly reset or enlarged.
Omit caps to reuse an enabled budget. Calls, tokens and USD limits are cumulative
across stages, invocations, workers, retries and timeouts, never reset or refunded.
Each actual wire attempt reserves atomically: UTF-8 payload bytes plus 8192 tokens
for prompt/schema/output, plus 65,536 image tokens where applicable. USD uses the
configured conservative rate. Unknown/timeout usage retains the full reservation;
reported actual usage/model are recorded separately. These bounds can stop work
well before the nominal call cap; verify bounds/prices for the selected model.

`backfill --dispatch` runs inline synchronously. `dispatch --inline` also runs
inline; omitting `--inline` queues Celery tasks instead. `--concurrency` is 1–4
inline threads (default 1), not a call cap or a limit on deployment-wide Celery
workers; it is ignored for enqueue/broker execution. Standalone dispatch takes
up to `--limit` (default 20) due rows ordered by availability then work PK. Its
optional church filter scopes dispatched work; preceding bounded version
reconciliation scans across churches. Output is processed/states inline or a
broker dispatch count. It has no checkpoint or dry-run; use backfill for those.

After configuring an authorized budget with sufficient remaining capacity:

```sh
python manage.py backfill_icon_taxonomy --resume-budget-blocked --dispatch \
  --budget "$BUDGET_NAME" --limit 20 --after-id 0
```

`--resume-budget-blocked` resets attempts and requeues scanned blocked work only;
it never resets budget counters, enlarges caps or discards cached observations.
An exhausted budget blocks again. It also works without inline dispatch, subject
to the same background billing caveat; ignored in dry-run.

Upload commits wake the dispatcher after three seconds (two-second debounce).
For recurring recovery, `ICON_TAXONOMY_BEAT_ENABLED=true` adds an opt-in one-minute
entry to existing Celery beat; it does not start/install a scheduler. Defaults
remain off. Without beat use the explicit dispatcher or a configured external
scheduler to recover broker failures/expired leases and changed dependencies.
A one-shot upload wake is not a recovery scheduler.

## Inspecting private outcomes and stopping

Use the ORM in the authorized runtime, keeping private evidence out of public logs:

```python
from django.db.models import Count
from icons.models import IconAnalysis, IconTaxonomyProjection, IconTaxonomyWork, TaxonomyBudget
from icons.services.taxonomy_inputs import dependencies_current, versions

work = IconTaxonomyWork.objects.all()  # optionally .filter(icon__church_id=church_id)
list(work.values("state").annotate(count=Count("pk")).order_by("state"))
list(work.filter(state__in=["unavailable", "budget_blocked"]).values("icon_id", "error", "attempts"))
list(TaxonomyBudget.objects.values("name", "enabled", "calls", "tokens", "microdollars",
                                  "max_calls", "max_tokens", "max_microdollars"))
# For an intended icon_id:
projection = IconTaxonomyProjection.objects.select_related("analysis").filter(icon_id=icon_id).first()
if projection:
    analysis = projection.analysis
    current_policy = analysis.versions == versions() and dependencies_current(analysis.dependencies)
    attributes = projection.attributes  # private per-attribute evidence, not assignment decisions
history = IconAnalysis.objects.filter(icon_id=icon_id).order_by("-created_at")
```

Policy currency alone does not replace metadata/generation/lease checks; processing
performs those before publishing. Work states distinguish execution failures from
completed unknown/contradicted attributes. No manual review workflow is required.
To stop future dispatch, disable dispatch/beat and the budget; already in-flight
calls cannot be unbilled. Preserve additive audit data and migration 0019. Reversing
it drops evidence and requires a separate retention/backup decision.

## Validation boundaries

`icons.tests.test_taxonomy_backfill` runs offline independently of matching modules
with filesystem images, mocked provider/SDK requests, stale mutation, budget and
lease contention, ingestion/command contracts and source-evidence checks. Existing
icon/upload/cache regressions remain applicable. Offline tests establish neither
visual accuracy nor real-world cost/latency/Armenian inscription recall. Full
Crabbox CI and independent review of final branches belong to the parent workflow.
