"""Tests for the data migration that re-keys Feast onto (church, name).

Migrations are usually left untested, but this one merges production data that cannot be
regenerated: LLM-generated contexts, curated icon assignments, and user thumbs. If it drops a
context or loses a vote total, nothing errors -- the content is simply gone. So the merge is
exercised here against real rows, through the historical models the migration actually sees.

``migrator`` rolls the schema back to the migration before this one, builds rows in the *old*
shape (feasts hanging off Day), then rolls forward and asserts what survived.
"""
import datetime

from django.db.migrations.executor import MigrationExecutor
from django.db import connection
from django.test import TransactionTestCase


class RekeyFeastMigrationTests(TransactionTestCase):
    """Runs the real migration over rows built in the pre-migration shape."""

    migrate_from = ("hub", "0060_alter_feast_name")
    migrate_to = ("hub", "0061_rekey_feast_to_commemoration")

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([target])
        executor.loader.build_graph()
        return executor.loader.project_state([target]).apps

    def setUp(self):
        self.old_apps = self._migrate(self.migrate_from)

    def tearDown(self):
        # Leave the database on the latest schema for whatever runs next.
        self._migrate(self.migrate_to)

    def _seed(self):
        """Build the duplication the migration exists to collapse.

        One commemoration ("Feast of the Holy Cross") recorded on three dates, each with its own
        context; plus a second, distinct commemoration that must be left alone.
        """
        Church = self.old_apps.get_model("hub", "Church")
        Day = self.old_apps.get_model("hub", "Day")
        Feast = self.old_apps.get_model("hub", "Feast")
        FeastContext = self.old_apps.get_model("hub", "FeastContext")

        church = Church.objects.create(name="Test Church")
        other_church = Church.objects.create(name="Other Church")

        feasts = []
        for year in (2025, 2026, 2027):
            day = Day.objects.create(date=datetime.date(year, 9, 14), church=church)
            feasts.append(Feast.objects.create(day=day, name="Feast of the Holy Cross"))

        # Oldest row carries the icon-less, designation-less state; a later one was curated.
        feasts[1].designation = "Martyrs"
        feasts[1].save()

        contexts = [
            FeastContext.objects.create(
                feast=feasts[0], text="oldest", short_text="oldest",
                active=True, thumbs_up=5, thumbs_down=1),
            FeastContext.objects.create(
                feast=feasts[1], text="middle", short_text="middle",
                active=True, thumbs_up=3, thumbs_down=0),
            FeastContext.objects.create(
                feast=feasts[2], text="newest", short_text="newest",
                active=True, thumbs_up=4, thumbs_down=2),
        ]
        for offset, context in enumerate(contexts):
            FeastContext.objects.filter(pk=context.pk).update(
                time_of_generation=datetime.datetime(
                    2025 + offset, 9, 14, tzinfo=datetime.timezone.utc))

        # A distinct commemoration, and the same name in a different church: neither may merge.
        day = Day.objects.create(date=datetime.date(2026, 1, 6), church=church)
        Feast.objects.create(day=day, name="Theophany")
        other_day = Day.objects.create(date=datetime.date(2026, 9, 14), church=other_church)
        Feast.objects.create(day=other_day, name="Feast of the Holy Cross")

        return church, other_church

    def test_duplicates_collapse_and_enrichment_survives(self):
        church, other_church = self._seed()
        new_apps = self._migrate(self.migrate_to)
        Feast = new_apps.get_model("hub", "Feast")
        FeastContext = new_apps.get_model("hub", "FeastContext")

        cross = Feast.objects.get(church_id=church.id, name="Feast of the Holy Cross")

        # Three rows became one, and the distinct commemoration is untouched.
        self.assertEqual(Feast.objects.filter(church_id=church.id).count(), 2)
        self.assertTrue(
            Feast.objects.filter(church_id=church.id, name="Theophany").exists())

        # The curated designation was rescued off the row that carried it.
        self.assertEqual(cross.designation, "Martyrs")

        # Every context was reparented rather than cascaded away with its old row.
        contexts = FeastContext.objects.filter(feast_id=cross.id)
        self.assertEqual(contexts.count(), 3)

        # The newest active context survives, holding the group's whole feedback total.
        survivor = contexts.get(active=True)
        self.assertEqual(survivor.text, "newest")
        self.assertEqual((survivor.thumbs_up, survivor.thumbs_down), (12, 3))

        # The losers are retired, not deleted, so the merge can be inspected afterwards.
        retired = sorted(c.text for c in contexts.filter(active=False))
        self.assertEqual(retired, ["middle", "oldest"])

    def test_the_same_name_in_another_church_is_not_merged(self):
        church, other_church = self._seed()
        new_apps = self._migrate(self.migrate_to)
        Feast = new_apps.get_model("hub", "Feast")

        self.assertEqual(Feast.objects.filter(name="Feast of the Holy Cross").count(), 2)
        self.assertEqual(Feast.objects.filter(church_id=other_church.id).count(), 1)

    def test_every_row_keeps_the_church_it_had(self):
        church, other_church = self._seed()
        new_apps = self._migrate(self.migrate_to)
        Feast = new_apps.get_model("hub", "Feast")

        self.assertFalse(Feast.objects.filter(church_id__isnull=True).exists())
        self.assertEqual(
            Feast.objects.get(church_id=other_church.id).name, "Feast of the Holy Cross")
