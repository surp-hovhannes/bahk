"""Small, offline admin fixtures. Intended only for disposable visual QA databases."""

from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction

from events.models import Event, EventType
from hub.models import (
    Church,
    Day,
    Devotional,
    DevotionalSet,
    Fast,
    FastParticipation,
    Feast,
    FeastContext,
    LLMPrompt,
    PassageText,
    Profile,
    Reading,
    ReadingContext,
)
from learning_resources.models import Video

ANCHOR = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


def upsert(model, *, key, **values):
    """Natural-key upsert without save hooks, signals, engines or file storage.

    bulk_create and QuerySet.update deliberately replace get/update_or_create:
    those call save(), which schedules Celery, geocoding and content generation.
    No signal receivers are disconnected, even temporarily. The command is a
    single transaction and is intended to run serially in a disposable database.
    Normalize auto timestamps after insertion so repeated runs have identical data.
    """
    row = model.objects.filter(**key).first()
    if row is None:
        model.objects.bulk_create([model(**key, **values)])
        row = model.objects.get(**key)
    for field in model._meta.concrete_fields:
        if getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
            values[field.name] = ANCHOR
    model.objects.filter(pk=row.pk).update(**values)
    row.refresh_from_db()
    return row


class Command(BaseCommand):
    help = "Seed deterministic offline admin visual QA data (disposable databases only)."

    @transaction.atomic
    def handle(self, *args, **options):
        church = upsert(Church, key={"name": "Visual QA Church"})
        fast = upsert(
            Fast,
            key={"church": church, "name": "Visual QA Autumn Fast", "year": 2026},
            description="A week of prayer, reflection and community.",
            culmination_feast="Visual QA Celebration",
            culmination_feast_date=(ANCHOR + timedelta(days=6)).date(),
        )
        days = [
            upsert(Day, key={"church": church, "date": (ANCHOR + timedelta(days=i)).date()}, fast=fast)
            for i in range(7)
        ]
        upsert(
            DevotionalSet,
            key={"fast": fast, "title": "Visual QA Daily Reflections"},
            description="Seven days of reflection.",
        )
        video = upsert(
            Video,
            key={"title": "Visual QA Reflection"},
            description="Offline fixture; no media uploaded.",
            category="devotional",
            video="",
        )
        devotional = upsert(
            Devotional,
            key={"day": days[0], "order": 1, "language_code": "en"},
            video=video,
            description="Begin the fast with gratitude.",
        )
        reading = upsert(
            Reading,
            key={
                "day": days[0],
                "book": "John",
                "start_chapter": 1,
                "start_verse": 1,
                "end_chapter": 1,
                "end_verse": 5,
            },
            sequence=1,
            passage_key="JHN.1.1-1.5",
        )
        upsert(
            PassageText,
            key={"passage_key": reading.passage_key, "language": "en"},
            text="Visual QA placeholder passage; no Bible service was contacted.",
            version="QA",
        )
        prompts = [
            upsert(
                LLMPrompt,
                key={"role": f"Visual QA reading prompt {i}"},
                model="gpt-4o-mini",
                prompt="Offline visual fixture only.",
                applies_to="readings",
                active=False,
            )
            for i in (1, 2)
        ]
        upsert(
            ReadingContext,
            key={"reading": reading, "prompt": prompts[0]},
            text="An offline reflection on light and hope.",
            active=True,
        )
        feast = upsert(
            Feast,
            key={"church": church, "observance_id": "visual-qa-celebration"},
            name="Visual QA Celebration",
            designation=Feast.Designation.SUNDAYS_DOMINICAL,
        )
        upsert(
            FeastContext,
            key={"feast": feast, "version": 1},
            text="A celebration of faith and community.",
            short_text="Gather in gratitude.",
            active=True,
        )

        for i, name in enumerate(("Anna", "David", "Mariam")):
            user = upsert(
                get_user_model(),
                key={"username": f"visual-qa-{name.lower()}"},
                first_name=name,
                email=f"visual-qa-{name.lower()}@example.test",
                password="!",
                is_staff=False,
                is_superuser=False,
                is_active=True,
                date_joined=ANCHOR - timedelta(days=i + 1),
            )
            profile = upsert(
                Profile,
                key={"user": user},
                name=name,
                church=church,
                location="Visual QA Parish",
                receive_promotional_emails=False,
                receive_upcoming_fast_push_notifications=False,
                receive_ongoing_fast_push_notifications=False,
            )
            upsert(Profile.fasts.through, key={"profile": profile, "fast": fast})
            upsert(
                FastParticipation,
                key={"profile": profile, "fast": fast, "left_at": None},
                joined_at=ANCHOR - timedelta(days=1),
                fast_original_id=fast.pk,
                fast_name=fast.name,
                fast_year=2026,
                fast_end_date=days[-1].date,
            )
            specs = [
                (EventType.USER_ACCOUNT_CREATED, None, {}),
                (EventType.USER_JOINED_FAST, fast, {}),
                (EventType.APP_OPEN, None, {"platform": "ios", "app_version": "1.0.0"}),
                (EventType.SCREEN_VIEW, None, {"screen": "fast_detail", "platform": "ios"}),
                (EventType.SESSION_START, None, {"session_id": f"visual-qa-{i}", "platform": "ios"}),
                (EventType.SESSION_END, None, {"session_id": f"visual-qa-{i}", "duration_seconds": 120}),
                (EventType.DEVOTIONAL_VIEWED, devotional, {"devotional_id": devotional.pk}),
                (EventType.CHECKLIST_USED, fast, {"items": ["prayer", "fasting"]}),
            ]
            for offset, (code, target, data) in enumerate(specs):
                event_type = upsert(
                    EventType,
                    key={"code": code},
                    name=dict(EventType.CORE_EVENT_TYPES)[code],
                    category=EventType._get_category_for_code(code),
                    requires_target=EventType._requires_target_for_code(code),
                )
                content_type = ContentType.objects.get_for_model(target) if target else None
                upsert(
                    Event,
                    key={"user": user, "title": f"Visual QA {name}: {code}"},
                    event_type=event_type,
                    timestamp=ANCHOR - timedelta(hours=i, minutes=offset),
                    content_type=content_type,
                    object_id=target.pk if target else None,
                    data=data,
                )
        self.stdout.write(self.style.SUCCESS("Visual QA data ready (anchor: 2026-10-19)."))
