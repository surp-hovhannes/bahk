# Issue 603 validation

The list queryset annotates `Count('prayers')`; serialization uses that value,
including zero, with the previous related-manager count as a fallback for
unannotated callers. Existing access, filters, ordering and detail serialization
are unchanged. No prayer objects or bodies are prefetched. Membership uniqueness
makes each linked prayer count once; shared and cross-church prayers still count.

Focused regressions cover exact counts, empty sets, anonymous/authenticated
visibility, search/church/category and combined filters, invalid/empty filters,
pagination, constant query count for one versus four sets, absence of prayer
body/translation/title selections, no prayer model hydration, and serializer fallback.

## Completed checks

- `pwd`: expected isolated worktree.
- `git status --short --branch`: clean prepared branch `fix/issue-603-prayer-count` before edits.
- `git rev-parse HEAD`: `a1a5d84dbdf40db8e4ba954503d4d4fb4f3bfa2b` before edits.
- `git remote -v`: canonical origin `https://github.com/surp-hovhannes/bahk.git`.
- `git diff --check`: passed (also repeated after final edits).
- Syntax check (passed, repeated after test edits):

```sh
python3 -c "import ast; from pathlib import Path; [ast.parse(Path(p).read_text()) for p in ['prayers/views.py', 'prayers/serializers.py', 'prayers/tests/test_prayer_set_list_counts.py']]; print('Python syntax: PASS')"
```

## Runtime blockers and skipped checks

- `command -v crabbox`: installed at `/opt/homebrew/bin/crabbox`.
- `docker ps --format '{{.Names}}'`: failed; local Docker daemon socket absent.
- `command -v ruff` / `which -a ruff python python3`: ruff and python absent; python3 available.
- `scripts/crabbox-box.sh warm`: sandbox attempt failed accessing Crabbox SSH
  configuration; escalated retry succeeded with lease `cbx_32d2a2aaf9a5`.
- `crabbox run --id cbx_32d2a2aaf9a5 --stop never -- docker ps --format '{{.Names}}'`:
  automatic approval review rejected syncing the private checkout to external
  Crabbox/Hetzner. No workaround attempted. Explicit approval for that transfer
  is needed to use remote validation.

The following checks were not executed because no approved runtime is available:

```sh
ruff check .
python manage.py test prayers.tests.test_prayer_set_list_counts prayers.tests.test_models_api.PrayerSetAPITests --noinput --settings=tests.test_settings
```

Run the Django command inside the repo Docker/Crabbox app runtime. Full suites,
format checks, and dependency installs were intentionally skipped per task scope;
CI owns the full suite. Tests remain runtime-unverified.
