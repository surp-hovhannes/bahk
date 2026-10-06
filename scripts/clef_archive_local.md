Local private Clef archive harness
=================================

Only invented data belongs in repository tests. The CLI requires an explicit
external `--archive` path and verifies its pinned byte length and SHA256 before
schema validation. It fails closed with constant error codes. It never extracts
members or prints private member paths, prose, records, or exception details.
All member bytes stay in memory; markdown bytes remain opaque. JSON Decimal
scores and CSV text retain their original representations; no extra measurement
precision or missing historical decisions are manufactured.

Run invented coverage independently:

```sh
/opt/homebrew/bin/python3.11 -B -m unittest discover -s tests -p test_clef_archive_local.py
```

Run private validation explicitly, with the external path supplied locally:

```sh
/opt/homebrew/bin/python3.11 -B scripts/clef_archive_local.py --archive "$PRIVATE_CLEF_ARCHIVE"
```

Archive parsing, replay, and the source audit run with socket creation and DNS
resolution blocked. The guard proves an attempted socket fails. Neither command
requires Django, a database, Docker, installed dependencies, or a provider.
Synthetic CI alone makes no claim that the private archive was validated.

Replay extracts real constants and decision functions with AST, then executes
the real task body with mocked ORM, providers, email, event/milestone/acceptance
operations, logging, and Celery task context. This is **offline smoke, not Django
integration proof**. Recorded replay uses neutral invented profanity flags;
independent invented cases exercise profanity and provider failures. Profanity
flags are never inferred from private input.

Historical and current replay results occupy separate output namespaces. The
historical question sets come from the question-change source position; current
replay uses the exact final question map for every cohort. Both question-set
identities and both real source SHA256 identities accompany the replay. Cohorts
remain separated by result position, run identity, and model. Run identities and
source paths are never printed. Archive case records and historical run records
are retained independently, including differences between their source versions.
Positional case IDs identify mismatches; they do not infer missing source IDs,
languages, categories, or labels. Agreement counts are not model accuracy or
training claims. Permissive agreement allows only replay escalation in place of
expected rejection or review. The preliminary flash safety miss remains a miss.

The final audit compares exact recovered input/title/description/raw-label strings
against tracked and untracked Git files in memory. It counts existing string
overlaps with HEAD separately from new private strings, since short titles and
labels may also be ordinary repository vocabulary. The latter count must be
zero. Nothing containing archive payloads is written, including ignored files.
