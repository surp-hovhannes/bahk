# Public API v1 reference documentation (#500)

## Goal

Publish a trustworthy, accessible human reference for every stable v1 route without advertising endpoints before the deployment-readiness gate enables them.

## Implementation

- `/docs/` retains the Coming soon state while `PUBLIC_API_RESOURCES_ENABLED=false`.
- The enabled state renders the complete anonymous, read-only v1 route inventory from reviewed illustrative data in `bahk/public_api/reference.py`; the docs request performs no resource or storage access.
- Each route includes method/path, parameters, purpose, request example, and exact-shape JSON response.
- The reference documents the shared error envelope, 400/404/405/406/429/503 examples, `Retry-After`, fair-use limits, five-minute application freshness, `Cache-Control: no-store`, languages, timezone semantics, Calendar partial failures, versioning, and the 180-day deprecation window.
- Semantic navigation, headings, table scopes, skip link, visible focus, wrapping code blocks, and mobile reflow provide accessible keyboard and small-screen use without JavaScript or external runtime dependencies.

## Verification

- Default-off placeholder and enabled-reference tests.
- Route-name synchronization between documented routes and the enabled URL list.
- Core policy/example/accessibility and authentication-misinformation assertions.
- Ruff/Python syntax, template inspection, `git diff --check`, independent code review, visual design QA, and Crabbox full CI before PR publication.
