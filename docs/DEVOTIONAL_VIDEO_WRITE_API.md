# Staff devotional-video API

Public video list and detail `GET` requests are unchanged. Authenticated staff may
`POST /api/learning-resources/videos/` and `PATCH
/api/learning-resources/videos/<id>/`. The strict write fields are `title`,
`description`, `language_code`, `upload_token`, and optional `thumbnail`.
`clear_thumbnail: true` is PATCH-only and cannot accompany `thumbnail`. Create
requires all metadata and `upload_token`; PATCH requires at least one change.
Client-provided category, IDs/timestamps, video paths/URLs, and unknown fields are
rejected. Every created or edited resource is devotional.

Direct video transfer uses staff-only POST endpoints:

1. `/api/learning-resources/devotional-videos/uploads/initialize/` with
   `file_name`, `file_size`, and `content_type`.
2. `.../complete/` with the opaque `upload_session`, returned `upload_id`, and
   ordered `parts` (`part_number`, `size`, `etag`). Submit its body directly to
   the returned S3 URL.
3. `.../finalize/` with only `upload_session`; this returns an opaque,
   user-bound, one-use `upload_token`, filename, and size.

Only basename `.mp4`/`video/mp4` and `.webm`/`video/webm` pairs from 1 through
500 MiB are accepted. Sessions expire after one hour and final tokens after 24
hours. Finalization verifies object existence and exact size. Video bytes never
pass through Django, and object keys are never accepted or returned by this API.
Each returned part includes its exact `size`; the client must upload exactly that
many bytes because the size is bound into the S3 signature.

Production requires django-storages S3 storage with the boto3 client contract
used by `django-s3-file-field` 1.0.1. The bucket must have an S3 lifecycle rule
that aborts incomplete multipart uploads after one day, covering clients that
initialize and then disappear. The API also makes a best-effort abort when a
server-side preparation failure makes an upload unusable. Aborting an already
completed multipart upload does not delete its completed object.

Every dedicated upload is recorded in a private database ledger before its
session is returned. Its object key is always below `videos/devotional-cli/`.
Final tokens are owner-bound, and token consumption, video persistence, and the
ledger transition to `attached` occur in one transaction. A failed video save
therefore leaves the ready ledger available for retry or audited cleanup.

Run `python manage.py cleanup_devotional_video_uploads` for a dry-run listing of
expired initialized/ready uploads. Run
`python manage.py cleanup_devotional_video_uploads --execute` daily to abort the
recorded incomplete multipart upload or delete the exact completed object, then
mark the ledger `cleaned`. The command is idempotent and never scans general
`videos/` keys, attached uploads, or legacy/admin uploads. Keep the 24-hour token
retention and the bucket's one-day incomplete-multipart lifecycle rule aligned.

Success is 200 (PATCH/upload steps) or 201 (create); malformed, expired, or used
capabilities return 400; authentication/authorization returns 401/403; missing
devotional video returns 404; unsupported methods return 405.

The installed package's legacy `/api/s3-upload/upload-initialize/`,
`upload-complete/`, and `finalize/` routes remain available to `S3FileInput` in
Django admin with their original names and paths. A Django staff-session gate
protects all three before delegating to `django-s3-file-field`; they are not CLI
publishing endpoints. The dedicated endpoints above also require staff access.
