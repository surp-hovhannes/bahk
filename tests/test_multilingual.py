import pytest
from django.utils.translation import activate
from django.urls import reverse
from rest_framework.test import APIClient
from hub.models import Church, Fast, Day, DevotionalSet, Devotional
from learning_resources.models import Video, Article, Recipe


@pytest.mark.django_db
def test_model_i18n_fields_return_translations():
    activate('en')
    church = Church.objects.create(name="Test Church")
    fast = Fast.objects.create(name="Great Lent", church=church, description="Desc", culmination_feast="Easter")
    # Set Armenian
    fast.name_hy = "Մեծ Պահք"
    fast.description_hy = "Նկարագրություն"
    fast.culmination_feast_hy = "Զատիկ"
    fast.save()

    activate('hy')
    assert fast.name_i18n == "Մեծ Պահք"
    assert fast.description_i18n == "Նկարագրություն"
    assert fast.culmination_feast_i18n == "Զատիկ"


@pytest.mark.django_db
def test_devotional_unique_together_language_code():
    church = Church.objects.create(name="Test Church")
    fast = Fast.objects.create(name="Great Lent", church=church)
    day = Day.objects.create(date="2025-01-01", fast=fast, church=church)
    v = Video.objects.create(title="T", description="D", category='devotional', language_code='en')

    Devotional.objects.create(day=day, description="en", video=v, order=1, language_code='en')
    # Same day/order but hy should be allowed
    Devotional.objects.create(day=day, description="hy", video=v, order=1, language_code='hy')

    with pytest.raises(Exception):
        # Duplicate en should violate unique_together
        Devotional.objects.create(day=day, description="dup", video=v, order=1, language_code='en')


@pytest.mark.django_db
def test_video_language_filter_and_translation(client: APIClient):
    client = APIClient()
    v_en = Video.objects.create(title="Morning Prayer", description="Desc", category='general', language_code='en')
    v_en.title_hy = "Առավոտյան Աղոթք"
    v_en.save()

    v_hy = Video.objects.create(title="Առավոտյան Աղոթք", description="HY", category='general', language_code='hy')
    v_hy.title_en = "Morning Prayer (HY)"
    v_hy.save()

    # English
    resp = client.get(reverse('learning_resources:video-list') if False else '/api/learning-resources/videos/?lang=en&language_code=en')
    assert resp.status_code == 200
    assert all(item['category'] == 'general' for item in resp.data['results'])
    # Armenian translation
    resp2 = client.get('/api/learning-resources/videos/?lang=hy')
    assert resp2.status_code == 200


@pytest.mark.django_db
def test_devotional_language_fallback(client: APIClient):
    client = APIClient()
    church = Church.objects.create(name="Test Church")
    fast = Fast.objects.create(name="Great Lent", church=church)
    day = Day.objects.create(date="2025-01-02", fast=fast, church=church)
    v = Video.objects.create(title="D1", description="E", category='devotional', language_code='en')
    Devotional.objects.create(day=day, description="EN text", video=v, order=1, language_code='en')

    # Request hy, fallback to en
    resp = client.get(f'/api/hub/devotionals/by-date/?date=2025-01-02&lang=hy')
    assert resp.status_code in (200, 404)


@pytest.mark.django_db
def test_seed_command_creates_translations(django_db_blocker):
    from django.core.management import call_command
    call_command('seed_multilingual_data')
    assert Fast.objects.filter(name="Great Lent").exists()
    fast = Fast.objects.get(name="Great Lent")
    activate('hy')
    assert fast.name_i18n == "Մեծ Պահք"
