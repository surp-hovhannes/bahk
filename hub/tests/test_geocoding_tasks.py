from unittest.mock import patch

from django.test import TestCase

from hub.models import GeocodingCache
from hub.tasks.geocoding_tasks import batch_geocode_profiles
from tests.fixtures.test_data import TestDataFactory


class BatchGeocodeProfilesTests(TestCase):
    def test_failed_batch_geocode_does_not_write_zero_coordinates_to_profile(self):
        profile = TestDataFactory.create_profile(location="Atlantis")

        with patch(
            "hub.tasks.geocoding_tasks.geocoder.batch_geocode",
            return_value={"Atlantis": None},
        ):
            result = batch_geocode_profiles.apply(kwargs={"update_all": False}).get()

        profile.refresh_from_db()
        self.assertEqual(result["status"], "success")
        self.assertIsNone(profile.latitude)
        self.assertIsNone(profile.longitude)

        cache_entry = GeocodingCache.objects.get(location_text="atlantis")
        self.assertEqual(cache_entry.latitude, 0)
        self.assertEqual(cache_entry.longitude, 0)
        self.assertEqual(cache_entry.error_count, 1)

    def test_failed_cache_entry_is_not_reused_as_profile_coordinates(self):
        profile = TestDataFactory.create_profile(location="Atlantis")
        GeocodingCache.objects.create(
            location_text="atlantis",
            latitude=0,
            longitude=0,
            error_count=1,
        )

        with patch(
            "hub.tasks.geocoding_tasks.geocoder.batch_geocode",
            return_value={},
        ):
            result = batch_geocode_profiles.apply(kwargs={"update_all": False}).get()

        profile.refresh_from_db()
        self.assertEqual(result["status"], "success")
        self.assertIsNone(profile.latitude)
        self.assertIsNone(profile.longitude)
