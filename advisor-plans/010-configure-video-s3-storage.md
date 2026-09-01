# Plan 010: Configure the video field for multipart S3 uploads

> **Executor instructions**: This is a hold-lane migration. Execute only with explicit approval for the migration, which has been granted. Keep this change in a dedicated PR; do not mix it with the notification-namespace or agent-guidance work.
>
> **Drift check**: `git diff --stat 5af5c7d..HEAD -- learning_resources/models.py learning_resources/migrations/0010_alter_video_video.py learning_resources/tests.py tests/test_settings.py`

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: Medium
- **Depends on**: none
- **Category**: bug, migration
- **Planned at**: commit `5af5c7d`, 2026-09-01

## Why this matters

`django-s3-file-field` accepts `storages.backends.s3.S3Storage` or MinIO storage for its multipart workflow. `Video.video` currently receives Django's lazy `DefaultStorage` proxy, which the package rejects with `s3_file_field.W001`; the same type check would prevent multipart upload manager construction in production. Configure the field with concrete `S3Storage` so direct video uploads use a package-supported backend.

The field storage change is serialized by Django as an `AlterField` migration. It changes Django model state only; it does not alter the database column or transform stored video objects. A dedicated review and rollback path are required.

## Scope

**In scope**:

- `learning_resources/models.py` — configure `Video.video` with `S3Storage()`.
- `learning_resources/migrations/0010_alter_video_video.py` — generated `AlterField` migration.
- `learning_resources/tests.py` — assert the model field is an `S3Storage` and accepted by `MultipartManager`.
- `tests/test_settings.py` — silence only `s3_file_field.E002`, whose live-bucket probe cannot run in credential-free tests.

**Out of scope**:

- AWS credentials, buckets, CORS, object data, or upload URL changes.
- `bahk/urls.py`, `hub/urls.py`, and `AGENTS.md`; those belong to the separate routine warning-remediation PR.
- Suppressing `s3_file_field.W001`; it must disappear because the storage type is corrected.

## Implementation

1. Import `S3Storage` from `storages.backends.s3` and pass `storage=S3Storage()` to `Video.video`'s existing `S3FileField`. Preserve its upload path and help text.
2. Generate `0010_alter_video_video.py` with `python manage.py makemigrations learning_resources --skip-checks --settings=tests.test_settings`. Review it: the only operation must be `AlterField(Video.video)` with the existing upload path/help text plus `S3Storage()`.
3. Add a no-network unit test that asserts the field uses `S3Storage` and `MultipartManager.supported_storage()` returns true.
4. In test settings only, silence `s3_file_field.E002` with a comment explaining it is the package's live S3 bucket probe and tests intentionally lack AWS credentials. Do not silence W001.

## Verification

Run from the local Docker app container when Crabbox is unavailable:

```bash
docker exec -e IS_PRODUCTION=false <app-container> python manage.py test \
  learning_resources.tests.VideoStorageConfigurationTests \
  --exclude-tag=performance --settings=tests.test_settings

docker exec -e IS_PRODUCTION=false <app-container> python manage.py check \
  --settings=tests.test_settings

docker exec -e IS_PRODUCTION=false <app-container> python manage.py makemigrations \
  --check --dry-run --skip-checks --settings=tests.test_settings
```

Expected: targeted tests pass; this dedicated branch may still report the separate `urls.W005` warning until `fix/system-check-warnings` lands. In an integration checkout containing both branches, checks report no unsilenced issues and no new migrations are detected.

## Review checklist

- The migration contains no `RunPython`, `RunSQL`, data deletion, or schema-column operation.
- The only system-check suppression is test-only `s3_file_field.E002`; W001 is not silenced.
- `S3Storage()` imports without AWS credentials when `IS_PRODUCTION=false`.
- No routine URL/agent-guidance files appear in the migration PR.

## Rollback

Revert the dedicated migration commit and migrate `learning_resources` back to `0009`. Because the migration only changes model-field configuration, it does not delete or rewrite existing video references or S3 objects. Stop and report if migration inspection reveals database DDL, data migration, or any storage-object operation.
