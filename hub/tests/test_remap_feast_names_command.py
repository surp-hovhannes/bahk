"""Tests for the ``remap_feast_names`` command.

The rule itself is covered in ``test_feast_rename``; what is checked here is the command's
contract with whoever runs it against production: a dry run reports without writing, ``--apply``
writes exactly what was reported, ``--church`` scopes, and running it twice does nothing the
second time.  That last one matters because the command is meant to be safe to re-run after any
engine upgrade, including one that renamed nothing.
"""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from hub.models import Church, Feast, FeastContext
from hub.services.feast_rename import engine_names

SCRAPED = "Saints Peter the Patriarch, Blaise the Bishop and Absalom the Deacon"
CURRENT = "Sts. Peter the Patriarch, Blaise the Bishop and Absalom the Deacon"
KEY = "peter_the_patriarch_blaise"


class RemapFeastNamesCommandTests(TestCase):

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())

    def run_command(self, *args):
        out = StringIO()
        call_command("remap_feast_names", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_dry_run_reports_the_rekey_without_writing_it(self):
        feast = Feast.objects.create(church=self.church, name=SCRAPED)

        output = self.run_command()

        self.assertIn("DRY RUN", output)
        self.assertIn(KEY, output)
        self.assertIn("Re-run with --apply", output)
        feast.refresh_from_db()
        self.assertEqual(feast.name, SCRAPED)
        self.assertIsNone(feast.observance_id)

    def test_apply_keys_the_row_and_refreshes_what_the_key_derives(self):
        feast = Feast.objects.create(church=self.church, name=SCRAPED)

        self.run_command("--apply")

        feast.refresh_from_db()
        self.assertEqual(feast.observance_id, KEY)
        self.assertEqual(feast.name, CURRENT)
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
        self.assertEqual(survivor.observance_id, KEY)
        self.assertEqual(survivor.name, CURRENT)
        self.assertEqual(survivor.designation, "Martyrs")
        self.assertEqual(survivor.contexts.get(active=True).text, "curated")

    def test_a_second_run_finds_nothing_left_to_do(self):
        Feast.objects.create(church=self.church, name=SCRAPED)
        self.run_command("--apply")

        output = self.run_command()

        self.assertIn("0 re-keyed, 0 merged", output)
        self.assertIn("0 refreshed in place", output)
        self.assertNotIn("Re-run with --apply", output)

    def test_a_keyed_row_missing_only_its_translation_is_refreshed_not_re_keyed(self):
        feast = Feast.objects.create(church=self.church, name=CURRENT, observance_id=KEY)

        output = self.run_command("--apply")

        self.assertIn("refresh", output)
        feast.refresh_from_db()
        self.assertEqual(feast.observance_id, KEY)
        self.assertTrue(feast.name_hy)

    def test_a_keyed_row_with_a_stale_display_name_is_corrected_in_place(self):
        """The failure this re-key exists to end: a renamed feast is an UPDATE, not an orphan."""
        feast = Feast.objects.create(church=self.church, name=SCRAPED, observance_id=KEY,
                                     designation="Martyrs")

        self.run_command("--apply")

        feast.refresh_from_db()
        self.assertEqual(feast.name, CURRENT)
        self.assertEqual(feast.designation, "Martyrs")
        self.assertEqual(Feast.objects.filter(church=self.church).count(), 1)

    def test_an_unresolvable_name_is_reported_and_left_alone(self):
        """Never deleted: whatever it is, its contexts and icon are not reproducible."""
        feast = Feast.objects.create(church=self.church, name="Not A Commemoration Anyone Publishes")

        output = self.run_command("--apply")

        self.assertIn("unresolved", output)
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
        self.assertEqual(mine.observance_id, KEY)
        self.assertEqual(theirs.observance_id, None)
        self.assertEqual(theirs.name, SCRAPED)

    def test_an_unknown_church_is_an_error_rather_than_a_silent_no_op(self):
        with self.assertRaises(CommandError):
            self.run_command("--church", "No Such Church")

    def test_a_legacy_row_resolves_through_the_map_date_not_the_map_name(self):
        """The map's target NAMES went stale at 2.0.0; its dates did not.

        This row's old spelling maps to "Commemoration of 200 Fathers of the Holy Council of
        Ephesus (AD 431)" -- which 2.x does not emit, because it now says "of the 200 Fathers".
        Resolving through the stored name would strand the row. Resolving through the date the
        entry recorded lands it on the observance, whatever the engine currently calls it.
        """
        stale_target = "Commemoration of 200 Fathers of the Holy Council of Ephesus (AD 431)"
        self.assertNotIn(stale_target, engine_names())

        feast = Feast.objects.create(
            church=self.church,
            name="Commemoration of 200 Fathers of the Holy Council of Ephesus (AD 341)",
        )

        self.run_command("--apply")

        feast.refresh_from_db()
        self.assertEqual(feast.observance_id, "200_fathers_ephesus")
        self.assertEqual(
            feast.name,
            "Commemoration of the 200 Fathers of the Holy Council of Ephesus (AD 431)")
