# Post-fast encouragement (#449)

The daily Celery beat task runs at 09:00 in the configured Celery timezone. It selects fasts whose last scheduled Day was yesterday and emails their current Profile.fasts members. It respects receive_promotional_emails, active accounts, and nonempty email addresses. Early departures are excluded. Dedicated participation dates are tracked separately in issue #543.

In Django admin, open Notifications → Post fast encouragement emails. Select a fast and enter its subject and plain-text message. Only one message per fast is allowed. A fast without a custom message sends no encouragement email; blank messages or subjects are also skipped. There is no shared or built-in default. Legacy unassigned records remain stored but are never sent; the admin form requires assigning a fast before saving. Paragraphs are preserved and HTML is escaped; the greeting, signature, app link, and signed unsubscribe link are supplied by the template.

The task records sent_at for each user/fast pair and locks that record during delivery to serialize concurrent workers. Successful recipients are skipped on reruns. It respects the shared email-count limit and schedules a continuation with the original completion date when rate limited. Failed sends are logged and remain eligible for an explicit rerun of send_post_fast_encouragement_task(completed_on='YYYY-MM-DD'). As with SMTP generally, a crash after provider acceptance but before database commit can produce a duplicate on retry.

## Migration and rollback

notifications.0007_post_fast_encouragement adds only the email-copy and delivery tables; it does not alter existing memberships or data. Apply it before starting workers with the new daily schedule. No messages are sent by the migration.

To roll back, first disable the daily schedule and drain pending encouragement tasks, then deploy the prior code. Prefer retaining the tables. Reversing the migration drops editable copy and delivery history: export both tables before reversal and restore delivery history before re-enabling this feature to prevent repeat sends. Do not reverse unrelated application migrations.
