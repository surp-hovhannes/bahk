"""Tests for the offline ``get_feast_for_date`` service.

Replaces the retired ``scrape_feast`` tests: feast names now come from the offline
``armenian_lectionary`` engine (``"Liturgical Day"``) rather than sacredtradition.am.
"""
from datetime import date, datetime
from unittest.mock import patch

from django.test import TestCase

from hub.models import Church
from hub.services.feast_service import get_feast_for_date


class GetFeastForDateTests(TestCase):
    """Tests for the ``get_feast_for_date`` service."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_returns_engine_liturgical_day(self, mock_compute):
        """The engine's ``Liturgical Day`` is returned as the English feast name."""
        mock_compute.return_value = {"Liturgical Day": "Nativity and Theophany of Our Lord Jesus Christ"}

        result = get_feast_for_date(self.test_date, self.church)

        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Nativity and Theophany of Our Lord Jesus Christ")
        self.assertEqual(result["name_en"], "Nativity and Theophany of Our Lord Jesus Christ")
        # The engine is English-only; Armenian is left for downstream fallback.
        self.assertIsNone(result["name_hy"])
        mock_compute.assert_called_once_with(self.test_date)

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_normalizes_datetime_to_date(self, mock_compute):
        """A ``datetime`` argument is reduced to a ``date`` before hitting the engine."""
        mock_compute.return_value = {"Liturgical Day": "Test Feast"}

        get_feast_for_date(datetime(2025, 12, 25, 9, 30), self.church)

        mock_compute.assert_called_once_with(self.test_date)

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_placeholder_names_return_none(self, mock_compute):
        """Engine placeholders (not real commemorations) are treated as no feast."""
        for placeholder in (
            "(commemoration)",
            "(movable ordinary-time reading)",
            "Pentecost (day not yet in validated table)",
            "",
            "   ",
        ):
            with self.subTest(placeholder=placeholder):
                mock_compute.return_value = {"Liturgical Day": placeholder}
                self.assertIsNone(get_feast_for_date(self.test_date, self.church))

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_missing_liturgical_day_returns_none(self, mock_compute):
        """A result with no ``Liturgical Day`` key yields no feast."""
        mock_compute.return_value = {}
        self.assertIsNone(get_feast_for_date(self.test_date, self.church))

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_date_outside_validated_range_returns_none(self, mock_compute):
        """Dates outside the validated year window are not served, and skip the engine."""
        self.assertIsNone(get_feast_for_date(date(1999, 1, 1), self.church))
        self.assertIsNone(get_feast_for_date(date(2100, 1, 1), self.church))
        mock_compute.assert_not_called()

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_unsupported_church_returns_none(self, mock_compute):
        """Churches outside SUPPORTED_CHURCHES get no feast, and skip the engine."""
        unsupported_church = Church.objects.create(name="Unsupported Church")

        result = get_feast_for_date(self.test_date, unsupported_church)

        self.assertIsNone(result)
        mock_compute.assert_not_called()
