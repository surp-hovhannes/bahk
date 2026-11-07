# Church selection after social sign-in

Validated locally on 2026-11-06 against backend main `0e2af189`.

New Apple/Google accounts previously had no church, so the Today fast requests
returned HTTP 400. Social signup now assigns the sole church by its actual ID.
Social sign-in, profile reads, and church-scoped content also repair existing
unassigned profiles when there is exactly one church. Existing selections are
preserved, including a concurrent selection. No migration or bulk update is used.

With multiple churches, authenticated content requires a saved selection and
returns `church_selection_required` until it exists. The companion mobile change
shows a required screen and saves the choice through the existing profile API.
The backend can be deployed first to recover existing single-church accounts;
the mobile release is needed for the required multi-church screen.

Validation in an isolated local Docker app container with local Redis:

- `ruff check .`: passed before and after tests.
- Default Django suite, parallel, excluding `performance` and `slow`, using
  `tests.test_settings`: 2,667 tests, successful with 49 skipped.
- Church-selection and social-auth tests: 86 passed. Covers both providers,
  actual fast content, existing-profile repair, multiple/zero churches, explicit
  profile selection, and preserving existing/concurrent selections.
- No production account, feature flag, provider setting, or deployment was changed.

The companion mobile change has Jest coverage and an iOS 27 simulator fixture
flow for both choices. Real-device confirmation against the deployed backend
remains a post-deployment check.
