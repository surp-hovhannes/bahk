"""Synthetic routing regressions; mocked decisions do not measure model accuracy."""

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from prayers.clef_moderation import decide
from prayers.models import PrayerRequest, PrayerRequestAcceptance
from prayers.tasks import moderate_prayer_request_task
from prayers.tests.test_clef_moderation import clef_answers
from tests.base import BaseTestCase

CASES = json.loads(
    (Path(__file__).resolve().parents[2] / "tests/fixtures/prayer_moderation_cases.json").read_text()
)


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}})
class CrisisBeforeProfanityTests(BaseTestCase):
    def setUp(self):
        self.requester = self.create_user(email="synthetic-routing@example.test")
        self.case_number = 0

    def create_request(self, case):
        return PrayerRequest.objects.create(
            title=case["title"], description=case["description"], requester=self.requester,
            duration_days=3, status="pending_moderation", reviewed=False,
            expiration_date=timezone.now() + timedelta(days=3),
        )

    def run_case(self, engine, case, decision, status, alert, severity, review, error=None):
        self.case_number += 1
        self.requester = self.create_user(email=f"synthetic-routing-{self.case_number}@example.test")
        request = self.create_request(case)
        answers = clef_answers(crisis=0.9 if case["crisis"] else 0.03)
        evidence = decision if engine == "llm" else {
            **decision, "engine": "clef", "model": "clef",
            "probabilities": {key: value["noul"] for key, value in answers.items()},
        }

        def model_call(*args, **kwargs):
            # Neither rejection nor approval effects may precede model evaluation.
            request.refresh_from_db()
            self.assertEqual(request.status, "pending_moderation")
            self.assertFalse(request.reviewed)
            event.assert_not_called()
            milestone.assert_not_called()
            email.assert_not_called()
            if error:
                raise RuntimeError(error)
            return evidence

        with override_settings(PRAYER_MODERATION_ENGINE=engine, PRAYER_MODERATION_CLEF_MODEL="clef"), \
             patch("prayers.tasks._llm_moderation_result", side_effect=model_call) as llm, \
             patch("prayers.tasks.clef_moderation_result", side_effect=model_call) as clef, \
             patch("prayers.tasks._send_moderation_alert_email") as email, \
             patch("prayers.tasks.Event.create_event") as event, \
             patch("prayers.tasks.UserMilestone.create_milestone") as milestone:
            result = moderate_prayer_request_task(request.id)
            selected, unused = (clef, llm) if engine == "clef" else (llm, clef)
            selected.assert_called_once()
            unused.assert_not_called()
            if alert:
                email.assert_called_once_with(request, alert)
            else:
                email.assert_not_called()
            if status != "approved":
                event.assert_not_called()
                milestone.assert_not_called()
            else:
                event.assert_called_once()
                milestone.assert_called_once()

        request.refresh_from_db()
        self.assertEqual(result["success"], error is None)
        self.assertEqual(request.status, status)
        self.assertEqual(request.moderation_severity, severity)
        self.assertEqual(request.requires_human_review, review)
        self.assertEqual(request.reviewed, error is None)
        self.assertEqual(request.moderation_result["profanity_check"], {
            "passed": not (case["title_profanity"] or case["description_profanity"]),
            "title_contains_profanity": case["title_profanity"],
            "description_contains_profanity": case["description_profanity"],
        })
        self.assertEqual(request.moderation_result["llm_check"], {"error": error} if error else evidence)
        self.assertEqual(PrayerRequestAcceptance.objects.filter(prayer_request=request).exists(), status == "approved")
        if not error:
            self.assertIsNotNone(request.moderated_at)

    def test_crisis_precedes_profanity_in_either_field_for_both_engines(self):
        decision = decide({key: value["noul"] for key, value in clef_answers(crisis=0.9).items()})
        for engine in ("llm", "clef"):
            for case in CASES[:3]:
                with self.subTest(engine=engine, case=case["id"]):
                    self.run_case(engine, case, decision, "rejected", "critical_safety_concern", "critical", True)

    def test_critical_severity_overrides_approval_without_escalate_action(self):
        decision = {"approved": True, "severity": " CRITICAL ", "suggested_action": "approve"}
        for engine in ("llm", "clef"):
            with self.subTest(engine=engine):
                self.run_case(engine, CASES[0], decision, "rejected", "critical_safety_concern", "critical", True)

    def test_action_only_escalation_persists_critical(self):
        decision = {"approved": True, "severity": "low", "suggested_action": " ESCALATE ", "reason": "Safety"}
        for engine in ("llm", "clef"):
            for case in (CASES[3], CASES[4]):
                with self.subTest(engine=engine, case=case["id"]):
                    self.run_case(engine, case, decision, "rejected", "critical_safety_concern", "critical", True)

    def test_profanity_rejects_all_noncritical_model_routes(self):
        for engine in ("llm", "clef"):
            for approved, severity, action, review in (
                (True, "low", "approve", False), (False, "medium", "reject", False),
                (True, "high", "flag_for_review", True),
            ):
                with self.subTest(engine=engine, action=action):
                    decision = {"approved": approved, "severity": severity,
                                "suggested_action": action, "requires_human_review": review, "reason": "Synthetic"}
                    self.run_case(engine, CASES[3], decision, "rejected", "profanity_detected", severity, False)

    def test_engine_error_with_profanity_remains_pending_for_review(self):
        for engine in ("llm", "clef"):
            for case in CASES[:4]:
                with self.subTest(engine=engine, case=case["id"]):
                    self.run_case(engine, case, {}, "pending_moderation", "llm_error", "high", True,
                                  error="Synthetic provider failure")

    def test_nonprofane_routes_remain_unchanged(self):
        for engine in ("llm", "clef"):
            for approved, severity, action, status, alert, review in (
                (True, "low", "approve", "approved", None, False),
                (False, "medium", "reject", "rejected", "llm_rejected", False),
                (True, "high", "flag_for_review", "pending_moderation", "requires_review", True),
                (False, "critical", "reject", "rejected", "critical_safety_concern", True),
            ):
                with self.subTest(engine=engine, action=action, severity=severity):
                    self.run_case(engine, CASES[4], {"approved": approved, "severity": severity,
                                  "suggested_action": action}, status, alert, severity, review)

    def test_already_reviewed_is_unchanged(self):
        for engine in ("llm", "clef"):
            with self.subTest(engine=engine):
                request = self.create_request(CASES[0])
                request.reviewed = True
                request.moderation_result = {"existing": "evidence"}
                request.save()
                before = PrayerRequest.objects.filter(pk=request.pk).values().get()
                with override_settings(PRAYER_MODERATION_ENGINE=engine), \
                     patch("prayers.tasks._llm_moderation_result") as llm, \
                     patch("prayers.tasks.clef_moderation_result") as clef, \
                     patch("prayers.tasks._send_moderation_alert_email") as email, \
                     patch("prayers.tasks.profanity.contains_profanity") as profanity:
                    self.assertEqual(moderate_prayer_request_task(request.id),
                                     {"success": True, "already_moderated": True})
                for mock in (llm, clef, email, profanity):
                    mock.assert_not_called()
                self.assertEqual(PrayerRequest.objects.filter(pk=request.pk).values().get(), before)
