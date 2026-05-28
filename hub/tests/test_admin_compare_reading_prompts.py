from datetime import date
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, reverse

from hub.models import Church, Day, LLMPrompt, Reading
from hub.views.admin import compare_reading_prompts


class CompareReadingPromptsRouteTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin_user = user_model.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.nonstaff_user = user_model.objects.create_user(
            username="member",
            email="member@example.com",
            password="password",
        )

        church = Church.objects.get(pk=Church.get_default_pk())
        day = Day.objects.create(date=date(2026, 1, 15), church=church)
        self.reading = Reading.objects.create(
            day=day,
            book="Genesis",
            start_chapter=1,
            start_verse=1,
            end_chapter=1,
            end_verse=5,
        )
        self.prompt1 = LLMPrompt.objects.create(
            model="gpt-4.1-mini",
            role="Role 1",
            prompt="Prompt 1",
        )
        self.prompt2 = LLMPrompt.objects.create(
            model="gpt-4o-mini",
            role="Role 2",
            prompt="Prompt 2",
        )

    def test_reverse_and_admin_get_return_expected_context(self):
        self.client.force_login(self.admin_user)

        url = reverse("hub_reading_compare_prompts", args=[self.reading.pk])
        mounted_url = f"/hub/hub/reading/{self.reading.pk}/compare-prompts/"
        response = self.client.get(url)
        mounted_response = self.client.get(mounted_url)
        match = resolve(url)
        mounted_match = resolve(mounted_url)

        self.assertEqual(url, f"/api/hub/reading/{self.reading.pk}/compare-prompts/")
        self.assertEqual(match.func, compare_reading_prompts)
        self.assertEqual(match.url_name, "hub_reading_compare_prompts")
        self.assertEqual(match.kwargs["reading_id"], self.reading.pk)
        self.assertEqual(mounted_match.func, compare_reading_prompts)
        self.assertEqual(mounted_match.url_name, "hub_reading_compare_prompts")
        self.assertEqual(mounted_match.kwargs["reading_id"], self.reading.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mounted_response.status_code, 200)
        self.assertEqual(response.context["reading"], self.reading)
        self.assertCountEqual(response.context["prompts"], [self.prompt1, self.prompt2])
        self.assertEqual(response.context["outputs"], [])

    def test_compare_prompts_requires_staff_access(self):
        url = reverse("hub_reading_compare_prompts", args=[self.reading.pk])

        anonymous_response = self.client.get(url)
        self.assertEqual(anonymous_response.status_code, 302)
        self.assertIn("/admin/login/", anonymous_response.url)

        self.client.force_login(self.nonstaff_user)
        nonstaff_response = self.client.get(url)
        self.assertEqual(nonstaff_response.status_code, 302)
        self.assertIn("/admin/login/", nonstaff_response.url)

    def test_compare_prompts_returns_404_for_missing_reading(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("hub_reading_compare_prompts", args=[self.reading.pk + 999])
        )

        self.assertEqual(response.status_code, 404)

    def test_compare_prompts_post_populates_outputs_for_selected_prompts(self):
        self.client.force_login(self.admin_user)
        service1 = Mock()
        service1.generate_context.return_value = "Context from prompt 1"
        service2 = Mock()
        service2.generate_context.return_value = "Context from prompt 2"

        with patch.object(
            LLMPrompt,
            "get_llm_service",
            side_effect=[service1, service2],
        ):
            response = self.client.post(
                reverse("hub_reading_compare_prompts", args=[self.reading.pk]),
                {"prompt1": self.prompt1.pk, "prompt2": self.prompt2.pk},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["outputs"],
            [
                {"prompt": self.prompt1, "text": "Context from prompt 1"},
                {"prompt": self.prompt2, "text": "Context from prompt 2"},
            ],
        )
