"""Tests for the guarded feast icon assignment command."""

from datetime import date
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import TestCase, TransactionTestCase, override_settings

from hub.cache import feast_api_cache_key
from hub.models import Church, Day, Feast
from icons.models import Icon


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}})
class AssignFeastIconCommandTests(TestCase):
    def setUp(self):
        self.church = Church.objects.create(name="Command Church")
        self.other_church = Church.objects.create(name="Other Church")
        self.day = Day.objects.create(date=date(2026, 7, 21), church=self.church)
        self.icon = self.create_icon("Approved Icon", self.church)
        self.replacement = self.create_icon("Replacement Icon", self.church)
        self.other_icon = self.create_icon("Other Church Icon", self.other_church)
        with (
            patch("hub.signals.match_icon_to_feast_task.delay"),
            patch("hub.signals.determine_feast_designation_task.delay"),
        ):
            self.feast = Feast.objects.create(church=self.church, name="Command Feast")

    def create_icon(self, title, church):
        return Icon.objects.create(
            title=title,
            church=church,
            image=SimpleUploadedFile(f"{title}.jpg", b"image", content_type="image/jpeg"),
        )

    def run_command(self, *args):
        stdout = StringIO()
        stderr = StringIO()
        call_command("assign_feast_icon", *args, stdout=stdout, stderr=stderr)
        return stdout.getvalue(), stderr.getvalue()

    def confirmed_args(self, icon=None):
        icon = icon or self.icon
        return (
            "--feast-id",
            str(self.feast.pk),
            "--icon-id",
            str(icon.pk),
            "--execute",
            "--confirm",
            f"feast:{self.feast.pk}:icon:{icon.pk}",
        )

    def test_preview_is_non_mutating(self):
        out, err = self.run_command("--feast-id", str(self.feast.pk), "--icon-id", str(self.icon.pk))
        self.feast.refresh_from_db()
        self.assertEqual(err, "")
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("DRY RUN", out)
        self.assertIn(f"feast_id={self.feast.pk}", out)
        self.assertIn(f"church_id={self.church.pk}", out)
        self.assertIn("date=null", out)
        self.assertIsNone(self.feast.icon_id)

    def test_execute_requires_exact_confirmation(self):
        base = ("--feast-id", str(self.feast.pk), "--icon-id", str(self.icon.pk), "--execute")
        with self.assertRaises(CommandError):
            self.run_command(*base)
        with self.assertRaises(CommandError):
            self.run_command(*base, "--confirm", "wrong")
        self.feast.refresh_from_db()
        self.assertIsNone(self.feast.icon_id)

    def test_assignment(self):
        out, err = self.run_command(*self.confirmed_args())
        self.feast.refresh_from_db()
        self.assertEqual(err, "")
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("ASSIGNED", out)
        self.assertEqual(self.feast.icon_id, self.icon.pk)

    def test_missing_records_and_cross_church_icon_are_rejected(self):
        with self.assertRaisesMessage(CommandError, "Feast 999999 does not exist"):
            self.run_command("--feast-id", "999999", "--icon-id", str(self.icon.pk))
        with self.assertRaisesMessage(CommandError, "Icon 999999 does not exist"):
            self.run_command("--feast-id", str(self.feast.pk), "--icon-id", "999999")
        with self.assertRaisesMessage(CommandError, "different church"):
            self.run_command("--feast-id", str(self.feast.pk), "--icon-id", str(self.other_icon.pk))
        self.feast.refresh_from_db()
        self.assertIsNone(self.feast.icon_id)

    def test_conflict_is_rejected_without_success_output(self):
        self.feast.icon = self.icon
        self.feast.save(update_fields=["icon"])
        stdout = StringIO()
        with self.assertRaises(CommandError):
            call_command(
                "assign_feast_icon",
                "--feast-id",
                str(self.feast.pk),
                "--icon-id",
                str(self.replacement.pk),
                stdout=stdout,
            )
        self.feast.refresh_from_db()
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(self.feast.icon_id, self.icon.pk)

    def test_forced_replacement_and_force_requires_execute(self):
        self.feast.icon = self.icon
        self.feast.save(update_fields=["icon"])
        with self.assertRaises(CommandError):
            self.run_command(
                "--feast-id",
                str(self.feast.pk),
                "--icon-id",
                str(self.replacement.pk),
                "--force",
            )
        out, _err = self.run_command(*self.confirmed_args(self.replacement), "--force")
        self.feast.refresh_from_db()
        self.assertIn("REPLACED", out)
        self.assertIn(f"current_icon_id={self.icon.pk}", out)
        self.assertEqual(self.feast.icon_id, self.replacement.pk)

    def test_idempotent_retry_is_no_op_without_save(self):
        self.run_command(*self.confirmed_args())
        with patch("hub.models.Feast.save") as save:
            out, _err = self.run_command(*self.confirmed_args())
        self.feast.refresh_from_db()
        self.assertIn("NO-OP", out)
        save.assert_not_called()
        self.assertEqual(self.feast.icon_id, self.icon.pk)

    def test_dry_run_and_execute_are_contradictory(self):
        with self.assertRaises(CommandError):
            self.run_command(*self.confirmed_args(), "--dry-run")

    def test_positive_ids_are_required(self):
        with self.assertRaises(CommandError):
            self.run_command("--feast-id", "0", "--icon-id", str(self.icon.pk))
        with self.assertRaises(CommandError):
            self.run_command("--feast-id", str(self.feast.pk), "--icon-id", "-1")


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class AssignFeastIconCacheTests(TestCase):
    def test_assignment_invalidates_feast_api_cache(self):
        church = Church.objects.create(name="Cache Church")
        day = Day.objects.create(date=date(2026, 7, 22), church=church)
        icon = Icon.objects.create(
            title="Cache Icon",
            church=church,
            image=SimpleUploadedFile("cache.jpg", b"image", content_type="image/jpeg"),
        )
        with (
            patch("hub.signals.match_icon_to_feast_task.delay"),
            patch("hub.signals.determine_feast_designation_task.delay"),
        ):
            feast = Feast.objects.create(church=church, name="Cache Feast")
        key = feast_api_cache_key(day.date, church.pk, "en")
        cache.set(key, "cached")

        with self.captureOnCommitCallbacks(execute=True):
            call_command(
                "assign_feast_icon",
                "--feast-id",
                str(feast.pk),
                "--icon-id",
                str(icon.pk),
                "--execute",
                "--confirm",
                f"feast:{feast.pk}:icon:{icon.pk}",
                stdout=StringIO(),
            )

        self.assertNotEqual(feast_api_cache_key(day.date, church.pk, "en"), key)
        self.assertIsNone(cache.get(feast_api_cache_key(day.date, church.pk, "en")))


class FeastSaveCacheInvalidationTransactionTests(TransactionTestCase):
    def setUp(self):
        self.church = Church.objects.create(name="Transaction Church")
        self.day = Day.objects.create(date=date(2026, 7, 23), church=self.church)
        with (
            patch("hub.signals.invalidate_feast_api_cache_for_feast"),
            patch("hub.signals.match_icon_to_feast_task.delay"),
            patch("hub.signals.determine_feast_designation_task.delay"),
        ):
            self.feast = Feast.objects.create(church=self.church, name="Transaction Feast")

    def test_invalidation_waits_for_outer_commit(self):
        with patch("hub.signals.invalidate_feast_api_cache_for_feast") as invalidate:
            with transaction.atomic():
                self.feast.name = "Committed Feast"
                self.feast.save(update_fields=["name"])
                invalidate.assert_not_called()

            invalidate.assert_called_once_with(self.feast)

    def test_invalidation_is_discarded_on_rollback(self):
        with patch("hub.signals.invalidate_feast_api_cache_for_feast") as invalidate:
            try:
                with transaction.atomic():
                    self.feast.name = "Rolled Back Feast"
                    self.feast.save(update_fields=["name"])
                    invalidate.assert_not_called()
                    raise RuntimeError("force rollback")
            except RuntimeError:
                pass

            invalidate.assert_not_called()

    def test_autocommit_save_invalidates_immediately(self):
        with patch("hub.signals.invalidate_feast_api_cache_for_feast") as invalidate:
            self.feast.name = "Autocommit Feast"
            self.feast.save(update_fields=["name"])

            invalidate.assert_called_once_with(self.feast)
