"""Tests for app management models."""

from datetime import date

from django.test import TestCase

from app_management.models import Changelog


class ChangelogModelTests(TestCase):
    """Tests for app changelog entries."""

    def test_preserves_explicit_release_date(self):
        """Historical or planned release dates should not be overwritten."""
        release_date = date(2025, 1, 15)

        changelog = Changelog.objects.create(
            title="Release notes",
            description="Released on a specific day.",
            version="1.2.3",
            date=release_date,
        )

        changelog.refresh_from_db()
        self.assertEqual(changelog.date, release_date)
