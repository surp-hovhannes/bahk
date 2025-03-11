import os
import shutil
import pytest
from django.conf import settings

# This tells pytest to use django-specific fixtures
pytest_plugins = [
    "pytest_django.fixtures",
    "tests.fixtures.model_fixtures",
]

# Mark all tests to use the Django database
@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(db):
    """Give all tests access to the database."""
    pass

@pytest.fixture(scope='session', autouse=True)
def setup_and_teardown():
    # No media directory setup/cleanup needed since app doesn't use MEDIA_ROOT
    yield