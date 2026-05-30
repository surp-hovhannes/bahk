from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from events.models import Announcement
from hub.models import Church, Fast, FastParticipantMap, Day, DevotionalSet, Devotional
from learning_resources.models import Article, Recipe, Video


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
