"""Tests for the get_or_create_feast_for_date utility function."""
from datetime import date, timedelta
from unittest.mock import patch

from django.db.models.query import QuerySet
from django.test import TestCase

from hub.models import Church, Day, Feast
from hub.services.feast_service import FeastDataUnavailable
from hub.utils import _get_or_create_feast_for_observance, get_or_create_feast_for_date
from tests.fixtures.test_data import TestDataFactory


def _commemoration(observance_id, name_en, name_hy=None):
    """One entry of what ``get_feast_for_date`` returns."""
    return {
        "observance_id": observance_id,
        "name": name_en,
        "name_en": name_en,
        "name_hy": name_hy,
    }


class GetOrCreateFeastForDateTests(TestCase):
    """Tests for the get_or_create_feast_for_date utility function."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_create_feast_when_none_exists(self, mock_engine):
        """Test creating a feast when none exists."""
        mock_engine.return_value = [
            _commemoration("christmas", "Christmas", "Սուրբ Ծնունդ")]

        feasts, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        self.assertEqual(len(feasts), 1)
        self.assertEqual(status_dict["status"], "success")
        self.assertEqual(status_dict["created"], 1)
        self.assertEqual(feasts[0].name, "Christmas")
        self.assertEqual(feasts[0].name_hy, "Սուրբ Ծնունդ")
        self.assertEqual(feasts[0].observance_id, "christmas")

        # The feast belongs to the church, not to a day -- and resolving one does not mint a Day.
        self.assertEqual(feasts[0].church, self.church)
        self.assertFalse(Day.objects.filter(date=self.test_date, church=self.church).exists())

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_two_commemorations_get_a_row_each(self, mock_engine):
        """185 days in range name two commemorations, and each is its own feast.

        This is the whole point: one card per thing commemorated, rather than one card carrying a
        joined string.
        """
        mock_engine.return_value = [
            _commemoration("hermit_st_anton", "The Hermit St. Anton"),
            _commemoration("hermit_sts_tryphon", "The Hermit Sts. Tryphon and Barsauma"),
        ]

        feasts, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        self.assertEqual(len(feasts), 2)
        self.assertEqual(status_dict["created"], 2)
        self.assertEqual(
            [feast.observance_id for feast in feasts],
            ["hermit_st_anton", "hermit_sts_tryphon"])
        self.assertEqual(Feast.objects.filter(church=self.church).count(), 2)

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_day_with_no_commemoration_creates_nothing(self, mock_engine):
        """5,070 of the engine's 9,861 days commemorate nobody, and mint no rows.

        An empty list is a real answer, so it is reported as a skip with its own reason rather
        than as missing data.
        """
        mock_engine.return_value = []

        feasts, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        self.assertEqual(feasts, [])
        self.assertEqual(status_dict["status"], "skipped")
        self.assertEqual(status_dict["reason"], "no_commemorations")
        self.assertFalse(Feast.objects.filter(church=self.church).exists())

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_skip_when_feast_already_exists(self, mock_engine):
        """An already-complete commemoration is returned untouched.

        The engine is always consulted now, because the observance is what identifies the row --
        there is no way to know which feast a date maps to without asking. What is skipped is the
        write: the row exists and already carries its names, so nothing is saved.
        """
        existing_feast = Feast.objects.create(
            church=self.church, name="Existing Feast", observance_id="existing_feast")
        existing_feast.name_hy = "Existing Armenian"
        existing_feast.save(update_fields=['i18n'])

        mock_engine.return_value = [
            _commemoration("existing_feast", "Existing Feast", "Existing Armenian")]

        feasts, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        self.assertEqual(feasts, [existing_feast])
        self.assertEqual(status_dict["created"], 0)
        self.assertEqual(status_dict["refreshed"], 0)

    def test_skip_when_fast_associated_with_check_fast_true(self):
        """Test skipping feast lookup when Fast is associated and check_fast=True."""
        fast = TestDataFactory.create_fast(church=self.church, name="Lenten Fast")
        Day.objects.create(date=self.test_date, church=self.church, fast=fast)

        feasts, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        self.assertEqual(feasts, [])
        self.assertEqual(status_dict["status"], "skipped")
        self.assertEqual(status_dict["reason"], "fast_associated")
        self.assertEqual(status_dict["fast_name"], "Lenten Fast")

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_continue_when_fast_associated_with_check_fast_false(self, mock_engine):
        """Test continuing feast lookup when Fast is associated but check_fast=False."""
        fast = TestDataFactory.create_fast(church=self.church, name="Lenten Fast")
        Day.objects.create(date=self.test_date, church=self.church, fast=fast)

        mock_engine.return_value = [_commemoration("christmas", "Christmas")]

        feasts, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=False
        )

        self.assertEqual(len(feasts), 1)
        self.assertEqual(status_dict["status"], "success")
        mock_engine.assert_called_once()

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_skip_when_date_is_outside_the_engines_range(self, mock_engine):
        """``None`` now means only "outside the validated year window".

        That is a fact about the date rather than a failure, so it degrades to the same empty
        list as a day that simply commemorates nobody.
        """
        mock_engine.return_value = None

        feasts, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        self.assertEqual(feasts, [])
        self.assertEqual(status_dict["status"], "skipped")
        self.assertEqual(status_dict["reason"], "date_out_of_range")

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_unavailable_data_propagates_instead_of_becoming_an_empty_day(self, mock_engine):
        """A broken install must not be indistinguishable from "nothing to show today".

        An empty list is the commonest correct answer there is, so swallowing this here would
        serve a plausible-looking empty calendar with nothing anywhere going red.
        """
        mock_engine.side_effect = FeastDataUnavailable("catalog missing")

        with self.assertRaises(FeastDataUnavailable):
            get_or_create_feast_for_date(self.test_date, self.church, check_fast=True)

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_create_feast_with_english_only(self, mock_engine):
        """Test creating feast with only English name."""
        mock_engine.return_value = [_commemoration("christmas", "Christmas")]

        feasts, _ = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        self.assertEqual(feasts[0].name, "Christmas")
        self.assertIsNone(feasts[0].name_hy)

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_update_existing_feast_with_missing_translation(self, mock_engine):
        """Test updating existing feast with missing translation."""
        existing_feast = Feast.objects.create(
            church=self.church, name="Christmas", observance_id="christmas")

        mock_engine.return_value = [
            _commemoration("christmas", "Christmas", "Սուրբ Ծնունդ")]

        feasts, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        self.assertEqual(feasts, [existing_feast])
        self.assertEqual(status_dict["created"], 0)
        self.assertEqual(status_dict["refreshed"], 1)

        existing_feast.refresh_from_db()
        self.assertEqual(existing_feast.name_hy, "Սուրբ Ծնունդ")

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_overwrites_a_stored_translation_that_differs_from_the_engine(self, mock_engine):
        """The engine is the authority on both languages, so a stale hy name is replaced.

        Rows the retired sacredtradition.am scrape wrote took their Armenian from ``iL=3``, which
        is not a language code the source defines, so those values cannot be trusted; and the
        engine corrects its own translations across releases. Keeping whatever was stored first
        would freeze both mistakes.
        """
        existing_feast = Feast.objects.create(
            church=self.church, name="Christmas", observance_id="christmas")
        existing_feast.name_hy = "Existing Armenian"
        existing_feast.save(update_fields=['i18n'])

        mock_engine.return_value = [
            _commemoration("christmas", "Christmas", "Սուրբ Ծնունդ")]

        get_or_create_feast_for_date(self.test_date, self.church, check_fast=True)

        existing_feast.refresh_from_db()
        self.assertEqual(existing_feast.name_hy, "Սուրբ Ծնունդ")

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_an_unkeyed_row_is_adopted_rather_than_duplicated(self, mock_engine):
        """A row the backfill never reached must rejoin, not be shadowed by an empty twin.

        Migration 0067 keys every row it can resolve, but a seed, an admin, or a row it could not
        place carries no id -- and an id lookup would miss it, mint a duplicate, and take its
        designation, icon and contexts out of circulation without a word.
        """
        orphan = Feast.objects.create(
            church=self.church, name="Christmas", designation="Martyrs")
        self.assertIsNone(orphan.observance_id)

        mock_engine.return_value = [_commemoration("christmas", "Christmas")]

        feasts, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        self.assertEqual(status_dict["created"], 0)
        self.assertEqual(feasts[0].pk, orphan.pk)
        self.assertEqual(Feast.objects.filter(church=self.church).count(), 1)
        orphan.refresh_from_db()
        self.assertEqual(orphan.observance_id, "christmas")
        self.assertEqual(orphan.designation, "Martyrs")

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_adoption_does_not_steal_a_row_that_already_has_an_id(self, mock_engine):
        """Only unkeyed rows are adoptable; a keyed one is a different observance, not a match."""
        other = Feast.objects.create(
            church=self.church, name="Christmas", observance_id="some_other_observance")

        mock_engine.return_value = [_commemoration("christmas", "Christmas")]

        feasts, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        self.assertEqual(status_dict["created"], 1)
        self.assertNotEqual(feasts[0].pk, other.pk)
        other.refresh_from_db()
        self.assertEqual(other.observance_id, "some_other_observance")

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_the_row_is_found_by_its_observance_not_its_name(self, mock_engine):
        """The point of the re-key: a corrected display name updates the row, never orphans it."""
        existing = Feast.objects.create(
            church=self.church, name="Saints Cyricus and His Mother Julitta",
            observance_id="cyricus_and_his_mother_2", designation="Martyrs")

        mock_engine.return_value = [_commemoration(
            "cyricus_and_his_mother_2", "Sts. Cyricus and His Mother Julitta")]

        feasts, status_dict = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        self.assertEqual(status_dict["created"], 0)
        self.assertEqual(feasts[0].pk, existing.pk)
        self.assertEqual(Feast.objects.filter(church=self.church).count(), 1)
        feasts[0].refresh_from_db()
        self.assertEqual(feasts[0].name, "Sts. Cyricus and His Mother Julitta")
        self.assertEqual(feasts[0].designation, "Martyrs")

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_resolving_a_feast_does_not_mint_a_day(self, mock_engine):
        """Feasts do not hang off Day any more, so there is nothing to create one for."""
        mock_engine.return_value = [_commemoration("christmas", "Christmas")]

        self.assertFalse(Day.objects.filter(date=self.test_date, church=self.church).exists())

        feasts, _ = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True
        )

        self.assertFalse(Day.objects.filter(date=self.test_date, church=self.church).exists())
        self.assertEqual(len(feasts), 1)

    @patch('hub.services.feast_service.get_feast_for_date')
    def test_one_row_serves_every_recurrence(self, mock_engine):
        """The same commemoration on two dates resolves to a single row.

        This replaces a test that asserted the feast reused an existing Day. That is the whole
        point of the re-key: what used to be one row per occurrence is now one row, full stop.
        """
        mock_engine.return_value = [_commemoration("christmas", "Christmas")]

        first, first_status = get_or_create_feast_for_date(
            self.test_date, self.church, check_fast=True)
        second, second_status = get_or_create_feast_for_date(
            self.test_date + timedelta(days=365), self.church, check_fast=True)

        self.assertEqual(first_status["created"], 1)
        self.assertEqual(second_status["created"], 0)
        self.assertEqual(first[0].id, second[0].id)
        self.assertEqual(Feast.objects.filter(church=self.church).count(), 1)


class ConcurrentFirstSightingTests(TestCase):
    """Two workers resolving the same uncached date must not turn the race into a 500.

    ``(church, observance_id)`` is unique, so the loser's INSERT is rejected. It has to come back
    with the winner's row; the hand-rolled filter-then-save this replaced raised IntegrityError,
    which the view could only degrade on -- and a first sighting is exactly when a new
    commemoration goes live.
    """

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.commemoration = {
            "observance_id": "annunciation_to_the_virgin",
            "name": "Annunciation to the Virgin Mary",
            "name_en": "Annunciation to the Virgin Mary",
            "name_hy": "Աւետումն Ս. Աստուածածնի",
        }

    def test_the_loser_returns_the_winners_row(self):
        winner = Feast.objects.create(
            church=self.church,
            observance_id="annunciation_to_the_virgin",
            name="Annunciation to the Virgin Mary",
        )

        # The interleave: the winner has committed, but every read this worker makes ran before it
        # did, so the row is invisible until its own INSERT is rejected by the unique constraint.
        real_get = QuerySet.get
        blinded = []

        def blind(self, *args, **kwargs):
            if self.model is Feast and not blinded:
                blinded.append(True)
                raise Feast.DoesNotExist
            return real_get(self, *args, **kwargs)

        with patch.object(Feast.objects, "filter", return_value=Feast.objects.none()), \
                patch.object(QuerySet, "get", blind):
            feast, created, _ = _get_or_create_feast_for_observance(
                self.commemoration, self.church)

        self.assertTrue(blinded, "the race was never simulated")
        self.assertEqual(feast.pk, winner.pk)
        self.assertFalse(created)
        self.assertEqual(Feast.objects.count(), 1)
