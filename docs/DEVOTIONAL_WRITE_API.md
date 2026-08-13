# Devotional write API

The existing devotional collection and detail routes remain publicly readable.
Creating and editing devotionals requires an authenticated staff user.

## Create

`POST /api/devotionals/` accepts a JSON object with required `day`, `video`,
`language_code`, and non-negative `order` fields. `order` cannot be `null`.
`description` is optional and may be `null`. The day and video must be existing
object IDs, and the language must be configured by the application.

A successful create returns `201` with the normal public devotional
representation.

## Partial update

`PATCH /api/devotionals/<id>/` accepts one or more of the same fields. Omitted
fields, including `order`, are unchanged. If supplied, `order` cannot be `null`.
An empty object is rejected. A successful update returns `200` with the normal
public devotional representation. `PUT` and `DELETE` are not supported.

Unknown fields (including IDs, timestamps, categories, and video URLs/paths),
invalid fields, missing related objects, and duplicate `(day, order,
language_code)` combinations return `400`. A missing devotional returns `404`.
Unauthenticated and non-staff writes return `401` or `403` as appropriate.
