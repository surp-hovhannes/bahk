# Crabbox

Use Crabbox for local-container verification of the Bahk Django backend before spending cloud capacity.

This repo already has a devcontainer compose setup. The Crabbox local jobs use Docker socket pass-through to run that compose stack from inside the Crabbox lease.

Workflow:
- Warm early: crabbox warmup --provider local-container
- Reuse the returned slug for interactive checks and keep the cbx_ id in scripts/logs.
- Run checks with crabbox run --provider local-container --id <slug> -- <command>.
- Use crabbox status --id <slug> --wait before broad gates if needed.
- Use crabbox ssh --provider local-container --id <slug> to inspect the runner when a failure needs live context.
- Stop with crabbox stop --provider local-container <slug> when finished.

Preferred repo jobs:
- crabbox job run pytest
- crabbox job run ruff

Direct commands:
- crabbox run --provider local-container -- docker compose -f .devcontainer/docker-compose.yml ps
- crabbox run --provider local-container --shell 'docker compose -f .devcontainer/docker-compose.yml exec -T -e DJANGO_SETTINGS_MODULE=tests.test_settings -w /app app pytest'
- crabbox run --provider local-container --shell 'docker compose -f .devcontainer/docker-compose.yml exec -T -w /app app ruff check .'

Do not debug product failures on a reused box that fails sync sanity. Stop it, warm a fresh box, and rerun.
