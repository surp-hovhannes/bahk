from datetime import date, timedelta
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from events.models import Announcement
from hub.models import Church, Fast, FastParticipantMap, Day, DevotionalSet, Devotional
from hub.tasks.mapping_tasks import generate_participant_map
from learning_resources.models import Article, Recipe, Video


TEST_CACHES = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}


@override_settings(CACHES=TEST_CACHES)
class RegenerateCommandErrorTests(TestCase):
    def test_regenerate_feast_contexts_requires_target(self):
        with self.assertRaisesMessage(CommandError, "Please specify either --all"):
            call_command("regenerate_feast_contexts")

    def test_regenerate_feast_contexts_missing_feast_raises(self):
        with self.assertRaisesMessage(CommandError, "Feast with ID 999 not found"):
            call_command("regenerate_feast_contexts", feast_id=999)

    def test_regenerate_map_wait_failure_raises(self):
        church = Church.objects.create(name="Test Church")
        fast = Fast.objects.create(name="Test Fast", church=church)
        existing_map = FastParticipantMap.objects.create(
            fast=fast,
            map_file="fast_maps/existing.svg",
            participant_count=12,
        )
        task = Mock()
        task.id = "task-1"
        task.ready.return_value = True
        task.get.return_value = {"status": "error", "message": "render failed"}

        with patch(
            "hub.management.commands.regenerate_map.generate_participant_map.delay",
            return_value=task,
        ):
            with self.assertRaisesMessage(
                CommandError, "Map generation failed: render failed"
            ):
                call_command("regenerate_map", fast.id, wait=True)

        existing_map.refresh_from_db()
        self.assertEqual(existing_map.participant_count, 12)
        self.assertEqual(FastParticipantMap.objects.filter(fast=fast).count(), 1)

    @patch("hub.tasks.mapping_tasks.create_map", return_value=("fast_maps/new.svg", 4))
    def test_generate_participant_map_replaces_duplicate_maps_after_render(self, _):
        church = Church.objects.create(name="Test Church")
        fast = Fast.objects.create(name="Test Fast", church=church)
        older_map = FastParticipantMap.objects.create(
            fast=fast,
            map_file="fast_maps/old.svg",
            participant_count=12,
        )
        newer_map = FastParticipantMap.objects.create(
            fast=fast,
            map_file="fast_maps/newer.svg",
            participant_count=13,
        )

        result = generate_participant_map.run(fast.id)

        self.assertEqual(result["status"], "success")
        self.assertFalse(FastParticipantMap.objects.filter(pk=older_map.pk).exists())
        newer_map.refresh_from_db()
        self.assertEqual(newer_map.map_file.name, "fast_maps/new.svg")
        self.assertEqual(newer_map.participant_count, 4)
        self.assertEqual(FastParticipantMap.objects.filter(fast=fast).count(), 1)


@override_settings(CACHES=TEST_CACHES)
class SeedMultilingualDataTests(TestCase):
    def test_seed_multilingual_data_is_rerunnable(self):
        call_command("seed_multilingual_data")
        call_command("seed_multilingual_data")

        church = Church.objects.get(name="Armenian Apostolic Church")
        fast = Fast.objects.get(name="Great Lent", church=church)

        self.assertEqual(Fast.objects.filter(name="Great Lent", church=church).count(), 1)
        self.assertEqual(Day.objects.filter(fast=fast).count(), 1)
        self.assertEqual(DevotionalSet.objects.filter(fast=fast).count(), 1)
        self.assertEqual(Devotional.objects.filter(day__fast=fast).count(), 2)
        self.assertEqual(
            Video.objects.filter(category="devotional", language_code="en").count(),
            1,
        )
        self.assertEqual(
            Video.objects.filter(category="devotional", language_code="hy").count(),
            1,
        )
        self.assertEqual(Article.objects.filter(title="Fasting Basics").count(), 1)
        self.assertEqual(Recipe.objects.filter(title="Lentil Soup").count(), 1)
        self.assertEqual(
            Announcement.objects.filter(title="Welcome to Great Lent").count(),
            1,
        )

    def test_seed_multilingual_data_does_not_mutate_unrelated_rows(self):
        church, _ = Church.objects.get_or_create(name="Armenian Apostolic Church")
        same_name_date = date(2025, 4, 19)
        while Fast.objects.filter(
            church=church, culmination_feast_date=same_name_date
        ).exists() or same_name_date == date(2025, 4, 20):
            same_name_date += timedelta(days=1)
        other_fast = Fast.objects.create(
            name="Great Lent",
            church=church,
            year=2024,
            culmination_feast_date=same_name_date,
            description="Leave me alone.",
        )
        conflict_fast = (
            Fast.objects.filter(church=church, culmination_feast_date="2025-04-20")
            .exclude(name="Great Lent", year=2025)
            .first()
        )
        if conflict_fast is None:
            conflict_fast = Fast.objects.create(
                name="Other Seed Date Fast",
                church=church,
                year=2024,
                culmination_feast_date="2025-04-20",
                description="Keep my feast date.",
            )
        else:
            conflict_fast.description = "Keep my feast date."
            conflict_fast.save(update_fields=["description"])
        unrelated_video = Video.objects.create(
            category="devotional",
            language_code="en",
            title="Unrelated video",
            description="Not the seed video",
        )
        unrelated_article = Article.objects.create(
            title="Fasting Basics",
            body="Existing parish article",
        )

        call_command("seed_multilingual_data")

        other_fast.refresh_from_db()
        unrelated_video.refresh_from_db()
        unrelated_article.refresh_from_db()
        seed_fast = Fast.objects.get(name="Great Lent", church=church, year=2025)

        self.assertEqual(other_fast.description, "Leave me alone.")
        self.assertEqual(other_fast.culmination_feast_date, same_name_date)
        conflict_fast.refresh_from_db()
        self.assertEqual(conflict_fast.description, "Keep my feast date.")
        self.assertEqual(conflict_fast.culmination_feast_date.isoformat(), "2025-04-20")
        self.assertNotEqual(seed_fast.culmination_feast_date.isoformat(), "2025-04-20")
        self.assertEqual(unrelated_video.title, "Unrelated video")
        self.assertEqual(unrelated_video.description, "Not the seed video")
        self.assertEqual(unrelated_article.body, "Existing parish article")
        self.assertEqual(
            Video.objects.filter(
                category="devotional",
                language_code="en",
                description="Introduction to the fast",
            ).count(),
            1,
        )
        self.assertEqual(
            Article.objects.filter(
                title="Fasting Basics",
                body="Markdown: Fasting is a spiritual discipline...",
            ).count(),
            1,
        )
