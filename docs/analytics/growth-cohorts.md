# Growth Gate 1 cohorts

Run `python manage.py growth_cohorts --as-of 2026-05-01 --format json --min-cell 10`.
`--format md` wraps the same aggregate JSON in a Markdown report. The command
only reads data; it emits no identifiers, per-user rows, emails, or CSVs.

The as-of date means **exclusive midnight UTC at the start of that date**.
Weekly signup cohorts start Monday in UTC. All eligible historical weeks are
included, including partial/immature weeks. Population excludes staff, then
superusers, then configured IDs, then configured email domains. Exclusion counts
are sequential/disjoint and cover all signups before as-of, not event authors.

Optional Django settings `ANALYTICS_EXCLUDED_USER_IDS` and
`ANALYTICS_EXCLUDED_EMAIL_DOMAINS` accept lists or comma-separated strings. If a
setting is absent its same-named environment variable is read; both default to
empty. IDs must be integers; domain matching is case insensitive and exact after
`@` (not subdomains). Configure privately; never commit production values.

Meaningful actions use `MEANINGFUL_EVENT_CODES` and `analytics_events()`.
Fast joins use dated `FastParticipation.joined_at` authoritatively per surviving
profile/fast pair, including preserved deleted-fast snapshots. Legacy join Events
are fallback only through `legacy_transition_events()`. No-ops, unknown audit
timestamps, and orphaned linked Events are not dated evidence. Correlated
subqueries select the first qualifying action at/after signup; audit joins win
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
missing profiles, empty or invalid zones. The report includes fallback counts
and share per cohort, subject to suppression. Current timezone and current
exclusion flags are used, so historical reruns may change after profile edits.

Every metric has a numerator, mature denominator, excluded immature count,
percentage, and 95% Wilson interval. Counts below `--min-cell` appear as `<10`
(with the chosen threshold substituted). Percentages and intervals are null when
the numerator, denominator, or complementary cell is below threshold, or the
denominator is empty. Suppression also covers population, exclusions, fallback,
and first-action cells. These are descriptive cross-sectional results: small n
and selection effects prevent causal interpretation. Threshold 1 is useful only
for synthetic tests; production reports should retain the default privacy floor.

Freshness is the maximum stored Event.timestamp strictly before as-of (all event
codes, including noise); it is not evidence that ingestion is complete. Adding
noise cannot change cohort metrics, though a newer noise row can advance this
explicit freshness metadata. Audit-only activity can exist without an Event.
Screen views must not be trended across the client_ui schema cutoff.

Query count grows with distinct stored timezones, not users, events, or weeks.
The database performs first-action subqueries, existence checks, local-date
conversion and weekly counts; Python merges aggregate timezone buckets only.
Large histories can still require substantial database work; this command is an
offline reporting tool. No tables, indexes, models or migrations are changed.
