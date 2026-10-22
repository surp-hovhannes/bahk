# Notification funnel disclosure relations

`events.notification_funnel._protect_output` builds one internal list of
`(total_node, part_nodes)` equations over the finished report. Nodes contain raw
counts; output fields bind to canonical nodes rather than copies of their values.
The graph and raw counts are never included in the report.

The catalogue includes:

- Overall = sum of kinds for every event metric, including destination events,
  all opens, send failures, and opens missing a session.
- Attempts = accepted + rejected + send error + uncategorized remainder.
- Send failures = rejected + send error = sum of send error codes.
- Receipts received = receipt ok + receipt error; receipt error = sum of receipt
  error codes.
- All opens = matched + unmatched = tap + cold start + unknown-entry remainder.
- Destination events = sum of destination/result cells.
- Accepted = accepted tickets + accepted without a ticket.
- Matched opens = missing session + present session.
- Each ratio denominator = numerator + complement. Denominators bind to the same
  node as the corresponding total, including the internal destination total.
- Each kind's distinct open/associated user population is a subset of overall,
  represented as kind + outside-kind complement. Distinct users can overlap
  between kinds, so these populations must not be summed across kinds.
- Each prompt choice is a subset of shown users, or enabled users for OS results.
  Choices count distinct users who can appear in multiple choices over time;
  summing choices would invent an invalid additive partition. Unmatched prompt
  counts receive primary suppression independently.

Category relations include internal remainders for older or malformed rows.
These remainders and all complements receive the same protection as output cells.

The final pass marks every positive count below k hidden, then repeatedly hides
**every node in any equation touching a hidden node** until no new nodes are
hidden. This is deliberately stronger than choosing one complementary cell:
intersecting equations cannot reconstruct a hidden value because no released
arithmetic equation touches it. Shared denominator bindings propagate the same
suppression everywhere. Suppressed percentages and intervals are cleared.
The graph is bounded by the fixed report dimensions and adds no database queries.

No metric family is unconditionally disabled. Whole connected components can be
suppressed, including otherwise large counts, when a small count is connected to
them. Exact zeros are public unless connected to suppression; this changes the
previous blanket hiding of zero counts. A zero or zero-complement row has no
released percentage or interval. Released percentages require numerator,
denominator, and complement all at least k.

Tests include the 40-attempt plus five rejected-kind and 20/20/5 destination
regressions. A seeded 200-case test builds real reports and checks every generated
equation, primary suppression, every one-cell subtraction, output alias bindings,
and released percentages. Existing tests cover entry attribution, missing-session
partitions, prompt sequences, and eligible prior fast-join evidence.

This protects the declared within-report relations. Comparing overlapping report
windows or changing cutoffs still requires privacy review, as the report caveat
states. CI must run the Django tests; local validation for this change was limited
to syntax and whitespace checks.

Every positive event cell also requires at least k distinct eligible contributing
users. Internal event references retain the user and source row only inside this
module. Matched receipts inherit the eligible ticket owner's user and source row,
so null-user receipts cannot bypass eligibility and complements have the correct
contributors. Internal residuals receive the same two floors. Identifiers and
contributor sets are never serialized.

Before correlation and graph construction, reporting kinds with positive attempts
below k, or fewer than k distinct attempting users, are folded into `other_kinds`.
All their events and associations follow that bucket; nothing is discarded.
Overall remains the sum of the resulting event buckets. If the pooled bucket is
still sparse, normal complementary suppression applies (and can hide connected
large cells). Absent kinds remain structural zeros.

The repeatable-read snapshot counts candidate lifecycle rows before materializing
them. More than `MAX_LIFECYCLE_ROWS` (500,000, a module-level operational constant)
aborts with a recommendation to reduce `--since-days`. The bound includes unmatched
null-user receipts, which consume memory even though they do not advance freshness.
Freshness includes eligible lifecycle rows and successfully matched receipts only.
Text output renders labeled counts and ratios without embedding JSON.
