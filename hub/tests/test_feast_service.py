"""Tests for the offline ``get_feast_for_date`` service.

Replaces the retired ``scrape_feast`` tests: feast names now come from the offline
``armenian_lectionary`` engine (``"Liturgical Day"``, in ``en`` and ``hy``) rather than
sacredtradition.am.
"""
from datetime import date, datetime
from unittest.mock import patch

from django.test import TestCase

from hub.models import Church, Feast
from hub.services.feast_service import get_feast_for_date


def _engine_stub(en_name, hy_name=None):
    """Build a fake ``compute_armenian_lectionary`` that answers per ``language`` kwarg.

    ``hy_name`` defaults to ``en_name`` for tests that don't care about the Armenian value.
    """
    def _compute(_date, language="en"):
        return {"Liturgical Day": hy_name if (language == "hy" and hy_name) else en_name}
    return _compute


class GetFeastForDateTests(TestCase):
    """Tests for the ``get_feast_for_date`` service."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_returns_english_and_armenian_names(self, mock_compute):
        """Both the English and the Armenian ``Liturgical Day`` are returned."""
        mock_compute.side_effect = _engine_stub(
            "Nativity and Theophany of Our Lord Jesus Christ",
            "ՏՕՆ ԾՆՆԴԵԱՆ",
        )

        result = get_feast_for_date(self.test_date, self.church)

        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Nativity and Theophany of Our Lord Jesus Christ")
        self.assertEqual(result["name_en"], "Nativity and Theophany of Our Lord Jesus Christ")
        self.assertEqual(result["name_hy"], "ՏՕՆ ԾՆՆԴԵԱՆ")
        # Queried once per language.
        self.assertEqual(mock_compute.call_count, 2)

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_overlong_name_is_clamped_to_storage(self, mock_compute):
        """``name_en``/``name`` are clamped to what ``Feast.name`` can hold.

        Every name the engine actually produces fits (the longest is 289 characters), so this
        exercises the defensive clamp in ``_fit_to_storage`` rather than a real engine output.
        """
        max_length = Feast._meta.get_field("name").max_length
        overlong_name = "X" * (max_length + 1)
        mock_compute.side_effect = _engine_stub(overlong_name, "ՏՕՆ ԾՆՆԴԵԱՆ")

        result = get_feast_for_date(self.test_date, self.church)

        self.assertIsNotNone(result)
        self.assertEqual(len(result["name_en"]), max_length)
        self.assertEqual(result["name_en"], overlong_name[:max_length])
        self.assertEqual(result["name"], result["name_en"])
        self.assertEqual(result["name_hy"], "ՏՕՆ ԾՆՆԴԵԱՆ")

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_normalizes_datetime_to_date(self, mock_compute):
        """A ``datetime`` argument is reduced to a ``date`` before hitting the engine."""
        mock_compute.side_effect = _engine_stub("Test Feast")

        get_feast_for_date(datetime(2025, 12, 25, 9, 30), self.church)

        for call in mock_compute.call_args_list:
            self.assertEqual(call.args[0], self.test_date)

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_blank_names_return_none(self, mock_compute):
        """An empty or whitespace-only ``Liturgical Day`` is treated as no feast.

        The engine itself guarantees no placeholder marker (e.g. "(commemoration)") ever
        reaches a caller for a date in its validated range -- see
        ``armenian_lectionary``'s ``test_feast_contract.py::test_no_placeholder_reaches_callers``.
        """
        for blank in ("", "   "):
            with self.subTest(blank=blank):
                mock_compute.side_effect = _engine_stub(blank)
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
