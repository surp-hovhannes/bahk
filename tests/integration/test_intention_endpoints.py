"""Integration tests for Fast Intention endpoints."""

from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from hub.models import FastIntention
from tests.fixtures.test_data import TestDataFactory

User = get_user_model()


class IntentionEndpointTests(APITestCase):
    """Tests for the intention API endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.church = TestDataFactory.create_church()
        self.fast = TestDataFactory.create_fast(church=self.church)
        self.user = TestDataFactory.create_user()
        self.profile = TestDataFactory.create_profile(user=self.user, church=self.church)
        self.profile.fasts.add(self.fast)
        self.client.force_authenticate(user=self.user)
        self.intention_url = reverse('fast-intention', kwargs={'fast_id': self.fast.id})

    def test_get_intention_not_found(self):
        """Test GET returns 404 when no intention exists."""
        response = self.client.get(self.intention_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_intention_via_put(self):
        """Test PUT creates a new intention."""
        data = {"text": "Pray for peace", "is_public": True}
        response = self.client.put(self.intention_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["text"], "Pray for peace")
        self.assertTrue(response.data["is_public"])

    def test_get_intention_after_create(self):
        """Test GET returns the intention after creation."""
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="My prayer",
            is_public=False,
            is_active=True,
        )
        response = self.client.get(self.intention_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["text"], "My prayer")
        self.assertFalse(response.data["is_public"])

    def test_update_intention_via_put(self):
        """Test PUT updates an existing intention."""
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Old text",
            is_public=False,
            is_active=True,
        )
        data = {"text": "New text", "is_public": True}
        response = self.client.put(self.intention_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["text"], "New text")
        self.assertTrue(response.data["is_public"])

    def test_delete_intention_clears_text(self):
        """Test DELETE clears intention text."""
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="To be cleared",
            is_public=True,
            is_active=True,
        )
        response = self.client.delete(self.intention_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        intention = FastIntention.objects.get(user=self.user, fast=self.fast, is_active=True)
        self.assertEqual(intention.text, "")

    def test_text_too_long_rejected(self):
        """Test that text over 280 chars is rejected."""
        data = {"text": "x" * 281, "is_public": False}
        response = self.client.put(self.intention_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_profanity_rejected(self):
        """Test that profane text is rejected."""
        data = {"text": "shitty prayer", "is_public": False}
        response = self.client.put(self.intention_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_participant_gets_403(self):
        """Test that non-participants get 403."""
        other_user = TestDataFactory.create_user(username="other@example.com")
        other_profile = TestDataFactory.create_profile(user=other_user, church=self.church)
        self.client.force_authenticate(user=other_user)
        response = self.client.get(self.intention_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_participant_put_gets_403(self):
        """Test that non-participants cannot PUT intention."""
        other_user = TestDataFactory.create_user(username="other2@example.com")
        other_profile = TestDataFactory.create_profile(user=other_user, church=self.church)
        self.client.force_authenticate(user=other_user)
        data = {"text": "Prayer", "is_public": True}
        response = self.client.put(self.intention_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class JoinWithIntentionTests(APITestCase):
    """Tests for joining a fast with an intention."""

    def setUp(self):
        self.client = APIClient()
        self.church = TestDataFactory.create_church()
        self.fast = TestDataFactory.create_fast(church=self.church)
        self.user = TestDataFactory.create_user()
        self.profile = TestDataFactory.create_profile(user=self.user, church=self.church)
        self.client.force_authenticate(user=self.user)
        self.join_url = reverse('fast-join')

    def test_join_with_intention(self):
        """Test joining a fast with intention creates the intention."""
        data = {
            "fast_id": self.fast.id,
            "intention_text": "Pray for peace",
            "intention_is_public": True,
        }
        response = self.client.post(self.join_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        intention = FastIntention.objects.filter(
            user=self.user,
            fast=self.fast,
            is_active=True
        ).first()
        self.assertIsNotNone(intention)
        self.assertEqual(intention.text, "Pray for peace")
        self.assertTrue(intention.is_public)

    def test_join_without_intention(self):
        """Test joining without intention does not create one."""
        data = {"fast_id": self.fast.id}
        response = self.client.post(self.join_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        intention = FastIntention.objects.filter(
            user=self.user,
            fast=self.fast,
            is_active=True
        ).first()
        self.assertIsNone(intention)

    def test_rejoin_restores_soft_kept_intention(self):
        """Test rejoining restores a soft-kept intention."""
        # First join with intention
        self.profile.fasts.add(self.fast)
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Original prayer",
            is_public=True,
            is_active=True,
        )
        # Leave
        self.profile.fasts.remove(self.fast)
        FastIntention.objects.filter(user=self.user, fast=self.fast).update(is_active=False)
        # Rejoin without new intention text
        data = {"fast_id": self.fast.id}
        response = self.client.post(self.join_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        intention = FastIntention.objects.get(user=self.user, fast=self.fast, is_active=True)
        self.assertEqual(intention.text, "Original prayer")

    def test_rejoin_with_new_text_overrides(self):
        """Test rejoining with new text overrides soft-kept intention."""
        # First join with intention
        self.profile.fasts.add(self.fast)
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Old prayer",
            is_public=False,
            is_active=True,
        )
        # Leave
        self.profile.fasts.remove(self.fast)
        FastIntention.objects.filter(user=self.user, fast=self.fast).update(is_active=False)
        # Rejoin with new text
        data = {
            "fast_id": self.fast.id,
            "intention_text": "New prayer",
            "intention_is_public": True,
        }
        response = self.client.post(self.join_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        intention = FastIntention.objects.get(user=self.user, fast=self.fast, is_active=True)
        self.assertEqual(intention.text, "New prayer")
        self.assertTrue(intention.is_public)


class LeaveFastIntentionTests(APITestCase):
    """Tests for leaving a fast and intention soft-delete."""

    def setUp(self):
        self.client = APIClient()
        self.church = TestDataFactory.create_church()
        self.fast = TestDataFactory.create_fast(church=self.church)
        self.user = TestDataFactory.create_user()
        self.profile = TestDataFactory.create_profile(user=self.user, church=self.church)
        self.profile.fasts.add(self.fast)
        self.client.force_authenticate(user=self.user)
        self.leave_url = reverse('leave-fast')

    def test_leave_soft_keeps_intention(self):
        """Test leaving soft-keeps the intention."""
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="My prayer",
            is_public=True,
            is_active=True,
        )
        data = {"fast_id": self.fast.id}
        response = self.client.post(self.leave_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        intention = FastIntention.objects.get(user=self.user, fast=self.fast)
        self.assertFalse(intention.is_active)
        self.assertEqual(intention.text, "My prayer")


class ParticipantListIntentionVisibilityTests(APITestCase):
    """Tests for intention visibility on participant list."""

    def setUp(self):
        self.client = APIClient()
        self.church = TestDataFactory.create_church()
        self.fast = TestDataFactory.create_fast(church=self.church)
        self.user = TestDataFactory.create_user()
        self.profile = TestDataFactory.create_profile(user=self.user, church=self.church)
        self.profile.fasts.add(self.fast)
        self.client.force_authenticate(user=self.user)
        self.participants_url = reverse('fast-participants', kwargs={'fast_id': self.fast.id})

    def test_public_intention_visible_on_participant_list(self):
        """Test public intention appears on participant list."""
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Public prayer",
            is_public=True,
            is_active=True,
        )
        response = self.client.get(self.participants_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Find the current user in the participant list
        participant = next((p for p in response.data if p['id'] == self.profile.id), None)
        self.assertIsNotNone(participant)
        self.assertEqual(participant['intention'], "Public prayer")

    def test_private_intention_hidden_on_participant_list(self):
        """Test private intention is hidden on participant list."""
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Private prayer",
            is_public=False,
            is_active=True,
        )
        response = self.client.get(self.participants_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        participant = next((p for p in response.data if p['id'] == self.profile.id), None)
        self.assertIsNotNone(participant)
        self.assertIsNone(participant['intention'])
