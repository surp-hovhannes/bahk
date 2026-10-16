"""Isolated PostgreSQL settings for membership concurrency/migration tests.

Only use this module against a disposable test database, never production.
"""

import os

from tests.test_settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("TEST_POSTGRES_DB", "bahk_tests"),
        "USER": os.environ.get("TEST_POSTGRES_USER", "bahk_tests"),
        "PASSWORD": os.environ.get("TEST_POSTGRES_PASSWORD", "bahk_tests"),
        "HOST": os.environ.get("TEST_POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("TEST_POSTGRES_PORT", "5432"),
    },
}
