"""Tests for multi-commemoration feast reference matching."""
from datetime import date
from unittest.mock import patch, mock_open
import json

from django.test import TestCase

from hub.models import Church, Day, Feast
from hub.services.llm_service import _find_all_matching_feasts


class FindAllMatchingFeastsTests(TestCase):
    """Unit tests for _find_all_matching_feasts function."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 1, 21)
        self.day = Day.objects.create(date=self.test_date, church=self.church)

    def _create_mock_feasts_data(self, feasts_list):
        """Helper to create mock feasts.json data."""
        mock_data = json.dumps(feasts_list)
        return mock_open(read_data=mock_data)

    @patch('os.path.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data='[]')
    def test_returns_empty_list_when_no_matches(self, mock_file, mock_exists):
        """Test returns empty list when no matches found."""
        feast = Feast.objects.create(
            church=self.day.church,
            name="NonExistent Feast",
        )

        matches = _find_all_matching_feasts(feast)

        self.assertEqual(len(matches), 0)
        self.assertIsInstance(matches, list)

    @patch('os.path.exists', return_value=True)
    def test_returns_single_match_as_list(self, mock_exists):
        """Test returns single match as a list with one element."""
        feasts_data = [
            {
                "name": "Saint Gregory the Illuminator",
                "description": "The patron saint and first official head of the Armenian Apostolic Church.",
                "month": "January",
                "day": "21"
            }
        ]

        with patch('builtins.open', self._create_mock_feasts_data(feasts_data)):
            feast = Feast.objects.create(
                church=self.day.church,
                name="St. Gregory the Illuminator",
            )

            matches = _find_all_matching_feasts(feast)

            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]['name'], "Saint Gregory the Illuminator")

    @patch('os.path.exists', return_value=True)
    def test_returns_multiple_matches_above_threshold(self, mock_exists):
        """Test returns all matches above similarity threshold."""
        feasts_data = [
            {
                "name": "Saint Gregory the Illuminator",
                "description": "The patron saint and first official head of the Armenian Apostolic Church.",
                "month": "January",
                "day": "21"
            },
            {
                "name": "Commemoration of Saint Gregory",
                "description": "A celebration of Saint Gregory's life and teachings.",
                "month": "January",
                "day": "21"
            },
            {
                "name": "Unrelated Feast",
                "description": "This should not match.",
                "month": "March",
                "day": "15"
            }
        ]

        with patch('builtins.open', self._create_mock_feasts_data(feasts_data)):
            feast = Feast.objects.create(
                church=self.day.church,
                name="Saint Gregory",
            )

            matches = _find_all_matching_feasts(feast)

            # Should match the first two but not the third
            self.assertGreaterEqual(len(matches), 2)
            self.assertTrue(any("Gregory" in m['name'] for m in matches))
            self.assertFalse(any("Unrelated" in m['name'] for m in matches))

    @patch('os.path.exists', return_value=True)
    def test_sorts_by_similarity_score_descending(self, mock_exists):
        """Test results are sorted by similarity score (best match first)."""
        feasts_data = [
            {
                "name": "Saint Gregory Feast",
                "description": "Less specific match.",
                "month": "January",
                "day": "21"
            },
            {
                "name": "Saint Gregory the Illuminator",
                "description": "Exact match.",
                "month": "January",
                "day": "21"
            },
            {
                "name": "Gregory's Day",
                "description": "Shorter match.",
                "month": "January",
                "day": "21"
            }
        ]

        with patch('builtins.open', self._create_mock_feasts_data(feasts_data)):
            feast = Feast.objects.create(
                church=self.day.church,
                name="Saint Gregory the Illuminator",
            )

            matches = _find_all_matching_feasts(feast)

            # The best match should be first
            if len(matches) > 0:
                self.assertEqual(matches[0]['name'], "Saint Gregory the Illuminator")

    @patch('os.path.exists', return_value=True)
    def test_limits_to_max_commemorations(self, mock_exists):
        """Test limits results to MAX_COMMEMORATIONS_IN_CONTEXT."""
        # Create 10 similar feasts (more than MAX_COMMEMORATIONS_IN_CONTEXT = 5)
        feasts_data = [
            {
                "name": f"Saint Gregory Commemoration {i}",
                "description": "A Gregory commemoration.",
                "month": "January",
                "day": "21"
            }
            for i in range(10)
        ]

        with patch('builtins.open', self._create_mock_feasts_data(feasts_data)):
            feast = Feast.objects.create(
                church=self.day.church,
                name="Saint Gregory",
            )

            matches = _find_all_matching_feasts(feast)

            # Should be limited to 5
            self.assertLessEqual(len(matches), 5)

    @patch('os.path.exists', return_value=True)
    def test_date_boost_increases_score(self, mock_exists):
        """Test date matching increases score and prioritizes matches."""
        feasts_data = [
            {
                "name": "Saint Gregory",
                "description": "Same date.",
                "month": "January",
                "day": "21"
            },
            {
                "name": "Saint Gregory",
                "description": "Different date.",
                "month": "March",
                "day": "15"
            }
        ]

        with patch('builtins.open', self._create_mock_feasts_data(feasts_data)):
            feast = Feast.objects.create(
                church=self.day.church,
                name="Saint Gregory",
            )

            matches = _find_all_matching_feasts(feast)

            # The date-matching entry should be first
            if len(matches) > 0:
                self.assertEqual(matches[0]['month'], "January")
                self.assertEqual(matches[0]['day'], "21")

    @patch('os.path.exists', return_value=True)
    def test_works_without_date(self, mock_exists):
        """Test matching works for moveable feasts without fixed dates."""
        feasts_data = [
            {
                "name": "Feast of the Resurrection",
                "description": "Easter Sunday.",
                "month": "April",
                "day": "20"
            }
        ]

        with patch('builtins.open', self._create_mock_feasts_data(feasts_data)):
            # Create feast without associating it with a Day (moveable feast)
            feast = Feast(
                name="Feast of the Resurrection",
            )
            # Don't save to DB, just test matching logic

            matches = _find_all_matching_feasts(feast)

            # Should still find match based on name alone
            self.assertGreater(len(matches), 0)
            self.assertEqual(matches[0]['name'], "Feast of the Resurrection")

    @patch('os.path.exists', return_value=True)
    def test_armenian_name_matching(self, mock_exists):
        """Test matching works with Armenian feast names."""
        feasts_data = [
            {
                "name": "Սուրբ Գրիգոր Լուսավորիչ",
                "description": "Saint Gregory the Illuminator in Armenian.",
                "month": "January",
                "day": "21"
            }
        ]

        with patch('builtins.open', self._create_mock_feasts_data(feasts_data)):
            feast = Feast.objects.create(
                church=self.day.church,
                name="Saint Gregory the Illuminator",
            )
            feast.name_hy = "Սուրբ Գրիգոր Լուսավորիչ"
            feast.save(update_fields=['i18n'])

            matches = _find_all_matching_feasts(feast)

            # Should match based on Armenian name
            self.assertGreater(len(matches), 0)
            self.assertEqual(matches[0]['name'], "Սուրբ Գրիգոր Լուսավորիչ")

    @patch('os.path.exists', return_value=True)
    def test_filters_below_threshold(self, mock_exists):
        """Test filters out matches below MIN_MULTI_FEAST_SIMILARITY threshold (0.33)."""
        feasts_data = [
            {
                "name": "Saint Gregory the Illuminator",
                "description": "High similarity match.",
                "month": "January",
                "day": "21"
            },
            {
                "name": "Completely Unrelated Feast Day",
                "description": "Should be filtered out - similarity too low even with date boost.",
                "month": "March",  # Different date to avoid date boost pushing it over threshold
                "day": "15"
            }
        ]

        with patch('builtins.open', self._create_mock_feasts_data(feasts_data)):
            feast = Feast.objects.create(
                church=self.day.church,
                name="Saint Gregory",
            )

            matches = _find_all_matching_feasts(feast)

            # Should only include Gregory-related matches
            for match in matches:
                self.assertIn("Gregory", match['name'])

    @patch('hub.services.llm_service._llm_filter_feast_matches')
    @patch('hub.services.llm_service.feast_service.dates_for_feast_name')
    def test_holy_church_date_window_selects_only_canonical_reference(
        self,
        mock_dates_for_feast_name,
        mock_llm_filter,
    ):
        """The full 2026 Holy Church window bypasses fuzzy and LLM matching."""
        mock_dates_for_feast_name.return_value = [
            date(2026, 9, 15),
            date(2026, 9, 16),
            date(2026, 9, 17),
        ]
        feast = Feast.objects.create(
            church=self.day.church,
            name="Feast of the Holy Church",
        )
        excluded_names = {
            "Sunday of the World Church (Green Sunday)",
            "Feast of the Cathedral of Holy Etchmiadzin",
            "Commemoration of the Tabernacle of Old Testament (or the Old Ark) "
            "and the Feast of the New Holy Church",
        }

        matches = _find_all_matching_feasts(feast)
        match_names = {match['name'] for match in matches}

        self.assertEqual(match_names, {"Feast of the Holy Church"})
        self.assertTrue(match_names.isdisjoint(excluded_names))
        mock_llm_filter.assert_not_called()

    @patch('hub.services.llm_service._llm_filter_feast_matches')
    @patch(
        'hub.services.llm_service.feast_service.dates_for_feast_name',
        return_value=[
            date(2026, 2, 14),
            date(2027, 2, 14),
            date(2028, 2, 14),
        ],
    )
    def test_multi_year_date_collision_excludes_unrelated_reference(
        self,
        mock_dates_for_feast_name,
        mock_llm_filter,
    ):
        """A shared 2027 date does not mix Sunday of Expulsion into Tiarn'ndaraj."""
        feast = Feast.objects.create(
            church=self.day.church,
            name="Tiarn'ndaraj",
        )

        matches = _find_all_matching_feasts(feast)
        match_names = {match['name'] for match in matches}

        self.assertEqual(match_names, {"Tiarn’ndaraj"})
        self.assertNotIn("Sunday of Expulsion", match_names)
        mock_llm_filter.assert_not_called()

    @patch('hub.services.llm_service._llm_filter_feast_matches')
    @patch(
        'hub.services.llm_service.feast_service.dates_for_feast_name',
        return_value=[date(2026, 9, 15)],
    )
    @patch('os.path.exists', return_value=True)
    def test_invalid_or_nonoverlapping_upcoming_dates_use_fuzzy_fallback(
        self,
        mock_exists,
        mock_dates_for_feast_name,
        mock_llm_filter,
    ):
        """Malformed date windows are ignored without changing fuzzy fallback."""
        feasts_data = [
            {
                "name": "Feast of the Holy Church",
                "description": "Canonical reference with unusable dates.",
                "upcoming_dates": ["not-a-date", None, "2027-09-14"],
            },
            {
                "name": "Sunday of Expulsion",
                "description": "Low-similarity reference sharing a served date.",
                "upcoming_dates": ["2026-09-15"],
            },
        ]
        expected_matches = [feasts_data[0]]
        mock_llm_filter.return_value = expected_matches

        with patch('builtins.open', self._create_mock_feasts_data(feasts_data)):
            feast = Feast.objects.create(
                church=self.day.church,
                name="Feast of the Holy Church",
            )

            matches = _find_all_matching_feasts(feast)

        self.assertEqual(matches, expected_matches)
        mock_llm_filter.assert_called_once_with(feast, expected_matches)

    @patch('os.path.exists', return_value=False)
    def test_handles_missing_feasts_file(self, mock_exists):
        """Test handles missing feasts.json file gracefully."""
        feast = Feast.objects.create(
            church=self.day.church,
            name="Test Feast",
        )

        matches = _find_all_matching_feasts(feast)

        self.assertEqual(len(matches), 0)

    @patch('os.path.exists', return_value=True)
    @patch('builtins.open', side_effect=IOError("File read error"))
    def test_handles_file_read_error(self, mock_file, mock_exists):
        """Test handles file read errors gracefully."""
        feast = Feast.objects.create(
            church=self.day.church,
            name="Test Feast",
        )

        matches = _find_all_matching_feasts(feast)

        self.assertEqual(len(matches), 0)
