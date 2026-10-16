"""apply_icon_review writes only reviewed changes, and only to rows that still look as reviewed."""

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from hub.models import Church, Feast
from icons.models import Icon


def make_icon(church, title, tags=()):
    icon = Icon.objects.create(
        title=title, church=church, image=SimpleUploadedFile(f"{title}.jpg", b"fake", content_type="image/jpeg")
    )
    if tags:
        icon.tags.add(*tags)
    return icon


class ApplyIconReviewTests(TestCase):
    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.palm = make_icon(self.church, "Palm Sunday")
        self.wrong = make_icon(self.church, "Presentation of the Lord")
        self.lazarus = make_icon(self.church, "IlluminatedManuscript1450(5)", ["illuminated-manuscript"])
        with patch("hub.signals.match_icon_to_feast_task.delay"):
            self.empty = Feast.objects.create(church=self.church, name="Palm Sunday")
            self.mislabelled = Feast.objects.create(church=self.church, name="Holy Voskian Priests", icon=self.wrong)
            self.kept = Feast.objects.create(church=self.church, name="Kept", icon=self.palm)

    def review(self, feasts=(), icons=()):
        self.tmp = TemporaryDirectory()
        path = Path(self.tmp.name) / "review.json"
        path.write_text(json.dumps({"feasts": list(feasts), "icons": list(icons)}))
        return str(path)

    def run_command(self, path, apply=False):
        out = StringIO()
        call_command("apply_icon_review", path, apply=apply, stdout=out)
        return out.getvalue()

    def decisions(self):
        return self.review(
            feasts=[
                {"feast_id": self.empty.id, "choice": "icon", "icon_id": self.palm.id, "expected_icon_id": None},
                {"feast_id": self.mislabelled.id, "choice": "none", "icon_id": None, "expected_icon_id": self.wrong.id},
                {"feast_id": self.kept.id, "choice": "keep", "icon_id": self.palm.id, "expected_icon_id": self.palm.id},
            ],
            icons=[
                {
                    "icon_id": self.lazarus.id,
                    "choice": "update",
                    "expected_title": "IlluminatedManuscript1450(5)",
                    "title": "Resurrection of Lazarus",
                    "add_tags": ["lazarus", "Illuminated-Manuscript", "lazarus"],
                }
            ],
        )

    def test_dry_run_writes_nothing(self):
        output = self.run_command(self.decisions())
        self.assertIn("DRY RUN", output)
        self.assertIn("assign", output)
        self.assertIn("remove", output)
        self.empty.refresh_from_db()
        self.lazarus.refresh_from_db()
        self.assertIsNone(self.empty.icon_id)
        self.assertEqual(self.lazarus.title, "IlluminatedManuscript1450(5)")

    def test_apply_writes_reviewed_changes_and_is_repeatable(self):
        path = self.decisions()
        self.run_command(path, apply=True)
        for feast in (self.empty, self.mislabelled, self.kept):
            feast.refresh_from_db()
        self.lazarus.refresh_from_db()
        self.assertEqual(self.empty.icon_id, self.palm.id)
        self.assertIsNone(self.mislabelled.icon_id)
        self.assertEqual(self.kept.icon_id, self.palm.id)
        self.assertEqual(self.lazarus.title, "Resurrection of Lazarus")
        self.assertEqual(sorted(self.lazarus.tags.names()), ["illuminated-manuscript", "lazarus"])
        second = self.run_command(path, apply=True)
        self.assertIn("feast done 2", second)
        self.assertIn("icon done 1", second)

    def test_rows_changed_since_review_are_skipped(self):
        path = self.decisions()
        other = make_icon(self.church, "Someone else's choice")
        Feast.objects.filter(pk=self.mislabelled.pk).update(icon=other)
        Icon.objects.filter(pk=self.lazarus.pk).update(title="Renamed by staff")
        output = self.run_command(path, apply=True)
        self.mislabelled.refresh_from_db()
        self.lazarus.refresh_from_db()
        self.assertEqual(self.mislabelled.icon_id, other.id)
        self.assertEqual(self.lazarus.title, "Renamed by staff")
        self.assertIn("changed since review", output)

    def test_icon_from_another_church_is_refused(self):
        stranger = make_icon(Church.objects.create(name="Other Church"), "Palm Sunday")
        path = self.review(
            feasts=[{"feast_id": self.empty.id, "choice": "icon", "icon_id": stranger.id, "expected_icon_id": None}]
        )
        output = self.run_command(path, apply=True)
        self.empty.refresh_from_db()
        self.assertIsNone(self.empty.icon_id)
        self.assertIn("another church", output)

    def test_malformed_files_are_rejected_before_any_write(self):
        bad = self.review(
            feasts=[
                {"feast_id": self.empty.id, "choice": "icon", "icon_id": self.palm.id, "expected_icon_id": None},
                {"feast_id": self.kept.id, "choice": "replace"},
            ]
        )
        with self.assertRaises(CommandError):
            self.run_command(bad, apply=True)
        self.empty.refresh_from_db()
        self.assertIsNone(self.empty.icon_id)
        with self.assertRaises(CommandError):
            self.run_command(
                self.review(
                    icons=[{"icon_id": self.lazarus.id, "choice": "update", "expected_title": "x", "title": ""}]
                )
            )

    def test_committed_review_file_is_well_formed(self):
        path = Path(__file__).resolve().parents[1] / "data" / "icon_review" / "2026-10-16.json"
        from hub.management.commands.apply_icon_review import load_review

        feasts, icons = load_review(path)
        self.assertTrue(feasts and icons)
