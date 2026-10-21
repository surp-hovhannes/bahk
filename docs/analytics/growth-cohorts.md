# Growth Gate 1 cohorts

Run `python manage.py growth_cohorts --as-of 2026-05-01 --format json --min-cell 10`.
`--format md` wraps the same aggregate JSON in a Markdown report. The command
only reads data; it emits no identifiers, per-user rows, emails, or CSVs.

The as-of date means **exclusive midnight UTC at the start of that date**.
`--granularity week` (default) groups signups by Monday-based UTC week and keeps
the `week_start` key. `--granularity month` groups by UTC calendar month using
`month_start` (`YYYY-MM-01`). `--granularity all` combines all eligible signups
before as-of into one cohort labelled `cohort: all_signups`. The header records
`granularity`. Each run is self-consistent: it applies the same metric definitions,
maturity rules, exclusions and suppression to its chosen groups.

Comparing different granularities or as-of dates for the same data can narrow
small cells through **differencing**. Operators must not publish week-level and
month/all-level tables side by side without review. This is a minimal mitigation,
not a general privacy guarantee.

Weekly signup cohorts start Monday in UTC, while retention days use each user’s
profile timezone. All eligible historical weeks are
included, including partial/immature weeks. Population excludes staff, then
superusers, then configured IDs, then configured email domains. Exclusion counts
are sequential/disjoint and cover all signups before as-of, not event authors.
Each run uses the initially read population as its snapshot and ignores action rows for users who become eligible later.

Optional Django settings `ANALYTICS_EXCLUDED_USER_IDS` and
`ANALYTICS_EXCLUDED_EMAIL_DOMAINS` accept lists or comma-separated strings. If a
setting is absent, python-decouple reads the same-named environment variable or
`.env` entry; both default to empty. IDs must be integers; domain matching is case insensitive and exact after
`@` (not subdomains). Configure privately; never commit production values.

Meaningful actions use `MEANINGFUL_EVENT_CODES` and `analytics_events()`.
Fast joins use dated `FastParticipation.joined_at` authoritatively per surviving
profile/fast pair, including preserved deleted-fast snapshots. Legacy join Events
are fallback only through `legacy_transition_events()`, for pairs with no dated
audit join: the backfill leaves `joined_at` NULL after repeated legacy joins, and
those Events are then the pair's only dated join evidence. No-ops, unknown audit
timestamps, and orphaned linked Events are not dated evidence. Python
selects the first qualifying action at/after signup; audit joins win
same-timestamp ties, then Event codes sort lexically. Duplicate joins cannot
multiply a user's numerator. Account creation, screen views, app opens and
sessions never activate or establish retention.

Activation windows are elapsed 24h, 72h, and 7d, inclusive of both signup and the
window endpoint. A denominator includes only users whose endpoint is at/before
as-of minus a 24h ingestion lag. First-action distribution uses the activated
7d numerator as its denominator, restricted to users mature for 7d.

D1, D7, D30 mean **exact-day, not cumulative** meaningful return on those local
calendar days after signup. Separate any-return measures cover days 1–7 and
8–30. A return denominator matures only when the entire final local day has
closed before as-of; DST does not turn calendar days into elapsed 24h periods.
Profile timezone is used, with America/Los_Angeles fallback for UTC defaults,
missing profiles, empty or invalid zones. Zone names match case-insensitively,
as pytz validated them on the profile; tzdata folder names such as `America`
are invalid. The report includes fallback counts
and share per cohort, subject to suppression. Current timezone and current
exclusion flags are used, so historical reruns may change after profile edits.

Windowed metrics have a numerator, mature denominator, excluded immature count,
percentage, and 95% Wilson interval. Counts below `--min-cell` appear as `<10`
(with the chosen threshold substituted). Percentages and intervals are null when
the numerator, denominator, or complementary cell is below threshold, or the
denominator is empty. Secondary suppression uses the literal `"suppressed"`
for counts at or above the threshold: the numerator is hidden when denominator
minus numerator is below threshold; both numerator and denominator are hidden
when 0 < immature < threshold, since population is reported separately.
Percentages and intervals are also null for that small nonzero immature cell.
Exactly zero immature users is published as the integer `0`: there is no small
nonempty immature group to disclose, so zero alone does not hide either count.
Conversely, the immature count is hidden when the mature denominator is below
threshold, since population minus immature would otherwise recover it.
First-action counts and derived statistics are also withheld when their shared
activation numerator is hidden, preventing reconstruction through that distribution.
First-action `immature` mirrors the activation_7d cell, which counts the same users.
When any first-action category numerator is hidden or below threshold, the smallest
remaining visible categories are additionally suppressed (ties sort by event code)
until the hidden sum exceeds threshold × the hidden cells labelled `suppressed` by
at least the threshold: that label already reveals a count of at least the
threshold, so only the excess conceals anything. Categories 20/20/5 under a total
of 45 hide one 20; 30/10/0 under 40 hide both 30 and 10, because hiding 10 alone
would pin it at exactly 10 and every `<10` cell at 0. Suppressed categories'
percentages and intervals are null. If the visible categories run out first, the
remaining bound is accepted.

For nested windows with equal visible mature denominators, a nonzero difference
below threshold between visible numerators suppresses the larger numerator and
its percentage/interval. Pairs are derived from the window definitions: every
activation pair, and every retention window inside another (D1 and D7 within
days 1–7, D30 within days 8–30). Visibility is evaluated before this pass, deterministically;
zero differences remain visible. This is a minimal mitigation, not a general
privacy guarantee across overlapping reports, windows, or repeated runs.

The population-wide timezone fallback diagnostic has no maturity window and omits
`immature`; its `mature_denominator` is the entire eligible cohort. Its share and
interval are reported when both fallback and non-fallback counts meet the threshold,
with the same small-cell and complementary-count suppression as other metrics.
Suppression also covers population, exclusions, fallback,
and first-action cells. These are descriptive cross-sectional results: small n
and selection effects prevent causal interpretation. The command rejects thresholds
below 10 unless `--unsafe-min-cell` is explicitly passed for synthetic testing; `build_report` accepts any positive threshold.

Freshness is the maximum `analytics_events()` timestamp strictly before as-of,
truncated to the hour. Participation no-ops and legacy screen views cannot
advance it; notification lifecycle rows are also excluded once the shared
analytics filter supports them. It is not evidence that ingestion is complete.
Audit-only activity can exist without an Event. Screen views must not be trended
across the client_ui schema cutoff.

Query count is constant across users, events, weeks and stored timezones: at
most nine data queries (four sequential exclusion counts, one population,
three action streams, and freshness), plus transaction control. Empty populations
skip the action streams. Eligibility is decided once from the population and
applied to stream rows in Python, without a live eligibility subquery.

Outside an existing atomic block, the entire report opens one atomic transaction.
PostgreSQL uses a read-only REPEATABLE READ snapshot for population, exclusions,
streams and freshness. Inside an existing atomic block, the report uses the
caller’s transaction and snapshot semantics without changing isolation.
Streams use `.iterator(chunk_size=5000)` inside that transaction, so PostgreSQL
server-side cursors do not require WITH HOLD. Python memory is bounded by the
population and at most 31 distinct local days per user, rather than event history.
Each stream has per-user SQL bounds from signup through exclusive signup + 32
elapsed days, also bounded by as-of. Python applies the exact local-calendar
window through the midnight ending day 30, retaining the fall-DST tail.
Django duration arithmetic translates these bounds for SQLite and PostgreSQL.
No tables, indexes, models or migrations are changed.

### Operator notes

Use a direct PostgreSQL connection for the first post-deploy production run.
The first production run must capture EXPLAIN ANALYZE for the meaningful-event,
legacy-join, and freshness queries. The legacy-join query retains NOT IN over
linked events and correlated EXISTS predicates. Freshness now uses
`analytics_events()` with its NOT IN no-op subquery rather than the old plain MAX;
include that filtered aggregate when assessing query plans.
Per-user bounds shrink candidates, but fixed query count does not establish
production latency. PgBouncer transaction pooling still requires attention to
server-side cursors; use a direct connection or configure
`DISABLE_SERVER_SIDE_CURSORS=True` as appropriate for the deployment.

The snapshot transaction is opened only when the caller is not already in an
atomic block; nested callers supply their own snapshot semantics. A long
REPEATABLE READ READ ONLY transaction on a live primary holds back vacuum
cleanup through its xmin horizon. On a hot-standby replica it can be cancelled
due to recovery conflicts under `max_standby_streaming_delay`. It is also subject
to `idle_in_transaction_session_timeout`, including while Python processes rows
between queries.
