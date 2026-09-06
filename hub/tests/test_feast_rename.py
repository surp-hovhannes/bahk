"""Tests for the legacy feast-name bridge: the folding rule and the shipped artifact.

The fold is what lets a name the retired scrape mangled match the clean text the engine writes.
Getting it wrong is silent in both directions -- a name that fails to fold strands a row's
enrichment, and two names that fold together merge commemorations that are not the same feast --
so both directions are pinned here.
"""
import datetime

from django.test import TestCase

from hub.services import feast_rename
from hub.services.feast_rename import engine_names, load_name_map, normalize_feast_key

# A name the retired scrape stored, and what armenian-lectionary calls the same day.  The engine
# folds "Saints" to "Sts." as a reviewed style decision, so this pair is representative of the
# largest class of rename in the map.
SCRAPED = "Saints Peter the Patriarch, Blaise the Bishop and Absalom the Deacon"
CURRENT = "Sts. Peter the Patriarch, Blaise the Bishop and Absalom the Deacon"

class NormalizeFeastKeyTests(TestCase):
    """The three ways the scrape mangled a name all have to fold to the clean text's key."""

    def test_jammed_components_match_separated_ones(self):
        """The old scraper stripped <br> with no replacement; the engine joins on an em dash."""
        self.assertEqual(
            normalize_feast_key("Forty First day of EastertideBegining of the Fast"),
            normalize_feast_key("Forty First day of Eastertide — Begining of the Fast"),
        )

    def test_html_entities_are_unescaped_before_folding(self):
        self.assertEqual(
            normalize_feast_key("Sts.&nbsp;Peter &amp; Paul"),
            normalize_feast_key("Sts. Peter & Paul"),
        )

    def test_punctuation_and_case_do_not_separate_names(self):
        self.assertEqual(normalize_feast_key("St. Mary’s Box"), normalize_feast_key("st marys box"))

    def test_armenian_survives_the_fold(self):
        """Armenian is kept, not stripped -- a name is not allowed to fold to nothing."""
        self.assertEqual(normalize_feast_key("Սկիզբն պահոց"), "սկիզբնպահոց")

    def test_distinct_names_keep_distinct_keys(self):
        self.assertNotEqual(normalize_feast_key(SCRAPED), normalize_feast_key(CURRENT))

    def test_empty_input_is_tolerated(self):
        self.assertEqual(normalize_feast_key(None), "")
        self.assertEqual(normalize_feast_key(""), "")


class NameMapTests(TestCase):
    """The shipped artifact, and the half of it that survives an engine release."""

    def test_every_entry_carries_a_date(self):
        """The durable half.  A recorded target name is a snapshot; a date is not.

        Each entry says what an old source called a day AND which day that was, so a consumer can
        ask the current engine rather than trusting text captured against an older one.
        """
        dates = feast_rename.load_name_map_dates()
        self.assertEqual(len(dates), len(feast_rename.load_name_map_entries()))
        for day in dates.values():
            self.assertIsInstance(day, datetime.date)

    def test_no_entry_maps_a_name_the_engine_still_emits(self):
        """Such an entry would be dead weight: ``resolve_name`` returns a reachable name first.

        Keyed on the raw spelling, not the folded key.  A key legitimately collides with a
        reachable name's key -- that is what happens when the only difference between the old
        spelling and the current one is a separator the fold erases.
        """
        reachable = engine_names()
        spellings = [
            spelling
            for entry in feast_rename.load_name_map_entries()
            for spelling in entry["variants"]
        ]
        self.assertEqual(sorted(set(spellings) & reachable), [])

    def test_the_scrape_era_spelling_resolves(self):
        self.assertEqual(load_name_map()[normalize_feast_key(SCRAPED)], CURRENT)

    def test_a_jammed_scrape_era_spelling_resolves_to_the_same_name(self):
        """1.1.0 ran a day's components together exactly as the scraper did."""
        jammed = "Forty First day of EastertideBegining of the Fast"
        self.assertEqual(
            load_name_map()[normalize_feast_key(jammed)],
            "Forty First day of Eastertide — Beginning of the Fast",
        )

