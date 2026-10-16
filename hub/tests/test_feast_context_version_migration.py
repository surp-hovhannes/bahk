"""Data-preservation tests for append-only FeastContext version history."""

import datetime

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class FeastContextVersionMigrationTests(TransactionTestCase):
    migrate_from = ("hub", "0068_clear_fast_designation_on_commemorations")
    migrate_to = ("hub", "0069_feast_context_version_history")

    def _migrate(self, targets):
        if isinstance(targets, tuple):
            targets = [targets]
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        executor.loader.build_graph()
        return executor.loader.project_state(targets).apps

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        self.addCleanup(self._migrate, executor.loader.graph.leaf_nodes())
        self.old_apps = self._migrate(self.migrate_from)

    def _seed(self):
        Church = self.old_apps.get_model("hub", "Church")
        Feast = self.old_apps.get_model("hub", "Feast")
        FeastContext = self.old_apps.get_model("hub", "FeastContext")
        LLMPrompt = self.old_apps.get_model("hub", "LLMPrompt")

        church = Church.objects.create(name="Migration Test Church")
        prompt = LLMPrompt.objects.create(
            model="gpt-4o-mini",
            role="Historian",
            prompt="Explain this feast",
            applies_to="feasts",
        )
        first_feast = Feast.objects.create(
            church=church,
            observance_id="migration_test_first",
            name="First Feast",
        )
        second_feast = Feast.objects.create(
            church=church,
            observance_id="migration_test_second",
            name="Second Feast",
        )

        rows = [
            FeastContext.objects.create(
                feast=first_feast,
                prompt=prompt,
                text="latest body",
                short_text="latest summary",
                i18n={
                    "text_hy": "latest Armenian body",
                    "short_text_hy": "latest Armenian summary",
                },
                active=True,
                thumbs_up=7,
                thumbs_down=3,
            ),
            FeastContext.objects.create(
                feast=first_feast,
                prompt=prompt,
                text="earliest body",
                short_text="earliest summary",
                i18n={
                    "text_hy": "earliest Armenian body",
                    "short_text_hy": "earliest Armenian summary",
                },
                active=False,
                thumbs_up=2,
                thumbs_down=1,
            ),
            FeastContext.objects.create(
                feast=first_feast,
                prompt=None,
                text="same-time body",
                short_text="same-time summary",
                i18n={
                    "text_hy": "same-time Armenian body",
                    "short_text_hy": "same-time Armenian summary",
                },
                active=False,
                thumbs_up=11,
                thumbs_down=5,
            ),
            FeastContext.objects.create(
                feast=second_feast,
                prompt=prompt,
                text="other feast body",
                short_text="other feast summary",
                i18n={
                    "text_hy": "other Armenian body",
                    "short_text_hy": "other Armenian summary",
                },
                active=True,
                thumbs_up=13,
                thumbs_down=8,
            ),
        ]

        early = datetime.datetime(2024, 1, 1, 8, tzinfo=datetime.timezone.utc)
        late = datetime.datetime(2025, 1, 1, 8, tzinfo=datetime.timezone.utc)
        FeastContext.objects.filter(pk=rows[0].pk).update(time_of_generation=late)
        FeastContext.objects.filter(pk__in=[rows[1].pk, rows[2].pk]).update(
            time_of_generation=early
        )
        FeastContext.objects.filter(pk=rows[3].pk).update(time_of_generation=late)

        preserved = {
            row.pk: {
                "feast_id": row.feast_id,
                "prompt_id": row.prompt_id,
                "text": row.text,
                "short_text": row.short_text,
                "i18n": row.i18n,
                "active": row.active,
                "thumbs_up": row.thumbs_up,
                "thumbs_down": row.thumbs_down,
                "time_of_generation": early if row.pk in {rows[1].pk, rows[2].pk} else late,
            }
            for row in rows
        }
        return rows, preserved

    def test_backfill_versions_are_per_feast_and_ordered_by_timestamp_then_pk(self):
        rows, _ = self._seed()
        new_apps = self._migrate(self.migrate_to)
        FeastContext = new_apps.get_model("hub", "FeastContext")

        versions = dict(
            FeastContext.objects.values_list("pk", "version")
        )
        self.assertEqual(versions[rows[1].pk], 1)
        self.assertEqual(versions[rows[2].pk], 2)
        self.assertEqual(versions[rows[0].pk], 3)
        self.assertEqual(versions[rows[3].pk], 1)

    def test_backfill_preserves_content_translations_state_feedback_and_prompt(self):
        _, preserved = self._seed()
        new_apps = self._migrate(self.migrate_to)
        FeastContext = new_apps.get_model("hub", "FeastContext")

        for context in FeastContext.objects.order_by("pk"):
            expected = preserved[context.pk]
            for field, value in expected.items():
                self.assertEqual(getattr(context, field), value)
            self.assertEqual(context.operation, "generated")
            self.assertIsNone(context.created_by_id)
            self.assertEqual(context.additional_instructions, "")
            self.assertIsNone(context.restored_from_id)

    def test_constraint_enforces_positive_versions(self):
        rows, _ = self._seed()
        new_apps = self._migrate(self.migrate_to)
        FeastContext = new_apps.get_model("hub", "FeastContext")
        existing = FeastContext.objects.get(pk=rows[1].pk)

        with self.assertRaises(IntegrityError), transaction.atomic():
            FeastContext.objects.create(
                feast_id=existing.feast_id,
                text="zero version",
                short_text="zero version",
                version=0,
            )

    def test_reverse_removes_only_new_schema_and_keeps_existing_rows(self):
        _, preserved = self._seed()
        self._migrate(self.migrate_to)
        old_apps = self._migrate(self.migrate_from)
        FeastContext = old_apps.get_model("hub", "FeastContext")

        field_names = {field.name for field in FeastContext._meta.get_fields()}
        self.assertTrue(
            {
                "version",
                "operation",
                "created_by",
                "additional_instructions",
                "restored_from",
            }.isdisjoint(field_names)
        )
        self.assertEqual(FeastContext.objects.count(), len(preserved))
        for context in FeastContext.objects.order_by("pk"):
            expected = preserved[context.pk]
            for field, value in expected.items():
                self.assertEqual(getattr(context, field), value)
