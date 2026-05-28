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
