# FastParticipation migration and rollback

PR 560 includes the exact state-only choices migration from pending PR 558.
The graph is 0069 -> 0070_alter_llmprompt_model -> 0070_fastparticipation
-> 0071_backfill_fastparticipation. PR 558 can land first without creating
multiple hub leaves; its branch is unchanged. Its production legacy-model
audit remains a merge prerequisite for this branch too.

The backfill reconciles current memberships even when either event type or
the Fast content type is absent. It uses historical models and the migration
connection, orders equal timestamps by event ID, and skips deleted targets.
Profile.fasts is never modified.

0071 has a data-preserving no-op reverse. Reapplying derives complete periods
before inserting and reconciles the count of each (joined_at, left_at) pair.
This avoids duplicate closed rows and temporary open-period constraint failures,
while retaining duplicate-event multiplicity and any periods the live receiver
has since recorded. Existing rows are never deleted or overwritten.

For application rollback, deploy the previous application code and leave the
additive schema in place. Reversing 0071 alone preserves the audit data and is
safe to reapply. Reversing 0070_fastparticipation drops the entire audit table;
do that only with a separately authorized backup/export and removal plan.
Profile.fasts remains untouched. No production data operations were performed
while repairing this branch.
