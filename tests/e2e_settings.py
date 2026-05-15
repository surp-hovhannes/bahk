"""E2E test settings - like test_settings but with a persistent SQLite DB."""
from tests.test_settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': '/tmp/bahk_e2e.sqlite3',
    }
}
