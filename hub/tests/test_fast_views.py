from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import resolve, reverse
from django.utils import timezone
from hub.models import Day, FastIntention, FastParticipantMap
from hub.views.fast import (
    FastListView,
    FastByDateView,
    FastByFeastDateView,
    JoinFastView,
    FastIntentionView,
    FastParticipantsView,
    PaginatedFastParticipantsView,
    FastParticipantsMapView,
    FastStatsView,
)
from django.contrib.auth.models import AnonymousUser
from rest_framework import status
from rest_framework.test import APIRequestFactory
from rest_framework.test import force_authenticate
from datetime import timedelta
from django.core.cache import cache
from events.models import Event, EventType
from tests.fixtures.test_data import TestDataFactory
from rest_framework.exceptions import ValidationError
import pytz

User = get_user_model()

class JoinFastViewTest(TestCase):
    def setUp(self):
        self.church = TestDataFactory.create_church(name="Join Fast Church")
        self.user = TestDataFactory.create_user(
            username="joinfast@example.com",
            email="joinfast@example.com",
            password="testpass123",
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
        )
        self.fast = TestDataFactory.create_fast(
            church=self.church,
            name="Joinable Fast",
            description="Fast for join route tests",
        )
        self.url = reverse("fast-join")

    def test_mounted_hub_url_resolves_to_join_fast_view(self):
        match = resolve("/hub/fasts/join/")

        self.assertEqual(match.func.view_class, JoinFastView)
        self.assertEqual(match.url_name, "fast-join")

    def test_named_url_resolves_to_join_fast_view(self):
        match = resolve(self.url)

        self.assertEqual(match.func.view_class, JoinFastView)
        self.assertEqual(match.url_name, "fast-join")

    def test_join_fast_adds_membership(self):
        self.client.force_login(self.user)

        response = self.client.put(
            self.url,
            data={"fast_id": self.fast.id},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(self.profile.fasts.filter(id=self.fast.id).exists())
        self.assertEqual(self.profile.fasts.filter(id=self.fast.id).count(), 1)

    def test_join_fast_requires_authentication(self):
        response = self.client.put(
            self.url,
            data={"fast_id": self.fast.id},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(self.profile.fasts.filter(id=self.fast.id).exists())

    def test_join_fast_requires_valid_fast_id(self):
        self.client.force_login(self.user)

        missing_fast_id_response = self.client.put(
            self.url,
            data={},
            content_type="application/json",
        )
        invalid_fast_response = self.client.put(
            self.url,
            data={"fast_id": self.fast.id + 999},
            content_type="application/json",
        )

        self.assertEqual(missing_fast_id_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_fast_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(self.profile.fasts.filter(id=self.fast.id).exists())

    def test_join_fast_rejects_duplicate_membership(self):
        self.client.force_login(self.user)
        self.profile.fasts.add(self.fast)

        response = self.client.put(
            self.url,
            data={"fast_id": self.fast.id},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.profile.fasts.filter(id=self.fast.id).count(), 1)


class LeaveFastViewTest(TestCase):
    def setUp(self):
        self.church = TestDataFactory.create_church(name="Leave Fast Church")
        self.user = TestDataFactory.create_user(
            username="leavefast@example.com",
            email="leavefast@example.com",
            password="testpass123",
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
        )
        self.fast = TestDataFactory.create_fast(
            church=self.church,
            name="Joined Fast",
            description="Fast for leave route tests",
        )
        self.url = reverse("leave-fast")

    def test_leave_fast_removes_membership(self):
        self.profile.fasts.add(self.fast)
        self.client.force_login(self.user)

        response = self.client.put(
            self.url,
            data={"fast_id": self.fast.id},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["detail"], "Successfully left the fast.")
        self.assertFalse(self.profile.fasts.filter(id=self.fast.id).exists())

    def test_leave_fast_requires_authentication(self):
        self.profile.fasts.add(self.fast)

        response = self.client.put(
            self.url,
            data={"fast_id": self.fast.id},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertTrue(self.profile.fasts.filter(id=self.fast.id).exists())

    def test_leave_fast_requires_valid_fast_id(self):
        self.profile.fasts.add(self.fast)
        self.client.force_login(self.user)

        missing_fast_id_response = self.client.put(
            self.url,
            data={},
            content_type="application/json",
        )
        invalid_fast_response = self.client.put(
            self.url,
            data={"fast_id": self.fast.id + 999},
            content_type="application/json",
        )

        self.assertEqual(
            missing_fast_id_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(invalid_fast_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(self.profile.fasts.filter(id=self.fast.id).exists())

    def test_leave_fast_rejects_repeated_leave_attempt(self):
        self.profile.fasts.add(self.fast)
        self.client.force_login(self.user)

        first_response = self.client.put(
            self.url,
            data={"fast_id": self.fast.id},
            content_type="application/json",
        )
        second_response = self.client.put(
            self.url,
            data={"fast_id": self.fast.id},
            content_type="application/json",
        )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            second_response.json()["detail"],
            "You are not part of this fast.",
        )
        self.assertFalse(self.profile.fasts.filter(id=self.fast.id).exists())


class FastIntentionViewTest(TestCase):
    def setUp(self):
        self.church = TestDataFactory.create_church(name="Intention Fast Church")
        self.user = TestDataFactory.create_user(
            username="intention@example.com",
            email="intention@example.com",
            password="testpass123",
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
        )
        self.fast = TestDataFactory.create_fast(
            church=self.church,
            name="Intention Fast",
            description="Fast for intention route tests",
        )
        self.url = reverse("fast-intention", kwargs={"fast_id": self.fast.id})

    def test_mounted_url_resolves_to_fast_intention_view(self):
        match = resolve(f"/hub/fasts/{self.fast.id}/intention/")

        self.assertEqual(match.func.view_class, FastIntentionView)
        self.assertEqual(match.url_name, "fast-intention")
        self.assertEqual(match.kwargs["fast_id"], self.fast.id)

    def test_put_creates_intention_for_participant(self):
        self.profile.fasts.add(self.fast)
        self.client.force_login(self.user)

        response = self.client.put(
            self.url,
            data={"text": "Pray for peace", "is_public": True},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["text"], "Pray for peace")
        self.assertTrue(response.json()["is_public"])
        self.assertTrue(
            FastIntention.objects.filter(
                user=self.user,
                fast=self.fast,
                text="Pray for peace",
                is_public=True,
                is_active=True,
            ).exists()
        )

    def test_get_returns_existing_intention_for_participant(self):
        self.profile.fasts.add(self.fast)
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Pray for healing",
            is_public=False,
            is_active=True,
        )
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["text"], "Pray for healing")
        self.assertFalse(response.json()["is_public"])

    def test_fast_intention_requires_authentication(self):
        self.profile.fasts.add(self.fast)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_fast_intention_requires_fast_membership(self):
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.json()["detail"],
            "You are not a participant in this fast.",
        )

    def test_fast_intention_returns_not_found_for_missing_fast(self):
        self.client.force_login(self.user)
        url = reverse("fast-intention", kwargs={"fast_id": self.fast.id + 999})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class FastListViewTest(TestCase):
    def setUp(self):
        # Create a church using TestDataFactory
        self.church = TestDataFactory.create_church(name="Test Church")
        
        # Create a user with profile using TestDataFactory
        self.user = TestDataFactory.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='testpass123'
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church
        )
        
        # Create 3 fasts using TestDataFactory
        self.fasts = []
        today = timezone.now().date()
        
        for i in range(3):
            fast = TestDataFactory.create_fast(
                church=self.church,
                name=f"Test Fast {i}",
                description=f"Test Description {i}"
            )
            self.fasts.append(fast)
            
            # Create days for each fast (30 days before and after today) using TestDataFactory
            for j in range(-30, 31):
                date = today + timedelta(days=j)
                day = TestDataFactory.create_day(
                    date=date,
                    church=self.church
                )
                day.fast = fast
                day.save()
        
        # Associate user with first two fasts
        self.profile.fasts.add(self.fasts[0], self.fasts[1])
        
        # Create API request factory
        self.factory = APIRequestFactory()
        
    def tearDown(self):
        # Clear cache after each test
        cache.clear()
        
    def _create_request(self, user=None, query_params=None):
        """Helper method to create a properly configured request."""
        # Create base request
        request = self.factory.get('/api/fasts/')
        
        # Set up authentication
        if user:
            force_authenticate(request, user=user)
        request.user = user or AnonymousUser()
        
        # Set up query parameters
        request.query_params = query_params or {}
        
        # Set up default timezone if not provided
        if 'tz' not in request.query_params:
            request.query_params['tz'] = 'UTC'
            
        return request
        
    def test_list_fasts_authenticated(self):
        """Test listing fasts for an authenticated user."""
        # Create request
        request = self._create_request(user=self.user)
        
        # Create view instance
        view = FastListView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results
        self.assertEqual(queryset.count(), 3)  # Should see all fasts
        self.assertEqual(queryset[0].participant_count, 1)  # One participant
        
    def test_list_fasts_unauthenticated(self):
        """Test listing fasts for an unauthenticated user."""
        # Create request with church_id
        request = self._create_request(
            query_params={'church_id': self.church.id}
        )
        
        # Create view instance
        view = FastListView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results
        self.assertEqual(queryset.count(), 3)  # Should see all fasts
        
    def test_list_fasts_with_date_range(self):
        """Test listing fasts with specific date range."""
        today = timezone.now().date()
        start_date = today - timedelta(days=10)
        end_date = today + timedelta(days=10)
        
        # Create request with date range
        request = self._create_request(
            user=self.user,
            query_params={
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat()
            }
        )
        
        # Create view instance
        view = FastListView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results
        self.assertEqual(queryset.count(), 3)  # All fasts should be in range
        
    def test_list_fasts_with_timezone(self):
        """Test listing fasts with different timezone."""
        # Create request with timezone
        request = self._create_request(
            user=self.user,
            query_params={'tz': 'America/New_York'}
        )
        
        # Create view instance
        view = FastListView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results
        self.assertEqual(queryset.count(), 3)
        
    def test_list_fasts_caching(self):
        """Test that fasts are properly cached."""
        # Create request
        request = self._create_request(user=self.user)
        
        # Create view instance
        view = FastListView()
        view.request = request
        
        # First request - should hit database
        queryset1 = view.get_queryset()
        
        # Second request - should hit cache
        queryset2 = view.get_queryset()
        
        # Verify results are the same
        self.assertEqual(list(queryset1), list(queryset2))
        
    def test_list_fasts_cache_invalidation(self):
        """Test that cache is invalidated when fasts change."""
        # Create request
        request = self._create_request(user=self.user)
        
        # Create view instance
        view = FastListView()
        view.request = request
        
        # First request - should hit database
        queryset1 = list(view.get_queryset())
        
        # Modify a fast
        self.fasts[0].name = "Updated Fast"
        self.fasts[0].save()
        
        # Clear cache
        cache.clear()
        
        # Second request - should hit database again
        queryset2 = list(view.get_queryset())
        
        # Verify results are different
        self.assertNotEqual(queryset1[0].name, queryset2[0].name)
        
    def test_list_fasts_with_different_church(self):
        """Test listing fasts for a different church."""
        # Create another church and user
        other_church = TestDataFactory.create_church(name="Other Church")
        other_user = TestDataFactory.create_user(
            username='otheruser@example.com',
            email='otheruser@example.com',
            password='testpass123'
        )
        other_profile = TestDataFactory.create_profile(
            user=other_user,
            church=other_church
        )
        
        # Create a fast for the other church
        other_fast = TestDataFactory.create_fast(
            church=other_church,
            name="Other Church Fast",
            description="Other Description"
        )
        
        # Create days for the other church's fast
        today = timezone.now().date()
        for j in range(-30, 31):
            date = today + timedelta(days=j)
            day = TestDataFactory.create_day(
                date=date,
                church=other_church
            )
            day.fast = other_fast
            day.save()
        
        # Create request
        request = self._create_request(user=other_user)
        
        # Create view instance
        view = FastListView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results
        self.assertEqual(queryset.count(), 1)  # Should only see fasts from other church
        self.assertEqual(queryset[0].church, other_church)


class FastStatsViewTest(TestCase):
    def setUp(self):
        self.church = TestDataFactory.create_church(name="Stats Test Church")
        self.user = TestDataFactory.create_user(
            username="faststats@example.com",
            email="faststats@example.com",
            password="testpass123",
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
            timezone="UTC",
        )
        self.url = reverse("fast-stats")

    def tearDown(self):
        cache.clear()

    def test_mounted_url_resolves_to_fast_stats_view(self):
        match = resolve("/hub/fasts/stats/")

        self.assertEqual(match.func.view_class, FastStatsView)
        self.assertEqual(match.url_name, "fast-stats")

    def test_fast_stats_requires_authentication(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_fast_stats_returns_expected_summary(self):
        today = timezone.now().date()
        completed_fast = TestDataFactory.create_fast(
            church=self.church,
            name="Completed Fast",
        )
        ongoing_fast = TestDataFactory.create_fast(
            church=self.church,
            name="Ongoing Fast",
        )

        for offset, fast in [(-2, completed_fast), (-1, completed_fast), (0, ongoing_fast), (1, ongoing_fast)]:
            day = TestDataFactory.create_day(
                date=today + timedelta(days=offset),
                church=self.church,
            )
            day.fast = fast
            day.save()

        self.profile.fasts.add(completed_fast, ongoing_fast)

        checklist_used, _ = EventType.objects.get_or_create(
            code=EventType.CHECKLIST_USED,
            defaults={
                "name": "Checklist Used",
                "category": "analytics",
                "requires_target": False,
            },
        )
        Event.objects.create(
            event_type=checklist_used,
            user=self.user,
            title="Checklist completed",
        )

        self.client.force_login(self.user)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.json().keys()),
            {
                "joined_fasts",
                "total_fasts",
                "total_fast_days",
                "completed_fasts",
                "checklist_uses",
            },
        )
        self.assertCountEqual(
            response.json()["joined_fasts"],
            [completed_fast.id, ongoing_fast.id],
        )
        self.assertEqual(response.json()["total_fasts"], 2)
        self.assertEqual(response.json()["total_fast_days"], 3)
        self.assertEqual(response.json()["completed_fasts"], 1)
        self.assertEqual(response.json()["checklist_uses"], 1)


class FastParticipantsViewTest(TestCase):
    def setUp(self):
        self.church = TestDataFactory.create_church(name="Participants Fast Church")
        self.user = TestDataFactory.create_user(
            username="fastparticipants@example.com",
            email="fastparticipants@example.com",
            password="testpass123",
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
        )
        self.fast = TestDataFactory.create_fast(
            church=self.church,
            name="Participants Fast",
            description="Fast for participants route tests",
        )
        self.participant_user = TestDataFactory.create_user(
            username="participant@example.com",
            email="participant@example.com",
            password="testpass123",
        )
        self.participant_profile = TestDataFactory.create_profile(
            user=self.participant_user,
            church=self.church,
            name="Participant One",
            location="Glendale, CA",
        )
        self.other_user = TestDataFactory.create_user(
            username="otherparticipant@example.com",
            email="otherparticipant@example.com",
            password="testpass123",
        )
        self.other_profile = TestDataFactory.create_profile(
            user=self.other_user,
            church=self.church,
            name="Participant Two",
            location="Pasadena, CA",
        )
        self.fast.profiles.add(self.participant_profile, self.other_profile)
        self.url = reverse("fast-participants", kwargs={"fast_id": self.fast.id})

    def tearDown(self):
        cache.clear()

    def test_mounted_url_resolves_to_fast_participants_view(self):
        match = resolve(f"/hub/fasts/{self.fast.id}/participants/")

        self.assertEqual(match.func.view_class, FastParticipantsView)
        self.assertEqual(match.url_name, "fast-participants")
        self.assertEqual(match.kwargs["fast_id"], self.fast.id)

    def test_fast_participants_requires_authentication(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_fast_participants_returns_serialized_profiles(self):
        FastIntention.objects.create(
            user=self.participant_user,
            fast=self.fast,
            text="Pray for the parish",
            is_public=True,
            is_active=True,
        )
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.json()), 2)
        first_participant = response.json()[0]
        self.assertEqual(first_participant["id"], self.participant_profile.id)
        self.assertEqual(first_participant["name"], "Participant One")
        self.assertEqual(first_participant["location"], "Glendale, CA")
        self.assertEqual(first_participant["abbreviation"], "P")
        self.assertEqual(first_participant["user"], "Participant One")
        self.assertEqual(first_participant["intention"], "Pray for the parish")
        self.assertIn("profile_image", first_participant)
        self.assertIn("thumbnail", first_participant)

    def test_fast_participants_returns_not_found_for_missing_fast(self):
        self.client.force_login(self.user)
        url = reverse("fast-participants", kwargs={"fast_id": self.fast.id + 999})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class PaginatedFastParticipantsViewTest(TestCase):
    def setUp(self):
        self.church = TestDataFactory.create_church(name="Paginated Participants Church")
        self.user = TestDataFactory.create_user(
            username="paginatedfastparticipants@example.com",
            email="paginatedfastparticipants@example.com",
            password="testpass123",
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
        )
        self.fast = TestDataFactory.create_fast(
            church=self.church,
            name="Paginated Participants Fast",
            description="Fast for paginated participants route tests",
        )
        self.participant_user = TestDataFactory.create_user(
            username="paginatedparticipant@example.com",
            email="paginatedparticipant@example.com",
            password="testpass123",
        )
        self.participant_profile = TestDataFactory.create_profile(
            user=self.participant_user,
            church=self.church,
            name="Paginated Participant One",
            location="Glendale, CA",
        )
        self.other_user = TestDataFactory.create_user(
            username="paginatedotherparticipant@example.com",
            email="paginatedotherparticipant@example.com",
            password="testpass123",
        )
        self.other_profile = TestDataFactory.create_profile(
            user=self.other_user,
            church=self.church,
            name="Paginated Participant Two",
            location="Pasadena, CA",
        )
        self.fast.profiles.add(self.participant_profile, self.other_profile)
        self.url = reverse(
            "fast-participants-paginated",
            kwargs={"fast_id": self.fast.id},
        )

    def tearDown(self):
        cache.clear()

    def test_mounted_url_resolves_to_paginated_fast_participants_view(self):
        match = resolve(f"/hub/fasts/{self.fast.id}/participants/paginated/")

        self.assertEqual(match.func.view_class, PaginatedFastParticipantsView)
        self.assertEqual(match.url_name, "fast-participants-paginated")
        self.assertEqual(match.kwargs["fast_id"], self.fast.id)

    def test_paginated_fast_participants_requires_authentication(self):
        response = self.client.get(self.url, {"limit": 1})

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_paginated_fast_participants_returns_paginated_profiles(self):
        FastIntention.objects.create(
            user=self.participant_user,
            fast=self.fast,
            text="Pray for the parish",
            is_public=True,
            is_active=True,
        )
        self.client.force_login(self.user)

        response = self.client.get(self.url, {"limit": 1})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(set(payload.keys()), {"count", "next", "previous", "results"})
        self.assertEqual(payload["count"], 2)
        self.assertIsNotNone(payload["next"])
        self.assertIsNone(payload["previous"])
        self.assertEqual(len(payload["results"]), 1)
        first_participant = payload["results"][0]
        self.assertEqual(first_participant["id"], self.participant_profile.id)
        self.assertEqual(first_participant["name"], "Paginated Participant One")
        self.assertEqual(first_participant["location"], "Glendale, CA")
        self.assertEqual(first_participant["abbreviation"], "P")
        self.assertEqual(first_participant["user"], "Paginated Participant One")
        self.assertEqual(first_participant["intention"], "Pray for the parish")
        self.assertIn("profile_image", first_participant)
        self.assertIn("thumbnail", first_participant)

    def test_paginated_fast_participants_returns_empty_page_for_empty_fast(self):
        empty_fast = TestDataFactory.create_fast(
            church=self.church,
            name="Empty Paginated Participants Fast",
        )
        url = reverse(
            "fast-participants-paginated",
            kwargs={"fast_id": empty_fast.id},
        )
        self.client.force_login(self.user)

        response = self.client.get(url, {"limit": 1})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["count"], 0)
        self.assertIsNone(response.json()["next"])
        self.assertIsNone(response.json()["previous"])
        self.assertEqual(response.json()["results"], [])

    def test_paginated_fast_participants_returns_not_found_for_missing_fast(self):
        self.client.force_login(self.user)
        url = reverse(
            "fast-participants-paginated",
            kwargs={"fast_id": self.fast.id + 999},
        )

        response = self.client.get(url, {"limit": 1})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class FastParticipantsMapViewTest(TestCase):
    def setUp(self):
        self.church = TestDataFactory.create_church(name="Map Fast Church")
        self.user = TestDataFactory.create_user(
            username="fastmap@example.com",
            email="fastmap@example.com",
            password="testpass123",
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
            location="Los Angeles, CA",
            latitude=34.0522,
            longitude=-118.2437,
        )
        self.fast = TestDataFactory.create_fast(
            church=self.church,
            name="Map Fast",
            description="Fast for map route tests",
        )
        self.profile.fasts.add(self.fast)
        self.url = reverse("fast-participants-map", kwargs={"fast_id": self.fast.id})

    def tearDown(self):
        cache.clear()

    def test_mounted_url_resolves_to_fast_participants_map_view(self):
        match = resolve(f"/hub/fasts/{self.fast.id}/participants/map/")

        self.assertEqual(match.func.view_class, FastParticipantsMapView)
        self.assertEqual(match.url_name, "fast-participants-map")
        self.assertEqual(match.kwargs["fast_id"], self.fast.id)

    def test_fast_participants_map_requires_authentication(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_fast_participants_map_returns_existing_map_metadata(self):
        map_obj = FastParticipantMap.objects.create(
            fast=self.fast,
            map_file="fast_maps/map-fast.svg",
            participant_count=1,
            format="svg",
        )
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["map_url"], map_obj.map_url)
        self.assertEqual(response.json()["participant_count"], 1)
        self.assertEqual(response.json()["format"], "svg")
        self.assertIn("last_updated", response.json())
        self.assertIn("age_hours", response.json())

    def test_api_path_returns_existing_participants_map_metadata(self):
        map_obj = FastParticipantMap.objects.create(
            fast=self.fast,
            map_file="fast_maps/map-fast.svg",
            participant_count=1,
            format="svg",
        )
        self.client.force_login(self.user)

        response = self.client.get(
            f"/api/fasts/{self.fast.id}/participants/map/"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["map_url"], map_obj.map_url)
        self.assertEqual(response.json()["participant_count"], 1)
        self.assertEqual(response.json()["format"], "svg")
        self.assertIn("last_updated", response.json())
        self.assertIn("age_hours", response.json())

    def test_fast_participants_map_returns_not_found_for_missing_fast(self):
        self.client.force_login(self.user)
        url = reverse("fast-participants-map", kwargs={"fast_id": self.fast.id + 999})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json()["detail"], "Fast not found.")


class FastByFeastDateViewTest(TestCase):
    """Tests for the FastByFeastDateView endpoint."""
    
    def setUp(self):
        """Set up test data."""
        # Create a church using TestDataFactory
        self.church = TestDataFactory.create_church(name="Test Church")
        
        # Create a user with profile using TestDataFactory
        self.user = TestDataFactory.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='testpass123'
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church
        )
        
        # Create dates for testing
        today = timezone.now().date()
        self.feast_date_1 = today + timedelta(days=50)
        self.feast_date_2 = today + timedelta(days=100)
        
        # Create fasts with feast dates
        # Note: Due to unique constraint on (culmination_feast_date, church),
        # only one fast per church can have the same feast date
        self.fast_1 = TestDataFactory.create_fast(
            church=self.church,
            name="Fast with Feast 1",
            description="Test Fast 1"
        )
        self.fast_1.culmination_feast = "Easter"
        self.fast_1.culmination_feast_date = self.feast_date_1
        self.fast_1.save()
        
        self.fast_2 = TestDataFactory.create_fast(
            church=self.church,
            name="Fast with Different Feast",
            description="Test Fast 2"
        )
        self.fast_2.culmination_feast = "Christmas"
        self.fast_2.culmination_feast_date = self.feast_date_2  # Different date
        self.fast_2.save()
        
        # Create a fast without a feast date
        self.fast_no_feast = TestDataFactory.create_fast(
            church=self.church,
            name="Fast without Feast",
            description="Test Fast No Feast"
        )
        
        # Create days for each fast
        for fast in [self.fast_1, self.fast_2, self.fast_no_feast]:
            for j in range(-10, 30):
                date = today + timedelta(days=j)
                day = TestDataFactory.create_day(
                    date=date,
                    church=self.church
                )
                day.fast = fast
                day.save()
        
        # Associate user with some fasts
        self.profile.fasts.add(self.fast_1, self.fast_2)
        
        # Create API request factory
        self.factory = APIRequestFactory()
        
    def tearDown(self):
        """Clean up after each test."""
        cache.clear()

    def test_mounted_url_resolves_to_fast_by_feast_date_view(self):
        """The public hub URL should remain wired to this view."""
        match = resolve("/hub/fasts/by-feast-date/")

        self.assertEqual(match.func.view_class, FastByFeastDateView)
        self.assertEqual(match.url_name, "fast-by-feast-date")

    def test_api_url_returns_serialized_fasts_for_requested_feast_date(self):
        self.client.force_login(self.user)

        response = self.client.get(
            "/api/fasts/by-feast-date/",
            {
                "date": self.feast_date_1.isoformat(),
                "tz": "UTC",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(len(response.json()["results"]), 1)
        fast = response.json()["results"][0]
        self.assertEqual(fast["id"], self.fast_1.id)
        self.assertEqual(fast["name"], "Fast with Feast 1")
        self.assertEqual(fast["culmination_feast"], "Easter")
        self.assertEqual(
            fast["culmination_feast_date"],
            self.feast_date_1.isoformat(),
        )
        
    def _create_request(self, user=None, query_params=None):
        """Helper method to create a properly configured request."""
        # Create base request
        request = self.factory.get('/api/fasts/by-feast-date/')
        
        # Set up authentication
        if user:
            force_authenticate(request, user=user)
        request.user = user or AnonymousUser()
        
        # Set up query parameters
        request.query_params = query_params or {}
        
        # Set up default timezone if not provided
        if 'tz' not in request.query_params:
            request.query_params['tz'] = 'UTC'
            
        return request
    
    def test_find_fasts_by_feast_date_authenticated(self):
        """Test finding fasts by feast date for an authenticated user."""
        # Create request with date
        request = self._create_request(
            user=self.user,
            query_params={'date': self.feast_date_1.isoformat()}
        )
        
        # Create view instance
        view = FastByFeastDateView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results - should find fast_1
        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset[0].name, "Fast with Feast 1")
        
    def test_find_fasts_by_feast_date_unauthenticated(self):
        """Test finding fasts by feast date for an unauthenticated user."""
        # Create request with date and church_id
        request = self._create_request(
            query_params={
                'date': self.feast_date_1.isoformat(),
                'church_id': self.church.id
            }
        )
        
        # Create view instance
        view = FastByFeastDateView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results
        self.assertEqual(queryset.count(), 1)
        
    def test_find_fasts_by_different_feast_date(self):
        """Test finding fasts by a different feast date."""
        # Create request with different date
        request = self._create_request(
            user=self.user,
            query_params={'date': self.feast_date_2.isoformat()}
        )
        
        # Create view instance
        view = FastByFeastDateView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results - should only find fast_2
        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset[0].name, "Fast with Different Feast")
        
    def test_no_fasts_found_for_feast_date(self):
        """Test when no fasts have the specified feast date."""
        # Create request with a date that has no fasts
        nonexistent_date = timezone.now().date() + timedelta(days=500)
        request = self._create_request(
            user=self.user,
            query_params={'date': nonexistent_date.isoformat()}
        )
        
        # Create view instance
        view = FastByFeastDateView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results - should be empty
        self.assertEqual(queryset.count(), 0)
        
    def test_missing_feast_date_parameter(self):
        """Test that missing date parameter raises validation error."""
        # Create request without date
        request = self._create_request(user=self.user)
        
        # Create view instance
        view = FastByFeastDateView()
        view.request = request
        
        # Verify that ValidationError is raised
        with self.assertRaises(ValidationError) as context:
            view.get_queryset()
        
        self.assertIn("date query parameter is required", str(context.exception))
        
    def test_invalid_feast_date_format(self):
        """Test that invalid date format raises validation error."""
        # Create request with invalid date format
        request = self._create_request(
            user=self.user,
            query_params={'date': 'invalid-date'}
        )
        
        # Create view instance
        view = FastByFeastDateView()
        view.request = request
        
        # Verify that ValidationError is raised
        with self.assertRaises(ValidationError) as context:
            view.get_queryset()
        
        self.assertIn("Invalid date format", str(context.exception))
        
    def test_feast_date_with_timezone(self):
        """Test finding fasts by feast date with different timezone."""
        # Create request with timezone
        request = self._create_request(
            user=self.user,
            query_params={
                'date': self.feast_date_1.isoformat(),
                'tz': 'America/New_York'
            }
        )
        
        # Create view instance
        view = FastByFeastDateView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results
        self.assertEqual(queryset.count(), 1)
        
    def test_feast_date_caching(self):
        """Test that results are properly cached."""
        # Create request
        request = self._create_request(
            user=self.user,
            query_params={'date': self.feast_date_1.isoformat()}
        )
        
        # Create view instance
        view = FastByFeastDateView()
        view.request = request
        
        # First request - should hit database
        queryset1 = view.get_queryset()
        
        # Second request - should hit cache
        queryset2 = view.get_queryset()
        
        # Verify results are the same
        self.assertEqual(list(queryset1), list(queryset2))
        
    def test_feast_date_with_different_church(self):
        """Test finding fasts by feast date for a different church."""
        # Create another church and fast
        other_church = TestDataFactory.create_church(name="Other Church")
        other_user = TestDataFactory.create_user(
            username='otheruser@example.com',
            email='otheruser@example.com',
            password='testpass123'
        )
        other_profile = TestDataFactory.create_profile(
            user=other_user,
            church=other_church
        )
        
        # Create a fast for the other church with the same feast date
        other_fast = TestDataFactory.create_fast(
            church=other_church,
            name="Other Church Fast",
            description="Other Description"
        )
        other_fast.culmination_feast = "Easter"
        other_fast.culmination_feast_date = self.feast_date_1
        other_fast.save()
        
        # Create days for the other church's fast
        today = timezone.now().date()
        for j in range(-10, 30):
            date = today + timedelta(days=j)
            day = TestDataFactory.create_day(
                date=date,
                church=other_church
            )
            day.fast = other_fast
            day.save()
        
        # Create request for other user
        request = self._create_request(
            user=other_user,
            query_params={'date': self.feast_date_1.isoformat()}
        )
        
        # Create view instance
        view = FastByFeastDateView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify results - should only see fast from other church
        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset[0].church, other_church)
        self.assertEqual(queryset[0].name, "Other Church Fast")
        
    def test_feast_date_with_annotated_fields(self):
        """Test that annotated fields are included in the results."""
        # Create request
        request = self._create_request(
            user=self.user,
            query_params={'date': self.feast_date_1.isoformat()}
        )
        
        # Create view instance
        view = FastByFeastDateView()
        view.request = request
        
        # Get queryset
        queryset = view.get_queryset()
        
        # Verify annotated fields exist
        first_fast = queryset.first()
        self.assertTrue(hasattr(first_fast, 'participant_count'))
        self.assertTrue(hasattr(first_fast, 'total_days'))
        self.assertTrue(hasattr(first_fast, 'start_date'))
        self.assertTrue(hasattr(first_fast, 'end_date'))
        self.assertTrue(hasattr(first_fast, 'current_day_count'))


class FastByDateViewTest(TestCase):
    """Tests for the FastByDateView endpoint."""

    def setUp(self):
        self.church = TestDataFactory.create_church(name="Test Church")
        self.user = TestDataFactory.create_user(
            username='fastbydate@example.com',
            email='fastbydate@example.com',
            password='testpass123'
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
            timezone='America/New_York'
        )
        self.factory = APIRequestFactory()

        self.match_date = timezone.now().date() + timedelta(days=3)
        self.fast_on_date = TestDataFactory.create_fast(
            church=self.church,
            name="Fast On Date",
            description="Matches target date"
        )
        self.fast_other_date = TestDataFactory.create_fast(
            church=self.church,
            name="Fast Other Date",
            description="Does not match target date"
        )

        matching_day = TestDataFactory.create_day(
            date=self.match_date,
            church=self.church
        )
        matching_day.fast = self.fast_on_date
        matching_day.save()

        other_day = TestDataFactory.create_day(
            date=self.match_date + timedelta(days=1),
            church=self.church
        )
        other_day.fast = self.fast_other_date
        other_day.save()

        self.profile.fasts.add(self.fast_on_date)

    def tearDown(self):
        cache.clear()

    def test_mounted_url_resolves_to_fast_by_date_view(self):
        match = resolve("/hub/fasts/by-date/")

        self.assertEqual(match.func.view_class, FastByDateView)
        self.assertEqual(match.url_name, "fast-by-date")

    def test_api_url_returns_serialized_fasts_for_requested_date(self):
        response = self.client.get(
            "/api/fasts/by-date/",
            {
                "date": self.match_date.isoformat(),
                "church_id": self.church.id,
                "tz": "UTC",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.json()), 1)
        fast = response.json()[0]
        self.assertEqual(fast["id"], self.fast_on_date.id)
        self.assertEqual(fast["name"], "Fast On Date")
        self.assertEqual(fast["start_date"], self.match_date.isoformat())
        self.assertEqual(fast["end_date"], self.match_date.isoformat())
        self.assertEqual(fast["participant_count"], 1)

    def _create_request(self, user=None, query_params=None):
        request = self.factory.get('/api/fasts/by-date/')
        if user:
            force_authenticate(request, user=user)
        request.user = user or AnonymousUser()
        request.query_params = query_params or {}
        if 'tz' not in request.query_params:
            request.query_params['tz'] = 'UTC'
        return request

    def test_find_fasts_by_date_authenticated(self):
        request = self._create_request(
            user=self.user,
            query_params={'date': self.match_date.isoformat()}
        )

        view = FastByDateView()
        view.request = request

        queryset = view.get_queryset()

        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset[0].name, "Fast On Date")
        self.assertEqual(queryset[0].participant_count, 1)

    def test_find_fasts_by_date_unauthenticated(self):
        request = self._create_request(
            query_params={
                'date': self.match_date.isoformat(),
                'church_id': self.church.id
            }
        )

        view = FastByDateView()
        view.request = request

        queryset = view.get_queryset()

        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset[0].church, self.church)

    def test_invalid_date_format_raises_validation_error(self):
        request = self._create_request(
            user=self.user,
            query_params={'date': 'invalid-date'}
        )

        view = FastByDateView()
        view.request = request

        with self.assertRaises(ValidationError) as context:
            view.get_queryset()

        self.assertIn("Invalid date format", str(context.exception))

    def test_missing_date_defaults_to_local_today(self):
        local_today = timezone.now().astimezone(
            pytz.timezone('America/New_York')
        ).date()
        today_fast = TestDataFactory.create_fast(
            church=self.church,
            name="Today Fast",
            description="Matches local today"
        )
        today_day = TestDataFactory.create_day(
            date=local_today,
            church=self.church
        )
        today_day.fast = today_fast
        today_day.save()

        request = self._create_request(
            user=self.user,
            query_params={'tz': 'America/New_York'}
        )

        view = FastByDateView()
        view.request = request

        queryset = view.get_queryset()

        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset[0].name, "Today Fast")

    def test_date_with_different_church_is_scoped_to_authenticated_user(self):
        other_church = TestDataFactory.create_church(name="Other Church")
        other_user = TestDataFactory.create_user(
            username='otherfastbydate@example.com',
            email='otherfastbydate@example.com',
            password='testpass123'
        )
        TestDataFactory.create_profile(
            user=other_user,
            church=other_church
        )
        other_fast = TestDataFactory.create_fast(
            church=other_church,
            name="Other Church Fast",
            description="Other church match"
        )
        other_day = TestDataFactory.create_day(
            date=self.match_date,
            church=other_church
        )
        other_day.fast = other_fast
        other_day.save()

        request = self._create_request(
            user=other_user,
            query_params={'date': self.match_date.isoformat()}
        )

        view = FastByDateView()
        view.request = request

        queryset = view.get_queryset()

        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset[0].church, other_church)
        self.assertEqual(queryset[0].name, "Other Church Fast")

    def test_no_fasts_found_for_date(self):
        request = self._create_request(
            user=self.user,
            query_params={'date': (self.match_date + timedelta(days=30)).isoformat()}
        )

        view = FastByDateView()
        view.request = request

        queryset = view.get_queryset()

        self.assertEqual(queryset.count(), 0)


class FastSerializerDayNumberTest(TestCase):
    """Tests for current_day_number and total_number_of_days with has_day_zero."""

    def setUp(self):
        self.church = TestDataFactory.create_church(name="Day0 Church")
        self.user = TestDataFactory.create_user(
            username='dayzero@example.com',
            email='dayzero@example.com',
        )
        self.profile = TestDataFactory.create_profile(
            user=self.user,
            church=self.church,
        )
        self.factory = APIRequestFactory()
        self.today = timezone.now().date()

    def _make_fast(self, has_day_zero=False, day_offsets=None):
        """Create a fast with days at the given offsets relative to today."""
        if day_offsets is None:
            day_offsets = list(range(-4, 1))  # 5 days: -4 through 0 (today)
        fast = TestDataFactory.create_fast(church=self.church)
        fast.has_day_zero = has_day_zero
        fast.save()
        for offset in day_offsets:
            Day.objects.create(
                date=self.today + timedelta(days=offset),
                fast=fast,
                church=self.church,
            )
        return fast

    def _serialize(self, fast):
        """Serialize a single fast through FastSerializer."""
        from hub.serializers import FastSerializer
        request = self.factory.get('/api/fasts/')
        force_authenticate(request, user=self.user)
        request.user = self.user
        request.query_params = {'tz': 'UTC'}
        context = {'request': request, 'tz': pytz.UTC}
        return FastSerializer(fast, context=context).data

    def test_current_day_number_without_day_zero(self):
        """Default fast (has_day_zero=False): first day is Day 1."""
        fast = self._make_fast(
            has_day_zero=False,
            day_offsets=list(range(-4, 1)),  # 5 days up to today
        )
        data = self._serialize(fast)
        self.assertEqual(data['current_day_number'], 5)

    def test_current_day_number_with_day_zero(self):
        """Fast with has_day_zero=True: first day is Day 0."""
        fast = self._make_fast(
            has_day_zero=True,
            day_offsets=list(range(-4, 1)),  # 5 days up to today
        )
        data = self._serialize(fast)
        # 5 elapsed days minus 1 offset = Day 4
        self.assertEqual(data['current_day_number'], 4)

    def test_total_number_of_days_without_day_zero(self):
        """Default fast: total equals the raw day count."""
        fast = self._make_fast(
            has_day_zero=False,
            day_offsets=list(range(-4, 6)),  # 10 days total
        )
        data = self._serialize(fast)
        self.assertEqual(data['total_number_of_days'], 10)

    def test_total_number_of_days_with_day_zero(self):
        """Fast with Day 0: total excludes the zeroth day."""
        fast = self._make_fast(
            has_day_zero=True,
            day_offsets=list(range(-4, 6)),  # 10 day rows
        )
        data = self._serialize(fast)
        self.assertEqual(data['total_number_of_days'], 9)

    def test_current_day_number_no_elapsed_days(self):
        """When no days have elapsed yet, current_day_number is None."""
        fast = self._make_fast(
            has_day_zero=False,
            day_offsets=[1, 2, 3],  # all in the future
        )
        data = self._serialize(fast)
        self.assertIsNone(data['current_day_number'])
