"""Staff API and append-only service coverage for FeastContext history."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from hub.models import Church, Feast, FeastContext, LLMPrompt
from hub.services.feast_contexts import (
    FeastContextVersionInput,
    append_feast_context_version,
    feast_context_regeneration_lock_key,
    feast_context_task_status_key,
    set_feast_context_task_status,
)
from hub.tasks import generate_feast_context_task

DUMMY_CACHE = {
    "default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}
}
LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "feast-context-staff-api",
    }
}


@override_settings(
    CACHES=DUMMY_CACHE,
    MODELTRANS_AVAILABLE_LANGUAGES=["en", "hy"],
)
class FeastContextStaffAPITests(APITestCase):
    def setUp(self):
        self.church = Church.objects.create(name="Context Staff API Church")
        with patch("hub.signals.match_icon_to_feast_task.delay"), patch(
            "hub.signals.determine_feast_designation_task.delay"
        ):
            self.feast = Feast.objects.create(
                church=self.church,
                name="Context Staff API Feast",
                designation=Feast.Designation.SUNDAYS_DOMINICAL,
            )
        users = get_user_model()
        self.staff = users.objects.create_user(
            username="context-staff", is_staff=True
        )
        self.user = users.objects.create_user(username="context-user")
        self.prompt = LLMPrompt.objects.create(
            model="gpt-4o-mini",
            role="Context role",
            prompt="Context prompt",
            applies_to="feasts",
            active=True,
        )
        self.context = FeastContext.objects.create(
            feast=self.feast,
            version=1,
            text="Original English body",
            short_text="Original English summary",
            prompt=self.prompt,
            thumbs_up=7,
            thumbs_down=4,
        )
        self.context.text_hy = "Original Armenian body"
        self.context.short_text_hy = "Original Armenian summary"
        self.context.save()
        self.detail_url = reverse("feast-context-detail", args=[self.feast.id])

    def test_all_management_routes_require_staff(self):
        routes = [
            ("get", self.detail_url, None),
            ("patch", self.detail_url, {"language": "en", "text": "x", "short_text": "y"}),
            ("get", reverse("feast-context-history", args=[self.feast.id]), None),
            ("post", reverse("feast-context-regenerate", args=[self.feast.id]), {}),
            ("post", reverse("feast-context-restore", args=[self.feast.id]), {"version": 1}),
        ]
        for method, url, body in routes:
            with self.subTest(url=url):
                response = getattr(self.client, method)(url, body, format="json")
                self.assertIn(
                    response.status_code,
                    {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN},
                )
                self.client.force_login(self.user)
                response = getattr(self.client, method)(url, body, format="json")
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                self.client.logout()
        self.assertEqual(FeastContext.objects.count(), 1)

    def test_active_read_returns_metadata_bodies_and_languages(self):
        self.client.force_login(self.staff)

        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["version"], 1)
        self.assertEqual(response.data["operation"], FeastContext.Operation.GENERATED)
        self.assertEqual(response.data["prompt"]["id"], self.prompt.id)
        self.assertEqual(
            response.data["languages"]["hy"]["text"], "Original Armenian body"
        )
        self.assertEqual(response.data["thumbs_up"], 7)

    def test_manual_edit_appends_carries_other_languages_and_resets_feedback(self):
        self.client.force_login(self.staff)

        response = self.client.patch(
            self.detail_url,
            {
                "language": "hy",
                "text": "Edited Armenian body",
                "short_text": "Edited Armenian summary",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(FeastContext.objects.count(), 2)
        old = FeastContext.objects.get(pk=self.context.pk)
        new = self.feast.active_context
        self.assertFalse(old.active)
        self.assertEqual(old.text_hy, "Original Armenian body")
        self.assertEqual(new.version, 2)
        self.assertEqual(new.operation, FeastContext.Operation.MANUAL_EDIT)
        self.assertEqual(new.created_by, self.staff)
        self.assertEqual(new.prompt, self.prompt)
        self.assertEqual(new.text, "Original English body")
        self.assertEqual(new.short_text, "Original English summary")
        self.assertEqual(new.text_hy, "Edited Armenian body")
        self.assertEqual(new.short_text_hy, "Edited Armenian summary")
        self.assertEqual((new.thumbs_up, new.thumbs_down), (0, 0))

    def test_manual_edit_rejects_blank_unknown_language_and_unknown_fields(self):
        self.client.force_login(self.staff)
        payloads = [
            {"language": "fr", "text": "Body", "short_text": "Summary"},
            {"language": "en", "text": " ", "short_text": "Summary"},
            {"language": "en", "text": "Body", "short_text": ""},
            {"language": "en", "text": "Body", "short_text": "Summary", "version": 9},
            {},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.client.patch(self.detail_url, payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(FeastContext.objects.count(), 1)

    def test_history_is_newest_first_bounded_and_text_is_opt_in(self):
        self.client.force_login(self.staff)
        for number in range(2, 5):
            append_feast_context_version(
                self.feast.id,
                values=FeastContextVersionInput(
                    operation=FeastContext.Operation.MANUAL_EDIT,
                    actor_id=self.staff.id,
                ),
                language_updates={
                    "en": {"text": f"Body {number}", "short_text": f"Summary {number}"}
                },
            )

        response = self.client.get(
            reverse("feast-context-history", args=[self.feast.id]),
            {"page_size": 2},
        )
        with_text = self.client.get(
            reverse("feast-context-history", args=[self.feast.id]),
            {"include_text": "true", "page_size": 1000},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["version"] for row in response.data["results"]], [4, 3])
        self.assertNotIn("text", response.data["results"][0]["languages"]["en"])
        self.assertEqual(len(with_text.data["results"]), 4)
        self.assertEqual(with_text.data["results"][0]["languages"]["en"]["text"], "Body 4")

    def test_restore_copies_old_version_into_a_new_version_with_lineage(self):
        self.client.force_login(self.staff)
        append_feast_context_version(
            self.feast.id,
            values=FeastContextVersionInput(
                operation=FeastContext.Operation.MANUAL_EDIT,
                actor_id=self.staff.id,
            ),
            language_updates={
                "en": {"text": "Changed body", "short_text": "Changed summary"}
            },
        )

        response = self.client.post(
            reverse("feast-context-restore", args=[self.feast.id]),
            {"version": 1},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        restored = self.feast.active_context
        self.context.refresh_from_db()
        self.assertEqual(restored.version, 3)
        self.assertEqual(restored.operation, FeastContext.Operation.RESTORED)
        self.assertEqual(restored.restored_from, self.context)
        self.assertEqual(restored.created_by, self.staff)
        self.assertEqual(restored.text, "Original English body")
        self.assertEqual(restored.text_hy, "Original Armenian body")
        self.assertFalse(self.context.active)
        self.assertEqual((restored.thumbs_up, restored.thumbs_down), (0, 0))

    def test_restore_rejects_invalid_or_missing_version(self):
        self.client.force_login(self.staff)
        url = reverse("feast-context-restore", args=[self.feast.id])
        for payload in ({}, {"version": 0}, {"version": True}, {"version": 999}):
            with self.subTest(payload=payload):
                response = self.client.post(url, payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(FeastContext.objects.count(), 1)

    def test_feedback_still_targets_active_context_without_synthetic_votes(self):
        self.client.force_login(self.staff)
        self.client.patch(
            self.detail_url,
            {"language": "en", "text": "Edited", "short_text": "Edited short"},
            format="json",
        )
        self.client.logout()

        response = self.client.post(
            reverse("feast-context-feedback", args=[self.feast.id]),
            {"feedback_type": "up"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.context.refresh_from_db()
        active = self.feast.active_context
        active.refresh_from_db()
        self.assertEqual((self.context.thumbs_up, self.context.thumbs_down), (7, 4))
        self.assertEqual((active.thumbs_up, active.thumbs_down), (1, 0))


@override_settings(
    CACHES=LOCMEM_CACHE,
    MODELTRANS_AVAILABLE_LANGUAGES=["en", "hy"],
    CELERY_RESULT_BACKEND=None,
)
class FeastContextRegenerationAPITests(APITestCase):
    def setUp(self):
        cache.clear()
        self.church = Church.objects.create(name="Context Task Church")
        with patch("hub.signals.match_icon_to_feast_task.delay"), patch(
            "hub.signals.determine_feast_designation_task.delay"
        ):
            self.feast = Feast.objects.create(
                church=self.church,
                name="Context Task Feast",
                designation=Feast.Designation.SUNDAYS_DOMINICAL,
            )
        self.staff = get_user_model().objects.create_user(
            username="task-staff", is_staff=True
        )
        self.prompt = LLMPrompt.objects.create(
            model="gpt-4o-mini",
            role="Task role",
            prompt="Task prompt",
            applies_to="feasts",
            active=True,
        )
        self.client.force_login(self.staff)
        self.url = reverse("feast-context-regenerate", args=[self.feast.id])

    @patch("hub.tasks.llm_tasks.generate_feast_context_task.apply_async")
    def test_regeneration_queues_once_and_duplicate_returns_original_task(self, apply_async):
        first = self.client.post(
            self.url, {"additional_instructions": "Emphasize the liturgy."}, format="json"
        )
        second = self.client.post(
            self.url, {"additional_instructions": "Different request."}, format="json"
        )

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(second.data["task_id"], first.data["task_id"])
        apply_async.assert_called_once()
        self.assertEqual(
            apply_async.call_args.kwargs["kwargs"]["actor_id"], self.staff.id
        )
        self.assertEqual(
            apply_async.call_args.kwargs["kwargs"]["improvement_instructions"],
            "Emphasize the liturgy.",
        )

    @patch("hub.tasks.llm_tasks.generate_feast_context_task.apply_async")
    def test_regeneration_rejects_blank_instructions(self, apply_async):
        response = self.client.post(
            self.url, {"additional_instructions": "  "}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        apply_async.assert_not_called()

    def test_task_status_uses_safe_cache_metadata_without_result_backend(self):
        task_id = "0efda4cf-cdf7-464a-97b7-70f6870214dd"
        set_feast_context_task_status(
            task_id,
            feast_id=self.feast.id,
            state="FAILURE",
            ready=True,
            error="Context generation failed.",
        )

        response = self.client.get(
            reverse("feast-context-task-status", args=[self.feast.id, task_id])
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data), {"task_id", "feast_id", "state", "ready", "error"}
        )
        self.assertNotIn("prompt", response.data)
        self.assertNotIn("text", response.data)

    def test_task_status_does_not_cross_feast_boundaries(self):
        task_id = "bba1a80e-e155-4f66-a815-391c705563d0"
        set_feast_context_task_status(
            task_id,
            feast_id=self.feast.id + 1,
            state="PENDING",
            ready=False,
        )
        response = self.client.get(
            reverse("feast-context-task-status", args=[self.feast.id, task_id])
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("hub.services.llm_service.OpenAIService.generate_feast_context")
    def test_successful_task_sets_terminal_status_and_cleans_owned_lock(self, generate):
        task_id = "1d736033-c859-4e47-a1af-9b01db62c49f"
        cache.set(
            feast_context_regeneration_lock_key(self.feast.id), task_id, timeout=300
        )
        set_feast_context_task_status(
            task_id, feast_id=self.feast.id, state="PENDING", ready=False
        )
        generate.side_effect = [
            {"text": "English", "short_text": "English short"},
            {"text": "Armenian", "short_text": "Armenian short"},
        ]

        result = generate_feast_context_task.apply(
            args=[self.feast.id],
            kwargs={"force_regeneration": True, "actor_id": self.staff.id},
            task_id=task_id,
        )

        self.assertTrue(result.successful())
        self.assertIsNone(cache.get(feast_context_regeneration_lock_key(self.feast.id)))
        task_status = cache.get(feast_context_task_status_key(task_id))
        self.assertEqual(task_status["state"], "SUCCESS")
        self.assertTrue(task_status["ready"])


@override_settings(
    CACHES=DUMMY_CACHE,
    MODELTRANS_AVAILABLE_LANGUAGES=["en", "hy"],
)
class FeastContextGenerationTaskTests(TestCase):
    def setUp(self):
        self.church = Church.objects.create(name="Append Task Church")
        with patch("hub.signals.match_icon_to_feast_task.delay"), patch(
            "hub.signals.determine_feast_designation_task.delay"
        ):
            self.feast = Feast.objects.create(
                church=self.church,
                name="Append Task Feast",
                designation=Feast.Designation.SUNDAYS_DOMINICAL,
            )
        self.staff = get_user_model().objects.create_user(username="actor", is_staff=True)
        self.prompt = LLMPrompt.objects.create(
            model="gpt-4o-mini",
            role="Role",
            prompt="Prompt",
            applies_to="feasts",
            active=True,
        )
        self.old = FeastContext.objects.create(
            feast=self.feast,
            version=1,
            text="Old English",
            short_text="Old short",
            prompt=self.prompt,
            thumbs_up=8,
            thumbs_down=3,
        )
        self.old.text_hy = "Old Armenian"
        self.old.short_text_hy = "Old Armenian short"
        self.old.save()

    @patch("hub.services.llm_service.OpenAIService.generate_feast_context")
    def test_task_force_regeneration_appends_and_records_provenance(self, generate):
        generate.side_effect = [
            {"text": "New English", "short_text": "New English short"},
            {"text": "New Armenian", "short_text": "New Armenian short"},
        ]

        generate_feast_context_task(
            self.feast.id,
            force_regeneration=True,
            improvement_instructions="Editorial direction",
            actor_id=self.staff.id,
        )

        self.old.refresh_from_db()
        active = self.feast.active_context
        self.assertFalse(self.old.active)
        self.assertEqual(self.old.text, "Old English")
        self.assertEqual(active.version, 2)
        self.assertEqual(active.operation, FeastContext.Operation.REGENERATED)
        self.assertEqual(active.created_by, self.staff)
        self.assertEqual(active.additional_instructions, "Editorial direction")
        self.assertEqual(active.prompt, self.prompt)
        self.assertEqual(active.text_hy, "New Armenian")
        self.assertEqual((active.thumbs_up, active.thumbs_down), (0, 0))

    @patch("hub.services.llm_service.OpenAIService.generate_feast_context")
    def test_missing_translation_generation_appends_and_carries_existing_language(self, generate):
        self.old.text_hy = ""
        self.old.short_text_hy = ""
        self.old.save()
        generate.side_effect = [
            {"text": "Generated English", "short_text": "Generated English short"},
            {"text": "Generated Armenian", "short_text": "Generated Armenian short"},
        ]

        generate_feast_context_task(self.feast.id)

        self.old.refresh_from_db()
        active = self.feast.active_context
        self.assertFalse(self.old.active)
        self.assertEqual(active.version, 2)
        self.assertEqual(active.operation, FeastContext.Operation.GENERATED)
        self.assertEqual(active.text, "Old English")
        self.assertEqual(active.short_text, "Old short")
        self.assertEqual(active.text_hy, "Generated Armenian")


@override_settings(
    CACHES=LOCMEM_CACHE,
    MODELTRANS_AVAILABLE_LANGUAGES=["en", "hy"],
)
class FeastContextCommitInvalidationTests(TestCase):
    def setUp(self):
        self.church = Church.objects.create(name="Commit Cache Church")
        with patch("hub.signals.match_icon_to_feast_task.delay"), patch(
            "hub.signals.determine_feast_designation_task.delay"
        ):
            self.feast = Feast.objects.create(church=self.church, name="Commit Cache Feast")
        FeastContext.objects.create(
            feast=self.feast,
            version=1,
            text="Before",
            short_text="Before short",
        )

    @patch("hub.services.feast_contexts.invalidate_feast_api_cache_for_feast")
    def test_service_invalidation_is_registered_for_commit(self, invalidate):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            with transaction.atomic():
                append_feast_context_version(
                    self.feast.id,
                    values=FeastContextVersionInput(
                        operation=FeastContext.Operation.MANUAL_EDIT
                    ),
                    language_updates={
                        "en": {"text": "After", "short_text": "After short"}
                    },
                )
                invalidate.assert_not_called()
        self.assertGreaterEqual(len(callbacks), 1)
        callbacks[-1]()
        invalidate.assert_called_once_with(self.feast)

    @patch("hub.services.feast_contexts.invalidate_feast_api_cache_for_feast")
    def test_rolled_back_version_does_not_invalidate_cache(self, invalidate):
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    append_feast_context_version(
                        self.feast.id,
                        values=FeastContextVersionInput(
                            operation=FeastContext.Operation.MANUAL_EDIT
                        ),
                        language_updates={
                            "en": {"text": "After", "short_text": "After short"}
                        },
                    )
                    raise RuntimeError("roll back")
            except RuntimeError:
                pass
        invalidate.assert_not_called()
        self.assertEqual(FeastContext.objects.filter(feast=self.feast).count(), 1)
