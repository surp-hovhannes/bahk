"""Tests for the ``remap_feast_names`` command.

The rule itself is covered in ``test_feast_rename``; what is checked here is the command's
contract with whoever runs it against production: a dry run reports without writing, ``--apply``
writes exactly what was reported, ``--church`` scopes, and running it twice does nothing the
second time.  That last one matters because the command is meant to be safe to re-run after any
engine upgrade, including one that renamed nothing.
"""
import datetime
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from hub.models import Church, Feast, FeastContext

SCRAPED = "Saints Peter the Patriarch, Blaise the Bishop and Absalom the Deacon"
CURRENT = "Sts. Peter the Patriarch, Blaise the Bishop and Absalom the Deacon"


class RemapFeastNamesCommandTests(TestCase):

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())

    def run_command(self, *args):
        out = StringIO()
        call_command("remap_feast_names", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_dry_run_reports_the_rename_without_writing_it(self):
        feast = Feast.objects.create(church=self.church, name=SCRAPED)

        output = self.run_command()

        self.assertIn("DRY RUN", output)
        self.assertIn(CURRENT, output)
        self.assertIn("Re-run with --apply", output)
        feast.refresh_from_db()
        self.assertEqual(feast.name, SCRAPED)
        self.assertIsNone(feast.sample_date)

    def test_apply_renames_the_row_and_records_its_date(self):
        feast = Feast.objects.create(church=self.church, name=SCRAPED)

        self.run_command("--apply")

        feast.refresh_from_db()
        self.assertEqual(feast.name, CURRENT)
        self.assertIsNotNone(feast.sample_date)
        self.assertTrue(feast.name_hy)

    def test_apply_merges_the_stale_row_into_the_row_that_displaced_it(self):
        """The shape an engine upgrade leaves: enrichment on the old row, the name on a new one."""
        old = Feast.objects.create(church=self.church, name=SCRAPED, designation="Martyrs")
        FeastContext.objects.create(
            feast=old, text="curated", short_text="curated", active=True, thumbs_up=7)
        new = Feast.objects.create(church=self.church, name=CURRENT)

        output = self.run_command("--apply")

        self.assertIn("merge", output)
        self.assertFalse(Feast.objects.filter(pk=new.pk).exists())
        survivor = Feast.objects.get(pk=old.pk)
        self.assertEqual(survivor.name, CURRENT)
        self.assertEqual(survivor.designation, "Martyrs")
        self.assertEqual(survivor.contexts.get(active=True).text, "curated")

    def test_a_second_run_finds_nothing_left_to_do(self):
        Feast.objects.create(church=self.church, name=SCRAPED)
        self.run_command("--apply")

        output = self.run_command()

        self.assertIn("0 renamed, 0 merged", output)
        self.assertIn("0 refreshed in place", output)
        self.assertNotIn("Re-run with --apply", output)

    def test_a_current_name_missing_only_its_date_is_refreshed_not_renamed(self):
        feast = Feast.objects.create(church=self.church, name=CURRENT)

        output = self.run_command("--apply")

        self.assertIn("refresh", output)
        feast.refresh_from_db()
        self.assertEqual(feast.name, CURRENT)
        self.assertIsNotNone(feast.sample_date)

    def test_an_unresolvable_name_is_reported_and_left_alone(self):
        """Never deleted: whatever it is, its contexts and icon are not reproducible."""
        feast = Feast.objects.create(church=self.church, name="Not A Commemoration Anyone Publishes")

        output = self.run_command("--apply")

        self.assertIn("unmapped", output)
        self.assertIn("1 unresolved", output)
        self.assertTrue(Feast.objects.filter(pk=feast.pk).exists())
        feast.refresh_from_db()
        self.assertEqual(feast.name, "Not A Commemoration Anyone Publishes")

    def test_church_scoping_leaves_other_churches_untouched(self):
        other = Church.objects.create(name="Other Church")
        mine = Feast.objects.create(church=self.church, name=SCRAPED)
        theirs = Feast.objects.create(church=other, name=SCRAPED)

        self.run_command("--church", self.church.name, "--apply")

        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual(mine.name, CURRENT)
        self.assertEqual(theirs.name, SCRAPED)

    def test_an_unknown_church_is_an_error_rather_than_a_silent_no_op(self):
        with self.assertRaises(CommandError):
            self.run_command("--church", "No Such Church")

    def test_a_recorded_date_is_followed_ahead_of_the_map(self):
        """The recurring path: the row says which day it was named for, so the engine decides."""
        feast = Feast.objects.create(
            church=self.church,
            name="Whatever An Older Engine Called This Day",
            sample_date=datetime.date(2001, 1, 16),
        )

        self.run_command("--apply")

        feast.refresh_from_db()
        self.assertEqual(feast.name, CURRENT)
