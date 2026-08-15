"""Tests for the data migration that moves feast names onto engine 1.3.0's.

Migrations are usually left untested, but this one rewrites production data that cannot be
regenerated -- LLM-generated contexts, curated icon assignments, user thumbs -- and it merges rows
to do it.  If it strands a context or picks the wrong survivor, nothing errors; the content is
simply gone.  So it is exercised here against real rows, through the historical models the
migration actually sees.

``migrator`` rolls the schema back to before the rename, builds rows in the state the upgrade
leaves behind, then rolls forward and asserts what survived.
"""
import datetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

SCRAPED = "Saints Peter the Patriarch, Blaise the Bishop and Absalom the Deacon"
CURRENT = "Sts. Peter the Patriarch, Blaise the Bishop and Absalom the Deacon"


class RemapFeastMigrationTests(TransactionTestCase):
    """Runs the real migration over rows built in the pre-remap shape."""

    migrate_from = ("hub", "0064_feast_sample_date")
    migrate_to = ("hub", "0065_remap_feast_names_to_engine")

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
        """Build what an engine upgrade leaves behind.

        The scrape-era row holds everything worth keeping and is unreachable; the row a date
        lookup minted after the upgrade holds the current name and nothing else.
        """
        Church = self.old_apps.get_model("hub", "Church")
        Feast = self.old_apps.get_model("hub", "Feast")
        FeastContext = self.old_apps.get_model("hub", "FeastContext")

        church = Church.objects.create(name="Test Church")
        other_church = Church.objects.create(name="Other Church")

        stale = Feast.objects.create(church=church, name=SCRAPED, designation="Martyrs")
        FeastContext.objects.create(
            feast=stale, text="curated", short_text="curated",
            active=True, thumbs_up=7, thumbs_down=1)
        displaced = Feast.objects.create(church=church, name=CURRENT)
        FeastContext.objects.create(
            feast=displaced, text="regenerated", short_text="regenerated",
            active=True, thumbs_up=1, thumbs_down=0)
        for offset, context in enumerate(FeastContext.objects.order_by("id")):
            FeastContext.objects.filter(pk=context.pk).update(
                time_of_generation=datetime.datetime(
                    2025 + offset, 1, 16, tzinfo=datetime.timezone.utc))

        # A name nothing can resolve, and the same stale name in another church.
        Feast.objects.create(church=church, name="Not A Commemoration Anyone Publishes")
        Feast.objects.create(church=other_church, name=SCRAPED)

        return church, other_church

    def test_the_stale_row_survives_the_merge_carrying_the_current_name(self):
        church, _ = self._seed()
        new_apps = self._migrate(self.migrate_to)
        Feast = new_apps.get_model("hub", "Feast")

        survivors = Feast.objects.filter(church_id=church.id, name=CURRENT)
        self.assertEqual(survivors.count(), 1)

        # The curated designation came off the row that carried it, not the empty one.
        self.assertEqual(survivors.get().designation, "Martyrs")
        self.assertFalse(Feast.objects.filter(name=SCRAPED, church_id=church.id).exists())

    def test_every_context_is_reparented_and_the_feedback_total_is_kept(self):
        church, _ = self._seed()
        new_apps = self._migrate(self.migrate_to)
        Feast = new_apps.get_model("hub", "Feast")
        FeastContext = new_apps.get_model("hub", "FeastContext")

        survivor = Feast.objects.get(church_id=church.id, name=CURRENT)
        contexts = FeastContext.objects.filter(feast_id=survivor.id)
        self.assertEqual(contexts.count(), 2)

        active = contexts.get(active=True)
        self.assertEqual(active.text, "regenerated")
        self.assertEqual((active.thumbs_up, active.thumbs_down), (8, 1))
        self.assertEqual(contexts.get(active=False).text, "curated")

    def test_the_survivor_records_a_date_and_an_armenian_name(self):
        """Without a date the row cannot follow the *next* rename, which is the whole point."""
        church, _ = self._seed()
        new_apps = self._migrate(self.migrate_to)
        Feast = new_apps.get_model("hub", "Feast")

        survivor = Feast.objects.get(church_id=church.id, name=CURRENT)
        self.assertEqual(survivor.sample_date, datetime.date(2001, 1, 16))
        self.assertTrue((survivor.i18n or {}).get("name_hy"))

    def test_a_name_nothing_resolves_is_left_alone(self):
        church, _ = self._seed()
        new_apps = self._migrate(self.migrate_to)
        Feast = new_apps.get_model("hub", "Feast")

        self.assertTrue(Feast.objects.filter(
            church_id=church.id, name="Not A Commemoration Anyone Publishes").exists())

    def test_each_church_is_remapped_on_its_own(self):
        church, other_church = self._seed()
        new_apps = self._migrate(self.migrate_to)
        Feast = new_apps.get_model("hub", "Feast")

        # The other church had no colliding row, so its stale name is renamed rather than merged.
        self.assertEqual(Feast.objects.filter(church_id=other_church.id).count(), 1)
        self.assertEqual(Feast.objects.get(church_id=other_church.id).name, CURRENT)
        self.assertEqual(Feast.objects.filter(church_id=church.id).count(), 2)

    def test_running_forward_twice_changes_nothing(self):
        """The migration is idempotent, so a re-run after a partial deploy is safe."""
        church, _ = self._seed()
        self._migrate(self.migrate_to)
        self._migrate(self.migrate_from)
        new_apps = self._migrate(self.migrate_to)
        Feast = new_apps.get_model("hub", "Feast")

        self.assertEqual(Feast.objects.filter(church_id=church.id, name=CURRENT).count(), 1)
        self.assertEqual(Feast.objects.filter(church_id=church.id).count(), 2)
