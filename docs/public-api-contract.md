# Fast & Pray Public API Contract (v1)

## Status and ownership

This is the approved contract boundary for the Fast & Pray public API. The API maintainer owns changes to this document. A public-contract change requires reviewer approval, a contract-test update, and a changelog entry.

## Base URL and versioning

- Public consumers use `/api/v1/`.
- Existing `/api/` and `/hub/` routes remain available for internal and product-client callers, but are unsupported for new public integrations and have no public compatibility promise.
- Breaking public changes require a new path version, such as `/api/v2/`. Breaking changes include removing or renaming fields, changing types or nullability, changing request semantics, authentication, pagination, error codes, or HTTP-status behavior.
- Additive optional response fields are allowed in a stable version when they do not change existing semantics.

## Initial resource inventory

V1 is anonymous and read-only. The following resources are planned, not yet mounted as v1 resource routes:

| Resource | Planned route | Status | Follow-on work |
| --- | --- | --- | --- |
| Churches | `/api/v1/churches/` | planned | #494, #497, #496, #498 |
| Readings | `/api/v1/readings/` | planned | #494, #497, #496, #498 |
| Fasts | `/api/v1/fasts/` | planned | #494, #497, #496, #498 |
| Feasts | `/api/v1/feasts/` | planned | #494, #497, #496, #498 |
| Calendar | `/api/v1/calendar/` | planned | #499 |

A resource cannot be mounted until it has a presentation-neutral serializer (#497), consistent validation and errors (#496), anonymous traffic protections (#498), and contract coverage. It becomes stable only after verified reference documentation is published (#500).

## Excluded route families

All route families not listed in the inventory are excluded by default. In particular, v1 excludes:

- authentication, accounts, profiles, password reset, token, and registration routes;
- fast participation, user-fast, user-day, participant, map, stats, intention, and legacy fast routes;
- devotionals, patristic quotes, feedback, notifications, admin helpers, events, prayers, prayer requests, icons, learning resources, uploads, system tags, and all `/hub/` routes;
- the S3 upload helpers at `/api/s3-upload/`.

Internal URLconfs must never be mounted under `/api/v1/` as a shortcut for publishing a resource.

## Compatibility and deprecation

Stable v1 resources retain their response fields and request semantics for the lifetime of v1. A breaking change requires a new API version; the affected v1 route is not removed or repurposed in place.

When a successor version replaces v1, its deprecation is announced in the public reference documentation and release notes. V1 remains available for at least 180 days unless a security, privacy, or legal emergency requires faster retirement, and its routes return `Deprecation` and `Sunset` response headers during that version-level notice period.

## Errors

V1 reserves this error envelope; #496 defines the resource-level codes, validation, and status conventions:

```json
{
  "code": "machine_readable_code",
  "message": "Human-readable description.",
  "details": {}
}
```

`code` is stable. `message` may change. `details` is optional and resource-specific.

## Release gate

`/docs/` remains in its Coming soon state until #494 and #496–#500 satisfy their respective readiness criteria. The current `/api/v1/` root is only a service descriptor; it does not imply any planned resource is available.
