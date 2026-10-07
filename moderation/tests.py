from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse

from moderation.models import Responsibility, Review
from prayers.models import PrayerRequest
from events.models import EventType
from prayers.tasks import _send_moderation_alert_email


class ModerationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        EventType.objects.create(code=EventType.PRAYER_REQUEST_CREATED, name="Prayer created")
        User = get_user_model()
        cls.owner = User.objects.create_user(username="owner", email="owner@example.test")
        cls.general = User.objects.create_user(username="general", email="general@example.test", password="pass")
        cls.crisis = User.objects.create_user(username="crisis", email="crisis@example.test")
        cls.staff = User.objects.create_user(username="staff", is_staff=True)
        cls.superuser = User.objects.create_user(username="admin", is_staff=True, is_superuser=True)
        cls.inactive = User.objects.create_user(username="inactive", email="inactive@example.test", is_active=False)
        Responsibility.objects.create(user=cls.general, general=True)
        Responsibility.objects.create(user=cls.crisis, crisis=True)
        Responsibility.objects.create(user=cls.inactive, general=True, crisis=True)
        cls.routine = PrayerRequest.objects.create(
            requester=cls.owner, title="Routine synthetic prayer", description="Synthetic request"
        )
        cls.urgent = PrayerRequest.objects.create(
            requester=cls.owner,
            title="Secret synthetic crisis",
            description="Sensitive synthetic body",
            status="rejected",
            moderation_severity="critical",
            requires_human_review=True,
            moderation_result={"llm_check": {"suggested_action": "escalate"}},
        )

    def detail(self, obj):
        return reverse("moderation-detail", args=["prayer", obj.pk])

    def test_permission_matrix_and_no_crisis_leaks(self):
        for user, routine, urgent, dashboard in [
            (self.owner, 403, 403, 403),
            (self.staff, 403, 403, 403),
            (self.superuser, 403, 403, 403),
            (self.general, 200, 404, 200),
            (self.crisis, 404, 200, 200),
        ]:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.detail(self.routine)).status_code, routine)
                self.assertEqual(self.client.get(self.detail(self.urgent)).status_code, urgent)
                response = self.client.get(reverse("moderation-dashboard"))
                self.assertEqual(response.status_code, dashboard)
                if user == self.general:
                    self.assertNotContains(response, self.urgent.title)
                    self.assertContains(response, "Prayer · 1")
                    self.assertNotContains(
                        self.client.get(reverse("moderation-dashboard") + "?status=audit"), self.urgent.title
                    )

    def test_nonstaff_can_sign_in_but_not_admin(self):
        response = self.client.post(
            reverse("moderation-login"), {"username": "general@example.test", "password": "pass"}
        )
        self.assertRedirects(response, reverse("moderation-dashboard"))
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_direct_post_cannot_escalate_or_publish_crisis(self):
        self.client.force_login(self.general)
        self.assertEqual(
            self.client.post(self.detail(self.urgent), {"action": "approve", "note": "bad"}).status_code, 404
        )
        self.client.force_login(self.crisis)
        self.assertEqual(
            self.client.post(self.detail(self.urgent), {"action": "approve", "note": "bad"}).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                self.detail(self.urgent), {"action": "escalated", "note": "Human escalation recorded"}
            ).status_code,
            302,
        )
        self.urgent.refresh_from_db()
        self.assertEqual(self.urgent.status, "rejected")
        self.assertFalse(self.urgent.requires_human_review)
        self.assertEqual(Review.objects.get().reviewer, self.crisis)
        self.assertNotContains(self.client.get(reverse("moderation-dashboard")), self.urgent.title)

    def test_routine_approval_reuses_existing_behavior(self):
        self.client.force_login(self.general)
        response = self.client.post(self.detail(self.routine), {"action": "approve", "note": "Reviewed synthetic need"})
        self.assertEqual(response.status_code, 302)
        self.routine.refresh_from_db()
        self.assertEqual(self.routine.status, "approved")
        self.assertTrue(self.routine.acceptances.filter(user=self.owner, counts_for_milestones=False).exists())
        self.assertEqual(Review.objects.get().outcome, "approve")

    def test_assignment_admin_only_and_no_self_assignment(self):
        handler = admin.site._registry[Responsibility]
        request = RequestFactory().get("/")
        for user in [self.general, self.staff, self.owner]:
            request.user = user
            self.assertFalse(handler.has_change_permission(request))
        request.user = self.superuser
        self.assertTrue(handler.has_change_permission(request))
        with self.assertRaises(PermissionDenied):
            handler.save_model(request, Responsibility(user=self.superuser, crisis=True), None, False)

    def test_email_recipients_active_explicit_and_no_sensitive_body(self):
        _send_moderation_alert_email(self.urgent, "critical_safety_concern")
        self.assertEqual([m.to for m in mail.outbox], [[self.crisis.email]])
        self.assertNotIn(self.urgent.title, mail.outbox[0].body)
        self.assertNotIn(self.urgent.description, mail.outbox[0].body)
        self.assertNotIn(self.owner.email, mail.outbox[0].body)
        mail.outbox.clear()
        _send_moderation_alert_email(self.routine, "requires_review")
        self.assertEqual([m.to for m in mail.outbox], [[self.general.email]])
        Responsibility.objects.filter(user=self.general).update(general=False)
        mail.outbox.clear()
        _send_moderation_alert_email(self.routine, "requires_review")
        self.assertEqual(mail.outbox, [])

    def test_mock_email_duplicates_and_revocation(self):
        duplicate = get_user_model().objects.create_user(username="duplicate", email="GENERAL@EXAMPLE.TEST")
        Responsibility.objects.create(user=duplicate, general=True)
        with patch("moderation.notifications.send_mail") as sender:
            _send_moderation_alert_email(self.routine, "requires_review")
            self.assertEqual(sender.call_count, 1)
            self.assertEqual(sender.call_args.args[3], ["general@example.test"])

    def test_admin_direct_crisis_hidden_even_from_superuser(self):
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(f"/admin/prayers/prayerrequest/{self.urgent.pk}/change/").status_code, 302)
        self.assertNotContains(self.client.get("/admin/prayers/prayerrequest/"), self.urgent.title)
        Responsibility.objects.create(user=self.superuser, crisis=True)
        self.assertEqual(self.client.get(f"/admin/prayers/prayerrequest/{self.urgent.pk}/change/").status_code, 200)

    def test_inactive_and_revoked_dashboard_access(self):
        self.client.force_login(self.inactive)
        self.assertEqual(self.client.get(reverse("moderation-dashboard")).status_code, 302)
        self.client.force_login(self.general)
        Responsibility.objects.filter(user=self.general).update(general=False)
        self.assertEqual(self.client.get(reverse("moderation-dashboard")).status_code, 403)

    def test_existing_staff_api_cannot_read_another_users_crisis(self):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(self.superuser)
        self.assertEqual(client.get(reverse("prayers:prayer-request-detail", args=[self.urgent.pk])).status_code, 404)
        response = client.get(reverse("prayers:prayer-request-list"), {"status": "rejected"})
        self.assertNotIn(self.urgent.title, str(response.data))
        Responsibility.objects.create(user=self.superuser, crisis=True)
        self.assertEqual(client.get(reverse("prayers:prayer-request-detail", args=[self.urgent.pk])).status_code, 200)

    def test_icon_queue_notification_resolution_and_audit(self):
        from icons.models import Icon, IconFeedback

        from hub.models import Church

        icon = Icon.objects.create(title="Synthetic icon", church=Church.objects.create(name="Icon church"))
        with patch("moderation.notifications.send_mail") as sender, self.captureOnCommitCallbacks(execute=True):
            feedback = IconFeedback.objects.create(
                icon=icon, feedback_type="mislabel", description="Synthetic report", icon_title_at_time="Synthetic icon"
            )
        self.assertEqual(sender.call_count, 1)
        self.assertNotIn(feedback.description, sender.call_args.args[1])
        url = reverse("moderation-detail", args=["icon", feedback.pk])
        self.client.force_login(self.crisis)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.general)
        self.assertContains(self.client.get(reverse("moderation-dashboard")), "Synthetic icon")
        self.assertEqual(self.client.post(url, {"action": "resolve", "note": "Verified correction"}).status_code, 302)
        feedback.refresh_from_db()
        self.assertTrue(feedback.is_resolved)
        self.assertEqual(feedback.admin_notes, "Verified correction")
        self.assertNotContains(self.client.get(reverse("moderation-dashboard")), "Synthetic icon")
        self.assertContains(
            self.client.get(reverse("moderation-dashboard") + "?type=icon&status=audit"), "Synthetic icon"
        )

    @override_settings(FEAST_CONTEXT_REGENERATION_THRESHOLD=5)
    def test_context_signal_acknowledgement_new_votes_and_no_provider_calls(self):
        from hub.models import Feast, FeastContext, Church

        church = Church.objects.create(name="Synthetic church")
        feast = Feast.objects.create(church=church, observance_id="synthetic-feast", name="Synthetic feast")
        context = FeastContext.objects.create(
            feast=feast, text="Synthetic feedback context", short_text="Synthetic", thumbs_down=5
        )
        below = FeastContext.objects.create(
            feast=Feast.objects.create(church=church, observance_id="below", name="Below"),
            text="Below threshold",
            short_text="Below",
            thumbs_down=4,
        )
        self.client.force_login(self.general)
        url = reverse("moderation-detail", args=["feast", context.pk])
        response = self.client.get(reverse("moderation-dashboard"))
        self.assertContains(response, f"Feast context #{context.pk}")
        self.assertNotContains(response, f"Feast context #{below.pk}")
        self.assertContains(response, "not publication hold")
        self.assertEqual(
            self.client.post(url, {"action": "acknowledge", "note": "Reviewed feedback signal"}).status_code, 302
        )
        self.assertNotContains(self.client.get(reverse("moderation-dashboard")), f"Feast context #{context.pk}")
        FeastContext.objects.filter(pk=context.pk).update(thumbs_down=6)
        self.assertContains(self.client.get(reverse("moderation-dashboard")), f"Feast context #{context.pk}")
        self.assertContains(self.client.get(url), "Reviewed feedback signal")

    def test_both_capabilities_and_oldest_first(self):
        from datetime import timedelta
        from django.utils import timezone

        Responsibility.objects.filter(user=self.general).update(crisis=True)
        older = PrayerRequest.objects.create(
            requester=self.owner, title="Oldest synthetic routine", description="Synthetic"
        )
        PrayerRequest.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(days=2))
        self.client.force_login(self.general)
        response = self.client.get(reverse("moderation-dashboard") + "?type=prayer")
        self.assertContains(response, "Prayer · 3")
        self.assertContains(response, self.urgent.title)
        self.assertLess(
            response.content.index(older.title.encode()), response.content.index(self.routine.title.encode())
        )
        self.assertContains(response, "Request creation (not first flag)")

    def test_misclassification_is_a_note_not_a_publication_decision(self):
        self.client.force_login(self.crisis)
        response = self.client.post(
            self.detail(self.urgent),
            {
                "action": "misclassification",
                "note": "Synthetic review note",
                "expected_outcome": "approve",
                "regression_reference": "synthetic-case-reference",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.urgent.refresh_from_db()
        self.assertEqual(self.urgent.status, "rejected")
        self.assertTrue(self.urgent.requires_human_review)
        self.assertContains(self.client.get(reverse("moderation-dashboard")), self.urgent.title)
        self.assertContains(self.client.get(self.detail(self.urgent)), "synthetic-case-reference")

    def test_notification_failure_does_not_change_moderation_state(self):
        with patch("moderation.notifications.get_user_model", side_effect=RuntimeError("Synthetic database failure")):
            _send_moderation_alert_email(self.urgent, "critical_safety_concern")
        with patch("moderation.notifications.send_mail", side_effect=RuntimeError("Synthetic mail failure")):
            _send_moderation_alert_email(self.urgent, "critical_safety_concern")
        self.urgent.refresh_from_db()
        self.assertEqual(self.urgent.status, "rejected")
        self.assertTrue(self.urgent.requires_human_review)
