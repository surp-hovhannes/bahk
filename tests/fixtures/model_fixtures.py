"""Fixtures of models."""
import pytest

from django.contrib.auth.models import User

from hub.models import Church, Fast, Profile


@pytest.fixture
def sample_user_fixture():
    return User.objects.create(username="sample_user")


@pytest.fixture
def another_user_fixture():
    return User.objects.create(username="another_user")


@pytest.fixture
def sample_church_fixture():
    return Church.objects.create(name="Sample Church")


@pytest.fixture
def sample_fast_fixture(sample_church_fixture):
    return Fast.objects.create(name="Sample Fast", church=sample_church_fixture)


@pytest.fixture
def another_fast_fixture(sample_church_fixture):
    return Fast.objects.create(name="Another Fast", church=sample_church_fixture)


@pytest.fixture
def sample_profile_fixture(sample_user_fixture):
    return Profile.objects.create(user=sample_user_fixture)


@pytest.fixture
def another_profile_fixture(another_user_fixture):
    return Profile.objects.create(user=another_user_fixture)
