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

`ruff format --check` wants to reformat 220 files. `pytest` is missing. `npm test` placeholder fails. Use `scripts/crabbox-validate.sh ci` instead, and `--force` with `clawpatch open-pr` when needed.

### Crabbox

Config lives in `.crabbox.slug.conf` — slug `bahk-fast`, 45m idle, 4h TTL. Warm with `scripts/crabbox-box.sh warm`. Validate with `scripts/crabbox-validate.sh ci`. Never commit `.crabbox/` runtime state.

---

# Project Command Note

This project runs commands through Docker containers.

- Use the app container for Django/Python commands from the local host: `devcontainer-app-1`
- Preferred command pattern:

```bash
docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py <command>
```

Examples:

```bash
docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py migrate
docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py test --settings=tests.test_settings
docker exec -e IS_PRODUCTION=false devcontainer-app-1 python manage.py createsuperuser
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
