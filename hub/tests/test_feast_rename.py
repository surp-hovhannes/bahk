"""Tests for the rule that moves a stored feast name onto the one the engine emits now.

The rename is not cosmetic: ``Feast`` is keyed by ``(church, observance_id)``; the name used to be the lookup
key, so getting it wrong either strands two years of LLM contexts and curated icons under a name
nothing reaches, or merges two commemorations that are not the same feast.  Both fail silently,
which is why the merge path is exercised against real rows here rather than trusted to read
correctly.

Grouped by what each block protects:

  * ``NormalizeFeastKeyTests`` -- the folding that lets a scraped name match a clean one.
  * ``NameMapTests`` -- the shipped artifact agrees with the pinned engine.
  * ``PlanRenamesTests`` / ``ApplyGroupTests`` -- what the plan says, and what applying it does.
"""
import datetime

from django.test import TestCase

from hub.models import Church, Feast, FeastContext
from hub.services import feast_rename
from hub.services.feast_rename import (
    apply_group, commemoration_ids_for_date, describe, engine_name_for_date, engine_names,
    load_name_map, name_for_observance_id, normalize_feast_key, observance_ids,
    plan_renames, primary_commemoration_id_for_date, refresh_metadata, stale_metadata,
)

# A name the retired scrape stored, and what armenian-lectionary 1.3.0 calls the same day. The
# engine folds "Saints" to "Sts." as a reviewed style decision, so this pair is representative of
# the largest class of rename in the map.
SCRAPED = "Saints Peter the Patriarch, Blaise the Bishop and Absalom the Deacon"
CURRENT = "Sts. Peter the Patriarch, Blaise the Bishop and Absalom the Deacon"
# What both spellings actually are. The id is what the row is keyed by from here on; the two
# names above are the same observance, one release apart.
KEY = "peter_the_patriarch_blaise"


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
    """The checked-in artifact has to agree with the engine actually installed.

    It was generated against 1.3.0.  If the pin moves and a target name moves with it, every row
    the map points at that name is stranded again -- silently, since nothing errors. These are the
    tests that fail instead.
    """

    def test_every_entry_carries_a_date_the_engine_can_still_resolve(self):
        """The durable half of the artifact: a date, not a snapshot of display text.

        The map was generated against 1.3.0, and 126 of its 229 target NAMES stopped being
        emitted verbatim at 2.0.0 -- which is exactly the staleness the re-key exists to stop
        depending on. The dates beside them did not move, so that is what a row is placed by.
        """
        dates = feast_rename.load_name_map_dates()
        self.assertEqual(len(dates), len(feast_rename.load_name_map_entries()))

        resolvable = feast_rename._commemoration_ids_by_date()
        unresolvable = sorted(key for key, day in dates.items() if day not in resolvable)
        self.assertEqual(
            unresolvable, [],
            f"{len(unresolvable)} map entry date(s) the engine cannot resolve at all")

    def test_entries_whose_day_commemorates_nobody_get_no_id(self):
        """Not every legacy row names a commemoration, and those must not be given one.

        14 of the 244 entries are position labels and bare fast markers -- "Great MondayFast
        day", "Fifth day of PentecostFast day", "Third day of the Fast of Nativity." A row for
        one of those has no commemoration to be, so it stays unresolved and keeps a null id
        rather than being forced onto whatever else that date happens to carry.
        """
        dates = feast_rename.load_name_map_dates()
        no_commemoration = [day for day in dates.values()
                            if not primary_commemoration_id_for_date(day)]
        self.assertTrue(no_commemoration, "expected some position-label entries")

        for day in no_commemoration:
            self.assertEqual(commemoration_ids_for_date(day), ())

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


class EngineLookupTests(TestCase):
    """The date <-> name direction the remap runs on."""

    def test_a_date_resolves_to_the_current_name(self):
        self.assertEqual(engine_name_for_date(datetime.date(2001, 1, 16)), CURRENT)

    def test_the_armenian_name_is_available_for_the_same_date(self):
        name_hy = engine_name_for_date(datetime.date(2001, 1, 16), language="hy")
        self.assertTrue(name_hy)
        self.assertNotEqual(name_hy, CURRENT)

    def test_a_date_outside_the_range_resolves_to_nothing(self):
        self.assertEqual(engine_name_for_date(datetime.date(1900, 1, 1)), "")


class ObservanceIdTests(TestCase):
    """The identity a Feast row is keyed by, and why it is not the name."""

    def test_a_date_resolves_to_its_commemoration_id(self):
        self.assertEqual(commemoration_ids_for_date(datetime.date(2001, 1, 16)), (KEY,))

    def test_only_commemorations_are_keyed(self):
        """A day is not one observance, and the calendar-position component gets no row.

        2004-11-21 serves three: a Sunday-of-the-Holy-Cross position label, the Presentation,
        and an eve note.  Only the last two commemorate anything, so only those two are ids a
        Feast can hold.
        """
        self.assertEqual(
            commemoration_ids_for_date(datetime.date(2004, 11, 21)),
            ("presentation_of_the_holy_mother", "eve_of_fast_of_advent"))

    def test_the_leading_commemoration_is_the_one_a_legacy_row_is_placed_under(self):
        """A row keyed to a whole day inherits the first commemoration, not an arbitrary one."""
        self.assertEqual(
            primary_commemoration_id_for_date(datetime.date(2004, 11, 21)),
            "presentation_of_the_holy_mother")

    def test_a_date_outside_the_range_has_no_id(self):
        self.assertEqual(commemoration_ids_for_date(datetime.date(1900, 1, 1)), ())
        self.assertEqual(primary_commemoration_id_for_date(datetime.date(1900, 1, 1)), "")

    def test_the_id_survives_a_name_the_engine_no_longer_emits(self):
        """The whole point. The scrape's spelling is gone; the observance it named is not."""
        self.assertNotIn(SCRAPED, engine_names())
        self.assertIn(KEY, observance_ids())

    def test_display_text_is_read_from_the_id_without_a_date(self):
        """What let ``Feast.sample_date`` be retired: the identity states the name directly."""
        self.assertEqual(name_for_observance_id(KEY), CURRENT)
        self.assertTrue(name_for_observance_id(KEY, language="hy"))
        self.assertNotEqual(name_for_observance_id(KEY, language="hy"), CURRENT)

    def test_an_id_the_engine_does_not_serve_has_no_name(self):
        self.assertEqual(name_for_observance_id("not_a_real_observance"), "")


class FeastRenameTestCase(TestCase):
    """Shared fixtures: a church, and helpers for building rows in a given state."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.other_church = Church.objects.create(name="Other Church")

    def make_feast(self, name, **kwargs):
        return Feast.objects.create(church=self.church, name=name, **kwargs)

    def make_context(self, feast, text, generated_year, **kwargs):
        context = FeastContext.objects.create(
            feast=feast, text=text, short_text=text, **kwargs
        )
        FeastContext.objects.filter(pk=context.pk).update(
            time_of_generation=datetime.datetime(
                generated_year, 1, 1, tzinfo=datetime.timezone.utc)
        )
        return context

    def plan(self, feasts=None):
        feasts = self.church.feasts.all() if feasts is None else feasts
        return plan_renames(list(feasts), engine_names())


class PlanRenamesTests(FeastRenameTestCase):
    """What the plan reports, before anything is written."""

    def test_a_row_already_carrying_its_key_is_left_alone(self):
        self.make_feast(CURRENT, observance_id=KEY)
        groups, unresolved = self.plan()
        self.assertEqual(unresolved, [])
        self.assertEqual(describe(*groups[0]), "unchanged")

    def test_a_row_with_no_key_is_placed_by_its_name(self):
        """The bridge: rows written under the name key resolve through it, once."""
        self.make_feast(SCRAPED)
        groups, unresolved = self.plan()
        self.assertEqual(unresolved, [])
        key, group = groups[0]
        self.assertEqual(key, KEY)
        self.assertEqual(describe(key, group), "rekey")

    def test_two_spellings_of_one_observance_form_one_group(self):
        """The shape the upgrade actually leaves behind: the old row, and an empty new one."""
        old = self.make_feast(SCRAPED)
        new = self.make_feast(CURRENT)
        groups, _ = self.plan()
        key, group = groups[0]
        self.assertEqual(key, KEY)
        self.assertEqual(describe(key, group), "merge")
        self.assertEqual([f.id for f in group], [old.id, new.id])

    def test_a_stored_id_is_trusted_over_the_name(self):
        """An id is a contract, so a row that has one needs nothing re-derived."""
        feast = self.make_feast("Whatever This Row Says", observance_id=KEY)
        groups, unresolved = self.plan([feast])
        self.assertEqual(unresolved, [])
        self.assertEqual(groups[0][0], KEY)

    def test_a_legacy_row_for_a_two_commemoration_day_lands_on_the_leading_one(self):
        """It is not this module's job to invent the second row.

        2004-11-21 names two commemorations, and a legacy row stored the joined string for the
        whole day. It is placed under the leading commemoration -- the component its name and its
        enrichment were dominated by. The other gets its own row the first time the ordinary
        request path serves that date.
        """
        feast = self.make_feast(
            "Eleventh Sunday of the Holy Cross — Presentation of the Holy Mother of God to the "
            "Temple — Eve of the Fast of Advent")
        groups, unresolved = self.plan([feast])
        self.assertEqual(unresolved, [])
        self.assertEqual([key for key, _ in groups], ["presentation_of_the_holy_mother"])

    def test_a_row_nothing_resolves_is_reported_not_guessed(self):
        feast = self.make_feast("A Commemoration No Source Ever Published")
        groups, unresolved = self.plan([feast])
        self.assertEqual(groups, [])
        self.assertEqual(unresolved, [feast])

    def test_another_church_is_planned_separately(self):
        """Feasts are unique per church; one church's rename must not reach into another."""
        mine = self.make_feast(SCRAPED)
        theirs = Feast.objects.create(church=self.other_church, name=SCRAPED)
        groups, _ = self.plan([mine])
        self.assertEqual([f.id for f in groups[0][1]], [mine.id])
        theirs.refresh_from_db()
        self.assertEqual(theirs.name, SCRAPED)
        self.assertIsNone(theirs.observance_id)


class StaleMetadataTests(FeastRenameTestCase):
    """What counts as out of date beyond the name itself."""

    def test_a_row_with_no_key_is_stale(self):
        feast = self.make_feast(CURRENT)
        self.assertIn("observance_id", stale_metadata(feast, KEY))

    def test_a_stale_display_name_is_stale(self):
        """The name is derived from the key now, so a correction updates it in place."""
        feast = self.make_feast(SCRAPED, observance_id=KEY)
        self.assertIn("name", stale_metadata(feast, KEY))

    def test_a_fully_current_row_is_not_stale(self):
        feast = self.make_feast(CURRENT)
        refresh_metadata(feast, KEY)
        feast.save()
        self.assertEqual(stale_metadata(feast, KEY), [])

    def test_refresh_takes_both_names_from_the_engine(self):
        """The scrape's Armenian came from a language code the source does not define."""
        feast = self.make_feast(SCRAPED)
        feast.name_hy = "whatever the scrape stored"
        feast.save()

        refresh_metadata(feast, KEY)
        feast.save()
        feast.refresh_from_db()

        self.assertEqual(feast.observance_id, KEY)
        self.assertEqual(feast.name, CURRENT)
        self.assertEqual(feast.name_hy, name_for_observance_id(KEY, language="hy"))


class ApplyGroupTests(FeastRenameTestCase):
    """What applying a group does to the rows -- the only path that deletes anything."""

    def test_a_lone_row_is_re_keyed_in_place(self):
        feast = self.make_feast(SCRAPED, designation="Martyrs")
        apply_group(KEY, [feast], Feast, FeastContext)
        feast.save()
        feast.refresh_from_db()

        self.assertEqual(feast.observance_id, KEY)
        self.assertEqual(feast.designation, "Martyrs")

    def test_the_enriched_row_survives_and_the_empty_one_is_absorbed(self):
        """The stale row is the older one, and it is the one holding two years of enrichment."""
        old = self.make_feast(SCRAPED, designation="Martyrs")
        self.make_context(old, "curated", 2025, active=True, thumbs_up=5, thumbs_down=1)
        new = self.make_feast(CURRENT)

        keeper = apply_group(KEY, [old, new], Feast, FeastContext)
        keeper.save()

        self.assertEqual(keeper.id, old.id)
        self.assertEqual(Feast.objects.filter(church=self.church).count(), 1)
        self.assertFalse(Feast.objects.filter(id=new.id).exists())
        self.assertEqual(Feast.objects.get(id=old.id).observance_id, KEY)
        self.assertEqual(Feast.objects.get(id=old.id).designation, "Martyrs")

    def test_contexts_are_reparented_rather_than_cascaded_away(self):
        old = self.make_feast(SCRAPED)
        self.make_context(old, "older", 2024, active=True, thumbs_up=5, thumbs_down=1)
        new = self.make_feast(CURRENT)
        self.make_context(new, "newer", 2026, active=True, thumbs_up=3, thumbs_down=2)

        keeper = apply_group(KEY, [old, new], Feast, FeastContext)
        keeper.save()

        contexts = FeastContext.objects.filter(feast_id=keeper.id)
        self.assertEqual(contexts.count(), 2)

        # The newest active context wins, and carries the group's whole feedback total.
        survivor = contexts.get(active=True)
        self.assertEqual(survivor.text, "newer")
        self.assertEqual((survivor.thumbs_up, survivor.thumbs_down), (8, 3))
        self.assertEqual(contexts.get(active=False).text, "older")

    def test_the_icon_is_rescued_off_whichever_row_carries_it(self):
        from icons.models import Icon

        icon = Icon.objects.create(title="Peter", church=self.church)
        old = self.make_feast(SCRAPED)
        new = self.make_feast(CURRENT, icon=icon)

        keeper = apply_group(KEY, [old, new], Feast, FeastContext)
        keeper.save()

        self.assertEqual(Feast.objects.get(id=old.id).icon_id, icon.id)

    def test_three_spellings_of_one_commemoration_collapse_together(self):
        """Merging per target name, not pairwise, is what makes this come out as one row.

        Production really does hold three eras of one feast: what the scrape wrote, what an
        interim engine release wrote, and the empty row a lookup minted after the 1.3.0 upgrade.
        """
        rows = [
            self.make_feast("Saint Peter the Patriarch, Blaise the Bishop and Absalom the Deacon"),
            self.make_feast(SCRAPED),
            self.make_feast(CURRENT),
        ]
        keeper = apply_group(KEY, rows, Feast, FeastContext)
        keeper.save()

        self.assertEqual(Feast.objects.filter(church=self.church).count(), 1)
        self.assertEqual(Feast.objects.get(pk=keeper.pk).observance_id, KEY)
