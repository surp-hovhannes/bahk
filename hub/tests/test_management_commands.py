from datetime import date, timedelta
from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from events.models import Announcement
from hub.models import (
    Church,
    Fast,
    FastParticipantMap,
    Day,
    DevotionalSet,
    Devotional,
    Feast,
    FeastContext,
    LLMPrompt,
    Reading,
    ReadingContext,
)
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


@override_settings(CACHES=TEST_CACHES)
class AuditThumbsCommandTests(TestCase):
    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2026, 1, 15), church=self.church)
        self.reading = Reading.objects.create(
            day=self.day,
            book="John",
            start_chapter=3,
            start_verse=16,
            end_chapter=3,
            end_verse=18,
        )
        self.feast = Feast.objects.create(church=self.day.church, name="Theophany")
        self.reading_prompt = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            role="Reading role",
            prompt="Reading prompt",
            applies_to="readings",
        )
        self.feast_prompt = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            role="Feast role",
            prompt="Feast prompt",
            applies_to="feasts",
        )

    def test_audit_thumbs_groups_totals_with_default_since(self):
        ReadingContext.objects.create(
            reading=self.reading,
            text="Reading context",
            prompt=self.reading_prompt,
            thumbs_up=2,
            thumbs_down=1,
        )
        ReadingContext.objects.create(
            reading=self.reading,
            text="Reading context without prompt",
            thumbs_up=1,
            thumbs_down=3,
        )
        FeastContext.objects.create(
            feast=self.feast,
            text="Feast context",
            short_text="Short",
            prompt=self.feast_prompt,
            thumbs_up=4,
            thumbs_down=2,
        )

        output = StringIO()
        call_command("audit_thumbs", stdout=output)

        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "applies_to contexts thumbs_up thumbs_down",
                "feasts 1 4 2",
                "readings 2 3 4",
            ],
        )

    def test_audit_thumbs_explicit_since_excludes_old_contexts(self):
        old_context = ReadingContext.objects.create(
            reading=self.reading,
            text="Old reading context",
            prompt=self.reading_prompt,
            thumbs_up=20,
            thumbs_down=10,
        )
        ReadingContext.objects.filter(pk=old_context.pk).update(
            time_of_generation=timezone.now() - timedelta(days=31)
        )
        ReadingContext.objects.create(
            reading=self.reading,
            text="Recent reading context",
            prompt=self.reading_prompt,
            thumbs_up=2,
            thumbs_down=1,
        )

        output = StringIO()
        call_command("audit_thumbs", "--since=30d", stdout=output)

        self.assertIn("readings 1 2 1", output.getvalue().splitlines())
        self.assertNotIn("readings 2 22 11", output.getvalue().splitlines())

    def test_audit_thumbs_csv_output(self):
        FeastContext.objects.create(
            feast=self.feast,
            text="Feast context without prompt",
            short_text="Short",
            thumbs_up=5,
            thumbs_down=6,
        )

        output = StringIO()
        call_command("audit_thumbs", "--csv", stdout=output)

        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "applies_to,contexts,thumbs_up,thumbs_down",
                "feasts,1,5,6",
            ],
        )

    def test_audit_thumbs_rejects_invalid_since(self):
        with self.assertRaisesMessage(
            CommandError, "--since must be a day duration like 30d"
        ):
            call_command("audit_thumbs", "--since=30")

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
