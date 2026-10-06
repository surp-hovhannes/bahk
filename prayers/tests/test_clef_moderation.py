"""Tests for prayer request moderation with Cloudflare's Clef decision model."""

from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from hub.services.clef import ClefError, run_clef
from prayers.clef_moderation import QUESTIONS, clef_moderation_result, decide, moderation_state
from prayers.models import PrayerRequest, PrayerRequestAcceptance
from prayers.tasks import moderate_prayer_request_task
from tests.base import BaseTestCase

CLEAN = {"genuine": 0.94, "crisis": 0.03, "spam": 0.03, "inappropriate": 0.04, "incoherent": 0.08, "private_info": 0.04}


def probabilities(**overrides):
    return {**CLEAN, **overrides}


def clef_answers(**overrides):
    return {name: {"type": "noul", "noul": value} for name, value in probabilities(**overrides).items()}


class ClefResponseValidationTests(SimpleTestCase):
    def test_invalid_scores_are_rejected_for_every_question(self):
        request = SimpleNamespace(title="Invented support request", description="Invented details")
        for name in QUESTIONS:
            for value in (
                True,
                False,
                -0.01,
                1.01,
                float("nan"),
                float("inf"),
                -float("inf"),
                "0.03",
                "NaN",
                None,
                [],
                {},
            ):
                with self.subTest(question=name, value=value):
                    with patch("prayers.clef_moderation.run_clef", return_value=clef_answers(**{name: value})):
                        with self.assertRaisesMessage(ClefError, f"invalid probability for: {name}"):
                            clef_moderation_result(request)

    def test_malformed_answers_raise_a_safe_error(self):
        request = SimpleNamespace(title="Invented support request", description="")
        for answers in (
            None,
            [],
            {},
            {**clef_answers(), "crisis": None},
            {**clef_answers(), "crisis": {}},
            {**clef_answers(), "crisis": {"noul": "Invented private payload"}},
        ):
            with self.subTest(answers=answers), patch("prayers.clef_moderation.run_clef", return_value=answers):
                with self.assertRaises(ClefError) as caught:
                    clef_moderation_result(request)
                self.assertNotIn("Invented private payload", str(caught.exception))

    def test_zero_and_one_are_valid_probabilities(self):
        request = SimpleNamespace(title="Invented support request", description="")
        for value in (0, 1, 0.0, 1.0):
            with (
                self.subTest(value=value),
                patch("prayers.clef_moderation.run_clef", return_value=clef_answers(crisis=value)),
            ):
                result = clef_moderation_result(request)
                self.assertEqual(result["probabilities"]["crisis"], value)
                self.assertEqual(result["suggested_action"], "approve" if value == 0 else "escalate")


class ClefDecisionTests(SimpleTestCase):
    def test_clean_genuine_request_is_approved(self):
        result = decide(probabilities())
        self.assertTrue(result["approved"])
        self.assertEqual(result["severity"], "low")
        self.assertEqual(result["suggested_action"], "approve")

    def test_crisis_escalates_even_when_genuine(self):
        result = decide(probabilities(genuine=0.89, crisis=0.98))
        self.assertFalse(result["approved"])
        self.assertEqual(result["severity"], "critical")
        self.assertEqual(result["suggested_action"], "escalate")
        self.assertTrue(result["requires_human_review"])

    def test_crisis_threshold_is_deliberately_low(self):
        self.assertEqual(decide(probabilities(crisis=0.39))["suggested_action"], "escalate")

    def test_confident_violation_is_rejected(self):
        result = decide(probabilities(genuine=0.05, spam=0.95, incoherent=0.59))
        self.assertFalse(result["approved"])
        self.assertEqual(result["suggested_action"], "reject")
        self.assertIn("promotional", result["reason"])
        self.assertIn("incoherent or test content", result["concerns"])

    def test_violation_on_genuine_request_goes_to_review_not_rejection(self):
        result = decide(probabilities(genuine=0.70, spam=0.91))
        self.assertEqual(result["suggested_action"], "flag_for_review")
        self.assertTrue(result["requires_human_review"])

    def test_uncertain_request_goes_to_review(self):
        result = decide(probabilities(genuine=0.30, inappropriate=0.25))
        self.assertEqual(result["suggested_action"], "flag_for_review")
        self.assertEqual(result["severity"], "high")
        self.assertTrue(result["concerns"])

    def test_moderation_state_omits_empty_description(self):
        self.assertEqual(moderation_state(SimpleNamespace(title="Anxiety", description="")), "Title: Anxiety")
        self.assertEqual(
            moderation_state(SimpleNamespace(title="Job", description="Lost my job")),
            "Title: Job\nDescription: Lost my job",
        )


@override_settings(CLOUDFLARE_WORKERSAI_ACCOUNT_ID="acct", CLOUDFLARE_WORKERSAI_API_KEY="key")
class RunClefTests(SimpleTestCase):
    def response(self, status_code, payload):
        return SimpleNamespace(status_code=status_code, json=lambda: payload, text=str(payload))

    def test_returns_answers_and_sends_typed_questions(self):
        payload = {"success": True, "result": {"model": "clef", "answers": clef_answers()}}
        with patch("hub.services.clef.requests.post", return_value=self.response(200, payload)) as post:
            answers = run_clef("Title: Job", QUESTIONS)

        self.assertEqual(answers["genuine"]["noul"], CLEAN["genuine"])
        url = post.call_args.args[0]
        body = post.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/accounts/acct/ai/run/@cf/cloudflare/clef"))
        self.assertEqual(body["model"], "clef")
        self.assertEqual(body["questions"], QUESTIONS)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer key")

    def test_http_error_raises(self):
        payload = {"success": False, "errors": [{"message": "Bad input"}]}
        with patch("hub.services.clef.requests.post", return_value=self.response(400, payload)):
            with self.assertRaisesMessage(ClefError, "HTTP 400"):
                run_clef("state", QUESTIONS)

    def test_network_error_raises(self):
        with patch("hub.services.clef.requests.post", side_effect=requests.Timeout("slow")):
            with self.assertRaises(ClefError):
                run_clef("state", QUESTIONS)

    def test_missing_answer_raises(self):
        answers = clef_answers()
        del answers["crisis"]
        payload = {"success": True, "result": {"answers": answers}}
        with patch("hub.services.clef.requests.post", return_value=self.response(200, payload)):
            with self.assertRaisesMessage(ClefError, "crisis"):
                run_clef("state", QUESTIONS)

    @override_settings(CLOUDFLARE_WORKERSAI_API_KEY="")
    def test_missing_credentials_raise_without_calling_api(self):
        with patch("hub.services.clef.requests.post") as post:
            with self.assertRaises(ClefError):
                run_clef("state", QUESTIONS)
        post.assert_not_called()


@override_settings(PRAYER_MODERATION_ENGINE="clef", PRAYER_MODERATION_CLEF_MODEL="clef")
class ClefModerationTaskTests(BaseTestCase):
    def setUp(self):
        self.requester = self.create_user(email="clef@example.com")

    def create_request(self, title="Healing for my mom", description="Surgery on Tuesday."):
        return PrayerRequest.objects.create(
            title=title,
            description=description,
            requester=self.requester,
            duration_days=3,
            status="pending_moderation",
            reviewed=False,
            expiration_date=timezone.now() + timedelta(days=3),
        )

    def moderate(self, prayer_request, answers=None, error=None):
        with (
            patch("prayers.clef_moderation.run_clef", return_value=answers, side_effect=error) as run,
            patch("prayers.tasks._send_moderation_alert_email") as email,
            patch("anthropic.Anthropic") as anthropic,
        ):
            result = moderate_prayer_request_task(prayer_request.id)
        anthropic.assert_not_called()
        prayer_request.refresh_from_db()
        return result, run, email

    def test_approves_and_records_probabilities(self):
        prayer_request = self.create_request()
        result, run, email = self.moderate(prayer_request, clef_answers())

        self.assertEqual(result["status"], "approved")
        self.assertEqual(prayer_request.status, "approved")
        self.assertEqual(prayer_request.moderation_severity, "low")
        llm_check = prayer_request.moderation_result["llm_check"]
        self.assertEqual(llm_check["engine"], "clef")
        self.assertEqual(llm_check["probabilities"]["genuine"], CLEAN["genuine"])
        self.assertEqual(run.call_args.kwargs["model"], "clef")
        self.assertTrue(PrayerRequestAcceptance.objects.filter(prayer_request=prayer_request).exists())
        email.assert_not_called()

    def test_crisis_is_escalated_with_critical_alert(self):
        prayer_request = self.create_request("Goodbye", "This is my last post.")
        result, _, email = self.moderate(prayer_request, clef_answers(genuine=0.07, crisis=0.53))

        self.assertEqual(prayer_request.status, "rejected")
        self.assertTrue(prayer_request.requires_human_review)
        self.assertEqual(prayer_request.moderation_severity, "critical")
        email.assert_called_with(prayer_request, "critical_safety_concern")

    def test_crisis_with_profanity_runs_clef_and_preserves_probabilities(self):
        prayer_request = self.create_request("Fuck, help", "I plan to hurt myself tonight.")
        result, run, email = self.moderate(prayer_request, clef_answers(crisis=0.9))

        run.assert_called_once()
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(prayer_request.moderation_severity, "critical")
        self.assertTrue(prayer_request.requires_human_review)
        self.assertTrue(prayer_request.moderation_result["profanity_check"]["title_contains_profanity"])
        self.assertEqual(prayer_request.moderation_result["llm_check"]["probabilities"]["crisis"], 0.9)
        self.assertFalse(PrayerRequestAcceptance.objects.filter(prayer_request=prayer_request).exists())
        email.assert_called_once_with(prayer_request, "critical_safety_concern")

    def test_spam_is_rejected(self):
        prayer_request = self.create_request("Buy watches", "cheapwatchz.example")
        result, _, email = self.moderate(prayer_request, clef_answers(genuine=0.05, spam=0.95))

        self.assertEqual(prayer_request.status, "rejected")
        self.assertFalse(prayer_request.requires_human_review)
        email.assert_called_with(prayer_request, "llm_rejected")

    def test_clef_failure_falls_back_to_manual_review(self):
        prayer_request = self.create_request()
        result, _, email = self.moderate(prayer_request, error=ClefError("HTTP 503"))

        self.assertFalse(result["success"])
        self.assertEqual(prayer_request.status, "pending_moderation")
        self.assertTrue(prayer_request.requires_human_review)
        email.assert_called_with(prayer_request, "llm_error")

    def test_invalid_scores_fail_safe_with_or_without_profanity(self):
        for title in ("Invented support request", "Fuck, help"):
            for name, value in (
                ("crisis", -1),
                ("crisis", False),
                ("crisis", float("nan")),
                ("crisis", float("inf")),
                ("spam", -1),
                ("genuine", 1.1),
            ):
                with self.subTest(title=title, question=name, value=value):
                    prayer_request = self.create_request(title)
                    with (
                        patch("prayers.tasks.Event.create_event") as event,
                        patch("prayers.tasks.UserMilestone.create_milestone") as milestone,
                    ):
                        result, run, email = self.moderate(prayer_request, clef_answers(**{name: value}))
                    run.assert_called_once()
                    event.assert_not_called()
                    milestone.assert_not_called()
                    self.assertFalse(result["success"])
                    self.assertEqual(prayer_request.status, "pending_moderation")
                    self.assertEqual(prayer_request.moderation_severity, "high")
                    self.assertTrue(prayer_request.requires_human_review)
                    self.assertFalse(prayer_request.reviewed)
                    self.assertIsNone(prayer_request.moderated_at)
                    self.assertFalse(PrayerRequestAcceptance.objects.filter(prayer_request=prayer_request).exists())
                    email.assert_called_once_with(prayer_request, "llm_error")


class CompareClefModerationCommandTests(BaseTestCase):
    def test_skips_noncritical_profanity_rejections_without_provider_calls(self):
        requester = self.create_user(email="compare-profanity@example.test")
        for approved, severity, action in (
            (True, "low", "approve"),
            (True, "high", "flag_for_review"),
            (False, "medium", "reject"),
        ):
            PrayerRequest.objects.create(
                title="Invented profanity rejection",
                requester=requester,
                status="rejected",
                reviewed=True,
                moderated_at=timezone.now(),
                moderation_result={
                    "profanity_check": {"passed": False},
                    "llm_check": {"approved": approved, "severity": severity, "suggested_action": action},
                },
            )
        before = list(PrayerRequest.objects.values())
        out = StringIO()
        with patch("prayers.clef_moderation.run_clef") as provider:
            call_command("compare_clef_moderation", stdout=out)
        provider.assert_not_called()
        self.assertIn("Compared 0 requests", out.getvalue())
        self.assertIn("'profanity rejection': 3", out.getvalue())
        self.assertNotIn("Agreement:", out.getvalue())
        self.assertEqual(list(PrayerRequest.objects.values()), before)

    def test_compares_safety_escalations_even_with_profanity(self):
        requester = self.create_user(email="compare-escalation@example.test")
        for severity, action in ((" CRITICAL ", "approve"), ("low", " ESCALATE ")):
            PrayerRequest.objects.create(
                title="Invented safety escalation",
                requester=requester,
                status="rejected",
                reviewed=True,
                requires_human_review=True,
                moderated_at=timezone.now(),
                moderation_result={
                    "profanity_check": {"passed": False},
                    "llm_check": {"approved": True, "severity": severity, "suggested_action": action},
                },
            )
        before = list(PrayerRequest.objects.values())
        out = StringIO()
        with patch("prayers.clef_moderation.run_clef", return_value=clef_answers(crisis=0.9)) as provider:
            call_command("compare_clef_moderation", stdout=out)
        self.assertEqual(provider.call_count, 2)
        self.assertIn("Compared 2 requests", out.getvalue())
        self.assertIn("Agreement: 2/2", out.getvalue())
        self.assertEqual(list(PrayerRequest.objects.values()), before)

    def test_tabulates_previous_route_against_clef_without_writing(self):
        requester = self.create_user(email="compare@example.com")
        prayer_request = PrayerRequest.objects.create(
            title="Health",
            description="Please pray.",
            requester=requester,
            duration_days=3,
            status="approved",
            reviewed=True,
            moderated_at=timezone.now(),
            moderation_result={"llm_check": {"approved": True, "severity": "low", "suggested_action": "approve"}},
            expiration_date=timezone.now() + timedelta(days=3),
        )
        out = StringIO()
        with patch("prayers.clef_moderation.run_clef", return_value=clef_answers(genuine=0.3)):
            call_command("compare_clef_moderation", stdout=out)

        output = out.getvalue()
        self.assertIn("Agreement: 0/1", output)
        self.assertIn(f"#{prayer_request.id} [approved] approve -> review", output)
        self.assertNotIn("'Health'", output)
        prayer_request.refresh_from_db()
        self.assertEqual(prayer_request.moderation_result["llm_check"]["severity"], "low")
