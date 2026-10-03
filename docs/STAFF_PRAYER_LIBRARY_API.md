# Staff prayer-library API

Implements the backend dependency of [CLI #8](https://github.com/surp-hovhannes/fastandpray-cli/issues/8), alongside the existing [admin importer #415](https://github.com/surp-hovhannes/bahk/issues/415). This is library content, not user prayer requests. Public `prayers/`, `prayer-sets/`, `tags/?model=prayer` and request lifecycle routes/serializers are unchanged.

## Scope and safety

`/api/staff/prayer-library/<church_id>/` requires an authenticated active staff account. Non-superusers must have an explicit PrayerLibraryChurchGrant for the selected church; superusers may explicitly select any existing church. Every referenced prayer/set is selected from that church. Grants are managed only by superusers in Django admin; staff cannot create/edit/revoke grants, and user-editable Profile.church grants no authority. No grants are created automatically by the migration. No login or token refresh is performed. The API creates no tags, changes no shared taxonomy, and starts no icon/AI jobs. Supplied tag names must exactly identify existing shared taggit names; spelling suggestions are advisory CLI behavior only. Global tag creation/rename/merge/delete remain deferred until ownership and cross-model impact are agreed.

All mutations run inside one transaction after locking the selected Church, matching the existing admin bulk-import lock. Updates/deletes/membership/reorder also require a snapshot revision checked under that lock. Tags referenced by writes are locked and assigned as existing Tag objects. Public reads are unaffected. Bilingual staff reads take the church lock and hash the exact returned snapshot, so content and revision agree. Revisions include both translations, tag names, membership order/content, and affected-set IDs for prayer deletion; callers must treat them as opaque hashes.

## Read contract

GET query parameters:

- `kind=prayer|set`: list IDs, titles and revisions in selected church.
- `kind=prayer|set&id=ID`: `{"record":{...},"revision":"SHA256"}`. Records contain stable IDs, church_id, base EN fields and HY fields, category, tags (prayers), affected_set_ids (prayers), and prayers in canonical membership order (sets). Base fields never undergo language fallback. Empty/null HY values preserve missing/cleared translations.
- `operation_key=UUID`: account-bound `{"operation_key":"UUID","digest":"SHA256","status":"completed","result":{...}}`. Not found is 404; the receipt is never a source-text export.

Set export in the CLI produces the import content shape in a new file, retaining order, texts, translations, tags and descriptions, including empty sets/blank translations. IDs/church/revision belong to staff inspection/receipt output rather than new-record content import. Exporting existing content does not authorize duplicating it; default title-conflict checks still apply to re-import.

## Plan and execution contract

POST strict UTF-8 JSON, <=5 MiB; duplicate keys, unknown fields, non-finite numbers and invalid Unicode are rejected. All fields below are top-level. `preview` defaults to true and must be a boolean.

```json
{"action":"prayer.create","payload":{"record":{"title":"Synthetic Prayer 001","title_hy":"Սինթետիկ 001","text":"Synthetic English fixture.","text_hy":"Սինթետիկ հայերեն։","category":"general","tags":[]}},"if_match":null,"preview":true}
```

Preview is a read-only server plan (POST to avoid putting prose into URLs/logged query strings), distinct from CLI offline preview. It locks and validates but writes neither content nor receipt. Output contains account/church IDs, counts, references, positions, requested tags/changed fields, and conflict policy. A conflict/invalid input returns `plan.blocked=true`, reason_code and applicable existing_ids, without source prose. Referenced cross-church or missing records return 404. A plan is not an authorization reservation: execution repeats validation under lock.

Execution adds `preview:false`, a canonical UUID `operation_key` and `digest`. Digest is SHA-256 of UTF-8 compact, sorted-key JSON of exactly `{"action":ACTION,"payload":PAYLOAD,"if_match":REVISION_OR_NULL}`, using `ensure_ascii=False`. Python helper `prayers.staff_library.digest` and the CLI use the same canonical encoding. Different JSON whitespace/key order produces the same digest; field/tag/membership array order remains significant.

| action | payload | revision |
| --- | --- | --- |
| prayer.create / set.create | `{"record":<metadata>}` | null |
| prayer.update / set.update | `{"id":ID,"record":<partial metadata>}` | required |
| prayer.delete | `{"id":ID,"affected_set_ids":[SORTED_IDS]}` | required |
| set.delete | `{"id":ID}` | required |
| prayer.import | `{"prayers":[<new prayer>,...]}` | null |
| set.import | `{"prayer_sets":[{"title":"Set","category":"general","prayers":[<new prayer>,{"prayer_id":ID}]}]}` | null |
| members.add | `{"id":SET_ID,"prayer_id":ID,"position":POS}` | required |
| members.remove | `{"id":SET_ID,"prayer_id":ID}` | required |
| members.reorder | `{"id":SET_ID,"prayer_ids":[ID,...]}` | required |

Prayer metadata: title/text/category required on create; optional title_hy/text_hy/tags. Set metadata: title/category required; optional title_hy/description/description_hy. Updates are partial and cannot be empty. Explicit blank/null HY values clear that translation; omissions preserve it. Categories morning/evening/general, prayer titles <=200, set titles <=128, tag names <=100. Base titles/texts are nonblank. Full bilingual completeness is collection-specific, not imposed on every historic prayer.

Imports require a nonempty root array. Sets may have an empty prayers array, consistent with set.create and removal of the final member. Each member is exclusively a new prayer or `{"prayer_id":ID}`. Standalone import accepts only new prayers. New normalized title matches in church and duplicates within the input cause 409: no overwrite/skip or inferred merge. Reuse is explicit by ID, leaving its fields/tags untouched. New records reuse shared admin validation/translation helpers. The old browser admin importer remains its existing contract and does not accept this reference extension.

Reorder must be an exact permutation of current membership (including empty->empty), with no duplicate/missing/unknown/cross-church IDs. Membership edits/reorder atomically normalize order to 1..N. Set deletion removes memberships and preserves prayers. Prayer deletion requires acknowledgement of every affected set ID and resequences those sets. A stale/missing revision or changed deletion impact returns 409 and performs no writes. Malformed/boolean/duplicate acknowledgement IDs are rejected. Legacy cross-church memberships block set management or prayer deletion until separately repaired, preventing a cascade/order edit outside the selected scope.

## Durable receipts and retries

The receipt is written in the same transaction as content. It returns created/reused prayer IDs, created set IDs, set revisions/positions, or individual affected IDs/revisions. Receipts contain no source texts/titles or credentials. The `(church,key)` unique constraint reserves each key; it is bound to the original account/digest. Repeated identical requests return the original result even if revisions have since changed; same key/different request/account returns 409. There is no automatic write retry. After timeout/interruption, query the receipt first; a failed status lookup must prevent retry. A 404 permits submitting the same key/payload; the church lock makes this safe even if the first request is still finishing. Failed validation/persistence rolls back both content and receipt.

Receipts remain until the church is deleted. Deleting an account nulls the user reference, preserves the reserved key, and does not block account deletion. There is no scheduled receipt expiry.

## Migration and rollback

`prayers.0011_prayerlibraryoperation` depends only on `prayers.0010_prayer_set_icon` and the auth user model. It adds receipt and explicit church-grant tables and does not alter prayer content or the separate hub participation migrations/PR559/560. No production migration has been run.

Before reverting the migration in an environment with executed operations, disable staff writes and preserve/export receipt records. Dropping the receipt table loses replay protection; preserve grants before rollback too; do not retry old operations without their saved receipt evidence. Reverting API/CLI code alone leaves existing prayer data and receipts intact. This is an additive feature migration, not a clawpatch schema sweep.

## Provenance, content rights and tests

Canonical numbering, source URL, translator/edition/rights and subsection mappings stay in a reviewed CLI sidecar until a persisted contract is agreed. They are rejected in record bodies rather than ignored. Subsection tags must not be copied to whole-prayer tags. First-class sections/excerpts remain a separate product decision. Real Narek English permissions and Armenian edition are still pending; synthetic fixtures only were used, with no production reads/imports/publication.

`prayers.test_staff_library` covers permissions, cross-church references, 95 bilingual records in one ordered set, exact content round trip, receipts/digest conflicts, revisions, membership/reorder/delete, strict parsing, expired auth, blank translation clearing, empty sets and mid-persistence rollback. `prayers.test_staff_library_concurrency` verifies simultaneous identical-key imports and competing stale-revision updates on PostgreSQL; it is skipped on SQLite because SQLite cannot verify row locks. The CLI's `scripts/test_bahk_localhost.py` drives real HTTP against an ephemeral LiveServerTestCase with a test-only in-memory token and database, then destroys them.
