# Growth Gate 1 cohorts

Run `python manage.py growth_cohorts --as-of 2026-05-01 --format json --min-cell 10`.
`--format md` wraps the same aggregate JSON in a Markdown report. The command
only reads data; it emits no identifiers, per-user rows, emails, or CSVs.

The as-of date means **exclusive midnight UTC at the start of that date**.
Weekly signup cohorts start Monday in UTC, while retention days use each user’s
profile timezone. All eligible historical weeks are
included, including partial/immature weeks. Population excludes staff, then
superusers, then configured IDs, then configured email domains. Exclusion counts
are sequential/disjoint and cover all signups before as-of, not event authors.

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
when the immature count is below threshold (including zero), since population
is reported separately. Percentages and intervals are also null in that case.
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

Freshness is the maximum stored Event.timestamp strictly before as-of (all event
codes, including noise), truncated to the hour so no single row's exact time is
published; it is not evidence that ingestion is complete. Adding
noise cannot change cohort metrics, though a newer noise row can advance this
explicit freshness metadata. Audit-only activity can exist without an Event.
Screen views must not be trended across the client_ui schema cutoff.

Query count is constant across users, events, weeks and stored timezones: at
most nine queries (four sequential exclusion counts, one eligible population,
one meaningful-event stream, one legacy-join stream, one participation stream,
and freshness). Empty populations skip the three action streams. Eligible IDs use an uncorrelated SQL
subquery rather than a growing parameter list. Legacy join authority retains the
existing event-level predicates; there are no per-user metric subqueries.

Python computes first actions, local days, maturity and weekly counts. Streams
use `.iterator(chunk_size=5000)`; no complete event history is materialized.
On PostgreSQL, `.iterator()` uses server-side cursors, which fail behind
PgBouncer transaction pooling unless `DISABLE_SERVER_SIDE_CURSORS=True` is set
on the connection. Operators should run the command on a direct connection.
Work is linear in eligible users plus scanned meaningful events and joins;
memory is bounded by eligible users and their distinct first-31-day local days
(at most 31 per user), rather than all event rows. Exact per-user windows are
filtered in Python after a global timestamp bound. The local-day-30 tail across
a fall DST transition is retained even when it extends past 31 elapsed days.
The database may scan older users' later actions inside the global bounds, but
these are discarded immediately. Production latency still needs measurement;
query count alone does not guarantee a runtime. No tables, indexes, models or
migrations are changed.
