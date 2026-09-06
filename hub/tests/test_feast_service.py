"""Tests for the offline ``get_feast_for_date`` service.

Replaces the retired ``scrape_feast`` tests: commemorations now come from the offline
``armenian_lectionary`` engine (its ``"Observances"`` array, in ``en`` and ``hy``) rather than
sacredtradition.am.
"""
from datetime import date, datetime
from unittest.mock import patch

from django.test import TestCase

from hub.models import Church
from hub.services.feast_service import get_feast_for_date


def _observance(observance_id, name, is_comm=True, is_fast=False):
    return {"id": observance_id, "name": name, "is_comm": is_comm, "is_fast": is_fast}


def _engine_stub(observances_en, observances_hy=None):
    """Build a fake ``compute_armenian_lectionary`` that answers per ``language`` kwarg.

    ``observances_hy`` defaults to the English list for tests that don't care about Armenian.
    """
    def _compute(_date, language="en"):
        observances = (observances_hy if (language == "hy" and observances_hy is not None)
                       else observances_en)
        return {
            "Observances": observances,
            "Liturgical Day": " — ".join(o["name"] for o in observances),
        }
    return _compute


class GetFeastForDateTests(TestCase):
    """Tests for the ``get_feast_for_date`` service."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_returns_english_and_armenian_names(self, mock_compute):
        """A commemoration comes back with its id and its name in both languages."""
        mock_compute.side_effect = _engine_stub(
            [_observance("nativity", "Nativity and Theophany of Our Lord Jesus Christ")],
            [_observance("nativity", "ՏՕՆ ԾՆՆԴԵԱՆ")],
        )

        result = get_feast_for_date(self.test_date, self.church)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["observance_id"], "nativity")
        self.assertEqual(result[0]["name"], "Nativity and Theophany of Our Lord Jesus Christ")
        self.assertEqual(result[0]["name_en"], "Nativity and Theophany of Our Lord Jesus Christ")
        self.assertEqual(result[0]["name_hy"], "ՏՕՆ ԾՆՆԴԵԱՆ")
        # Queried once per language.
        self.assertEqual(mock_compute.call_count, 2)

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_two_commemorations_come_back_in_served_order(self, mock_compute):
        """185 days in range name two commemorations; both get an entry, in the engine's order."""
        mock_compute.side_effect = _engine_stub(
            [_observance("hermit_st_anton", "The Hermit St. Anton"),
             _observance("hermit_sts_tryphon", "The Hermit Sts. Tryphon, Barsauma and Onuphrius")],
            [_observance("hermit_st_anton", "Սրբոյն Անտոնի ճգնաւորին"),
             _observance("hermit_sts_tryphon", "Սրբոցն Տրիփոնի")],
        )

        result = get_feast_for_date(self.test_date, self.church)

        self.assertEqual([entry["observance_id"] for entry in result],
                         ["hermit_st_anton", "hermit_sts_tryphon"])
        self.assertEqual(result[1]["name_hy"], "Սրբոցն Տրիփոնի")

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_armenian_names_are_paired_by_id_not_position(self, mock_compute):
        """The hy list is joined on id, so a different order cannot mismatch the translations."""
        mock_compute.side_effect = _engine_stub(
            [_observance("first", "First"), _observance("second", "Second")],
            [_observance("second", "Երկրորդ"), _observance("first", "Առաջին")],
        )

        result = get_feast_for_date(self.test_date, self.church)

        by_id = {entry["observance_id"]: entry["name_hy"] for entry in result}
        self.assertEqual(by_id, {"first": "Առաջին", "second": "Երկրորդ"})

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_non_commemorations_are_dropped(self, mock_compute):
        """A position label is not a commemoration, so it gets no feast."""
        mock_compute.side_effect = _engine_stub([
            _observance("great_friday", "Great Friday", is_comm=False, is_fast=True),
            _observance("passion", "Remembrance of the Passion", is_comm=True),
        ])

        result = get_feast_for_date(self.test_date, self.church)

        self.assertEqual([entry["observance_id"] for entry in result], ["passion"])

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_a_fast_that_is_also_a_commemoration_is_kept(self, mock_compute):
        """The two marks are independent -- filtering out fasts would drop Great Friday."""
        mock_compute.side_effect = _engine_stub([
            _observance("great_friday", "Great Friday", is_comm=True, is_fast=True),
        ])

        result = get_feast_for_date(self.test_date, self.church)

        self.assertEqual([entry["observance_id"] for entry in result], ["great_friday"])

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_day_with_no_commemoration_returns_empty_list(self, mock_compute):
        """5,070 of the engine's 9,861 days commemorate nobody. That is an answer, not a failure.

        Distinct from ``None``, which means the engine gave no answer at all -- the caller has to
        be able to tell "nothing today" from "this install is broken".
        """
        mock_compute.side_effect = _engine_stub([
            _observance("wednesday_fast", "Wednesday Fast", is_comm=False, is_fast=True),
        ])

        result = get_feast_for_date(self.test_date, self.church)

        self.assertEqual(result, [])
        self.assertIsNotNone(result)

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_normalizes_datetime_to_date(self, mock_compute):
        """A ``datetime`` argument is reduced to a ``date`` before hitting the engine."""
        mock_compute.side_effect = _engine_stub([_observance("test", "Test Feast")])

        get_feast_for_date(datetime(2025, 12, 25, 9, 30), self.church)

        for call in mock_compute.call_args_list:
            self.assertEqual(call.args[0], self.test_date)

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_unresolved_day_returns_none(self, mock_compute):
        """The engine resolves its components all or nothing, so ``[]`` means "did not resolve".

        On an install missing ``observance_catalog.json`` every day answers this way, which is
        why it cannot be read as "no commemoration today".
        """
        mock_compute.side_effect = _engine_stub([])

        self.assertIsNone(get_feast_for_date(self.test_date, self.church))

    @patch("hub.services.feast_service.armenian_lectionary.compute_armenian_lectionary")
    def test_missing_observances_key_returns_none(self, mock_compute):
        """A result with no ``Observances`` key yields no feast."""
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
