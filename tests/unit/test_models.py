"""Tests models."""
import datetime

from django.db.utils import IntegrityError
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile


from hub.models import Church, Day, Fast, Profile
import os
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile




### Create minimal models ###

@pytest.mark.django_db
def test_create_church():
    """Tests creation of a Church model object."""
    name = "Armenian Apostolic Church"
    church = Church.objects.create(name=name)
    assert church
    assert church.name == name


@pytest.mark.django_db
def test_create_fast(sample_church_fixture):
    """Tests creation of a Fast model object."""
    name = "Fast of the Catechumens"
    fast = Fast.objects.create(name=name, church=sample_church_fixture)
    assert fast
    assert fast.name == name


@pytest.mark.django_db
def test_fast_image_upload():
    """Tests image upload for a Fast model object."""
    name = "Test Fast"
    church = Church.objects.create(name="Test Church")
    image_path = os.path.join(settings.BASE_DIR, 'hub', 'static', 'images', 'img.jpg')
    image = SimpleUploadedFile(name='img.jpg', content=open(image_path, 'rb').read(), content_type='image/jpeg')
    fast = Fast.objects.create(name=name, church=church, image=image)
    assert fast
    assert fast.name == name
    assert fast.image


@pytest.mark.django_db
def test_create_user_profile(sample_user_fixture):
    """Tests creation of a profile for a user."""
    profile = Profile.objects.create(user=sample_user_fixture)
    assert profile
    assert profile.user == sample_user_fixture
    assert sample_user_fixture.profile == profile

@pytest.mark.django_db
def test_profile_image_upload(sample_user_fixture):
    """Tests image upload for a Profile model object."""
    image_path = os.path.join(settings.BASE_DIR, 'staticfiles', 'images', 'img.jpg')
    image = SimpleUploadedFile(name='img.jpg', content=open(image_path, 'rb').read(), content_type='image/jpeg')
    profile = Profile.objects.create(user=sample_user_fixture, profile_image=image)
    
    assert profile.profile_image.name.startswith('profile_images/')
    assert profile.profile_image.name.endswith('img.jpg')

@pytest.mark.django_db
def test_create_day():
    """Tests creation of a Day model object for today."""
    date = datetime.date.today()
    day = Day.objects.create(date=date)
    assert day
    assert day.date == date


@pytest.mark.django_db
def test_create_duplicate_days():
    date = datetime.date.today()
    day1 = Day.objects.create(date=date)
    try:
        day2 = Day.objects.create(date=date)
        assert False
    except:
        # create a duplicate day (same date as existing) fails
        assert True


### Create complete models ###
    
@pytest.mark.django_db
def test_create_complete_user_profile(
    sample_user_fixture, 
    sample_church_fixture, 
    sample_fast_fixture,
    another_fast_fixture,
):
    """Tests creation of full user profile."""
    profile = Profile.objects.create(
        user=sample_user_fixture, 
        church=sample_church_fixture,
    )
    profile.fasts.set([sample_fast_fixture, another_fast_fixture])

    assert profile.user == sample_user_fixture
    assert sample_user_fixture.profile == profile
    assert profile.church == sample_church_fixture
    assert set(profile.fasts.all()) == {sample_fast_fixture, another_fast_fixture}
    assert set(sample_fast_fixture.profiles.all()) == {profile}
    assert set(another_fast_fixture.profiles.all()) == {profile}
    assert set(sample_church_fixture.profiles.all()) == {profile}


@pytest.mark.django_db
def test_create_complete_fast(
    sample_church_fixture, 
    sample_profile_fixture, 
    another_profile_fixture
):
    """Tests creation of a full fast with church and days."""
    name = "Completely Specified Fast"
    today = Day.objects.create(date=datetime.date.today())
    tomorrow = Day.objects.create(date=datetime.date.today() + datetime.timedelta(days=1))
    fast = Fast.objects.create(name=name, church=sample_church_fixture)
    fast.profiles.set([sample_profile_fixture, another_profile_fixture])
    fast.days.set([today, tomorrow])

    assert fast.name == name
    assert fast.church == sample_church_fixture
    assert set(fast.profiles.all()) == {sample_profile_fixture, another_profile_fixture}
    assert set(sample_profile_fixture.fasts.all()) == {fast}
    assert set(another_profile_fixture.fasts.all()) == {fast}
    assert set(fast.days.all()) == {today, tomorrow}
    assert set(today.fasts.all()) == {fast}
    assert set(tomorrow.fasts.all()) == {fast}


@pytest.mark.django_db
def test_create_complete_church(
    sample_profile_fixture,
    another_profile_fixture,
    sample_fast_fixture,
    another_fast_fixture
):
    """Tests creation of a completely specified church with fasts and user profiles."""
    name = "Completely Specified Church"
    church = Church.objects.create(name=name)
    church.profiles.set([sample_profile_fixture, another_profile_fixture])
    assert sample_profile_fixture.church == church
    assert another_profile_fixture.church == church

    sample_fast_fixture.church = church
    sample_fast_fixture.save(update_fields=["church"])
    another_fast_fixture.church = church
    another_fast_fixture.save(update_fields=["church"])
    assert set(church.fasts.all()) == {sample_fast_fixture, another_fast_fixture}


@pytest.mark.django_db
def test_constraint_unique_fast_name_church():
    """Tests that two fasts with the same name and church cannot be created."""
    fast_name = "fast"
    church = Church.objects.create(name="church")
    fast = Fast.objects.create(name=fast_name, church=church)
    with pytest.raises(IntegrityError):
        duplicate_fast = Fast.objects.create(name=fast_name, church=church, description="now there's a description")
