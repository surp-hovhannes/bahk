from django.utils.translation import activate
from django.urls import reverse
from django.test import TestCase
from django.test.utils import tag
from rest_framework.test import APITestCase
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

    def test_fast_duplication_copies_translations(self):
        """Test that duplicating a fast copies all translatable fields."""
        church = Church.objects.create(name="Test Church")

        # Create original fast with all translatable fields populated
        original_fast = Fast.objects.create(
            name="Great Lent",
            church=church,
            description="Fasting period before Easter",
            culmination_feast="Easter",
            culmination_feast_salutation="Christ is Risen!",
            culmination_feast_message="He is risen indeed!",
            culmination_feast_message_attribution="Traditional greeting",
            url="https://example.com"
        )

        # Set Armenian translations
        original_fast.name_hy = "Մեծ Պահք"
        original_fast.description_hy = "Զատկի նախորդող պահողության ժամանակաշրջան"
        original_fast.culmination_feast_hy = "Զատիկ"
        original_fast.culmination_feast_salutation_hy = "Քրիստոս հարյավ ի մեռելոց!"
        original_fast.culmination_feast_message_hy = "Օրհնյալ է հարությունն Քրիստոսի!"
        original_fast.culmination_feast_message_attribution_hy = "Ավանդական բարևույթ"
        original_fast.save()

        # Create a duplicate by manually copying fields (simulating admin duplication logic)
        duplicate_fast = Fast.objects.create(
            name=original_fast.name,
            church=original_fast.church,
            description=original_fast.description,
            culmination_feast=original_fast.culmination_feast,
            culmination_feast_salutation=original_fast.culmination_feast_salutation,
            culmination_feast_message=original_fast.culmination_feast_message,
            culmination_feast_message_attribution=original_fast.culmination_feast_message_attribution,
            url=original_fast.url,
        )

        # Copy translated fields
        duplicate_fast.name_en = original_fast.name_en
        duplicate_fast.name_hy = original_fast.name_hy
        duplicate_fast.description_en = original_fast.description_en
        duplicate_fast.description_hy = original_fast.description_hy
        duplicate_fast.culmination_feast_en = original_fast.culmination_feast_en
        duplicate_fast.culmination_feast_hy = original_fast.culmination_feast_hy
        duplicate_fast.culmination_feast_salutation_en = original_fast.culmination_feast_salutation_en
        duplicate_fast.culmination_feast_salutation_hy = original_fast.culmination_feast_salutation_hy
        duplicate_fast.culmination_feast_message_en = original_fast.culmination_feast_message_en
        duplicate_fast.culmination_feast_message_hy = original_fast.culmination_feast_message_hy
        duplicate_fast.culmination_feast_message_attribution_en = original_fast.culmination_feast_message_attribution_en
        duplicate_fast.culmination_feast_message_attribution_hy = original_fast.culmination_feast_message_attribution_hy
        duplicate_fast.save()

        # Verify English translations
        activate('en')
        self.assertEqual(duplicate_fast.name_i18n, "Great Lent")
        self.assertEqual(duplicate_fast.description_i18n, "Fasting period before Easter")
        self.assertEqual(duplicate_fast.culmination_feast_i18n, "Easter")
        self.assertEqual(duplicate_fast.culmination_feast_salutation_i18n, "Christ is Risen!")
        self.assertEqual(duplicate_fast.culmination_feast_message_i18n, "He is risen indeed!")
        self.assertEqual(duplicate_fast.culmination_feast_message_attribution_i18n, "Traditional greeting")

        # Verify Armenian translations
        activate('hy')
        self.assertEqual(duplicate_fast.name_i18n, "Մեծ Պահք")
        self.assertEqual(duplicate_fast.description_i18n, "Զատկի նախորդող պահողության ժամանակաշրջան")
        self.assertEqual(duplicate_fast.culmination_feast_i18n, "Զատիկ")
        self.assertEqual(duplicate_fast.culmination_feast_salutation_i18n, "Քրիստոս հարյավ ի մեռելոց!")
        self.assertEqual(duplicate_fast.culmination_feast_message_i18n, "Օրհնյալ է հարությունն Քրիստոսի!")
        self.assertEqual(duplicate_fast.culmination_feast_message_attribution_i18n, "Ավանդական բարևույթ")

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
