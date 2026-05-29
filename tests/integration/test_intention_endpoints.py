"""Integration tests for FastIntention API endpoints."""

from django.urls import reverse
from django.test import override_settings
from rest_framework import status
from hub.models import FastIntention
from tests.base import BaseAPITestCase


class JoinWithIntentionTests(BaseAPITestCase):
    """Tests for joining a fast with an intention."""

    def setUp(self):
        self.user = self.authenticate()
        self.church = self.create_church()
        self.profile = self.create_profile(user=self.user, church=self.church)
        self.fast = self.create_fast(church=self.church)

    def test_join_without_intention(self):
        url = reverse("fast-join")
        response = self.client.put(url, {"fast_id": self.fast.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            FastIntention.objects.filter(user=self.user, fast=self.fast).exists()
        )

    def test_join_with_intention(self):
        url = reverse("fast-join")
        response = self.client.put(url, {
            "fast_id": self.fast.id,
            "intention_text": "Pray for peace",
            "intention_is_public": True,
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        intention = FastIntention.objects.get(user=self.user, fast=self.fast)
        self.assertEqual(intention.text, "Pray for peace")
        self.assertTrue(intention.is_public)
        self.assertTrue(intention.is_active)

    def test_join_with_private_intention(self):
        url = reverse("fast-join")
        response = self.client.put(url, {
            "fast_id": self.fast.id,
            "intention_text": "Private prayer",
            "intention_is_public": False,
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        intention = FastIntention.objects.get(user=self.user, fast=self.fast)
        self.assertFalse(intention.is_public)

    def test_join_reactivates_soft_kept_intention(self):
        # Create and then soft-delete an intention
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Old intention",
            is_public=True,
            is_active=False,
        )
        self.profile.fasts.add(self.fast)
        self.profile.fasts.remove(self.fast)

        url = reverse("fast-join")
        response = self.client.put(url, {
            "fast_id": self.fast.id,
            "intention_text": "New intention",
            "intention_is_public": False,
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        intention = FastIntention.objects.get(user=self.user, fast=self.fast, is_active=True)
        self.assertEqual(intention.text, "New intention")
        self.assertFalse(intention.is_public)

    def test_join_reactivates_without_overwrite(self):
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Old intention",
            is_public=True,
            is_active=False,
        )
        self.profile.fasts.add(self.fast)
        self.profile.fasts.remove(self.fast)

        url = reverse("fast-join")
        response = self.client.put(url, {"fast_id": self.fast.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        intention = FastIntention.objects.get(user=self.user, fast=self.fast, is_active=True)
        self.assertEqual(intention.text, "Old intention")
        self.assertTrue(intention.is_public)

    def test_join_with_profanity_rejected(self):
        url = reverse("fast-join")
        response = self.client.put(url, {
            "fast_id": self.fast.id,
            "intention_text": "shitty day",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_join_with_too_long_text_rejected(self):
        url = reverse("fast-join")
        response = self.client.put(url, {
            "fast_id": self.fast.id,
            "intention_text": "x" * 281,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class LeaveFastIntentionTests(BaseAPITestCase):
    """Tests for leaving a fast and soft-keeping intention."""

    def setUp(self):
        self.user = self.authenticate()
        self.church = self.create_church()
        self.profile = self.create_profile(user=self.user, church=self.church)
        self.fast = self.create_fast(church=self.church)
        self.profile.fasts.add(self.fast)
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="My intention",
            is_public=True,
            is_active=True,
        )

    def test_leave_soft_keeps_intention(self):
        url = reverse("leave-fast")
        response = self.client.put(url, {"fast_id": self.fast.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        intention = FastIntention.objects.get(user=self.user, fast=self.fast)
        self.assertFalse(intention.is_active)
        self.assertEqual(intention.text, "My intention")


class FastIntentionEndpointTests(BaseAPITestCase):
    """Tests for the fasts/<fast_id>/intention/ endpoints."""

    def setUp(self):
        self.user = self.authenticate()
        self.church = self.create_church()
        self.profile = self.create_profile(user=self.user, church=self.church)
        self.fast = self.create_fast(church=self.church)
        self.profile.fasts.add(self.fast)

    def test_get_intention(self):
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Pray daily",
            is_public=False,
            is_active=True,
        )
        url = reverse("fast-intention", kwargs={"fast_id": self.fast.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["text"], "Pray daily")
        self.assertFalse(response.data["is_public"])

    def test_get_no_intention_returns_404(self):
        url = reverse("fast-intention", kwargs={"fast_id": self.fast.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_create_intention(self):
        url = reverse("fast-intention", kwargs={"fast_id": self.fast.id})
        response = self.client.put(url, {
            "text": "New intention",
            "is_public": True,
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        intention = FastIntention.objects.get(user=self.user, fast=self.fast)
        self.assertEqual(intention.text, "New intention")
        self.assertTrue(intention.is_public)

    def test_put_update_intention(self):
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Old",
            is_public=False,
            is_active=True,
        )
        url = reverse("fast-intention", kwargs={"fast_id": self.fast.id})
        response = self.client.put(url, {
            "text": "Updated",
            "is_public": True,
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        intention = FastIntention.objects.get(user=self.user, fast=self.fast)
        self.assertEqual(intention.text, "Updated")
        self.assertTrue(intention.is_public)

    def test_put_reactivates_soft_kept(self):
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Soft kept",
            is_public=False,
            is_active=False,
        )
        url = reverse("fast-intention", kwargs={"fast_id": self.fast.id})
        response = self.client.put(url, {
            "text": "Reactivated",
            "is_public": True,
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        intention = FastIntention.objects.get(user=self.user, fast=self.fast)
        self.assertTrue(intention.is_active)
        self.assertEqual(intention.text, "Reactivated")

    def test_delete_clears_text(self):
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="To clear",
            is_public=True,
            is_active=True,
        )
        url = reverse("fast-intention", kwargs={"fast_id": self.fast.id})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        intention = FastIntention.objects.get(user=self.user, fast=self.fast)
        self.assertEqual(intention.text, "")
        self.assertTrue(intention.is_active)

    def test_non_participant_get_returns_403(self):
        other_user = self.create_user(username="other")
        self.create_profile(user=other_user, church=self.church)
        self.client.force_authenticate(user=other_user)
        url = reverse("fast-intention", kwargs={"fast_id": self.fast.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_participant_put_returns_403(self):
        other_user = self.create_user(username="other2")
        self.create_profile(user=other_user, church=self.church)
        self.client.force_authenticate(user=other_user)
        url = reverse("fast-intention", kwargs={"fast_id": self.fast.id})
        response = self.client.put(url, {"text": "Hack"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_participant_delete_returns_403(self):
        other_user = self.create_user(username="other3")
        self.create_profile(user=other_user, church=self.church)
        self.client.force_authenticate(user=other_user)
        url = reverse("fast-intention", kwargs={"fast_id": self.fast.id})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_put_profanity_rejected(self):
        url = reverse("fast-intention", kwargs={"fast_id": self.fast.id})
        response = self.client.put(url, {"text": "shitty day"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_put_too_long_rejected(self):
        url = reverse("fast-intention", kwargs={"fast_id": self.fast.id})
        response = self.client.put(url, {"text": "x" * 281})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}})
class ParticipantPayloadIntentionTests(BaseAPITestCase):
    """Tests that intentions appear correctly on participant payloads."""

    def setUp(self):
        self.user = self.authenticate()
        self.church = self.create_church()
        self.profile = self.create_profile(user=self.user, church=self.church)
        self.fast = self.create_fast(church=self.church)
        self.profile.fasts.add(self.fast)

    def test_public_intention_on_participant_list(self):
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Public prayer",
            is_public=True,
            is_active=True,
        )
        url = reverse("fast-participants", kwargs={"fast_id": self.fast.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # The participant list should include the intention
        participant = response.data[0]
        self.assertEqual(participant["intention"], "Public prayer")

    def test_private_intention_hidden_on_participant_list(self):
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Private prayer",
            is_public=False,
            is_active=True,
        )
        url = reverse("fast-participants", kwargs={"fast_id": self.fast.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        participant = response.data[0]
        self.assertIsNone(participant["intention"])

    def test_inactive_intention_hidden(self):
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Inactive",
            is_public=True,
            is_active=False,
        )
        url = reverse("fast-participants", kwargs={"fast_id": self.fast.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        participant = response.data[0]
        self.assertIsNone(participant["intention"])
