# Project Command Note

This project runs commands through Docker containers.

- Use the app container for Django/Python commands: `bahk_devcontainer-app-1`
- Preferred command pattern:

```bash
docker exec -e IS_PRODUCTION=false bahk_devcontainer-app-1 python manage.py <command>
```

Examples:

```bash
docker exec -e IS_PRODUCTION=false bahk_devcontainer-app-1 python manage.py migrate
docker exec -e IS_PRODUCTION=false bahk_devcontainer-app-1 python manage.py test --settings=tests.test_settings
docker exec -e IS_PRODUCTION=false bahk_devcontainer-app-1 python manage.py createsuperuser
```
