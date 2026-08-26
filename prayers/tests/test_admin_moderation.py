"""Tests for the task-oriented prayer request moderation queue."""

from datetime import timedelta

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from prayers.admin import PrayerRequestAdmin, PrayerRequestAttentionFilter
from prayers.models import PrayerRequest


User = get_user_model()


class PrayerRequestModerationAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="moderation-requester",
            email="requester@example.com",
            password="password123",
        )
        cls.pending = PrayerRequest.objects.create(
            title="Pending request",
            description="Please pray",
            requester=cls.user,
            status="pending_moderation",
            reviewed=False,
        )
        cls.flagged = PrayerRequest.objects.create(
            title="Flagged request",
            description="Needs a person",
            requester=cls.user,
            status="approved",
            reviewed=True,
            requires_human_review=True,
            moderation_severity="high",
        )
        cls.expired = PrayerRequest.objects.create(
            title="Expired request",
            description="Past its active window",
            requester=cls.user,
            status="approved",
            reviewed=True,
        )
        cls.expired.expiration_date = timezone.now() - timedelta(hours=1)
        PrayerRequest.objects.filter(pk=cls.expired.pk).update(expiration_date=cls.expired.expiration_date)
        cls.resolved = PrayerRequest.objects.create(
            title="Resolved request",
            description="Already handled",
            requester=cls.user,
            status="completed",
            reviewed=True,
        )

    def setUp(self):
        self.model_admin = PrayerRequestAdmin(PrayerRequest, AdminSite())
        self.factory = RequestFactory()

    def filtered_ids(self, value):
        request = self.factory.get("/admin/prayers/prayerrequest/", {"attention": value})
        request.user = self.user
        list_filter = PrayerRequestAttentionFilter(
            request,
            request.GET.copy(),
            PrayerRequest,
            self.model_admin,
        )
        return set(list_filter.queryset(request, PrayerRequest.objects.all()).values_list("pk", flat=True))

    def test_needs_review_includes_unreviewed_pending_and_human_review(self):
        self.assertEqual(
            self.filtered_ids("needs_review"),
            {self.pending.pk, self.flagged.pk},
        )

    def test_expired_active_excludes_resolved_requests(self):
        self.assertEqual(self.filtered_ids("expired_active"), {self.expired.pk})

    def test_resolved_filter_groups_terminal_states(self):
        self.assertEqual(self.filtered_ids("resolved"), {self.resolved.pk})

    def test_badges_make_moderation_and_expiration_state_scannable(self):
        flagged = str(self.model_admin.moderation_state(self.flagged))
        expired = str(self.model_admin.expiration_state(self.expired))
        pending = str(self.model_admin.moderation_state(self.pending))

        self.assertIn("High", flagged)
        self.assertIn("Human review", flagged)
        self.assertIn("Expired", expired)
        self.assertNotIn('fp-admin-state--warning"></span>', pending)

    def test_queue_uses_annotated_counts_and_compact_pagination(self):
        queryset = self.model_admin.get_queryset(self.factory.get("/admin/"))

        self.assertEqual(self.model_admin.list_per_page, 50)
        self.assertEqual(self.model_admin.date_hierarchy, "created_at")
        self.assertIn("_admin_acceptance_count", queryset.query.annotations)
        self.assertIn("_admin_prayer_log_count", queryset.query.annotations)
