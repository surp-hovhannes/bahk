"""Tests for the migrations that re-key Feast onto the engine's observance ids.

Migrations are usually left untested, but this one rewrites production data that cannot be
regenerated and merges rows to do it: LLM-generated contexts, curated icon assignments, user
thumbs. If it strands a context or picks the wrong survivor, nothing errors -- the content is
simply gone. So it is exercised against real rows, through the historical models the migration
actually sees.

``migrator`` rolls the schema back to before the re-key, builds rows in the state the name key
left behind, then rolls forward and asserts what survived.
"""
import datetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

SCRAPED = "Saints Peter the Patriarch, Blaise the Bishop and Absalom the Deacon"
CURRENT = "Sts. Peter the Patriarch, Blaise the Bishop and Absalom the Deacon"
KEY = "peter_the_patriarch_blaise"


class ObservanceKeyMigrationTests(TransactionTestCase):
    """Runs the real migrations over rows built in the pre-re-key shape."""

    migrate_from = ("hub", "0065_remap_feast_names_to_engine")
    migrate_data_to = ("hub", "0067_backfill_feast_observance_keys")
    migrate_to = ("hub", "0068_finalize_feast_observance_key")

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
        """Two spellings of one observance, which the name key could not tell were the same.

        0065 merged rows whose *current* names matched. It could not merge these: one carries a
        name the engine emits today and the other a name it emitted a release ago, and by name
        alone they are two commemorations.
        """
        Church = self.old_apps.get_model("hub", "Church")
        Feast = self.old_apps.get_model("hub", "Feast")
        FeastContext = self.old_apps.get_model("hub", "FeastContext")

        church = Church.objects.create(name="Test Church")
        other_church = Church.objects.create(name="Other Church")

        stale = Feast.objects.create(
            church=church, name=SCRAPED, designation="Martyrs",
            sample_date=datetime.date(2001, 1, 16))
        FeastContext.objects.create(
            feast=stale, text="curated", short_text="curated",
            active=True, thumbs_up=7, thumbs_down=1)
        current = Feast.objects.create(
            church=church, name=CURRENT, sample_date=datetime.date(2001, 1, 16))
        FeastContext.objects.create(
            feast=current, text="regenerated", short_text="regenerated",
            active=True, thumbs_up=1, thumbs_down=0)
        for offset, context in enumerate(FeastContext.objects.order_by("id")):
            FeastContext.objects.filter(pk=context.pk).update(
                time_of_generation=datetime.datetime(
                    2025 + offset, 1, 16, tzinfo=datetime.timezone.utc))

        Feast.objects.create(church=church, name="Not A Commemoration Anyone Publishes")
        Feast.objects.create(church=other_church, name=SCRAPED)

        return church, other_church

    def test_both_spellings_collapse_onto_one_observance(self):
        church, _ = self._seed()
        new_apps = self._migrate(self.migrate_to)
        Feast = new_apps.get_model("hub", "Feast")

        keyed = Feast.objects.filter(church_id=church.id, observance_key=KEY)
        self.assertEqual(keyed.count(), 1)
        survivor = keyed.get()
        self.assertEqual(survivor.name, CURRENT)
        self.assertEqual(survivor.designation, "Martyrs")

    def test_every_context_is_reparented_and_the_feedback_total_is_kept(self):
        church, _ = self._seed()
        new_apps = self._migrate(self.migrate_to)
        Feast = new_apps.get_model("hub", "Feast")
        FeastContext = new_apps.get_model("hub", "FeastContext")

        survivor = Feast.objects.get(church_id=church.id, observance_key=KEY)
        contexts = FeastContext.objects.filter(feast_id=survivor.id)
        self.assertEqual(contexts.count(), 2)

        active = contexts.get(active=True)
        self.assertEqual(active.text, "regenerated")
        self.assertEqual((active.thumbs_up, active.thumbs_down), (8, 1))
        self.assertEqual(contexts.get(active=False).text, "curated")

    def test_a_row_nothing_resolves_keeps_a_null_key_rather_than_being_deleted(self):
        church, _ = self._seed()
        new_apps = self._migrate(self.migrate_to)
        Feast = new_apps.get_model("hub", "Feast")

        orphan = Feast.objects.get(
            church_id=church.id, name="Not A Commemoration Anyone Publishes")
        self.assertIsNone(orphan.observance_key)

    def test_several_unresolved_rows_can_coexist_under_the_new_constraint(self):
        """The constraint is partial, so NULL keys do not collide with each other."""
        church, _ = self._seed()
        new_apps = self._migrate(self.migrate_to)
        Feast = new_apps.get_model("hub", "Feast")

        Feast.objects.create(church_id=church.id, name="Another Unresolvable Name")
        self.assertEqual(
            Feast.objects.filter(church_id=church.id, observance_key__isnull=True).count(), 2)

    def test_each_church_is_re_keyed_on_its_own(self):
        church, other_church = self._seed()
        new_apps = self._migrate(self.migrate_to)
        Feast = new_apps.get_model("hub", "Feast")

        self.assertEqual(Feast.objects.filter(church_id=other_church.id).count(), 1)
        self.assertEqual(Feast.objects.get(church_id=other_church.id).observance_key, KEY)
        # Same observance, two churches: the constraint is per church, so both survive.
        self.assertEqual(Feast.objects.filter(observance_key=KEY).count(), 2)

    def test_running_forward_twice_changes_nothing(self):
        """Idempotent, so a re-run after a partial deploy is safe."""
        church, _ = self._seed()
        self._migrate(self.migrate_to)
        self._migrate(self.migrate_from)
        new_apps = self._migrate(self.migrate_to)
        Feast = new_apps.get_model("hub", "Feast")

        self.assertEqual(
            Feast.objects.filter(church_id=church.id, observance_key=KEY).count(), 1)
        self.assertEqual(Feast.objects.filter(church_id=church.id).count(), 2)

    def test_the_name_constraint_is_gone_before_the_data_migration_runs(self):
        """Three observances share the name "Fast day"; the old constraint forbade that."""
        church, _ = self._seed()
        apps = self._migrate(self.migrate_data_to)
        Feast = apps.get_model("hub", "Feast")

        Feast.objects.create(church_id=church.id, name="Fast day", observance_key="fast_day")
        Feast.objects.create(
            church_id=church.id, name="Fast day", observance_key="illuminator_fast_day_3")
        self.assertEqual(Feast.objects.filter(church_id=church.id, name="Fast day").count(), 2)
