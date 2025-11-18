"""Tests for the Prayer Requests API."""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from events.models import UserMilestone
from prayers.models import (
    PrayerRequest,
    PrayerRequestAcceptance,
    PrayerRequestPrayerLog,
)
from tests.base import BaseAPITestCase


class PrayerRequestAPITests(BaseAPITestCase):
    """API tests covering core prayer request flows."""

    def setUp(self):
        super().setUp()
        self.list_url = '/api/prayer-requests/'

    def create_prayer_request(self, requester, **overrides):
        """Helper to create an approved prayer request."""
        defaults = {
            'title': overrides.pop('title', 'Pray for family'),
            'description': overrides.pop('description', 'Please pray for my family.'),
            'requester': requester,
            'duration_days': overrides.pop('duration_days', 3),
            'status': overrides.pop('status', 'approved'),
            'reviewed': overrides.pop('reviewed', True),
        }
        defaults.update(overrides)
        return PrayerRequest.objects.create(**defaults)

    def _get_results(self, response):
        """Return list payload regardless of pagination."""
        if isinstance(response.data, list):
            return response.data
        return response.data.get('results', [])

    def test_list_returns_only_active_approved_requests(self):
        """List endpoint should filter out pending, expired, or deleted requests."""
        self.authenticate()
        other_user = self.create_user(email='requester@example.com')

        active = self.create_prayer_request(other_user, title='Active Request')

        # Pending request should be excluded
        self.create_prayer_request(other_user, title='Pending', status='pending_moderation')

        # Expired request (manually set expiration in the past)
        expired = self.create_prayer_request(other_user, title='Expired Request')
        expired.expiration_date = timezone.now() - timedelta(days=1)
        expired.save(update_fields=['expiration_date'])

        # Deleted request
        self.create_prayer_request(other_user, title='Deleted Request', status='deleted')

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._get_results(response)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], active.title)

    def test_accept_excludes_self_acceptances_from_milestones(self):
        """Self-acceptances should not block milestone progress for other requests."""
        intercessor = self.create_user(email='pray@example.com')
        requester = self.create_user(email='requester@example.com')
        self.authenticate(intercessor)

        # Create a previous self-acceptance that shouldn't count
        own_request = self.create_prayer_request(intercessor, title='My own need')
        PrayerRequestAcceptance.objects.create(
            prayer_request=own_request,
            user=intercessor,
            counts_for_milestones=False,
        )

        other_request = self.create_prayer_request(requester, title='Community need')

        accept_url = f'/api/prayer-requests/{other_request.id}/accept/'
        response = self.client.post(accept_url)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        acceptance = PrayerRequestAcceptance.objects.get(
            prayer_request=other_request,
            user=intercessor,
        )
        self.assertTrue(acceptance.counts_for_milestones)

        milestone_qs = UserMilestone.objects.filter(
            user=intercessor,
            milestone_type='first_prayer_request_accepted',
        )
        self.assertTrue(milestone_qs.exists())
        self.assertEqual(milestone_qs.count(), 1)

    def test_mark_prayed_requires_acceptance_and_prevents_duplicates(self):
        """Users must accept a request before logging prayer, and can only log once per day."""
        requester = self.create_user(email='requester2@example.com')
        intercessor = self.create_user(email='helper@example.com')
        self.authenticate(intercessor)

        request = self.create_prayer_request(requester, title='Needs prayer support')
        mark_url = f'/api/prayer-requests/{request.id}/mark-prayed/'

        # Cannot mark as prayed before accepting
        response = self.client.post(mark_url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('accept this prayer request', response.data['detail'])

        # Accept the request
        PrayerRequestAcceptance.objects.create(
            prayer_request=request,
            user=intercessor,
            counts_for_milestones=True,
        )

        # First mark should succeed
        response = self.client.post(mark_url)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            PrayerRequestPrayerLog.objects.filter(
                prayer_request=request,
                user=intercessor,
            ).count(),
            1,
        )

        # Second mark on same day should be rejected
        response = self.client.post(mark_url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already marked this prayer for today', response.data['detail'])
