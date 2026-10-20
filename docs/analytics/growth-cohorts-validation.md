# Growth cohort performance rewrite: hand trace

These are static fixture/model traces, **not executed Django test results**.
All Event fixtures use `Event.create_event` with a nonempty title and a Fast
target whenever `EventType._requires_target_for_code` requires one, satisfying
`Event.save()` / `full_clean()`. Timestamps and alternate users are assigned by
queryset update after validation. Participation fixtures use surviving Profiles
and distinct profile/fast pairs, satisfying the unique-open-period constraint.
These tests never log in a client, so their existing DummyCache setting cannot
break a cache-backed session. Internal aggregate spies inspect synthetic counts
before suppression; production output remains aggregate-only.

| Test (`test_` prefix omitted) | Hand-traced expected behavior |
| --- | --- |
| dst_exact_local_day_and_elapsed_activation | March 8 01:30 PST to March 9 00:15 PDT is 21h45 and local D1; both hit, D7 misses; freshness floors to 07:00 UTC. |
| activation_and_closed_day_maturity_boundaries | Signup March 8 00:00 UTC is March 7 LA; the exactly 24h action hits inclusively. March 10 as-of closes activation with its 24h lag and local D1, while March 9 does not. |
| exclusions_and_fallback | Staff, superuser, configured ID, then domain each remove one distinct fixture; UTC, invalid zones and tzdata directories resolve to LA fallback. |
| timezone_matches_case_insensitively_like_pytz | Lowercase Tokyo canonicalizes; March 8 signup to March 9 08:00 UTC is local D1, with no fallback. |
| exclusion_lists_read_through_decouple | Missing settings reach environment/config; CSV whitespace is stripped and configured IDs remain strings until validated. |
| legacy_join_dates_pair_with_undated_audit_row | NULL joined_at does not supersede legacy evidence; a targeted legacy join at +1h activates and has the join code. |
| authoritative_join_without_event_and_duplicate_legacy | Dated period supersedes legacy join for that pair; tracked no-op is excluded. Moving period from +1h to +2d removes 24h activation. |
| fallback_share_has_no_maturity_window | 10 fallback of 21 users gives 47.62%, with 11 complementary users and no immature field. |
| fallback_share_suppresses_small_cells | Counts below 10 hide directly; complements below 10 hide the numerator; denominator remains 21. |
| noise_invariance_property | Existing +7d action is later than all added exposure/session noise; meaningful metrics and freshness therefore remain identical. |
| suppression_command_and_wilson | Population 1 hides below k=10; 20/40 with 10 immature gives 50% and an enclosing Wilson interval. Both command formats expose metadata without usernames. |
| complementary_count_suppression | Complements 0 or 1 hide numerator; immature 0..9 hides numerator and denominator; denominator 3 with population 25 hides immature too. |
| query_count_does_not_grow_with_population_or_weeks | Existing fixture adds 12 users across weeks without adding SQL statements; the new bound is at most eight. |
| query_count_is_fixed_across_events_users_weeks_and_timezones | Adding 12 users across weeks, two timezones and 36 meaningful events adds no SQL statements; at most eight including sequential exclusions and freshness. |
| multiple_timezones_keep_independent_local_days | Same UTC signup/action pair spans March 7 to 9 in LA (D2), March 8 to 9 in Tokyo (D1); raw D1 aggregate is (1 hit, 2 mature, 0 immature). |
| fall_dst_day30_tail_and_31_day_edge | October 31 00:00 PDT to November 30 23:30 PST is 31d30m yet local D30; December 1 00:00 PST is D31 and excluded. |
| events_across_elapsed_31_day_edge | Spring signup's +30d action is local D30; +31d/+32d cannot add retention hits, and no action activates within 7d. |
| participation_wins_event_tie_and_earlier_event_wins | Period at the event's +1h timestamp supplies the join code; delaying it one second leaves devotional first. Spies verify the otherwise suppressed distribution counts. |
| event_ties_use_code_before_primary_key | Reverse insertion order gives the larger code the smaller pk; equal timestamps still choose the smaller code. Equal code/time rows have identical aggregate effects regardless of pk. |
| iterator_chunk_boundary_preserves_report_and_query_count | Six users, five events and five distinct-profile periods cross size-2 fetch boundaries; complete reports and SQL statement counts match default-size iteration. |
| pre_signup_and_account_creation_do_not_activate | Event one second before signup fails lower bound; account-created is outside meaningful codes. |
| exact_day_and_any_return_endpoints | +7d and +30d fall on local D7/D30 despite spring shift, hit inclusive return endpoints, and do not hit D1. |
| first_action_distribution_complementary_suppression | Counts 20/20/5 hide the 5 and smallest tied visible category; 30/10/0 requires hiding both positive categories to avoid pinning hidden counts. |
| nested_window_complementary_suppression | Shared-denominator differences 5 hide larger numerators; unequal denominators and equal numerators do not create a subtractable small positive band. |
| command_min_cell_floor | Thresholds 0/1/9 reject without unsafe override; explicit unsafe k=1 serializes successfully. |

The public builder, command, suppression helpers, event-selection helpers and
sequential exclusion order retain their existing definitions. The elapsed-31-day
bound is extended only where necessary to preserve the old local D30 endpoint.
Production latency and query plans still require measurement on representative
data. Lens review is required by the task's Lane 3 designation; no Lens tool or
instructions were available in this workspace/session when these notes were made.
