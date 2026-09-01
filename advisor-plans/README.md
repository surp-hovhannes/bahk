# Advisor plans

Implementation-ready plans and their current delivery status.

| ID | Plan | Priority | Effort | Risk | Status | Planned at |
| --- | --- | --- | --- | --- | --- | --- |
| 001 | [Brand and improve the Django admin](001-brand-django-admin.md) | P1 | L | Medium | Implemented | `b2c57cf` |
| 002 | [Standardize admin media previews](002-standardize-admin-media-previews.md) | P1 | M | Low | Implemented | `2f3b2e7` |
| 003 | [Improve calendar navigation](003-improve-calendar-navigation.md) | P1 | M | Low | Implemented | `2f3b2e7` |
| 004 | [Optimize Content & Calendar changelists](004-optimize-admin-changelists.md) | P1 | M | Medium | Implemented | `2f3b2e7` |
| 005 | [Streamline prayer-request moderation](005-streamline-prayer-request-moderation.md) | P1 | M | Low | Implemented | `2f3b2e7` |
| 006 | [Expand useful admin sorting](006-expand-admin-sortability.md) | P2 | S | Low | Implemented | `2f3b2e7` |
| 007 | [Improve content discovery and navigation](007-improve-content-discovery.md) | P2 | M | Low | Implemented | `2f3b2e7` |
| 008 | [Replace opaque relation pickers](008-improve-admin-relation-pickers.md) | P2 | M | Medium | Implemented | `2f3b2e7` |
| 010 | [Configure the video field for multipart S3 uploads](010-configure-video-s3-storage.md) | P1 | S | Medium | Implemented — dedicated migration branch | `5af5c7d` |

Plans in this directory are intentionally separate from application changes. Approve a plan before implementation, then keep its status aligned with delivery and verification.

## Execution order and dependencies

- Execute 002, 003, and 004 first; they are independent foundations.
- Execute 005 after 002 and 004 because the moderation queue uses the shared thumbnail renderer and annotated counts.
- Execute 006 after 004 because sortable count columns depend on annotations.
- Execute 007 after 003 so calendar search behavior is defined once.
- Execute 008 last; it depends on the search fields from 003 and 007 and the persisted media previews from 002.

All seven plans were selected on 2026-08-26. No audit finding was rejected.

Plans 002-008 are implemented in the `codex/admin-improvement-audit` working tree. Record the implementation commit when the branch is prepared for review.

Plan 010 is a separately approved migration and must remain in its dedicated PR.
