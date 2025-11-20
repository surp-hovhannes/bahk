"""Tests for the Prayer Requests feature."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test.utils import tag
from django.utils import timezone
from rest_framework import status

from events.models import Event, EventType, UserActivityFeed, UserMilestone
from prayers.models import (
    PrayerRequest,
    PrayerRequestAcceptance,
    PrayerRequestPrayerLog,
)
from prayers.tasks import (
    check_expired_prayer_requests_task,
    send_daily_prayer_count_notifications_task,
    moderate_prayer_request_task,
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

    @tag('integration')
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

    @tag('integration')
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

    @tag('integration')
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

    @tag('integration')
    def test_create_triggers_moderation_task_and_respects_active_limit(self):
        """Creating a request enqueues moderation and enforces max active cap."""
        creator = self.create_user(email='creator@example.com')
        self.authenticate(creator)

        with patch('prayers.views.moderate_prayer_request_task.delay') as mock_delay:
            response = self.client.post(self.list_url, {
                'title': 'Need prayer',
                'description': 'Please pray for my job search.',
                'duration_days': 3,
                'is_anonymous': False,
            }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_delay.assert_called_once()

        created_request = PrayerRequest.objects.filter(requester=creator).latest('id')
        created_request.status = 'completed'
        created_request.save(update_fields=['status'])

        for idx in range(PrayerRequest.MAX_ACTIVE_REQUESTS_PER_USER):
            self.create_prayer_request(
                creator,
                title=f'Existing {idx}',
                status='approved',
                reviewed=True,
            )

        with patch('prayers.views.moderate_prayer_request_task.delay') as mock_delay_blocked:
            response = self.client.post(self.list_url, {
                'title': 'Too many',
                'description': 'Should fail.',
                'duration_days': 2,
                'is_anonymous': False,
            }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('active prayer requests', response.data['non_field_errors'][0])
        mock_delay_blocked.assert_not_called()

    @tag('integration')
    def test_update_permissions_and_status_constraints(self):
        """Only owners can edit pending requests, and approved ones are locked."""
        owner = self.create_user(email='owner@example.com')
        other_user = self.create_user(email='stranger@example.com')
        prayer_request = self.create_prayer_request(
            owner,
            status='pending_moderation',
            reviewed=False,
        )

        self.authenticate(other_user)
        response = self.client.patch(
            f'/api/prayer-requests/{prayer_request.id}/',
            {'title': 'Hacked'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.authenticate(owner)
        response = self.client.patch(
            f'/api/prayer-requests/{prayer_request.id}/',
            {'title': 'Updated title'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        prayer_request.refresh_from_db()
        self.assertEqual(prayer_request.title, 'Updated title')

        prayer_request.status = 'approved'
        prayer_request.save(update_fields=['status'])

        response = self.client.patch(
            f'/api/prayer-requests/{prayer_request.id}/',
            {'title': 'Another update'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            'pending moderation',
            response.data['non_field_errors'][0]
        )

    @tag('integration')
    def test_delete_marks_status_deleted_and_is_owner_only(self):
        """Destroy endpoint soft-deletes and rejects non-owners."""
        owner = self.create_user(email='owner2@example.com')
        other_user = self.create_user(email='other@example.com')
        prayer_request = self.create_prayer_request(owner, title='Delete me')

        self.authenticate(other_user)
        response = self.client.delete(f'/api/prayer-requests/{prayer_request.id}/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.authenticate(owner)
        response = self.client.delete(f'/api/prayer-requests/{prayer_request.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        prayer_request.refresh_from_db()
        self.assertEqual(prayer_request.status, 'deleted')

    @tag('integration')
    def test_accepted_endpoint_returns_only_current_user_acceptances(self):
        """Accepted list should include only the authenticated user's requests."""
        user = self.create_user(email='acceptor@example.com')
        self.authenticate(user)
        requester_one = self.create_user(email='req1@example.com')
        requester_two = self.create_user(email='req2@example.com')

        tracked_request = self.create_prayer_request(requester_one, title='Track me')
        other_request = self.create_prayer_request(requester_two, title='Not mine')

        PrayerRequestAcceptance.objects.create(
            prayer_request=tracked_request,
            user=user,
            counts_for_milestones=True,
        )
        PrayerRequestAcceptance.objects.create(
            prayer_request=other_request,
            user=self.create_user(email='someoneelse@example.com'),
            counts_for_milestones=True,
        )

        response = self.client.get('/api/prayer-requests/accepted/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._get_results(response)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], tracked_request.id)

    @tag('integration')
    def test_send_thanks_requires_completed_status_and_notifies_acceptors(self):
        """Requester can only send thanks once completed, and recipients receive feed items."""
        requester = self.create_user(email='thankful@example.com')
        intercessor = self.create_user(email='helper2@example.com')
        prayer_request = self.create_prayer_request(requester, title='Need thanks')
        PrayerRequestAcceptance.objects.create(
            prayer_request=prayer_request,
            user=intercessor,
            counts_for_milestones=True,
        )

        self.authenticate(requester)
        response = self.client.post(
            f'/api/prayer-requests/{prayer_request.id}/send-thanks/',
            {'message': 'Appreciate your prayers!'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        prayer_request.status = 'completed'
        prayer_request.save(update_fields=['status'])

        response = self.client.post(
            f'/api/prayer-requests/{prayer_request.id}/send-thanks/',
            {'message': 'Appreciate your prayers!'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['recipient_count'], 1)

        feed_items = UserActivityFeed.objects.filter(
            user=intercessor,
            activity_type='prayer_request_thanks'
        )
        self.assertEqual(feed_items.count(), 1)

    @tag('integration', 'slow')
    def test_check_expired_prayer_requests_task_marks_requests_completed(self):
        """Expired approved requests should be completed via task."""
        requester = self.create_user(email='expiry@example.com')
        prayer_request = self.create_prayer_request(requester, title='Soon expired')
        prayer_request.expiration_date = timezone.now() - timedelta(days=1)
        prayer_request.save(update_fields=['expiration_date'])

        result = check_expired_prayer_requests_task()
        prayer_request.refresh_from_db()

        self.assertEqual(prayer_request.status, 'completed')
        self.assertEqual(result['completed_count'], 1)
        self.assertTrue(
            Event.objects.filter(
                event_type__code=EventType.PRAYER_REQUEST_COMPLETED,
                object_id=prayer_request.id
            ).exists()
        )

    @tag('integration', 'slow')
    def test_send_daily_prayer_count_notifications_task_creates_activity(self):
        """Daily notification task should create activity feed entries."""
        requester = self.create_user(email='daily@example.com')
        intercessor = self.create_user(email='intercessor_daily@example.com')
        prayer_request = self.create_prayer_request(requester, title='Daily coverage')

        PrayerRequestPrayerLog.objects.create(
            prayer_request=prayer_request,
            user=intercessor,
            prayed_on_date=timezone.localdate(),
        )

        result = send_daily_prayer_count_notifications_task()
        self.assertGreaterEqual(result['notifications_sent'], 1)
        self.assertTrue(
            UserActivityFeed.objects.filter(
                user=requester,
                activity_type='prayer_request_daily_count',
                object_id=prayer_request.id
            ).exists()
        )

    @tag('integration', 'slow')
    def test_moderation_task_auto_accepts_requester_without_milestone_credit(self):
        """Moderation approval should auto-accept requester with milestone flag disabled."""
        requester = self.create_user(email='autoaccept@example.com')
        prayer_request = PrayerRequest.objects.create(
            title='Pending moderation',
            description='Needs review',
            requester=requester,
            duration_days=3,
            status='pending_moderation',
            reviewed=False,
            expiration_date=timezone.now() + timedelta(days=3),
        )

        mock_response = SimpleNamespace(
            content=[SimpleNamespace(text='{"approved": true, "reason": "ok"}')]
        )

        with patch('prayers.tasks.get_llm_service'), patch('anthropic.Anthropic') as mock_anthropic:
            mock_client = mock_anthropic.return_value
            mock_client.messages.create.return_value = mock_response
            result = moderate_prayer_request_task(prayer_request.id)

        self.assertEqual(result['status'], 'approved')
        acceptance = PrayerRequestAcceptance.objects.get(
            prayer_request=prayer_request,
            user=requester
        )
        self.assertFalse(acceptance.counts_for_milestones)
