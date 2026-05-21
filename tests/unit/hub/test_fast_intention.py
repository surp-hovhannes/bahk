"""Unit tests for FastIntention model, serializer, and visibility rules."""

from django.test import TestCase
from django.contrib.auth.models import User
from hub.models import Church, Fast, Profile, FastIntention
from hub.serializers import FastIntentionSerializer, ParticipantSerializer
from tests.base import BaseTestCase


class FastIntentionModelTests(BaseTestCase):
    """Tests for the FastIntention model."""

    def setUp(self):
        self.church = self.create_church()
        self.fast = self.create_fast(church=self.church)
        self.user = self.create_user()
        self.profile = self.create_profile(user=self.user, church=self.church)
        self.profile.fasts.add(self.fast)

    def test_create_intention(self):
        intention = FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Pray for peace",
            is_public=True,
        )
        self.assertEqual(intention.text, "Pray for peace")
        self.assertTrue(intention.is_public)
        self.assertTrue(intention.is_active)

    def test_unique_active_intention_per_user_fast(self):
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="First intention",
            is_active=True,
        )
        with self.assertRaises(Exception):
            FastIntention.objects.create(
                user=self.user,
                fast=self.fast,
                text="Second intention",
                is_active=True,
            )

    def test_soft_kept_allows_second_inactive(self):
        FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="First intention",
            is_active=True,
        )
        FastIntention.objects.filter(user=self.user, fast=self.fast).update(is_active=False)
        # Should not raise
        second = FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Second intention",
            is_active=True,
        )
        self.assertTrue(second.is_active)

    def test_str_representation(self):
        intention = FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Test",
            is_public=False,
        )
        self.assertIn(self.user.email, str(intention))
        self.assertIn("private", str(intention))
        self.assertIn("active", str(intention))


class FastIntentionSerializerTests(BaseTestCase):
    """Tests for FastIntentionSerializer validation."""

    def test_valid_text(self):
        serializer = FastIntentionSerializer(data={"text": "Pray for peace", "is_public": True})
        self.assertTrue(serializer.is_valid())

    def test_text_too_long(self):
        serializer = FastIntentionSerializer(data={"text": "x" * 281})
        self.assertFalse(serializer.is_valid())
        self.assertIn("text", serializer.errors)

    def test_profanity_rejected(self):
        serializer = FastIntentionSerializer(data={"text": "shitty day"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("text", serializer.errors)

    def test_blank_text_allowed(self):
        serializer = FastIntentionSerializer(data={"text": "", "is_public": False})
        self.assertTrue(serializer.is_valid())


class ParticipantSerializerIntentionTests(BaseTestCase):
    """Tests for ParticipantSerializer intention visibility."""

    def setUp(self):
        self.church = self.create_church()
        self.fast = self.create_fast(church=self.church)
        self.user = self.create_user()
        self.profile = self.create_profile(user=self.user, church=self.church)

    def test_public_intention_visible(self):
        intention = FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Public prayer",
            is_public=True,
            is_active=True,
        )
        serializer = ParticipantSerializer(
            self.profile,
            context={"fast_id": self.fast.id}
        )
        # Attach intention manually to simulate prefetch
        self.profile._intention = intention
        result = serializer.data
        self.assertEqual(result["intention"], "Public prayer")

    def test_private_intention_hidden(self):
        intention = FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Private prayer",
            is_public=False,
            is_active=True,
        )
        self.profile._intention = intention
        serializer = ParticipantSerializer(
            self.profile,
            context={"fast_id": self.fast.id}
        )
        result = serializer.data
        self.assertIsNone(result["intention"])

    def test_inactive_intention_hidden(self):
        intention = FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="Old prayer",
            is_public=True,
            is_active=False,
        )
        self.profile._intention = intention
        serializer = ParticipantSerializer(
            self.profile,
            context={"fast_id": self.fast.id}
        )
        result = serializer.data
        self.assertIsNone(result["intention"])

    def test_no_intention_returns_none(self):
        serializer = ParticipantSerializer(
            self.profile,
            context={"fast_id": self.fast.id}
        )
        result = serializer.data
        self.assertIsNone(result["intention"])

    def test_empty_text_returns_none(self):
        intention = FastIntention.objects.create(
            user=self.user,
            fast=self.fast,
            text="",
            is_public=True,
            is_active=True,
        )
        self.profile._intention = intention
        serializer = ParticipantSerializer(
            self.profile,
            context={"fast_id": self.fast.id}
        )
        result = serializer.data
        self.assertIsNone(result["intention"])
