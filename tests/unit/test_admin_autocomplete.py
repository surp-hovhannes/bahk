"""Tests for searchable relation pickers in high-volume admin forms."""

from django.contrib.admin.sites import AdminSite
from django.test import SimpleTestCase

from hub.admin import (
    DayAdmin,
    DevotionalAdmin,
    DevotionalSetAdmin,
    FastAdmin,
    FastIntentionAdmin,
    FeastAdmin,
    FeastContextAdmin,
    PatristicQuoteAdmin,
    ProfileAdmin,
    ReadingAdmin,
    ReadingContextAdmin,
)
from hub.models import (
    Day,
    Devotional,
    DevotionalSet,
    Fast,
    FastIntention,
    Feast,
    FeastContext,
    PatristicQuote,
    Profile,
    Reading,
    ReadingContext,
)
from learning_resources.admin import BookmarkAdmin
from learning_resources.models import Bookmark
from prayers.admin import (
    PrayerAdmin,
    PrayerRequestAcceptanceAdmin,
    PrayerRequestAdmin,
    PrayerRequestPrayerLogAdmin,
    PrayerSetAdmin,
    PrayerSetMembershipInline,
)
from prayers.models import (
    Prayer,
    PrayerRequest,
    PrayerRequestAcceptance,
    PrayerRequestPrayerLog,
    PrayerSet,
    PrayerSetMembership,
)


class AdminAutocompleteTests(SimpleTestCase):
    def test_calendar_and_content_forms_use_searchable_relations(self):
        site = AdminSite()
        expected = (
            (DevotionalAdmin(Devotional, site), {"video", "day"}),
            (DevotionalSetAdmin(DevotionalSet, site), {"fast"}),
            (FastAdmin(Fast, site), {"church"}),
            (ProfileAdmin(Profile, site), {"user", "church", "fasts"}),
            (DayAdmin(Day, site), {"church", "fast"}),
            (ReadingAdmin(Reading, site), {"day"}),
            (ReadingContextAdmin(ReadingContext, site), {"reading", "prompt"}),
            (FeastAdmin(Feast, site), {"icon"}),
            (FeastContextAdmin(FeastContext, site), {"feast", "prompt"}),
            (PatristicQuoteAdmin(PatristicQuote, site), {"churches", "fasts"}),
            (FastIntentionAdmin(FastIntention, site), {"user", "fast"}),
        )

        for model_admin, fields in expected:
            with self.subTest(model_admin=model_admin.__class__.__name__):
                self.assertTrue(fields.issubset(set(model_admin.autocomplete_fields)))

    def test_prayer_and_bookmark_forms_use_searchable_relations(self):
        site = AdminSite()
        expected = (
            (PrayerAdmin(Prayer, site), {"church", "fast", "video", "icon"}),
            (PrayerSetAdmin(PrayerSet, site), {"church", "icon"}),
            (PrayerRequestAdmin(PrayerRequest, site), {"requester", "icon"}),
            (
                PrayerRequestAcceptanceAdmin(PrayerRequestAcceptance, site),
                {"prayer_request", "user"},
            ),
            (
                PrayerRequestPrayerLogAdmin(PrayerRequestPrayerLog, site),
                {"prayer_request", "user"},
            ),
            (BookmarkAdmin(Bookmark, site), {"user"}),
        )

        for model_admin, fields in expected:
            with self.subTest(model_admin=model_admin.__class__.__name__):
                self.assertTrue(fields.issubset(set(model_admin.autocomplete_fields)))

        inline = PrayerSetMembershipInline(PrayerSet, site)
        self.assertEqual(inline.model, PrayerSetMembership)
        self.assertIn("prayer", inline.autocomplete_fields)
