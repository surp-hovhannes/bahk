# AGENTS.md — Bahk Repo

## Clawpatch Notes (Repo-Specific)

### Migration / Schema Findings Are Hold-Lane

Any clawpatch finding that touches migrations, schema changes, or data-loss paths requires explicit Der Hayr approval before fixing. Do not include these in routine sweeps. They need: rollback planning, data-preservation review, and a separate PR.

Currently held: Bookmark `object_id` BigAutoField, nullable fast year uniqueness, reading context migration data drop, M2M-to-FK migration data drop.

### Parallel Test + Cache Contamination

`@cache_page` decorators poison parallel test workers. `cache.clear()` doesn't work — the middleware caches at a different level. Fix with:

```python
@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}})
class MyTestCase(TestCase):
    ...
```

### Baseline Validation Is Broken

`ruff format --check` wants to reformat 220 files. `pytest` is missing. `npm test` placeholder fails. When Crabbox is installed, use `scripts/crabbox-validate.sh ci` instead; otherwise use the local Docker fallback below. Use `--force` with `clawpatch open-pr` when needed.

### Crabbox

Config lives in `.crabbox.slug.conf` — slug `bahk-fast`, 45m idle, 4h TTL. When the `crabbox` executable is installed, warm with `scripts/crabbox-box.sh warm` and validate with `scripts/crabbox-validate.sh ci`; never commit `.crabbox/` runtime state.

When Crabbox is unavailable, run Django/Python validation in the local Docker app container. Discover its current name with `docker ps --format '{{.Names}}'` rather than assuming a compose-project prefix, then use `docker exec -e IS_PRODUCTION=false <app-container> python manage.py <command>`.

---

# Project Command Note

Use Crabbox as the preferred validation environment when its executable is installed. Otherwise run Django/Python commands through the local Docker app container.

- Discover the current local app-container name with:

```bash
docker ps --format '{{.Names}}'
```

- Docker command pattern:

```bash
docker exec -e IS_PRODUCTION=false <app-container> python manage.py <command>
```

Examples:

```bash
docker exec -e IS_PRODUCTION=false <app-container> python manage.py migrate
docker exec -e IS_PRODUCTION=false <app-container> python manage.py test --settings=tests.test_settings
docker exec -e IS_PRODUCTION=false <app-container> python manage.py createsuperuser
```

When running from inside crabbox, the app container, or another environment that is already inside the project runtime, do not use `docker exec`. Run Django/Python commands directly:

```bash
python manage.py <command>
```

Default test command inside that runtime:

```bash
python manage.py test --noinput --parallel --exclude-tag=performance --exclude-tag=slow --settings=tests.test_settings
```

Only include `performance` or `slow` tests when the change explicitly touches those tests or performance behavior.
