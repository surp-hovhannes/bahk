

"""Fixtures of models."""
import datetime
import pytest

from django.contrib.auth.models import User

from hub.models import User, Church, Fast, Profile, Day


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
    return Fast.objects.create(name="Sample Fast", church=sample_church_fixture, description="A sample fast.")


@pytest.fixture
def another_fast_fixture(sample_church_fixture):
    return Fast.objects.create(name="Another Fast", church=sample_church_fixture, description="Another sample fast.")


@pytest.fixture
def sample_profile_fixture(sample_user_fixture):
    return Profile.objects.create(user=sample_user_fixture)


@pytest.fixture
def another_profile_fixture(another_user_fixture):
    return Profile.objects.create(user=another_user_fixture)


@pytest.fixture
def sample_day_fixture():
    date = datetime.date.today() + datetime.timedelta(days=1)
    return Day.objects.create(date=date)


@pytest.fixture
def today_fixture():
    return Day.objects.create(date=datetime.date.today())

# complete models

@pytest.fixture
def complete_fast_fixture(sample_profile_fixture, sample_church_fixture, sample_day_fixture, today_fixture):
    fast = Fast.objects.create(name="Complete Fast", church=sample_church_fixture, description="complete fast", culmination_feast="Culmination Feast", culmination_feast_date=datetime.date.today() + datetime.timedelta(days=2))
    fast.profiles.set([sample_profile_fixture])
    fast.days.set([sample_day_fixture, today_fixture])

    return fast

# incomplete models

@pytest.fixture
def incomplete_fast_fixture(sample_profile_fixture, sample_church_fixture, sample_day_fixture, today_fixture):
    fast = Fast.objects.create(name="incomplete Fast", church=sample_church_fixture, description="fast without culmination feast")
    fast.profiles.set([sample_profile_fixture])
    fast.days.set([sample_day_fixture, today_fixture])

    return fast
