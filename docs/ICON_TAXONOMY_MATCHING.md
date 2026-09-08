# Icon taxonomy matching and evaluation

This matching follow-up depends on the independent catalogue backfill pipeline.
See [the canonical catalogue backfill guide](ICON_TAXONOMY.md) for ingestion,
private storage/evidence, cumulative budgets, guarded dispatch, checkpoints and
recovery. Its commands and migration remain owned by that dependency. The unused
request-interpretation cache table from migration 0019 gains its opt-in consumer
here; the migration definition is unchanged.

The installed `armenian-lectionary` library remains the sole calendar authority.
Only `evaluate_icon_taxonomy --calendar` enumerates calendar names in this feature;
its output is a test artifact, never runtime reference data. Non-festal thematic
requests and icons remain first-class. An engine-supplied bilingual commemoration
can be used for a request without persisting calendar aliases. Existing calendar
services are unchanged.

All new routing, adaptation and provider dispatch remain disabled by default.
Runtime routing defaults to the existing GPT-4.1-mini baseline. Existing
control/v1/v2 experiment profiles and prompts remain available; see also
[paired matching evaluation](ICON_MATCH_PAIRED_EVALUATION.md). No existing icon
selection is rewritten by integrating this code. No live rollout is performed.

## Evidence currency at retrieval and assignment

Fixed ingestion rules publish supported/unknown/contradicted attributes. Exact
readable qualified inscriptions can corroborate source metadata; known image-only
inscriptions produce suggestions. Automatic event eligibility requires corroborated
event and participant evidence. Complete pairs accept separate member tags;
partial members are suggestions. Sourced open-group IDs need no invented members.
Independent themes survive identity disagreement.

Used meanings, aliases, collisions, relations, unresolved filename components and
code/release versions have dependency fingerprints. Changed dependencies make
projections ineligible on query/assignment before reconciliation queues a new
revision; unrelated additions do not force paid reanalysis. Search and assignment
use database generations and dependency guards, never image downloads/resizing.
Unannounced external storage overwrites require explicit reconciliation before
DB-only retrieval can observe them, as described in the backfill guide.

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

## Test partition and limitations

`icons.tests.test_taxonomy` retains matching/assignment, stale retrieval, scope,
request-adapter, router and calendar-evaluation regressions. It retains the
matching assertions of mixed tests, including source metadata/filename ambiguity,
while the 39 independent ingestion/command/provider/budget tests live in
`icons.tests.test_taxonomy_backfill`. Neither suite subclasses the other's TestCase;
plain fixture setup is local, so discovery does not duplicate the backfill suite.
Hub matching/service/consumer/profile/evaluation tests and prayer-import regressions
remain part of validation. Providers and SDK calls are mocked offline.

The local shared virtualenv may contain lectionary 2.0 despite the source pin
`<2.0.0`; do not mutate it to hide unrelated failures. Full Crabbox CI and independent
review of each final branch belong to the parent workflow. Offline evidence does
not establish visual accuracy, Armenian inscription recall, production latency,
real pricing or full calendar availability. There is no live model-as-judge or
manual content-review queue.

Unresolved scene/event source claims block generic-portrait fallback, even if the
model offers reassurance. Ordinary search tags alone do not. Arbitrary unmarked
prose remains semantically uncertain; this is not exhaustive scene recognition.
Scene-only event evidence with missing required participants ranks as related
specific evidence and cannot suppress a fully covered portrait. Supported events
without required participants can be exact-event suggestions, but the strict
automatic-assignment gate remains in force.
