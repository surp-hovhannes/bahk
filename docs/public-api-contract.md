# Fast & Pray Public API Contract (v1)

## Status and ownership

This is the approved contract boundary for the Fast & Pray public API. The API maintainer owns changes to this document. A public-contract change requires reviewer approval, a contract-test update, and a changelog entry.

## Base URL and versioning

- Public consumers use `/api/v1/`.
- Existing `/api/` and `/hub/` routes remain available for internal and product-client callers. They are unsupported for new third-party integrations, but `/api/` has compatibility obligations because shipped clients depend on it. Breaking or materially incompatible changes to `/api/` require app-version coordination and must not be justified solely by this public-v1 policy.
- Breaking public changes require a new path version, such as `/api/v2/`. Breaking changes include removing or renaming fields, changing types or nullability, changing request semantics, authentication, pagination, error codes, or HTTP-status behavior.
- Adding a resource or route to the current stable version is non-breaking, subject to the same readiness gates. A version bump is for retracting or changing an existing promise, not for opening a release channel.
- Additive optional response fields are allowed in a stable version when they do not change existing semantics.
- Versioned public code owns field selection, naming, shaping, and formatting. Shared queries, calculations, and side effects belong in reusable `hub/services/` functions; public views should not copy internal serializers or business logic.

## Initial resource inventory

V1 is anonymous and read-only. The following is the initial inventory and a floor, not a closed list; later resources may be added to v1 after readiness review:

| Resource | Route | Status | Follow-on work |
| --- | --- | --- | --- |
| Churches | `/api/v1/churches/` | default-disabled | #494, #497, #496, #498 |
| Readings | `/api/v1/readings/` | default-disabled | #494, #497, #496, #498 |
| Fasts | `/api/v1/fasts/` | default-disabled | #494, #497, #496, #498 |
| Feasts | `/api/v1/feasts/` | default-disabled | #494, #497, #496, #498 |
| Icons | `/api/v1/icons/` | default-disabled | #494, #497, #496, #498 |
| Calendar | `/api/v1/calendar/` | planned | #499 |

A resource cannot be mounted until it has a presentation-neutral serializer (#497), consistent validation and errors (#496), anonymous traffic protections (#498), and contract coverage. It becomes stable only after verified reference documentation is published (#500).

The `/api/v1/` root descriptor is live with `status: "pre-release"`, but the resource endpoints are not publicly released until issues #494, #496, #497, #498, #499, and #500 satisfy their gates. Each mounted resource also requires a golden contract test asserting its exact response key set and relevant nullability and URL rules, in addition to serializer, validation, documentation, and traffic-control readiness.

## Excluded route families

All route families not listed in the inventory are excluded by default. In particular, v1 excludes:

- authentication, accounts, profiles, password reset, token, and registration routes;
- fast participation, user-fast, user-day, participant, map, stats, intention, and legacy fast routes;
- devotionals, patristic quotes, feedback, notifications, admin helpers, events, prayers, prayer requests, unsupported icon upload, feedback, matching, and admin families, learning resources, uploads, system tags, and all `/hub/` routes;
- the S3 upload helpers at `/api/s3-upload/`.

Internal URLconfs must never be mounted under `/api/v1/` as a shortcut for publishing a resource.

## Compatibility and deprecation

Stable v1 resources retain their response fields and request semantics for the lifetime of v1. A breaking change requires a new API version; the affected v1 route is not removed or repurposed in place.

If a successor version is required, its deprecation of v1 is announced in the public reference documentation and release notes. V1 remains available for at least 180 days unless a security, privacy, or legal emergency requires faster retirement, and its routes return `Deprecation` and `Sunset` response headers during that version-level notice period.

Normally, no more than two public major versions are supported concurrently. Supporting additional overlap requires explicit maintainer approval and a retirement plan.

## Errors and request validation

Every public-v1 error uses this envelope:

```json
{
  "code": "machine_readable_code",
  "message": "Human-readable description.",
  "details": {}
}
```

`code` and the documented `details` keys are stable public contract. `message`
may change. `details` is always an object; it is empty when no structured
context applies.

Public views ignore Authorization headers and render JSON only. An unsupported
Accept header (for example, `text/html`) returns HTTP 406 with code
`not_acceptable` and empty `details`, also as JSON. Unmatched paths under
`/api/v1/` return HTTP 404 with code `not_found` and empty `details`, with
`application/json` content type regardless of the request method or Accept
header. Non-v1 Django 404 behavior is unchanged.

Public resource routes validate only the parameters they accept. Missing
optional parameters retain the route's documented default; a supplied invalid
parameter never falls back to that default. Accepted parameters are validated before
any resource lookup, including empty results and unknown IDs. Unknown parameters
are ignored.

| Parameter | Valid values | Failure code | Details |
| --- | --- | --- | --- |
| `date`, `start_date`, `end_date` | Exact ISO `YYYY-MM-DD` calendar date | `invalid_date` | `parameter`, `value` |
| `start_date` + `end_date` | `start_date <= end_date` | `invalid_date_range` | `start_date`, `end_date` |
| `lang` | `en` or `hy` | `unsupported_language` | `parameter`, `value`, `supported` |
| `tz` | IANA timezone name, e.g. `America/Los_Angeles` | `invalid_timezone` | `parameter`, `value` |
| Required `church_id` | Positive canonical integer | `missing_parameter` or `invalid_church_id` | `parameter` (and `value` for invalid) |
| `limit` | Whole number from 1 through 100 | `invalid_pagination` | `parameter`, `value` |
| `offset` | Non-negative whole number | `invalid_pagination` | `parameter`, `value` |

Routes that resolve a church return `church_not_found` with `details.church_id`
when the syntactically valid ID is unknown. Unknown public resources use
`resource_not_found` with `details.resource`. Both use HTTP 404. Validation
errors use HTTP 400.

## Default-disabled pre-release routes

All routes below are anonymous, read-only JSON endpoints, registered only when
`PUBLIC_API_RESOURCES_ENABLED=true` (default: `false`). Keep this disabled pending
#498 traffic readiness; #540 will strengthen this minimal gate. The root and
final JSON not-found fallback remain live in either state. A required
`church_id` is a canonical positive integer discovered through
`GET /api/v1/churches/`.

Collection endpoints use one limit/offset envelope. The default `limit` is 25
and the maximum is 100; collections are ordered by ascending ID; `next` and `previous` are URLs or `null`.

```json
{
  "count": 123,
  "next": "https://example.test/api/v1/churches/?limit=25&offset=25",
  "previous": null,
  "results": []
}
```

| Route | Parameters | Response |
| --- | --- | --- |
| `GET /api/v1/churches/` | optional `lang`, `limit`, `offset` | Paginated Church objects. Use `id` as `church_id` for church-scoped routes. |
| `GET /api/v1/icons/` | optional `church_id`, `lang`, `limit`, `offset` | Paginated Icon objects. |
| `GET /api/v1/fasts/` | required `church_id`; optional `start_date`, `end_date`, `tz`, `lang`, `limit`, `offset` | Paginated Fast objects whose days overlap the inclusive range. Omit the range for 180 days before through 180 days after today in `tz`. |
| `GET /api/v1/fasts/{id}/` | optional `lang` | One Fast object, or `resource_not_found` (404). |
| `GET /api/v1/fasts/by-date/` | required `church_id`, `date`; optional `lang`, `limit`, `offset` | Paginated Fast objects active on the inclusive ISO date. |
| `GET /api/v1/fasts/by-feast-date/` | required `church_id`, `date`; optional `lang`, `limit`, `offset` | Paginated Fast objects with that culmination-feast date. |
| `GET /api/v1/readings/` | required `church_id`, `date`; optional `lang` | `{ "date": "YYYY-MM-DD", "readings": [Reading] }`. Returns only stored citations; an unimported calendar day has an empty list. |
| `GET /api/v1/feasts/` | required `church_id`, `date`; optional `lang` | `{ "date": "YYYY-MM-DD", "feasts": [Feast] }`. The date resolves offline; only stored commemorations are returned, in service order; zero matches return `feasts: []`. Legacy service dictionaries and future lists are normalized. When the model supports `observance_id`, lookup uses that stable ID; otherwise it uses the legacy name. No rows are created. |

The public route layer does not create calendar rows, retrieve passage text,
generate contexts, write caches, or enqueue background jobs. `/api/v1/fasts/{id}/days/`
and every other internal `/api/` or `/hub/` route remain unsupported: v1 has
no public Day schema, so the product route is not republished as a shortcut.

## Schema (presentation-neutral serializers)

Approved fields per resource for the public v1 serializers. The serializer
is the single source of truth for field selection, naming, and shape; the
endpoint (when mounted) is a thin pass-through. Every field below is
covered by a contract test asserting exact key set, value type, and
nullability.

Missing optional values serialize as JSON `null`, never as the empty string.
User-facing text follows the requested language (`?lang=` query parameter,
falling back to the Django-resolved request language, then `en`) and falls
back to the canonical/base-language field when no translation is registered.

Public serializers MUST NOT trigger thumbnail generation, access any
`ImageSpecField.url`, perform S3 I/O, write to cache, or write to models.
Thumbnails come exclusively from the pre-populated `cached_thumbnail_url`
column; the public field serializes to `null` when that cache is empty.
`image_url` uses the original image field's URL only when an image is
present, otherwise `null`.

### Church

| Field | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | integer | no | Primary key. |
| `name` | string | no | Canonical/base language. Church names do not currently have translations. |

### Fast

| Field | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | integer | no | Primary key. |
| `church_id` | integer | no | Owning church. |
| `name` | string | no | Localized name. |
| `description` | string | yes | Localized description. |
| `start_date` | date (ISO 8601) | yes | Pre-annotated only. Serializers MUST NOT query related days. `null` when absent. |
| `end_date` | date (ISO 8601) | yes | Pre-annotated only. Serializers MUST NOT query related days. `null` when absent. |
| `culmination_feast` | string | yes | Localized culmination name. |
| `culmination_feast_date` | date (ISO 8601) | yes | |
| `year` | integer | yes | |
| `image_url` | string | yes | URL of the original uploaded image only. |
| `thumbnail_url` | string | yes | Cached thumbnail URL only. `null` when no cache. MUST NOT access `image_thumbnail.url`. |
| `learn_more_url` | string | yes | Mapped from `Fast.url`. |

Excluded (non-exhaustive): `modal_id`, `countdown`, `joined`,
`participant_count`, `days_to_feast`, `has_passed`, `next_fast_date`,
`total_number_of_days`, `current_day_number`,
`culmination_feast_salutation`, `culmination_feast_message`,
`culmination_feast_message_attribution`, `has_day_zero`, `image`,
`image_thumbnail`, `cached_thumbnail_url`, `cached_thumbnail_updated`,
and the nested `church` object.

### Reading (citation-only)

| Field | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | integer | no | Primary key. |
| `sequence` | integer | yes | Order within the day's readings. |
| `book` | string | no | Localized book name. |
| `start_chapter` | integer | no | |
| `start_verse` | integer | no | |
| `end_chapter` | integer | no | |
| `end_verse` | integer | no | |

Excluded (non-exhaustive): legacy `text*` and `text_hy*` fields,
`text_copyright`, `text_version`, `text_fetched_at`, `fums_token`,
`passage_key`, `day`, and any AI-generated context/thumbs or passage text.

### Feast

| Field | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | integer | no | Primary key. |
| `name` | string | no | Localized name. |
| `icon` | object (IconPublicSerializer) | yes | Nested icon, or `null` when no icon is matched. |

Excluded (non-exhaustive): `church`, `church_id`, `designation`,
context/votes/LLM/prayer fields.

### Icon

| Field | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | integer | no | Primary key. |
| `title` | string | no | Canonical/base language. Icon titles do not currently have translations. |
| `image_url` | string | yes | URL of the original uploaded image only. |
| `thumbnail_url` | string | yes | Cached thumbnail URL only. `null` when no cache. MUST NOT access `thumbnail.url`. |

Excluded (non-exhaustive): `church`, `church_id`, `tags`, `tag_list`,
`image_hash`, `phash`, `image_content_digest`, `image_revision`,
`original_filename`, `filename_provenance`, `created_at`, `updated_at`,
and any cache columns.

## Change history

A public-contract change requires reviewer approval, a contract-test
update, and an entry in this changelog. Entries are reverse-chronological.

| Date | Change |
| --- | --- |
| 2026-09-09 | Default-disabled resource registration pending #498; made accepted parameter validation eager, isolated Fast dates by owning church, and aligned Feast responses with the pending observance-ID/`feasts[]` migration. Added route contracts. (Issue #494 / PR #539.) |
| 2026-09-09 | Made the anonymous, JSON-only boundary shared by v1 views; defined JSON `not_found` responses for unmatched v1 paths and JSON `not_acceptable` responses for unsupported Accept headers on mounted views. (Issue #496.) |
| 2026-09-08 | Implemented the pre-release Church, Icon, Fast, Reading, and Feast resource routes under `/api/v1/`; documented strict route parameters, consistent collection pagination, read-only calendar lookup behavior, and the explicitly unsupported Fast-days route. (Issue #494.) |
| 2026-09-08 | Defined v1's shared validation rules and stable error envelope for dates, ranges, languages, timezones, and church IDs. (Issue #496.) |
| 2026-09-08 | Added Icons to the initial planned v1 inventory; narrowed the icon exclusion to icon upload, feedback, matching, and admin families; defined exact serializer field/type/nullability/localization/media rules for Church, Fast, Reading, Feast, and Icon; pinned public thumbnail behavior to the cached URL only and forbade `ImageSpecField.url` access during serialization. (Issue #497.) |

## Release gate

`/docs/` remains in its Coming soon state until #494 and #496–#500 satisfy their respective readiness criteria. The current `/api/v1/` root is a live service descriptor, but it does not imply any planned resource is publicly released or available.
