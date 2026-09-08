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

| Resource | Planned route | Status | Follow-on work |
| --- | --- | --- | --- |
| Churches | `/api/v1/churches/` | planned | #494, #497, #496, #498 |
| Readings | `/api/v1/readings/` | planned | #494, #497, #496, #498 |
| Fasts | `/api/v1/fasts/` | planned | #494, #497, #496, #498 |
| Feasts | `/api/v1/feasts/` | planned | #494, #497, #496, #498 |
| Calendar | `/api/v1/calendar/` | planned | #499 |

A resource cannot be mounted until it has a presentation-neutral serializer (#497), consistent validation and errors (#496), anonymous traffic protections (#498), and contract coverage. It becomes stable only after verified reference documentation is published (#500).

The `/api/v1/` root descriptor is live with `status: "pre-release"`, but the resource endpoints are not publicly released until issues #494, #496, #497, #498, #499, and #500 satisfy their gates. Each mounted resource also requires a golden contract test asserting its exact response key set and relevant nullability and URL rules, in addition to serializer, validation, documentation, and traffic-control readiness.

## Excluded route families

All route families not listed in the inventory are excluded by default. In particular, v1 excludes:

- authentication, accounts, profiles, password reset, token, and registration routes;
- fast participation, user-fast, user-day, participant, map, stats, intention, and legacy fast routes;
- devotionals, patristic quotes, feedback, notifications, admin helpers, events, prayers, prayer requests, icons, learning resources, uploads, system tags, and all `/hub/` routes;
- the S3 upload helpers at `/api/s3-upload/`.

Internal URLconfs must never be mounted under `/api/v1/` as a shortcut for publishing a resource.

## Compatibility and deprecation

Stable v1 resources retain their response fields and request semantics for the lifetime of v1. A breaking change requires a new API version; the affected v1 route is not removed or repurposed in place.

If a successor version is required, its deprecation of v1 is announced in the public reference documentation and release notes. V1 remains available for at least 180 days unless a security, privacy, or legal emergency requires faster retirement, and its routes return `Deprecation` and `Sunset` response headers during that version-level notice period.

Normally, no more than two public major versions are supported concurrently. Supporting additional overlap requires explicit maintainer approval and a retirement plan.

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

`/docs/` remains in its Coming soon state until #494 and #496–#500 satisfy their respective readiness criteria. The current `/api/v1/` root is a live service descriptor, but it does not imply any planned resource is publicly released or available.
