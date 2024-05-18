import os
import shutil
import pytest
from django.conf import settings

pytest_plugins = [
    "tests.fixtures.model_fixtures",
]

@pytest.fixture(scope='session', autouse=True)
def setup_and_teardown():
    # Ensure the test media directory is clean before tests
    if os.path.exists(settings.MEDIA_ROOT):
        shutil.rmtree(settings.MEDIA_ROOT)
    os.makedirs(settings.MEDIA_ROOT)

    yield

    # Clean up the test media directory after tests
    if os.path.exists(settings.MEDIA_ROOT):
        shutil.rmtree(settings.MEDIA_ROOT)