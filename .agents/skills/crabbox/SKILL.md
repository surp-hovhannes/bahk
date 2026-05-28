---
name: crabbox
description: Use Crabbox for remote validation in this repo, including warmed reusable boxes for lint, typecheck, tests, coverage, CI, and e2e checks.
---

# Crabbox for Bahk

Use Crabbox when this Bahk checkout needs remote Linux verification on Hetzner.

Run from `/Users/oshii/clawd/fast-and-pray-pm-agent/code/bahk`:

- Start or reuse a warmed box: `scripts/crabbox-box.sh warm`
- Full CI-style check: `scripts/crabbox-validate.sh ci`
- Lint only: `scripts/crabbox-validate.sh lint`
- Typecheck/system check: `scripts/crabbox-validate.sh typecheck`
- Django tests only: `scripts/crabbox-validate.sh test`
- Stop the warmed box: `scripts/crabbox-box.sh stop`

This repo uses `.devcontainer/docker-compose.yml`. Do not hand-roll a separate
Python environment on the Crabbox host. The Crabbox jobs build and run the
devcontainer `app` service plus Redis, then execute commands inside the
container.

Validation should reuse the active lease with `--stop never`:

```bash
scripts/crabbox-box.sh warm
scripts/crabbox-validate.sh ci
```

Raw `crabbox job run <job>` remains disposable and follows the stop policy in
`.crabbox.yaml`.

The test job mirrors `.github/workflows/django_ci.yml`:

`python manage.py test --noinput --parallel --exclude-tag=performance --exclude-tag=slow --settings=tests.test_settings`

This checkout is configured for direct Hetzner mode. It should not use the
closed OpenClaw broker.
