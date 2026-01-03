"""Tests for FeastPrayer model and functionality."""
from datetime import date

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.utils.translation import activate
from rest_framework.test import APIClient, APIRequestFactory

from hub.models import Church, Day, Feast
from prayers.models import FeastPrayer
from prayers.serializers import FeastPrayerSerializer

User = get_user_model()


class FeastPrayerModelTests(TestCase):
    """Test FeastPrayer model functionality."""

    def setUp(self):
        """Set up test data."""
        self.church = Church.objects.create(name='Test Church')
        self.day = Day.objects.create(
            church=self.church,
            date=date(2026, 1, 1)
        )
        self.feast = Feast.objects.create(
            day=self.day,
            name='The Nativity of Our Lord Jesus Christ',
            designation='Nativity of Christ, Feasts of the Mother of God, Presentation of the Lord'
        )
        self.feast.name_hy = 'Ծնունդ Տեառն Մերոյ Յիսուսի Քրիստոսի'
        self.feast.save(update_fields=['i18n'])

        self.feast_prayer = FeastPrayer.objects.create(
            designation='Nativity of Christ, Feasts of the Mother of God, Presentation of the Lord',
            title='Prayer for {feast_name}',
            text='O Lord, on this day of {feast_name}, we gather...'
        )
        self.feast_prayer.title_hy = 'Աղոթք {feast_name}'
        self.feast_prayer.text_hy = 'Տեր, այս օրը {feast_name}, մենք հավաքվում ենք...'
        self.feast_prayer.save(update_fields=['i18n'])

    def test_feast_prayer_creation(self):
        """Test creating a feast prayer."""
        self.assertEqual(FeastPrayer.objects.count(), 1)
        self.assertEqual(
            self.feast_prayer.designation,
            'Nativity of Christ, Feasts of the Mother of God, Presentation of the Lord'
        )
        self.assertEqual(self.feast_prayer.title, 'Prayer for {feast_name}')
        self.assertIn('{feast_name}', self.feast_prayer.text)

    def test_unique_designation_constraint(self):
        """Test that only one prayer per designation is allowed."""
        with self.assertRaises(IntegrityError):
            FeastPrayer.objects.create(
                designation='Nativity of Christ, Feasts of the Mother of God, Presentation of the Lord',
                title='Another prayer',
                text='This should fail'
            )

    def test_render_for_feast_english(self):
        """Test rendering prayer with feast name in English."""
        activate('en')
        rendered = self.feast_prayer.render_for_feast(self.feast, lang='en')

        self.assertEqual(
            rendered['title'],
            'Prayer for The Nativity of Our Lord Jesus Christ'
        )
        self.assertIn('The Nativity of Our Lord Jesus Christ', rendered['text'])
        self.assertNotIn('{feast_name}', rendered['text'])

    def test_render_for_feast_armenian(self):
        """Test rendering prayer with feast name in Armenian."""
        activate('hy')
        rendered = self.feast_prayer.render_for_feast(self.feast, lang='hy')

        self.assertEqual(
            rendered['title'],
            'Աղոթք Ծնունդ Տեառն Մերոյ Յիսուսի Քրիստոսի'
        )
        self.assertIn('Ծնունդ Տեառն Մերոյ Յիսուսի Քրիստոսի', rendered['text'])
        self.assertNotIn('{feast_name}', rendered['text'])

    def test_render_without_placeholder(self):
        """Test rendering prayer without placeholder."""
        prayer = FeastPrayer.objects.create(
            designation='Martyrs',
            title='Prayer for Martyrs',
            text='O Lord, we honor the martyrs who gave their lives.'
        )

        rendered = prayer.render_for_feast(self.feast, lang='en')
        self.assertEqual(rendered['title'], 'Prayer for Martyrs')
        self.assertEqual(rendered['text'], 'O Lord, we honor the martyrs who gave their lives.')

    def test_string_representation(self):
        """Test __str__ method."""
        expected = 'Prayer for Nativity of Christ, Feasts of the Mother of God, Presentation of the Lord'
        self.assertEqual(str(self.feast_prayer), expected)


class FeastPrayerSerializerTests(TestCase):
    """Test FeastPrayer serializer."""

    def setUp(self):
        """Set up test data."""
        self.factory = APIRequestFactory()
        self.church = Church.objects.create(name='Test Church')
        self.day = Day.objects.create(
            church=self.church,
            date=date(2026, 1, 1)
        )
        self.feast = Feast.objects.create(
            day=self.day,
            name='St. Stephen the Protomartyr',
            designation='Martyrs'
        )

        self.feast_prayer = FeastPrayer.objects.create(
            designation='Martyrs',
            title='Prayer for {feast_name}',
            text='We honor {feast_name} who witnessed to the faith.'
        )

    def test_serializer_with_feast_context(self):
        """Test serializer returns rendered prayer when feast in context."""
        request = self.factory.get('/', {'lang': 'en'})
        serializer = FeastPrayerSerializer(
            self.feast_prayer,
            context={'request': request, 'feast': self.feast, 'lang': 'en'}
        )

        data = serializer.data
        self.assertEqual(data['title_template'], 'Prayer for {feast_name}')
        self.assertEqual(data['title_rendered'], 'Prayer for St. Stephen the Protomartyr')
        self.assertIn('St. Stephen the Protomartyr', data['text_rendered'])
        self.assertNotIn('{feast_name}', data['text_rendered'])

    def test_serializer_without_feast_context(self):
        """Test serializer returns template when no feast in context."""
        request = self.factory.get('/', {'lang': 'en'})
        serializer = FeastPrayerSerializer(
            self.feast_prayer,
            context={'request': request, 'lang': 'en'}
        )

        data = serializer.data
        self.assertEqual(data['title_template'], 'Prayer for {feast_name}')
        self.assertEqual(data['title_rendered'], '')
        self.assertEqual(data['text_rendered'], '')

    def test_serializer_fields(self):
        """Test serializer includes all expected fields."""
        request = self.factory.get('/', {'lang': 'en'})
        serializer = FeastPrayerSerializer(
            self.feast_prayer,
            context={'request': request, 'lang': 'en'}
        )

        data = serializer.data
        expected_fields = [
            'id', 'designation', 'title_template', 'text_template',
            'title_rendered', 'text_rendered', 'created_at', 'updated_at'
        ]
        for field in expected_fields:
            self.assertIn(field, data)


class FeastPrayerAPITests(TestCase):
    """Test FeastPrayer API integration."""

    def setUp(self):
        """Set up test data."""
        self.client = APIClient()
        self.church = Church.objects.create(name='Test Church')
        # Set as default church
        Church._default_pk = self.church.pk

        self.day = Day.objects.create(
            church=self.church,
            date=date(2026, 1, 6)
        )
        self.feast = Feast.objects.create(
            day=self.day,
            name='Theophany',
            designation='Sundays, Dominical Feast Days'
        )

        self.feast_prayer = FeastPrayer.objects.create(
            designation='Sundays, Dominical Feast Days',
            title='Prayer for {feast_name}',
            text='On this holy day of {feast_name}, we rejoice.'
        )

    def test_feast_endpoint_includes_prayer_field(self):
        """Test that feast endpoint includes prayer field in response."""
        response = self.client.get('/api/feasts/', {'date': '2026-01-06', 'lang': 'en'})

        self.assertEqual(response.status_code, 200)
        self.assertIn('feast', response.data)

        # If there's a feast, verify it has a prayer field
        if response.data['feast']:
            self.assertIn('prayer', response.data['feast'])
            # Prayer can be None if there's no matching prayer for the designation
            # This is expected behavior

    def test_feast_endpoint_without_prayer(self):
        """Test that feast endpoint handles missing prayer gracefully."""
        # Create a feast with different designation that has no prayer
        day2 = Day.objects.create(
            church=self.church,
            date=date(2026, 1, 7)
        )
        Feast.objects.create(
            day=day2,
            name='St. John the Baptist',
            designation='Martyrs'
        )

        response = self.client.get('/api/feasts/', {'date': '2026-01-07', 'lang': 'en'})

        self.assertEqual(response.status_code, 200)
        self.assertIn('feast', response.data)
        self.assertIsNotNone(response.data['feast'])
        self.assertIn('prayer', response.data['feast'])

        # Prayer should be None for feasts without a prayer
        prayer = response.data['feast']['prayer']
        self.assertIsNone(prayer)

    def test_feast_endpoint_armenian_translation(self):
        """Test feast endpoint with Armenian language accepts lang parameter."""
        response = self.client.get('/api/feasts/', {'date': '2026-01-06', 'lang': 'hy'})

        self.assertEqual(response.status_code, 200)
        self.assertIn('feast', response.data)

        # Verify the endpoint accepts Armenian language parameter
        # The actual translation rendering is tested in model tests

    def tearDown(self):
        """Clean up after tests."""
        # Reset default church
        Church._default_pk = None
