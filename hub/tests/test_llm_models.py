import datetime
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.forms import modelform_factory
from django.test import SimpleTestCase, TestCase

from hub.models import LLMPrompt
from hub.services.llm_models import (
    DEPRECATED,
    MODEL_LIFECYCLE,
    THINKING_HEADROOM_TOKENS,
    ModelLifecycle,
    UnsupportedModelError,
    can_activate,
    is_past_shutdown,
    provider_for,
    resolve_model,
)
from hub.services.llm_requests import LLMResponseError, anthropic_message, anthropic_text, openai_chat_completion

REFERENCE_DATE = datetime.date(2026, 10, 1)
AFTER_ALL_SHUTDOWNS = datetime.date(2027, 1, 1)


def _response(*blocks, stop_reason="end_turn", stop_details=None):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, stop_details=stop_details)


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _thinking():
    return SimpleNamespace(type="thinking", thinking="", signature="sig")


class ResolveModelTests(SimpleTestCase):
    def test_active_and_unknown_models_pass_through(self):
        for model in ("claude-haiku-5-5", "claude-sonnet-4-6", "gpt-4o-mini", "gpt-6-luna"):
            self.assertEqual(resolve_model(model, AFTER_ALL_SHUTDOWNS), model)

    def test_retired_claude_model_is_redirected(self):
        self.assertEqual(resolve_model("claude-3-5-sonnet-20241022", REFERENCE_DATE), "claude-sonnet-5-5")

    def test_sonnet_45_redirects_immediately_independent_of_retirement_date(self):
        model = "claude-sonnet-4-5-20250929"
        for day in (REFERENCE_DATE, datetime.date(2026, 10, 30), AFTER_ALL_SHUTDOWNS):
            with self.subTest(day=day):
                self.assertEqual(resolve_model(model, day), "claude-sonnet-5-5")
                self.assertFalse(is_past_shutdown(model, day))
        self.assertIsNone(MODEL_LIFECYCLE[model].shutdown)
        with self.assertLogs("hub.services.llm_models", level="WARNING") as logs:
            resolve_model(model, REFERENCE_DATE)
        self.assertIn("immediate routing policy", logs.output[0])
        self.assertNotIn("past shutdown", logs.output[0])

    def test_openai_models_follow_provider_replacements(self):
        self.assertEqual(resolve_model("o4-mini", datetime.date(2026, 10, 22)), "o4-mini")
        self.assertEqual(resolve_model("o4-mini", datetime.date(2026, 10, 23)), "gpt-5.6-terra")
        self.assertEqual(resolve_model("gpt-5", AFTER_ALL_SHUTDOWNS), "gpt-5.6-sol")
        self.assertEqual(resolve_model("gpt-5-nano", AFTER_ALL_SHUTDOWNS), "gpt-5.6-luna")

    def test_shutdown_without_reviewed_replacement_raises(self):
        lifecycle = ModelLifecycle(DEPRECATED, datetime.date(2026, 1, 1))
        with patch.dict(MODEL_LIFECYCLE, {"gpt-legacy": lifecycle}):
            with self.assertRaisesMessage(UnsupportedModelError, "no reviewed replacement"):
                resolve_model("gpt-legacy", AFTER_ALL_SHUTDOWNS)

    def test_provider_for(self):
        self.assertEqual(provider_for("claude-haiku-5-5"), "anthropic")
        self.assertEqual(provider_for("gpt-6.1-sol"), "openai")
        self.assertEqual(provider_for("o4-mini"), "openai")
        with self.assertRaises(UnsupportedModelError):
            provider_for("gemini-pro")

    def test_can_activate(self):
        self.assertTrue(can_activate("claude-sonnet-5-5"))
        self.assertFalse(can_activate("claude-sonnet-4-5-20250929"))
        self.assertFalse(can_activate("claude-3-5-sonnet-20241022"))
        self.assertFalse(can_activate("gpt-5"))


class ModelChoicesTests(SimpleTestCase):
    def test_every_choice_has_a_provider(self):
        for model, _ in LLMPrompt.MODEL_CHOICES:
            provider_for(model)

    def test_claude_5_5_models_are_selectable(self):
        choices = dict(LLMPrompt.MODEL_CHOICES)
        for model in ("claude-sonnet-5-5", "claude-haiku-5-5"):
            self.assertIn(model, choices)
            self.assertTrue(can_activate(model))

    def test_labels_mark_every_unselectable_choice(self):
        for model, label in LLMPrompt.MODEL_CHOICES:
            self.assertEqual(can_activate(model), not label.endswith(("(deprecated)", "(retired)")), model)


class AnthropicRequestTests(SimpleTestCase):
    def test_sonnet_45_request_uses_immediate_replacement_with_thinking_parameters(self):
        client = Mock()
        with patch("hub.services.llm_models.datetime.date") as date_cls:
            date_cls.today.return_value = REFERENCE_DATE
            anthropic_message(client, model="claude-sonnet-4-5-20250929", messages=[], max_tokens=100)
        client.messages.create.assert_called_once_with(
            model="claude-sonnet-5-5", messages=[], max_tokens=100 + THINKING_HEADROOM_TOKENS,
            output_config={"effort": "low"},
        )

    def test_thinking_model_gets_effort_and_headroom(self):
        client = Mock()

        anthropic_message(client, model="claude-haiku-5-5", messages=[], max_tokens=200)

        client.messages.create.assert_called_once_with(
            model="claude-haiku-5-5",
            messages=[],
            max_tokens=200 + THINKING_HEADROOM_TOKENS,
            output_config={"effort": "low"},
        )

    def test_explicit_effort_is_kept(self):
        client = Mock()

        anthropic_message(client, model="claude-sonnet-5-5", messages=[], max_tokens=100, effort="medium")

        self.assertEqual(client.messages.create.call_args.kwargs["output_config"], {"effort": "medium"})

    def test_retired_model_request_goes_to_replacement(self):
        client = Mock()

        anthropic_message(client, model="claude-3-5-sonnet-20241022", messages=[], max_tokens=100)

        kwargs = client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-sonnet-5-5")
        self.assertEqual(kwargs["max_tokens"], 100 + THINKING_HEADROOM_TOKENS)

    def test_gpt6_uses_reasoning_parameters(self):
        client = Mock()

        openai_chat_completion(client, model="gpt-6-luna", messages=[], max_tokens=100, temperature=0.2)

        client.chat.completions.create.assert_called_once_with(
            model="gpt-6-luna", messages=[], max_completion_tokens=100
        )


class AnthropicTextTests(SimpleTestCase):
    def test_text_only(self):
        self.assertEqual(anthropic_text(_response(_text(" Martyrs \n"))), "Martyrs")

    def test_thinking_first_response_reads_text_block(self):
        self.assertEqual(anthropic_text(_response(_thinking(), _text("Fast"))), "Fast")

    def test_multiple_text_blocks_are_joined(self):
        self.assertEqual(anthropic_text(_response(_text('{"a": '), _text("1}"))), '{"a": 1}')

    def test_refusal_raises(self):
        details = SimpleNamespace(category="general_harms", explanation="")
        with self.assertRaisesMessage(LLMResponseError, "general_harms"):
            anthropic_text(_response(_text("partial"), stop_reason="refusal", stop_details=details))

    def test_truncated_during_thinking_raises(self):
        with self.assertRaisesMessage(LLMResponseError, "max_tokens"):
            anthropic_text(_response(_thinking(), stop_reason="max_tokens"))

    def test_empty_content_raises(self):
        with self.assertRaises(LLMResponseError):
            anthropic_text(_response())


class LLMPromptValidationTests(TestCase):
    def _legacy_moderation_prompt(self, active=False):
        return LLMPrompt.objects.create(
            model="gpt-4o-mini", role="r", prompt="p", applies_to="prayer_requests", active=active,
        )

    def _edit_form(self, prompt, active):
        form_class = modelform_factory(LLMPrompt, fields=["model", "role", "prompt", "applies_to", "active"])
        return form_class(
            data={"model": prompt.model, "role": prompt.role, "prompt": "edited",
                  "applies_to": prompt.applies_to, "active": active},
            instance=prompt,
        )

    def test_active_non_claude_moderation_row_stays_editable_in_form(self):
        prompt = self._legacy_moderation_prompt(active=True)
        form = self._edit_form(prompt, active=True)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        prompt.refresh_from_db()
        self.assertEqual(prompt.model, "gpt-4o-mini")
        self.assertEqual(prompt.prompt, "edited")
        self.assertTrue(prompt.active)

    def test_non_claude_moderation_row_can_be_deactivated_in_form(self):
        prompt = self._legacy_moderation_prompt(active=True)
        form = self._edit_form(prompt, active=False)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        prompt.refresh_from_db()
        self.assertFalse(prompt.active)
        self.assertEqual(prompt.model, "gpt-4o-mini")

    def test_inactive_non_claude_moderation_row_stays_editable(self):
        prompt = self._legacy_moderation_prompt()
        prompt.prompt = "edited"
        prompt.full_clean()

    def test_non_claude_moderation_row_cannot_be_newly_activated(self):
        prompt = self._legacy_moderation_prompt()
        form = self._edit_form(prompt, active=True)
        self.assertFalse(form.is_valid())
        self.assertIn("Claude models only", str(form.errors))

    def test_non_claude_reading_row_cannot_be_assigned_to_moderation(self):
        prompt = LLMPrompt.objects.create(model="gpt-4o-mini", role="r", prompt="p")
        prompt.applies_to = "prayer_requests"
        with self.assertRaisesMessage(ValidationError, "Claude models only"):
            prompt.clean()

    def test_admin_action_rejects_provider_mismatch_before_deactivating_current_prompt(self):
        from django.contrib.admin.sites import AdminSite
        from hub.admin import LLMPromptAdmin

        current = LLMPrompt.objects.create(
            model="claude-sonnet-4-6", role="r", prompt="p", applies_to="prayer_requests", active=True,
        )
        legacy = self._legacy_moderation_prompt()
        admin = LLMPromptAdmin(LLMPrompt, AdminSite())
        with patch.object(admin, "message_user") as message:
            admin.make_active(Mock(), LLMPrompt.objects.filter(pk=legacy.pk))
        current.refresh_from_db()
        legacy.refresh_from_db()
        self.assertTrue(current.active)
        self.assertFalse(legacy.active)
        self.assertIn("Claude models only", message.call_args.args[1])

    def _prompt(self, **kwargs):
        defaults = {"model": "claude-sonnet-5-5", "role": "r", "prompt": "p", "applies_to": "readings"}
        defaults.update(kwargs)
        return LLMPrompt(**defaults)

    def test_cannot_create_with_deprecated_model(self):
        with self.assertRaisesMessage(ValidationError, "deprecated or retired"):
            self._prompt(model="claude-sonnet-4-5-20250929").clean()

    def test_legacy_inactive_row_stays_editable(self):
        prompt = LLMPrompt.objects.create(model="claude-3-5-sonnet-20241022", role="r", prompt="p")
        prompt.prompt = "edited"
        prompt.clean()

    def test_legacy_row_cannot_be_activated(self):
        prompt = LLMPrompt.objects.create(model="o4-mini", role="r", prompt="p")
        prompt.active = True
        with self.assertRaisesMessage(ValidationError, "deprecated or retired"):
            prompt.clean()

    def test_moderation_requires_claude(self):
        with self.assertRaisesMessage(ValidationError, "Claude models only"):
            self._prompt(model="gpt-4o-mini", applies_to="prayer_requests").clean()

    def test_unsupported_model_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "Unsupported model"):
            self._prompt(model="gemini-pro").clean()


class ModerationModelTests(TestCase):
    def test_non_claude_prompt_falls_back_to_default_moderation_model(self):
        from prayers.tasks import _get_moderation_prompt_and_service

        LLMPrompt.objects.create(
            model="gpt-4o-mini", role="", prompt="{title}", applies_to="prayer_requests", active=True
        )
        request = SimpleNamespace(title="Healing", description="")

        model, _, prompt_text = _get_moderation_prompt_and_service(request)

        self.assertEqual(model, "claude-sonnet-5-5")
        self.assertEqual(prompt_text, "Healing")

    def test_refusal_routes_request_to_human_review(self):
        from django.utils import timezone

        from prayers.models import PrayerRequest
        from prayers.tasks import moderate_prayer_request_task
        from tests.fixtures.test_data import TestDataFactory

        prayer_request = PrayerRequest.objects.create(
            title="Please pray",
            description="For my family",
            requester=TestDataFactory.create_user(),
            duration_days=3,
            status="pending_moderation",
            reviewed=False,
            expiration_date=timezone.now() + datetime.timedelta(days=3),
        )
        refusal = _response(
            stop_reason="refusal", stop_details=SimpleNamespace(category="general_harms", explanation="")
        )
        with patch("anthropic.Anthropic") as client_cls, patch("prayers.tasks._send_moderation_alert_email"):
            client_cls.return_value.messages.create.return_value = refusal
            moderate_prayer_request_task(prayer_request.id)

        prayer_request.refresh_from_db()
        self.assertEqual(prayer_request.status, "pending_moderation")
        self.assertTrue(prayer_request.requires_human_review)


class AuditLLMModelsCommandTests(TestCase):
    def test_reports_affected_active_prompts_without_prompt_text(self):
        prompt = LLMPrompt.objects.create(model="o4-mini", role="r", prompt="SECRET PROMPT TEXT", active=True)
        out = StringIO()

        call_command("audit_llm_models", stdout=out)

        self.assertIn("o4-mini", out.getvalue())
        self.assertIn("need a new model", out.getvalue())
        self.assertNotIn(prompt.prompt, out.getvalue())
        with self.assertRaises(CommandError):
            call_command("audit_llm_models", "--strict", stdout=StringIO())

    def test_strict_passes_with_supported_models(self):
        LLMPrompt.objects.create(model="claude-haiku-5-5", role="r", prompt="p", active=True)

        call_command("audit_llm_models", "--strict", stdout=StringIO())
