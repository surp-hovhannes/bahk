# Moderation responsibilities and review dashboard

Stacked on PR #564 at `e693b1774b6145402152d50ca955fc326739316d` (includes #570).
Implements the lightweight review scope in #569 without changing automated prayer routing.

## Access model

`moderation.Responsibility` is an explicit assignment to an existing account:

| Assignment | Routine prayers, icon reports, generated context signals | Crisis prayers |
| --- | --- | --- |
| General | Dashboard, review actions, notices | No content, counts, actions or notices |
| Crisis | No routine access unless General also assigned | Dashboard, acknowledgement/escalation records, notices |
| Both | Both capabilities | Both capabilities |
| Staff/admin/superuser without assignments | No moderation dashboard or notices | No crisis access |

Assignments require an active account. There is no superuser `has_perm` bypass.
Only active existing superusers can manage assignment rows through Django admin;
the form refuses assignments to the acting administrator's own account. Assignment
changes use Django admin's existing change history. Routine Django admin model
permissions remain useful to maintainers for their existing inspection tools.
Crisis prayer rows and associated acceptance/log rows are removed from admin
querysets unless the actor has an explicit Crisis assignment, including superusers.
The existing prayer API also excludes other users' crisis submissions without that
assignment. A requester retains existing access to their own submission.

**Security tradeoff:** a superuser still controls accounts and can grant another
account Crisis responsibility. A code/database operator can change assignments
outside this application. This is an explicit trust boundary, not an attempt to
sandbox an operator who controls the database. Do not give routine moderators
superuser privileges. These capabilities do not grant model administration rights.

## Safe initial assignment: Matthew / Fr Mesrop only

No production permissions, credentials, guessed account IDs or email addresses are
created by this migration. No staff-wide backfill occurs. **Actual production
access assignment requires Matthew's approval.**

After release approval, another authorized existing superuser should open
Admin → Moderation → Responsibilities, verify Matthew / Fr Mesrop's existing
account identity with him, and assign Crisis to that account only. Assign General
separately as appropriate. Confirm the account is active and its existing email is
correct before saving. Routine maintainers receive General only. Verify the
result by signing in at `/moderation/login/` using the existing account email.
If no second authorized administrator exists, arrange an explicitly approved,
reviewed operator bootstrap; do not invent credentials or run a broad data
migration. This PR does not execute that bootstrap.

## Review behavior

`/moderation/` shows oldest-first queues, backlog counts, source dates and a
25-item preview per content type. Prayer ages are creation dates; context ages
are generation dates, **not first-flag timestamps**. Context queues use the
existing configured regeneration thresholds and active versions. Downvotes are
feedback signals rather than publication holds. Opening a page does not invoke
an AI provider, regenerate content, or change moderation state.

Routine pending prayers reuse existing approve/reject behavior (events,
milestones, requester acceptance). Crisis actions only record human review or
escalation and leave the rejected content unpublished. Django admin status edits
and bulk approval also refuse crisis publication, even for designated staff or
superusers. Forged Save as new requests are denied. Acceptance and prayer-log
forms validate submitted prayer IDs against the actor's crisis responsibility
on both creation and change; filtering autocomplete alone is insufficient. This is not a clinical
assessment or an on-call/dispatch system. Icon resolution preserves the existing
resolution fields and notes. Context acknowledgement applies to the current
version/downvote total; additional downvotes can reopen its queue, and replacing
an active context naturally removes the old version from backlog.

Every dashboard action stores actor, outcome, timestamp, required note and
optional expected outcome / sanitized regression reference. Notes render as
escaped text. No private request is automatically exported to an evaluation.
Rejected prayers and resolved icon reports remain available in the audit view.

Notices go to active accounts with the matching explicit assignment. Duplicate
addresses are normalized, and each address receives a separate message. Email
contains a sign-in link, with no title, body, requester identity or model evidence.
Prayer routing emits its existing alert events. New icon submissions notify
General reviewers after commit; context feedback notifies General reviewers on
crossing the existing threshold (not on every subsequent vote). Revocation or
inactivity removes future delivery eligibility. Historic mailbox content cannot
be recalled.

## Schema and rollback

The additive migration creates assignment and review tables, with no data
backfill or changes to prayer/context storage. Before any rollback, preserve the
review audit records under the approved retention policy. Rolling back the new
app drops those tables; it does not erase prayer requests or existing icon notes.
Retention and operational review cadence remain policy decisions for Matthew.

## Synthetic evaluation publication

The published [evaluation records](evaluations/clef-synthetic-evaluation.md) and
[JSON fixture](evaluations/clef-synthetic-evaluation.json) contain **33 synthetic
cases and 144 observed rows**. Offline final-cohort replay gives **31/33 strict
and 33/33 permissive** agreement.
One invented payment-handle case and its five rows are intentionally withheld.
The full private historical baseline was 34 cases / 149 rows; its final cohort
result was 32/34 strict and 34/34 permissive. Those denominators must not be
presented as results for the public subset. No private archive or transcript was
fetched during implementation; no substitute case or scores were invented.

## Validation evidence

The moderation pages extend Bahk's existing Django admin templates and reuse
`fastandpray-admin.css`, native dashboard cards, icons, breadcrumbs, forms,
buttons, tables and theme controls. The additional stylesheet only handles
content wrapping and responsive overflow. Staff retain their permission-filtered
admin navigation; nonstaff receive only moderation navigation and logout, with
no new access to `/admin/`. The login also uses the native Bahk wordmark and
login layout. The moderation link on the admin index now sits inside the main
content column so it does not displace the existing dashboard grid.

Validation used isolated SQLite test databases, the locmem email backend and
mocked deliveries. External socket connections were blocked. No production
account assignment, provider evaluation, deployment or live email was performed.

- Full Django suite (`--parallel=2 --exclude-tag=performance --exclude-tag=slow`):
  **2,201 tests passed, 38 skipped**, with blank AWS settings, metadata lookup
  disabled and an in-memory Celery broker/result backend. External sockets were
  blocked. The earlier Redis/S3 runtime failures pass with this safe test setup.
- Focused moderation/admin discovery/dashboard regression suite: 52 tests passed.
  Native-theme and nonstaff navigation/login tests preserve scoped access. HTTP tests
  reproduced both blockers before the fixes, then verified crisis form/bulk
  publication denial, forged Save as new denial, and foreign-key authorization
  on acceptance/log creation and change for staff and superusers. Routine
  approval and explicitly authorized crisis references still work.
- Existing private offline replay harness: 14 tests passed; no archive input was
  fetched or used. All 33 published case blocks and 144 probability/outcome table
  rows agree between Markdown and JSON; public final-cohort replay passes.
- Repository Ruff lint passed. New moderation files pass Ruff format check.
  The whole-repository format check has the same 307 unformatted files on this
  branch and the unchanged stacked base; unrelated formatting was not applied.
- Django-rendered synthetic dashboard and admin validation pages were inspected
  in Chromium at desktop and 390px mobile widths. Crisis publication and forged
  foreign-key errors are visible; general-only pages contain no crisis title.
  No page-level horizontal overflow was found. Browser external requests were
  aborted, with local static assets supplied directly. All 28 desktop/mobile,
  light/dark screenshots use the same declared typography and header colors as
  the rendered regular admin. External font requests were blocked, so both use
  the same local fallback during offline QA.
- The additive moderation migration reports no model-state changes. Migration
  checking disabled the test-only offline video-storage shim, which cannot be
  serialized by the existing project's migration autodetector.
