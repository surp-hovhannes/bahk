"""Tests for the ``audit_feast_duplicates`` command.

Since the re-key the command reports one thing: stored feast names the engine no longer emits.
Those are unreachable -- names come from the engine per request, so nothing will ever look them
up again -- while their designation, icon and generated contexts sit in the database untouched.
It is silent, because nothing errors, which is why it is worth a command.

The merge rule the command used to dry-run now lives in ``hub/tests/test_feast_merge.py``.
"""
import datetime
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from hub.management.commands.audit_feast_duplicates import engine_names
from hub.models import Church, Feast


class EngineNamesTests(TestCase):
    """The reachability set stored names are checked against."""

    def test_covers_a_known_name_and_stays_bounded(self):
        names = engine_names(2026, 2026)
        self.assertIn("Fast day", names)
        # A year emits far fewer names than it has days; that compression is the whole argument
        # for keying feasts by commemoration, so assert it rather than mere non-emptiness.
        self.assertLess(len(names), 365)

    def test_a_name_the_engine_never_emits_is_absent(self):
        self.assertNotIn("Presentation of Jesus at the Temple", engine_names(2026, 2026))


class AuditCommandTests(TestCase):
    """The command itself: read-only, and it names what would be stranded."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        # One name the engine really emits, and one invented -- the seed fixtures are full of the
        # latter, which is how this check earned its place.
        Feast.objects.create(church=self.church, name="Fast day")
        self.stranded = Feast.objects.create(
            church=self.church,
            name="Presentation of Jesus at the Temple",
            designation=Feast.Designation.MARTYRS,
        )

    def _run(self, **kwargs):
        out = StringIO()
        call_command("audit_feast_duplicates", stdout=out, **kwargs)
        return out.getvalue()

    def test_flags_the_unreachable_name_and_not_the_reachable_one(self):
        output = self._run()
        self.assertIn("Presentation of Jesus at the Temple", output)
        self.assertIn("1 name(s) the engine never emits", output)

    def test_reports_what_the_stranded_row_is_holding(self):
        """So a reader can judge whether to rename it onto a live name or drop it."""
        self.assertIn(f"designation={str(Feast.Designation.MARTYRS)!r}", self._run())

    def test_writes_nothing(self):
        before = list(Feast.objects.values_list("id", "name", "designation", "icon_id"))
        self._run(verbose=True)
        self.assertEqual(
            list(Feast.objects.values_list("id", "name", "designation", "icon_id")), before)

    def test_verbose_lists_reachable_names_too(self):
        self.assertIn("Fast day", self._run(verbose=True))

    def test_unknown_church_is_an_error(self):
        with self.assertRaises(CommandError):
            call_command("audit_feast_duplicates", church="Nonesuch", stdout=StringIO())

    def test_a_church_with_no_feasts_is_reported_not_skipped(self):
        empty = Church.objects.create(name="Empty Church")
        self.assertIn(f"{empty.name}: no feasts", self._run())
