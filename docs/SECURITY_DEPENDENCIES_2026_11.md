# Django and DRF remediation: issues #567 and #568

Verified November 23, 2026 against main `45000955b169e581e568944ce9fccc9bc1585834`.
One coordinated patch changes Django 5.2.17 to 5.2.19 and Django REST Framework
3.17.2 to 3.18.3, and bounds Sentry below 3 to keep a clean rebuild compatible.
Python remains 3.11. No application settings or migrations change.

## Released packages and security scope

- [Django 5.2.19 on PyPI](https://pypi.org/project/Django/5.2.19/)
  is a final, non-yanked November 3 release requiring Python >=3.10.
  The [supported 5.2 LTS branch](https://www.djangoproject.com/download/#supported-versions)
  receives extended support through April 2028.
  [5.2.20 is expected December 1](https://docs.djangoproject.com/en/5.2/releases/5.2.20/)
  and is not the target of this patch.
- [DRF 3.18.3 on PyPI](https://pypi.org/project/djangorestframework/3.18.3/)
  is a final, non-yanked October 6 release requiring Python >=3.10 and Django >=5.2.
  The September date in #568 describes 3.18.1, not 3.18.3.
  [Official release notes](https://www.django-rest-framework.org/community/release-notes/#3183)
  identify 3.18.3 as the content-negotiation regression follow-up to 3.18.2.

[Django 5.2.18 advisories](https://docs.djangoproject.com/en/5.2/releases/5.2.18/)
cover language lookup (CVE-2026-77050), header parsing (CVE-2026-84429), spatial
lookup bytes (CVE-2026-87890), editable-primary-key formsets (CVE-2026-87975),
and the geometry depth mitigation follow-up (CVE-2026-15830).
[5.2.19 advisories](https://docs.djangoproject.com/en/5.2/releases/5.2.19/)
cover multipart memory use (CVE-2026-108147), geometry logging (CVE-2026-111211),
GeoJSON CRS references (CVE-2026-111362), Vary cache keys (CVE-2026-113593),
and SpatialReference strings (CVE-2026-114582).

Bahk uses ordinary request/header parsing, multipart profile images, language
selection and `cache_page`/`vary_on_headers('Authorization')` in `hub/views/fast.py`.
These are relevant source surfaces, not a claim of deployed exploitability.
No GeoDjango imports or spatial model fields were found in application code;
the map/geopandas dependencies do not establish use of Django's GIS input paths.
No explicit editable primary-key model declarations were found in this inventory.

DRF's patched advisories are
[content negotiation](https://github.com/encode/django-rest-framework/security/advisories/GHSA-33wh-fxxf-88vv),
[JSON charset handling](https://github.com/encode/django-rest-framework/security/advisories/GHSA-3547-9m27-7rxg),
and [TokenProxy deletion](https://github.com/encode/django-rest-framework/security/advisories/GHSA-xw6w-gcfp-cf8m).
All name 3.18.2 as patched; 3.18.3 includes those fixes. Bahk uses DRF JSON and
content negotiation, with SimpleJWT/session authentication. No TokenProxy or
DRF TokenAuthentication use was found. Verification uses ordinary regression
inputs only, without vulnerability reproduction.

## Install inputs and resolution

`requirements.txt` is the Python package input used by the devcontainer Dockerfile,
cloud startup script and both Django CI lanes. `pyproject.toml` supplies the Python
3.11 range and Ruff configuration, without another dependency list. `Procfile`
starts gunicorn/Celery from that installed runtime. There is no tracked Python
lockfile, constraints file, requirements-dev.txt, Pipfile, or alternate Python
deployment manifest to synchronize. `package-lock.json` is for JavaScript tools.

Capture the pre-update environment with `python -m pip freeze`, update using
`python -m pip install -r requirements.txt`, then compare freezes and run
`python -m pip check`. Both CI install lanes now run that consistency check.
Unpinned dependencies remain an existing reproducibility limitation;
this patch does not introduce a broad lockfile. Existing unrelated version pins
remain unchanged. Without the Sentry bound, a fresh resolver on November 23 selects 112 application packages,
including 41 version differences from the captured cloud runtime (among them
Celery 5.7.0, Gunicorn 27.0.1, Pillow 13.0.0 and Sentry SDK 3.0.0). Thus the
controlled two-package comparison and a normal clean CI build validate different
dependency sets. A new broad lock derived from this cloud snapshot would not
establish the unknown deployed dependency set; preserve the actual build's
resolved environment before rollout.

The additional `sentry-sdk[django]<3` constraint is required for a normal rebuild:
Sentry 3.0.0 passes `pip check` but normal startup fails with
`TypeError: Unknown option 'profiles_sample_rate'` from the existing initialization
in `bahk/settings.py`. Django tests and `CI=true` bypass that initialization, so a
passing test run alone misses it. The new subprocess test in
`tests/test_settings_import.py` runs `manage.py check` with `CI=false`, development
mode, an empty DSN and empty AWS settings. It fails with 3.0.0 before the bound.
[Upstream 3.0 release notes](https://github.com/getsentry/sentry-python/releases/tag/3.0.0)
and the [migration guide](https://docs.sentry.io/platforms/python/migration/2.x-to-3.x)
confirm breaking configuration changes. The compatible constraint permits the
snapshot's 2.71.0 and the current stable, non-yanked
[2.76.0](https://pypi.org/project/sentry-sdk/2.76.0/), which supports Python 3.11
and retains the Django extra. Migrating the application's Sentry configuration
to 3.x is separate work.

## Mobile error contracts and write inventory

Keep DRF's `LIST_SERIALIZER_ERRORS_AS_DICT=True` default. No reachable writable
`ListSerializer` or bulk `many=True` validation was found in the mounted APIs:

| Surface | Input/output contract |
| --- | --- |
| Registration | One user object with one nested profile object; unique email validation |
| Profile, password reset, JWT, social auth | Single-object inputs |
| Staff user groups | Generated `ManyRelatedField` accepts relation lists; it is not a `ListSerializer` |
| Prayer request create/update | Single-object serializer; scalar title, description, duration and icon ID |
| Prayer-set membership | `many=True` is used only to serialize output |
| Events/statistics/activity | Nested list serializers and ListFields shape output; views pass instances, not input data |
| Public v1 resources | GET/HEAD/OPTIONS and explicitly read-only serializers |
| Staff devotional video/library and upload endpoints | Single-object contracts and explicit field validation |

Model latitude/longitude FloatFields are absent from the mounted write
serializers. No writable DRF FloatField was found, so non-finite float acceptance
is not an application API contract here. JSONField data is not a DRF FloatField.
There is no custom global exception handler or list serializer class.

`hub.tests.test_dependency_api_contracts` verifies rendered HTTP bodies through
mounted endpoints: registration, JWT login/refresh, a default authenticated JSON
GET and successful PATCH, ordinary UTF-8 JSON/Accept parameters, nested field
errors, duplicate email, pagination, partial multipart updates, session CSRF,
and valid/invalid generated group-relation lists without partial updates.
Empty and mixed valid/invalid arrays at the top level and in the nested profile
are rejected as single-object errors without partial creation. These paths never
produce per-item list errors, so no legacy-format setting or removal deadline is
needed. Existing suites cover prayer requests, join/leave, password reset, image
uploads and public API permissions. Mobile clients are outside this checkout;
device/staging checks remain a maintainer integration gate.

## Review and rollout boundary

Local tests use isolated test databases and dummy/offline external integrations.
Migration inspection is read-only; any pre-existing schema findings stay separate
under AGENTS.md. Collect static assets into a temporary local directory and verify
admin and DRF manifest entries. Import the WSGI and Celery applications without
starting workers or consuming queues.

This PR does not inspect production package versions or retained build artifacts,
run production/staging traffic, deploy, merge, or change live settings. Maintainers
must retain the previous verified artifact and resolved environment before an
approved rollout, perform staged mobile/admin/worker checks, and monitor results.
Rollback is reverting the dependency change and rebuilding the previous artifact;
it must not reverse migrations or modify user data. Reverting the security pins
restores the corresponding dependency exposure.

## Local verification notes

- Updated runtime: Python 3.11.16, Django 5.2.19, DRF 3.18.3; `pip check` passes.
  Installer artifact SHA-256 values match the non-yanked PyPI release files.
  Normalized before/after freezes differ only in Django and DRF. A second
  requirements install with `--dry-run` requests no changes. A clean venv install,
  constrained to the captured resolved versions, also passes `pip check`; its
  application versions match (the snapshot's extra `tblib` test helper is absent).
- All ten added HTTP contract tests pass on Django 5.2.17/DRF 3.17.2, establishing
  the old behavior before comparison with the upgraded pair.
- The PostgreSQL 17 CI test selection passes locally: 118 tests, no skips.
- The required full Django suite passes on the controlled upgraded runtime:
  2,718 tests, 49 skips, no failures, with four parallel workers and
  `REQUIRE_REDIS_TESTS=1`. The subsequently added startup subprocess regression
  is verified separately and will also run in exact-head CI.
- The focused API run on the upgraded pair passes: 166 tests covering the ten
  new contracts, registration, refresh, reset confirmation, image upload,
  join/leave, prayer requests and public resources. `PYTHONWARNINGS=default`
  exposes existing Django 6.0, pyparsing and Sentry deprecations; no DRF
  list-error compatibility warning occurs.
- Read-only `makemigrations --check --dry-run` detects no changes when the
  test-only `Video.video` offline storage is restored to its declared `S3Storage`
  **within the inspection process**. Leaving the offline class in place cannot
  serialize it; replacing it with generic `default_storage` instead produces an
  artificial storage diff. No migration was written or applied by inspection.
- Static collection with the production WhiteNoise static backend in a temporary
  local directory produces 184 manifest entries. Admin CSS, DRF CSS and DRF
  `ajax-form.js` resolve to existing hashed files with `DEBUG=False`.
- WSGI imports and Celery default-task imports pass (71 registered tasks).
  No worker or scheduler was started.
- The four settings/startup tests pass with both the controlled Sentry 2.71.0
  runtime and the fresh Sentry 2.76.0 runtime. The new startup test fails against
  Sentry 3.0.0 with the observed configuration error, and passes after the bound.
- The first full run had one failure in
  `events.tests.test_engagement_report_command.EngagementReportCommandTestCase.test_s3_upload_missing_settings`:
  the cloud snapshot supplies AWS settings, defeating that test's assumption and
  reaching a failed network proxy. The same failure reproduces on the old
  framework pair; it is unrelated to these pins. For offline validation, use
  `AWS_ACCESS_KEY_ID='' AWS_SECRET_ACCESS_KEY='' AWS_STORAGE_BUCKET_NAME=''`.
  The report-command suite and all ten new contracts pass together on the old
  pair with that environment (28 tests). No application or test setting was
  changed to mask this environment issue.
