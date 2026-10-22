# Notification funnel (Growth Gate 1)

Run locally in the existing project runtime:

```sh
python manage.py notification_funnel --as-of 2026-10-21 --since-days 30 --format json
python manage.py notification_funnel --as-of 2026-10-21 --since-days 7 --format text
```

This read-only report consumes existing lifecycle data regardless of telemetry
flags; it changes no switches, settings, ledger state or database rows. The
exclusive `as_of` is UTC midnight; the inclusive start is that instant minus
`since_days` (1–400). Sends use stored server event timestamp as sent_at. Opens,
receipts, destinations and prompts use server receipt timestamp, not the optional
client clock. Only sends within the same window can correlate; opens for older
sends appear as unmatched. Receipts outside the window do not contribute. Near
as_of, pending receipts and 30-minute association windows are incomplete.
Freshness is the latest eligible notification event in this window, rounded down
to the hour. Shared cohort freshness explicitly excludes notification lifecycle.

Staff, superusers, ANALYTICS_EXCLUDED_USER_IDS and
ANALYTICS_EXCLUDED_EMAIL_DOMAINS are excluded exactly as in growth cohorts.
Receipt rows have no user, and are counted only through eligible sends. There is
no automatic exclusion based on an account name; configure test accounts using
those settings. Unknown kinds map to unclassified. The D7 mapping combines
prayer_thanks and prayer_request_completed as prayer_request_notifications;
other kinds retain their names. Overall is the union, not a second population.

## Definitions and denominators

* Attempted tokens: one stored send row per attempted message × token. Missing
  telemetry cannot be reconstructed. Outcome cells divide by attempted tokens:
  provider_accepted, provider_rejected, and send_error (a separate third outcome).
  Acceptance means **accepted by provider**, never delivered.
* Send error breakdown uses attempted tokens, with the producer's bounded Expo
  error vocabulary and `other`; it includes provider rejections and send errors.
* Accepted tickets: accepted send rows with a ticket_id; divides by attempted
  tokens. Receipt coverage = unique matching receipts / accepted tickets.
  Receipt join requires both notif_id and ticket_id, and receipt time >= send
  time. Repeated receipts count once. Receipt outcomes and bounded errors divide
  by matched receipts. `ok` means **handed to the platform push service per Expo
  receipt**, not seen on the device. Missing receipts may reflect ledger
  capacity, expiry, disabled recording or a provider still processing a ticket.
* Opens: raw client event counts split into tap and cold_start. A match requires
  notif_id, the **same user**, and open receipt time >= send time. Ingestion-time
  matched/kind hints are not trusted. Unmatched opens are counted in unclassified
  because the true send kind is unknown. They are included in overall.
  Eligible client version population is **not derivable** from these events.
  No percentage against all sends is calculated. A future eligible-client open
  rate would be a **lower bound**: reporting requires an OTA version containing
  tracking and a logged-in user. Do not trend across rollout.
* Destinations: event counts opened / unresolved / rejected by allowlisted
  destination, divided by destination events in the corresponding group.
  Uncorrelated or wrong-user results belong to unclassified.
* Association: distinct users with their first recorded combined prayer action
  or first recorded fast join within **[open, open + 30 minutes]**, divided by
  distinct users with correlated opens. Prayer combines prayer_set_viewed,
  devotional_viewed and prayer_request_accepted (meaningful cohort prayer
  actions). Prior recorded activity disqualifies a lifetime-first association;
  missing historical telemetry limits that interpretation. Fast joins combine
  authoritative FastParticipation and dated legacy transition events, as in
  cohorts. If the open has a session_id, the action must have the same one;
  participation rows lack sessions and cannot satisfy such opens. Missing open
  sessions permit time-only association, disclosed as opens_missing_session.
  One user may associate with multiple notification groups. Association is not
  causation. No activity after as_of is considered.
* Prompts: distinct users by trigger, shown -> selected enable/not_now -> OS
  granted/denied/undetermined. Selected requires a preceding shown in the window;
  OS result requires preceding enable for that trigger. Selected divides by
  shown users, OS results by enable users; shown is a raw user count. Unmatched
  stages are separately counted. No prompt attempt identifier exists, so this
  is a user funnel across the window, not a per-attempt conversion. A user may
  choose differently on repeated prompts; choices are not disjoint partitions.

## Privacy and performance

Minimum k is 10 and cannot be lowered. No user, token, notification identifier,
content or arbitrary error text leaves the report. Rate cells reuse cohort
Wilson 95% intervals and complement suppression: numerator, denominator and
complement must all meet k. If a count is hidden, its denominator is also hidden.
Raw counts below k (including zero) are suppressed. Sparse additive breakdowns
receive conservative complementary suppression, including their totals; sparse
kind cells also hide overall totals. Suppression propagates across kind
partitions and every metric that exposes a hidden total as its denominator,
including dependent ticket and receipt totals, until no further cells change. Prompt stages are conservatively hidden
together when a positive cell is below k. This intentionally reduces utility for sparse
data. `mature_denominator` is the reused cohort output key; it means the eligible
metric denominator here, not a fully matured observation window.

Suppression is not differential privacy. Overlapping metrics, group membership,
cutoffs and repeated exports may permit differencing. **Do not publish reports
with different cutoffs side by side without privacy review**, including daily
stepping, overlapping since-days windows and combinations with other reports.

Queries bound lifecycle events by type and timestamp, stream compact rows and
join opaque IDs in memory. Existing Event(event_type, -timestamp),
Event(user, -timestamp) and participation join indexes support this structure;
no migration is added. Prior-action EXISTS uses indexed user lookups, rather
than full-table JSON scans. Use QuerySet.explain() on the type/time queryset and
prior-action querysets in a representative read replica before large exports.
Runtime and memory scale with the selected window's events and matched users;
seconds is the target, not yet measured against production volume. The report
uses a read-only repeatable-read transaction on PostgreSQL (caller supplies
snapshot semantics inside an existing transaction).
