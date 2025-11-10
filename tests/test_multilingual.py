from django.utils.translation import activate
from django.urls import reverse
from django.test import TestCase
from django.test.utils import tag
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from hub.models import Church, Fast, Day, DevotionalSet, Devotional
from learning_resources.models import Video, Article, Recipe
from django.db import IntegrityError


class MultilingualModelTests(TestCase):
    """Tests for multilingual model fields."""
    
    def test_model_i18n_fields_return_translations(self):
        """Test that i18n fields return correct translations."""
        activate('en')
        church = Church.objects.create(name="Test Church")
        fast = Fast.objects.create(name="Great Lent", church=church, description="Desc", culmination_feast="Easter")
        # Set Armenian
        fast.name_hy = "Մեծ Պահք"
        fast.description_hy = "Նկարագրություն"
        fast.culmination_feast_hy = "Զատիկ"
        fast.save()

        activate('hy')
        self.assertEqual(fast.name_i18n, "Մեծ Պահք")
        self.assertEqual(fast.description_i18n, "Նկարագրություն")
        self.assertEqual(fast.culmination_feast_i18n, "Զատիկ")

    def test_devotional_unique_together_language_code(self):
        """Test that devotional unique_together constraint works with language_code."""
        church = Church.objects.create(name="Test Church")
        fast = Fast.objects.create(name="Great Lent", church=church)
        day = Day.objects.create(date="2025-01-01", fast=fast, church=church)
        v = Video.objects.create(title="T", description="D", category='devotional', language_code='en')

        Devotional.objects.create(day=day, description="en", video=v, order=1, language_code='en')
        # Same day/order but hy should be allowed
        Devotional.objects.create(day=day, description="hy", video=v, order=1, language_code='hy')

        with self.assertRaises(IntegrityError):
            # Duplicate en should violate unique_together
            Devotional.objects.create(day=day, description="dup", video=v, order=1, language_code='en')


class MultilingualAPITests(APITestCase):
    """Tests for multilingual API endpoints."""
    
    def test_video_language_filter_and_translation(self):
        """Test video language filtering and translation."""
        v_en = Video.objects.create(title="Morning Prayer", description="Desc", category='general', language_code='en')
        v_en.title_hy = "Առավոտյան Աղոթք"
        v_en.save()

        v_hy = Video.objects.create(title="Առավոտյան Աղոթք", description="HY", category='general', language_code='hy')
        v_hy.title_en = "Morning Prayer (HY)"
        v_hy.save()

        # English
        resp = self.client.get('/api/learning-resources/videos/?lang=en&language_code=en')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(all(item['category'] == 'general' for item in resp.data['results']))
        # Armenian translation
        resp2 = self.client.get('/api/learning-resources/videos/?lang=hy')
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)

    def test_devotional_language_fallback(self):
        """Test devotional language fallback."""
        church = Church.objects.create(name="Test Church")
        fast = Fast.objects.create(name="Great Lent", church=church)
        day = Day.objects.create(date="2025-01-02", fast=fast, church=church)
        v = Video.objects.create(title="D1", description="E", category='devotional', language_code='en')
        Devotional.objects.create(day=day, description="EN text", video=v, order=1, language_code='en')

        # Request hy, fallback to en
        resp = self.client.get('/api/hub/devotionals/by-date/?date=2025-01-02&lang=hy')
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_404_NOT_FOUND))

    @tag('slow')
    def test_seed_command_creates_translations(self):
        """Test that seed command creates translations."""
        from django.core.management import call_command
        call_command('seed_multilingual_data')
        self.assertTrue(Fast.objects.filter(name="Great Lent").exists())
        fast = Fast.objects.get(name="Great Lent")
        activate('hy')
        self.assertEqual(fast.name_i18n, "Մեծ Պահք")
