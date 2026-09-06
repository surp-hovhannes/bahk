# Independent icon catalogue evidence and matching

Icons need no feast association. The installed `armenian-lectionary` library is
the calendar authority; this pipeline creates no second calendar or manual review
queue. The GPT-4.1 mini matching baseline, existing router defaults and A/B controls
are unchanged. Taxonomy suggestions use private current projections and preserve
the strict automatic-assignment guard. Backfill changes evidence/work records,
recovered filenames and image digests; it changes no existing feast/prayer image
assignments, titles or tags.

## Versions, identity and evidence

The default release is `catalogue-v2`. The seeded definitions changed, so v1 must
remain immutable: write commands reject an explicitly pinned `catalogue-v1`.
Choose a new release before reconciling. No new migration is needed. Completed
analyses remain immutable; reconciliation publishes a new projection from a new
analysis or reuses an already complete compatible analysis.

Canonical identity preserves names, roles, places, epithets and ordinals. Role
order is normalized, but bishop and patriarch are distinct unless a sourced alias
explicitly establishes equivalence. Known Christ and Marian title aliases resolve
after saint/depiction markers are removed. A saint-marked unqualified name can be
a metadata suggestion; it cannot establish unambiguous identity or authorize an
assignment. Unqualified tag-only names remain weak candidates unless a title or sourced group
provides identity context; they neither publish depicted identity nor compete with
a stronger title. Qualified tags and known aliases remain useful. Descriptive
separators are normalized before saint/role grammar without changing raw sources.
Unknown language/qualifiers and negation remain unresolved. City names
are not a provenance blacklist. Denomination, style, church/product provenance,
unknown scenes and arbitrary portrait tags do not mint person concepts.

Original source text, parsed identity, qualifiers and recognized filename
transformations remain in private evidence. Filenames are weak clues. Recognized
extensions, storage random suffixes and product suffixes can be removed for lookup;
content numbers and unknown suffixes remain. Filenames resolve existing aliases
but never independently create competing person concepts. Conflicting filename
clues remain uncertainty diagnostics.

Stage one receives only a private image derivative. The new strict observation
schema separates `code`, literal `text`, `region`, `uncertain`, and inscription
`literal_spans`. A code or model agreement alone is insufficient proof. Activity
rules require explicit literal support, including action/object relationships:
washing hands beside visible feet is not foot washing. A shared meal around a
table need not repeat the literal code `shared_supper`. Bowed posture does not
establish repentance. Themes derived from activities or sourced scene relations
have `inferred` evidence level; metadata-only scene relations remain labelled in
their cited evidence. Generic attire, halos, beards and gestures never upgrade a
person's identity.

Readable exact inscription spans can support identity. Legacy quoted spans use
conservative exact matching, without fuzzy saint matching or invented
transliteration. Unreadable/uncertain observations prevent a corroboration upgrade.
The signature extension requires sourced definitions, multiple independently
supported distinctive features, and a unique match with collision checks. Its
code-owned distinctive-feature registry is deliberately empty: there are no
built-in person signatures and no promise of visual identity proof without
inscriptions. Adding one requires sources, feature rules, counterexamples and a
rule-version change; generic activity codes are not distinctive identity features.

Stage two has one required object slot per supplied claim ID and closed observation
references. Empty claim sets skip this call entirely. Historical array responses
remain replayable: valid entries survive foreign IDs, invalid references or a
malformed sibling entry. Identical semantic duplicates coalesce; conflicting
duplicates make only that claim unknown. Raw assertions and per-entry sanitized
accepted/discarded/ambiguous diagnostics are retained. Grossly malformed or
oversized responses fail boundedly. Duplicate JSON object keys are rejected rather
than silently choosing a value. Metadata disagreement, missing names and extra
figures are uncertainty. Affirmative incompatible identifying evidence in a
single-figure portrait can contradict a competing subject; context booleans alone
cannot. Independent nonidentity evidence survives comparison failures.

Matching and command inspection share `projection_diagnostics`: missing projection,
analysis incomplete, component version mismatch, dependency change, metadata
fingerprint, image digest/revision, church and work revision/state/fingerprint.
All applicable codes are returned without private values. Currency is stricter
than completion. Exact events rank before portrait fallback; incomplete or
uncertain requests remain suggestions. Identity requests do not become generic
theme requests. Automatic assignment still requires full qualified corroborated
identity, the appropriate depiction, current evidence and no unresolved constraints.

## Ingestion and retained observations

`Icon.save` preserves uploaded Unicode basenames, strips directory/control
characters and bounds length. Only a saved replacement image changes its captured
filename. Legacy recovery recognizes the timestamp/UUID storage-stem pattern and
marks `recovered_storage_stem`; unknown paths remain blank. Use
`icons.services.ingestion.ingest_icon` for imports. API/admin/tag write routes
invalidate projections and advance durable work revisions. Rollback discards
outbox changes and wakeups.

Supported image saves advance content digest/generation, including same-path
replacement. SQL/bulk writes and external storage overwrites need explicit
reconciliation. Storage reads/provider calls occur outside database transactions;
publication rechecks inputs, bytes, dependencies and lease. Images are bounded by
25 MiB compressed data, 40 million pixels and an EXIF-oriented RGB derivative at
most 1536×1536. Missing/unreadable input becomes unavailable.

Observation cache identity includes church, image bytes, requested model/profile,
observation prompt/schema and processor. Acceptance-rule/release changes do not
alter the image-only cache key. Explicit recovery can reuse compatible retained
legacy Luna evidence, labelled `retained_compatible` with its original observation
key; it is not relabelled as a response to the new prompt. Terra cannot reuse Luna
image observations. Completed audit history and cumulative reservations are kept.

Dependency snapshots include used meanings, sourced aliases and competitors,
relations and unresolved lookup terms (including inscription spans). Unrelated
catalogue additions should not stale an analysis. A newly recognized relevant
alias can correctly stale earlier evidence. Inspect the entire completed batch,
then reconcile affected IDs using retained observations. Do not treat immediate
per-row completion as final cohort eligibility.

## Commands, selection and inspection

Run in the configured application runtime. These examples require separate
production authorization; documentation is not authorization for a paid run.
Discover all flags with:

```sh
python manage.py help backfill_icon_taxonomy
python manage.py help dispatch_icon_taxonomy
```

Exact read-only preview and persisted inspection:

```sh
python manage.py backfill_icon_taxonomy --dry-run --church "$CHURCH_ID" --icon-ids 11 17 23
python manage.py backfill_icon_taxonomy --status --church "$CHURCH_ID" --icon-ids 11 17 23 --budget "$BUDGET_NAME"
```

Dry-run writes nothing, creates no budget and calls no provider, even when dispatch
flags are supplied. It may read private storage. Status also avoids storage reads.
Neither seeds vocabulary. Validation rejects invalid limits/checkpoints, duplicate
or out-of-scope IDs, nonfinite caps, invalid concurrency and incompatible model or
inline flags before writes or budget provisioning.

`--icon-ids` is exact, unique and bounded by `--limit` (default 100 for backfill,
20 for dispatch). It cannot combine with checkpoint flags. Alternatively use an
exclusive lower checkpoint and inclusive upper bound:

```sh
python manage.py backfill_icon_taxonomy --dry-run --after-id 0 --through-id 500 --limit 100
python manage.py backfill_icon_taxonomy --dry-run --after-id "$NEXT_AFTER_ID" --through-id 500 --limit 100
```

Rows are ascending PK. `next_after_id` is the last scanned ID, unchanged for an
empty batch. Omitting church scans all churches. Enqueue without `--dispatch`
makes no direct provider calls, but may wake enabled background workers and become
billable. Use explicit inline ownership for a bounded pilot.

## Inline execution, model profiles and budgets

Dispatch and beat remain disabled by default; no scheduler is installed. A normal
bounded pilot needs no custom Python wrapper:

```sh
python manage.py backfill_icon_taxonomy --dispatch --enable-inline \
  --church "$CHURCH_ID" --icon-ids 11 17 23 --budget "$BUDGET_NAME" \
  --model gpt-5.6-luna --concurrency 1
```

`--enable-inline` requires explicit inline execution and an explicit budget. It
enables dispatch only in this command process and restores settings on every exit.
Before lengthy reconciliation, the selected work (including explicit resumes) is
atomically owned in `inline_pending`/`inline_running` states invisible to
background workers, and reconciliation suppresses global queue wakes. Deferred
inline retries stay in `inline_retry` until explicitly resumed. Other selections
and churches remain untouched. Active leases are not stolen.

The configured model default remains Luna. Inline `--model` allowlists
`gpt-5.6-luna` (`luna-none-v1`) and `gpt-5.6-terra` (`terra-none-v1`), both using
reasoning `none`, image input, identical strict contracts and no SDK retries. The
original Luna prompts remain as historical constants. Requested model/profile and
contract versions are fingerprinted; returned model and usage are retained.

Prerequisites include application-owned provider credentials, an enabled cumulative
budget and a finite conservative `ICON_TAXONOMY_MAX_USD_PER_MILLION_TOKENS` covering
the selected model's maximum price. Profile floors are 1.2 USD/M for Luna and 12
USD/M for Terra (input/cache-write/output 0.2/0.25/1.2 and 2/2.5/12 respectively,
as supplied for this evaluation). Verify official provider capabilities/prices
before a live evaluation. No worker-side live evaluation is part of this change.
`ICON_TAXONOMY_TIMEOUT` defaults to 60 seconds and is bounded at 120 seconds.

To provision a new authorized budget, supply all three finite positive caps:

```sh
python manage.py backfill_icon_taxonomy --dispatch --enable-inline \
  --icon-ids 11 17 23 --budget "$BUDGET_NAME" \
  --max-calls "$CAP_CALLS" --max-tokens "$CAP_TOKENS" --max-spend "$CAP_USD"
```

Existing caps must match; an existing disabled budget is rejected even with caps.
Counters and caps are never reset, enlarged or refunded by these commands.
Reservations include UTF-8 payload/schema/instruction bytes, 8192 tokens of framing
and output allowance (output is capped at 4096), and 65,536 image tokens. The
configured maximum model rate prices the reservation. Unknown/timeout usage keeps
the full reservation. Recorded input/output usage is reported separately and is not
an invoice; reserved USD is not actual spend. Inspect remaining capacity before a
resume. Disable the dedicated pilot budget on exit from the operator's overall
workflow, including failed/partial runs; this command does not silently disable a
shared budget or enable a disabled one.

Standalone dispatch also scopes reconciliation and execution consistently:

```sh
python manage.py dispatch_icon_taxonomy --inline --enable-inline \
  --church "$CHURCH_ID" --icon-ids 11 17 23 --budget "$BUDGET_NAME"
```

Without `--inline`, this command queues Celery tasks and can be billable. Inline
concurrency is 1–4 process-local threads, not a call cap or a deployment-wide worker
limit. Use backfill for status, dry-run and checkpoint pagination.

## Recovery and final outcomes

Backfill JSON includes per-icon `before_state`, `action`, `after_state`,
`processing_result`, `analysis_id`, assertion counts by status/evidence level,
`before_freshness`, final `freshness` and sanitized diagnostic codes. It includes
requested versions, summary counts, `budget_reservations` and separate
`recorded_usage`. Raw titles, paths, inscriptions and response prose are not logged.
Complete, partially useful, unknown and contradicted attributes are different from
execution state. Final freshness is checked after all selected rows finish.

Execution failures emit the full JSON then exit nonzero. Some rows may already
have completed. Preserve the JSON and resume failed IDs; advancing the pagination
checkpoint alone will not revisit them. Budget-blocked work requires explicit
resume with an enabled budget (an exhausted budget simply blocks again):

```sh
python manage.py backfill_icon_taxonomy --dispatch --enable-inline \
  --resume-budget-blocked --icon-ids 11 17 --budget "$BUDGET_NAME"
```

Changed rules/release/dependencies can justify a new analysis after unavailable
work. This preserves old evidence and reuses compatible observations:

```sh
python manage.py backfill_icon_taxonomy --dry-run --recover-unavailable --icon-ids 11 17
python manage.py backfill_icon_taxonomy --dispatch --enable-inline \
  --recover-unavailable --icon-ids 11 17 --budget "$BUDGET_NAME"
```

Normal backfill does not reset unchanged terminal failures. Recovery requires
exact IDs or an inclusive upper bound; unchanged malformed evidence/unavailable
images do not get another identical failing analysis key. Error codes distinguish
transport failures, schema/reference problems and missing/oversized image input.
Transport retries remain bounded to three work attempts, with 30/60-second backoff.
Explicitly resume due inline retries or expired abandoned inline leases:

```sh
python manage.py backfill_icon_taxonomy --dispatch --enable-inline \
  --resume-inline --icon-ids 11 17 --budget "$BUDGET_NAME"
```

Active leases are never reclaimed by this flag. Global worker expired leases keep
their existing recovery behavior; inline leases cannot drift to a different
background budget. Observation contention defers without consuming a work attempt.

Upload wakeups retain their existing debounce. Optional existing Celery beat
recovery remains off; no broad configuration/scheduler change is required. Stop
future calls by disabling the dedicated budget and dispatch/beat as appropriate;
already in-flight requests cannot be unbilled. Keep migration 0019 and audit data.

## Offline replay and verification boundaries

Synthetic fixtures are committed; private production exports and full replay
outputs must remain local. The disposable SQLite helper loads only test settings
and forbids provider calls:

```sh
MPLCONFIGDIR=/private/tmp/bahk-mpl-cache python scripts/replay-icon-taxonomy.py \
  /private/tmp/bahk-expanded-results.json --output /private/tmp/bahk-hardening-offline-report.json
```

Historical reference mapping uses saved assertion labels or explicitly labelled
singleton source spans. Foreign historical IDs stay invalid even if a current ID
happens to coincide; unresolved mappings are reported, never invented. Replay
reports old/new support grades, unknown/contradicted attributes, invalid references,
partial usability and cohort dependency currency. A normalization recovery is not
new visual proof. Production freshness causes absent from the export remain
unknown. Separate fresh mock-contract tests exercise the new wire contract; neither
test category establishes new-model compliance or independent real-image accuracy.

Focused suites: `icons.tests.test_taxonomy`, `icons.tests.test_taxonomy_backfill`,
`icons.tests.test_taxonomy_hardening`. The parent owns independent review, required
`scripts/crabbox-validate.sh ci`, publishing and any separately authorized isolated
paired live evaluation. No deployment, production mutation or paid calls occur as
part of offline repair/replay.

After a writable `backfill_icon_taxonomy` batch, selected complete projections whose
only freshness issue is dependency growth are recomputed from retained observations
and comparisons, without provider calls or budget reservations. Original analyses
remain immutable. The final JSON rows and summary `fresh`/`stale` counts describe the
post-reconciliation state; active, incomplete, and unselected work is not reclaimed
by this pass. Dry-run and status remain read-only.
