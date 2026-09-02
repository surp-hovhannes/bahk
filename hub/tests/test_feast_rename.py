"""Tests for the rule that moves a stored feast name onto the one the engine emits now.

The rename is not cosmetic: ``Feast`` is keyed by ``(church, name)`` and the name is the lookup
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
    apply_group, describe, engine_name_for_date, engine_names, load_name_map,
    normalize_feast_key, plan_renames, refresh_metadata, sample_date_for_name, stale_metadata,
)

# A name the retired scrape stored, and what armenian-lectionary 1.3.0 calls the same day. The
# engine folds "Saints" to "Sts." as a reviewed style decision, so this pair is representative of
# the largest class of rename in the map.
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
    """The checked-in artifact has to agree with the engine actually installed.

    It was generated against 1.3.0.  If the pin moves and a target name moves with it, every row
    the map points at that name is stranded again -- silently, since nothing errors. These are the
    tests that fail instead.
    """

    def test_every_target_is_a_name_the_engine_emits(self):
        reachable = engine_names()
        missing = sorted(set(load_name_map().values()) - reachable)
        self.assertEqual(missing, [], f"{len(missing)} map target(s) the engine no longer emits")

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

    def test_sample_date_round_trips_through_the_engine(self):
        day = sample_date_for_name(CURRENT)
        self.assertIsNotNone(day)
        self.assertEqual(engine_name_for_date(day), CURRENT)


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
        return plan_renames(list(feasts), engine_names(), engine_name_for_date)


class PlanRenamesTests(FeastRenameTestCase):
    """What the plan reports, before anything is written."""

    def test_a_current_name_is_left_alone(self):
        self.make_feast(CURRENT)
        groups, unmapped = self.plan()
        self.assertEqual(unmapped, [])
        self.assertEqual(describe(*groups[0]), "unchanged")

    def test_a_scrape_era_name_is_a_rename(self):
        self.make_feast(SCRAPED)
        groups, unmapped = self.plan()
        self.assertEqual(unmapped, [])
        target, group = groups[0]
        self.assertEqual(target, CURRENT)
        self.assertEqual(describe(target, group), "rename")

    def test_the_stale_row_and_the_row_it_collides_with_form_one_group(self):
        """The shape the upgrade actually leaves behind: the old row, and an empty new one."""
        old = self.make_feast(SCRAPED)
        new = self.make_feast(CURRENT)
        groups, _ = self.plan()
        target, group = groups[0]
        self.assertEqual(target, CURRENT)
        self.assertEqual(describe(target, group), "merge")
        self.assertEqual([f.id for f in group], [old.id, new.id])

    def test_a_recorded_date_outranks_the_map(self):
        """sample_date is the recurring path; the map only covers rows written before it.

        The row is named for a day the engine now calls something else, so the date -- not the
        stored text -- decides where it lands.
        """
        feast = self.make_feast("Some Name An Engine Once Emitted",
                                sample_date=datetime.date(2001, 1, 16))
        groups, unmapped = self.plan([feast])
        self.assertEqual(unmapped, [])
        self.assertEqual(groups[0][0], CURRENT)

    def test_a_name_nothing_resolves_is_reported_not_guessed(self):
        feast = self.make_feast("A Commemoration No Source Ever Published")
        groups, unmapped = self.plan([feast])
        self.assertEqual(groups, [])
        self.assertEqual(unmapped, [feast])

    def test_another_church_is_planned_separately(self):
        """Feasts are unique per church; one church's rename must not reach into another."""
        mine = self.make_feast(SCRAPED)
        theirs = Feast.objects.create(church=self.other_church, name=SCRAPED)
        groups, _ = self.plan([mine])
        self.assertEqual([f.id for f in groups[0][1]], [mine.id])
        theirs.refresh_from_db()
        self.assertEqual(theirs.name, SCRAPED)


class StaleMetadataTests(FeastRenameTestCase):
    """What counts as out of date beyond the name itself."""

    def test_a_row_with_no_recorded_date_is_stale(self):
        feast = self.make_feast(CURRENT)
        self.assertIn("sample_date", stale_metadata(feast, CURRENT))

    def test_a_date_that_no_longer_produces_the_name_is_stale(self):
        feast = self.make_feast(CURRENT, sample_date=datetime.date(2001, 1, 18))
        self.assertIn("sample_date", stale_metadata(feast, CURRENT))

    def test_a_fully_current_row_is_not_stale(self):
        feast = self.make_feast(CURRENT)
        refresh_metadata(feast, CURRENT)
        feast.save()
        self.assertEqual(stale_metadata(feast, CURRENT), [])

    def test_refresh_takes_the_armenian_name_from_the_engine(self):
        """The scrape's Armenian came from a language code the source does not define."""
        feast = self.make_feast(SCRAPED)
        feast.name_hy = "whatever the scrape stored"
        feast.save()

        refresh_metadata(feast, CURRENT)
        feast.save()
        feast.refresh_from_db()

        self.assertEqual(feast.name, CURRENT)
        self.assertEqual(
            feast.name_hy, engine_name_for_date(feast.sample_date, language="hy"))


class ApplyGroupTests(FeastRenameTestCase):
    """What applying a group does to the rows -- the only path that deletes anything."""

    def test_a_lone_stale_row_is_renamed_in_place(self):
        feast = self.make_feast(SCRAPED, designation="Martyrs")
        apply_group(CURRENT, [feast], Feast, FeastContext)
        feast.save()
        feast.refresh_from_db()

        self.assertEqual(feast.name, CURRENT)
        self.assertEqual(feast.designation, "Martyrs")

    def test_the_enriched_row_survives_and_the_empty_one_is_absorbed(self):
        """The stale row is the older one, and it is the one holding two years of enrichment."""
        old = self.make_feast(SCRAPED, designation="Martyrs")
        self.make_context(old, "curated", 2025, active=True, thumbs_up=5, thumbs_down=1)
        new = self.make_feast(CURRENT)

        keeper = apply_group(CURRENT, [old, new], Feast, FeastContext)
        keeper.save()

        self.assertEqual(keeper.id, old.id)
        self.assertEqual(Feast.objects.filter(church=self.church).count(), 1)
        self.assertFalse(Feast.objects.filter(id=new.id).exists())
        self.assertEqual(Feast.objects.get(id=old.id).name, CURRENT)
        self.assertEqual(Feast.objects.get(id=old.id).designation, "Martyrs")

    def test_contexts_are_reparented_rather_than_cascaded_away(self):
        old = self.make_feast(SCRAPED)
        self.make_context(old, "older", 2024, active=True, thumbs_up=5, thumbs_down=1)
        new = self.make_feast(CURRENT)
        self.make_context(new, "newer", 2026, active=True, thumbs_up=3, thumbs_down=2)

        keeper = apply_group(CURRENT, [old, new], Feast, FeastContext)
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

        keeper = apply_group(CURRENT, [old, new], Feast, FeastContext)
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
        keeper = apply_group(CURRENT, rows, Feast, FeastContext)
        keeper.save()

        self.assertEqual(Feast.objects.filter(church=self.church).count(), 1)
        self.assertEqual(Feast.objects.get(pk=keeper.pk).name, CURRENT)
