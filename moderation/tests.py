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

    def test_crisis_reviewer_can_see_requester_contact_details(self):
        self.owner.first_name = "Synthetic"
        self.owner.last_name = "Requester"
        self.owner.save(update_fields=["first_name", "last_name"])
        self.urgent.is_anonymous = True
        self.urgent.save(update_fields=["is_anonymous"])
        self.client.force_login(self.crisis)
        response = self.client.get(self.detail(self.urgent))
        self.assertContains(response, "Synthetic Requester")
        self.assertContains(response, f"account #{self.owner.pk}")
        self.assertContains(response, 'href="mailto:owner@example.test"')
        self.assertContains(response, "submitted anonymously for public display")
        self.client.force_login(self.general)
        self.assertEqual(self.client.get(self.detail(self.urgent)).status_code, 404)
        response = self.client.get(self.detail(self.routine))
        self.assertNotContains(response, "Contact email:")
        self.assertNotContains(response, self.owner.email)

    def test_crisis_requester_without_email_has_explicit_contact_empty_state(self):
        self.owner.email = ""
        self.owner.save(update_fields=["email"])
        self.client.force_login(self.crisis)
        response = self.client.get(self.detail(self.urgent))
        self.assertContains(response, "No contact email on this account.")
        self.assertNotContains(response, 'href="mailto:')

    def test_admin_quick_link_requires_explicit_responsibility_and_opens_scoped_dashboard(self):
        for general, crisis in [(False, False), (True, False), (False, True), (True, True)]:
            with self.subTest(general=general, crisis=crisis):
                Responsibility.objects.update_or_create(
                    user=self.staff, defaults={"general": general, "crisis": crisis}
                )
                self.client.force_login(self.staff)
                response = self.client.get(reverse("admin:index"))
                links = [a for a in response.context["admin_quick_actions"] if a["name"] == "Moderation Dashboard"]
                self.assertEqual(len(links), int(general or crisis))
                if links:
                    self.assertContains(response, 'class="module fp-quick-actions"')
                    self.assertEqual(links[0]["url"], reverse("moderation-dashboard"))
                    dashboard = self.client.get(links[0]["url"])
                    self.assertEqual(dashboard.status_code, 200)
                    if not crisis:
                        self.assertNotContains(dashboard, self.urgent.title)
                else:
                    self.assertNotContains(response, "Moderation Dashboard")
        self.client.force_login(self.superuser)
        self.assertNotContains(self.client.get(reverse("admin:index")), "Moderation Dashboard")
        self.staff.is_active = False
        self.staff.save(update_fields=["is_active"])
        request = RequestFactory().get(reverse("admin:index"))
        request.user = self.staff
        self.assertFalse(admin.site.each_context(request)["moderation_access"])

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
        with self.assertLogs("moderation.notifications", "ERROR"):
            _send_moderation_alert_email(self.routine, "requires_review")
        self.assertEqual([m.to for m in mail.outbox], [["fastandprayhelp@gmail.com"]])
        self.assertNotIn(self.routine.title, mail.outbox[0].body)

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
                "regression_reference": "case-01",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.urgent.refresh_from_db()
        self.assertEqual(self.urgent.status, "rejected")
        self.assertTrue(self.urgent.requires_human_review)
        self.assertContains(self.client.get(reverse("moderation-dashboard")), self.urgent.title)
        self.assertContains(self.client.get(self.detail(self.urgent)), "case-01 — Healing for my mom")

    def test_notification_failure_does_not_change_moderation_state(self):
        with patch("moderation.notifications.get_user_model", side_effect=RuntimeError("Synthetic database failure")):
            _send_moderation_alert_email(self.urgent, "critical_safety_concern")
        with patch("moderation.notifications.send_mail", side_effect=RuntimeError("Synthetic mail failure")):
            _send_moderation_alert_email(self.urgent, "critical_safety_concern")
        self.urgent.refresh_from_db()
        self.assertEqual(self.urgent.status, "rejected")
        self.assertTrue(self.urgent.requires_human_review)

    def test_unknown_or_withheld_test_reference_cannot_close_a_review(self):
        self.client.force_login(self.crisis)
        for reference in ["case-14", "case-99", "an arbitrary object id", "<script>bad()</script>"]:
            with self.subTest(reference=reference):
                response = self.client.post(
                    self.detail(self.urgent),
                    {
                        "action": "acknowledge",
                        "note": "Keep my notes",
                        "regression_reference": reference,
                    },
                )
                self.assertEqual(response.status_code, 400)
                self.assertContains(response, "Choose an existing test case from the list.", status_code=400)
                self.assertContains(response, "Keep my notes", status_code=400)
                self.assertFalse(Review.objects.exists())
                self.urgent.refresh_from_db()
                self.assertTrue(self.urgent.requires_human_review)

    def test_invalid_correct_decision_cannot_be_recorded(self):
        self.client.force_login(self.general)
        response = self.client.post(
            self.detail(self.routine),
            {
                "action": "misclassification",
                "note": "This needs review",
                "expected_outcome": "free text decision",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Choose a correct decision from the list.", status_code=400)
        self.assertFalse(Review.objects.exists())
        self.routine.refresh_from_db()
        self.assertEqual(self.routine.status, "pending_moderation")

    def test_selected_case_is_saved_and_shown_even_without_a_corrected_decision(self):
        self.client.force_login(self.crisis)
        response = self.client.post(
            self.detail(self.urgent),
            {
                "action": "acknowledge",
                "note": "Synthetic follow-up completed",
                "regression_reference": "case-15",
            },
        )
        self.assertEqual(response.status_code, 302)
        review = Review.objects.get()
        self.assertEqual(review.regression_reference, "case-15")
        self.assertEqual(review.expected_outcome, "")
        response = self.client.get(self.detail(self.urgent))
        self.assertContains(response, "Related test case: case-15")
        self.assertContains(response, "Close after follow-up")

    def test_optional_test_catalogue_is_not_required_to_complete_a_review(self):
        self.client.force_login(self.crisis)
        with patch("moderation.forms.published_test_cases", return_value=()):
            response = self.client.get(self.detail(self.urgent))
            self.assertContains(response, "No test cases are currently available.")
            response = self.client.post(
                self.detail(self.urgent),
                {
                    "action": "acknowledge",
                    "note": "Follow-up complete",
                    "regression_reference": "",
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Review.objects.get().regression_reference, "")

    def test_old_audit_references_are_preserved_and_escaped(self):
        Review.objects.create(
            kind="prayer",
            object_id=self.routine.pk,
            reviewer=self.general,
            outcome="misclassification",
            note="Prior review",
            signal_version="old",
            expected_outcome="Legacy decision",
            regression_reference="<script>legacy</script>",
        )
        self.client.force_login(self.general)
        response = self.client.get(self.detail(self.routine))
        self.assertContains(response, "Correct decision: Legacy decision")
        self.assertContains(response, "Related test case: &lt;script&gt;legacy&lt;/script&gt;")
        self.assertNotContains(response, "<script>legacy</script>")

    def test_icon_report_has_one_explicit_closure_action_and_no_prayer_case_selector(self):
        from hub.models import Church
        from icons.models import Icon, IconFeedback

        church = Church.objects.create(name="Report preview church")
        icon = Icon.objects.create(title="Icon to correct", church=church)
        with patch("moderation.notifications.send_mail"):
            feedback = IconFeedback.objects.create(icon=icon, feedback_type="mislabel", description="Incorrect label")
        url = reverse("moderation-detail", args=["icon", feedback.pk])
        self.client.force_login(self.general)
        response = self.client.get(url)
        self.assertContains(response, '<option value="resolve">Close report</option>', html=True)
        self.assertNotContains(response, '<option value="acknowledge">')
        self.assertNotContains(response, 'id="review-reference"')
        self.assertContains(response, "Open report")
        for payload in [
            {"action": "acknowledge", "note": "Seen"},
            {"action": "resolve", "note": "Checked", "regression_reference": "case-01"},
            {"action": "misclassification", "note": "Checked", "expected_outcome": "approve"},
        ]:
            self.assertIn(self.client.post(url, payload).status_code, [400, 403])
            feedback.refresh_from_db()
            self.assertFalse(feedback.is_resolved)
            self.assertFalse(Review.objects.exists())
        response = self.client.post(url, {"action": "resolve", "note": "Corrected the icon title"})
        self.assertEqual(response.status_code, 302)
        feedback.refresh_from_db()
        self.assertTrue(feedback.is_resolved)
        self.assertEqual(feedback.admin_notes, "Corrected the icon title")
        icon.refresh_from_db()
        self.assertEqual(icon.title, "Icon to correct")

    def prayer_admin_payload(self, obj, status):
        return {
            "title": obj.title,
            "description": obj.description,
            "requester": obj.requester_id,
            "duration_days": obj.duration_days,
            "status": status,
            "requires_human_review": "on",
            "_save": "Save",
        }

    def test_crisis_admin_form_cannot_publish_even_for_designated_superuser(self):
        Responsibility.objects.create(user=self.superuser, general=True, crisis=True)
        self.client.force_login(self.superuser)
        for severity, result in [("critical", {}), ("high", {"llm_check": {"suggested_action": "escalate"}})]:
            with self.subTest(severity=severity):
                PrayerRequest.objects.filter(pk=self.urgent.pk).update(
                    moderation_severity=severity, moderation_result=result
                )
                self.urgent.refresh_from_db()
                response = self.client.post(
                    f"/admin/prayers/prayerrequest/{self.urgent.pk}/change/",
                    self.prayer_admin_payload(self.urgent, "approved"),
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("status", response.context["adminform"].form.errors)
                self.urgent.refresh_from_db()
                self.assertEqual(self.urgent.status, "rejected")
                self.assertTrue(self.urgent.requires_human_review)
                self.assertFalse(self.urgent.acceptances.exists())

    def test_admin_bulk_approval_skips_crisis_but_approves_routine(self):
        Responsibility.objects.create(user=self.superuser, general=True, crisis=True)
        PrayerRequest.objects.filter(pk=self.urgent.pk).update(status="pending_moderation")
        self.client.force_login(self.superuser)
        response = self.client.post(
            "/admin/prayers/prayerrequest/",
            {"action": "approve_requests", "_selected_action": [self.urgent.pk, self.routine.pk], "index": "0"},
        )
        self.assertEqual(response.status_code, 302)
        self.urgent.refresh_from_db()
        self.routine.refresh_from_db()
        self.assertEqual(self.urgent.status, "pending_moderation")
        self.assertTrue(self.urgent.requires_human_review)
        self.assertEqual(self.routine.status, "approved")
        self.assertFalse(self.urgent.acceptances.exists())

    def test_related_admin_forms_reject_forged_crisis_ids_without_capability(self):
        from django.contrib.auth.models import Permission
        from prayers.models import PrayerRequestAcceptance, PrayerRequestPrayerLog

        self.staff.user_permissions.add(*Permission.objects.filter(content_type__app_label="prayers"))
        Responsibility.objects.create(user=self.staff, general=True)
        for actor in [self.staff, self.superuser]:
            self.client.force_login(actor)
            for model, suffix in [
                (PrayerRequestAcceptance, "prayerrequestacceptance"),
                (PrayerRequestPrayerLog, "prayerrequestprayerlog"),
            ]:
                with self.subTest(actor=actor.username, model=suffix):
                    response = self.client.post(
                        f"/admin/prayers/{suffix}/add/",
                        {
                            "prayer_request": self.urgent.pk,
                            "user": self.owner.pk,
                            "prayed_on_date": "2026-10-07",
                            "_save": "Save",
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("prayer_request", response.context["adminform"].form.errors)
                    self.assertFalse(model.objects.filter(prayer_request=self.urgent).exists())
                    self.assertNotContains(response, self.urgent.title)

    def test_related_admin_change_and_creation_permission_matrix(self):
        from django.contrib.auth.models import Permission
        from prayers.models import PrayerRequestAcceptance, PrayerRequestPrayerLog

        self.staff.user_permissions.add(*Permission.objects.filter(content_type__app_label="prayers"))
        for actor in [self.staff, self.superuser]:
            for crisis_capability in [False, True]:
                Responsibility.objects.update_or_create(
                    user=actor, defaults={"general": True, "crisis": crisis_capability}
                )
                self.client.cookies.clear()
                self.client.force_login(actor)
                for model, suffix in [
                    (PrayerRequestAcceptance, "prayerrequestacceptance"),
                    (PrayerRequestPrayerLog, "prayerrequestprayerlog"),
                ]:
                    with self.subTest(actor=actor.username, crisis=crisis_capability, model=suffix):
                        values = {"prayer_request": self.routine, "user": self.owner}
                        if model is PrayerRequestPrayerLog:
                            values["prayed_on_date"] = "2026-10-07"
                        original = model.objects.create(**values)
                        payload = {
                            "prayer_request": self.urgent.pk,
                            "user": self.owner.pk,
                            "prayed_on_date": "2026-10-07",
                            "_save": "Save",
                        }
                        response = self.client.post(f"/admin/prayers/{suffix}/{original.pk}/change/", payload)
                        self.assertEqual(response.status_code, 302 if crisis_capability else 200)
                        original.refresh_from_db()
                        self.assertEqual(
                            original.prayer_request_id, self.urgent.pk if crisis_capability else self.routine.pk
                        )
                        if not crisis_capability:
                            self.assertIn("prayer_request", response.context["adminform"].form.errors)
                            self.assertNotContains(response, self.urgent.title)
                        original.delete()
                        response = self.client.post(f"/admin/prayers/{suffix}/add/", payload)
                        self.assertEqual(response.status_code, 302 if crisis_capability else 200)
                        self.assertEqual(
                            model.objects.filter(prayer_request=self.urgent).count(), int(crisis_capability)
                        )
                        model.objects.all().delete()

    def test_crisis_publication_denied_for_crisis_only_staff_and_superuser(self):
        from django.contrib.auth.models import Permission

        self.staff.user_permissions.add(*Permission.objects.filter(content_type__app_label="prayers"))
        for actor in [self.staff, self.superuser]:
            Responsibility.objects.update_or_create(user=actor, defaults={"general": False, "crisis": True})
            self.client.force_login(actor)
            response = self.client.post(
                f"/admin/prayers/prayerrequest/{self.urgent.pk}/change/",
                self.prayer_admin_payload(self.urgent, "approved"),
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("status", response.context["adminform"].form.errors)
            self.urgent.refresh_from_db()
            self.assertEqual(self.urgent.status, "rejected")

    def test_crisis_admin_cannot_forge_save_as_new_to_publish_a_copy(self):
        Responsibility.objects.create(user=self.superuser, general=True, crisis=True)
        self.client.force_login(self.superuser)
        payload = self.prayer_admin_payload(self.urgent, "approved")
        payload["_saveasnew"] = "Save as new"
        response = self.client.post(f"/admin/prayers/prayerrequest/{self.urgent.pk}/change/", payload)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(PrayerRequest.objects.count(), 2)

    def test_admin_theme_reuse_keeps_nonstaff_navigation_scoped(self):
        self.client.force_login(self.general)
        response = self.client.get(reverse("moderation-dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "admin/css/fastandpray-admin.css")
        self.assertContains(response, "fp-admin-section__grid")
        self.assertContains(response, "fp-admin-app")
        self.assertContains(response, "General moderation")
        self.assertNotContains(response, 'href="/admin/"')
        self.assertNotContains(response, "/admin/auth/user/")
        self.assertNotContains(response, "/admin/password_change/")
        self.assertNotContains(response, self.urgent.title)
        detail = self.client.get(self.detail(self.routine))
        self.assertContains(detail, 'id="nav-sidebar"')
        self.assertContains(detail, "module aligned")
        self.assertNotContains(detail, 'href="/admin/"')
        self.assertNotContains(detail, "Existing admin tools")
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_admin_shell_does_not_promote_nonstaff_model_permissions(self):
        from django.contrib.auth.models import Permission

        self.general.user_permissions.add(*Permission.objects.filter(content_type__app_label="auth"))
        self.client.force_login(self.general)
        response = self.client.get(self.detail(self.routine))
        self.assertNotContains(response, "/admin/auth/")
        self.assertEqual(response.context["available_apps"], [])
        self.assertEqual(response.context["admin_sections"][0]["slug"], "moderation")

    def test_staff_shell_uses_existing_permission_filtered_admin_navigation(self):
        from django.contrib.auth.models import Permission

        self.staff.user_permissions.add(Permission.objects.get(codename="view_prayerrequest"))
        Responsibility.objects.create(user=self.staff, general=True)
        self.client.force_login(self.staff)
        response = self.client.get(self.detail(self.routine))
        self.assertContains(response, 'href="/admin/"')
        self.assertContains(response, "/admin/prayers/prayerrequest/")
        self.assertNotContains(response, "/admin/auth/user/")

    def test_moderation_login_reuses_admin_brand_without_staff_login_gate(self):
        response = self.client.get(reverse("moderation-login"))
        self.assertContains(response, "admin/css/login.css")
        self.assertContains(response, "admin/brand/wordmark.png")
        self.assertNotContains(response, 'href="/admin/"')
        self.assertContains(response, "Moderation")
        response = self.client.post(reverse("moderation-login"), {"username": self.general.email, "password": "pass"})
        self.assertRedirects(response, reverse("moderation-dashboard"))

    def test_outcome_has_no_default_and_errors_render_inline_keeping_the_note(self):
        self.client.force_login(self.general)
        detail = self.client.get(self.detail(self.routine))
        self.assertContains(detail, '<option value="" selected>Choose a decision…</option>', html=True)
        self.assertContains(detail, "Approve and publish")
        response = self.client.post(self.detail(self.routine), {"action": "", "note": "Checking with Fr."})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Choose a decision.", status_code=400)
        self.assertContains(response, "Checking with Fr.", status_code=400)
        self.routine.refresh_from_db()
        self.assertEqual(self.routine.status, "pending_moderation")
        response = self.client.post(
            self.detail(self.routine), {"action": "misclassification", "note": "A long typed note"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Choose the decision you think is correct.", status_code=400)
        self.assertContains(response, "A long typed note", status_code=400)
        self.assertFalse(Review.objects.exists())

    def test_moderation_evidence_is_formatted(self):
        self.client.force_login(self.crisis)
        response = self.client.get(self.detail(self.urgent))
        self.assertContains(response, 'class="fp-review-evidence"')
        self.assertContains(response, "&quot;suggested_action&quot;: &quot;escalate&quot;")
        self.assertNotContains(response, "{&#x27;llm_check&#x27;")

    def test_unassigned_user_gets_explanation_and_sign_out(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.detail(self.routine))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "no moderation responsibility assigned", status_code=403)
        self.assertContains(response, reverse("moderation-logout"), status_code=403)
        self.assertNotContains(response, self.routine.title, status_code=403)

    @override_settings(FEAST_CONTEXT_REGENERATION_THRESHOLD=5)
    def test_regenerated_context_stays_queued_and_audit_shows_reviewed_items(self):
        from hub.models import Church, Feast, FeastContext
        from icons.models import Icon, IconFeedback

        church = Church.objects.create(name="Synthetic church")
        feast = Feast.objects.create(church=church, observance_id="synthetic-feast", name="Synthetic feast")
        replaced = FeastContext.objects.create(
            feast=feast, text="Downvoted text", short_text="Downvoted", thumbs_down=5, active=False
        )
        icon = Icon.objects.create(title="Synthetic icon", church=church)
        with patch("moderation.notifications.send_mail"):
            feedback = IconFeedback.objects.create(icon=icon, feedback_type="mislabel", description="Report")
        self.client.force_login(self.general)
        dashboard = reverse("moderation-dashboard")
        response = self.client.get(dashboard + "?type=feast")
        self.assertContains(response, f"Feast context #{replaced.pk}")
        self.assertContains(response, "replaced by regeneration")
        self.assertNotContains(self.client.get(dashboard + "?type=feast&status=audit"), f"#{replaced.pk}")

        for kind, pk in [("feast", replaced.pk), ("icon", feedback.pk)]:
            action = "resolve" if kind == "icon" else "acknowledge"
            self.client.post(reverse("moderation-detail", args=[kind, pk]), {"action": action, "note": "Reviewed"})
        self.assertNotContains(self.client.get(dashboard + "?type=feast"), f"#{replaced.pk}")
        self.assertContains(self.client.get(dashboard + "?type=feast&status=audit"), f"Feast context #{replaced.pk}")
        self.assertContains(
            self.client.get(dashboard + "?type=icon&status=audit"),
            reverse("moderation-detail", args=["icon", feedback.pk]),
        )

    def test_dashboard_query_count_does_not_scale_with_queue_size(self):
        self.client.force_login(self.general)
        url = reverse("moderation-dashboard") + "?type=prayer"

        def queries():
            from django.db import connection
            from django.test.utils import CaptureQueriesContext

            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(self.client.get(url).status_code, 200)
            return len(captured)

        before = queries()
        for n in range(40):
            PrayerRequest.objects.create(requester=self.owner, title=f"Bulk {n}", description="Synthetic")
        self.assertEqual(queries(), before)
        self.assertContains(self.client.get(url), "Showing the oldest 25 of 41.")

    def test_dashboard_rechecks_crisis_authorization_after_reading_signal_ids(self):
        from moderation import views

        self.client.force_login(self.general)
        original = views.outstanding_pks
        for classification in [
            {"moderation_severity": "critical"},
            {"moderation_result": {"llm_check": {"suggested_action": "escalate"}}},
        ]:
            with self.subTest(classification=classification):
                PrayerRequest.objects.filter(pk=self.routine.pk).update(
                    moderation_severity="low", moderation_result=None
                )

                def classify_after_ids(kind, queryset):
                    pks = original(kind, queryset)
                    self.assertIn(self.routine.pk, pks)
                    PrayerRequest.objects.filter(pk=self.routine.pk).update(**classification)
                    return pks

                with patch("moderation.views.outstanding_pks", side_effect=classify_after_ids):
                    response = self.client.get(reverse("moderation-dashboard") + "?type=prayer")
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, self.routine.title)
                self.assertNotContains(response, self.detail(self.routine))
                self.assertEqual(response.context["groups"][0]["count"], 0)
                self.assertEqual(response.context["groups"][0]["items"], [])

    def test_dashboard_handles_deleted_signal_and_fills_preview_with_permitted_items(self):
        from moderation import views

        self.client.force_login(self.general)
        for n in range(25):
            PrayerRequest.objects.create(requester=self.owner, title=f"Next {n}", description="Synthetic")
        original = views.outstanding_pks

        def delete_after_ids(kind, queryset):
            pks = original(kind, queryset)
            PrayerRequest.objects.filter(pk=self.routine.pk).delete()
            return pks

        with patch("moderation.views.outstanding_pks", side_effect=delete_after_ids):
            response = self.client.get(reverse("moderation-dashboard") + "?type=prayer")
        self.assertEqual(response.status_code, 200)
        group = response.context["groups"][0]
        self.assertEqual(group["count"], 25)
        self.assertEqual(len(group["items"]), 25)
        self.assertNotContains(response, self.routine.title)
        self.assertContains(response, "Next 24")
