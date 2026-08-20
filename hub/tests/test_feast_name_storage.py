"""Regression tests for the ``Feast.name`` storage limit.

Two Armenian lectionary feast names enumerate their saints inside the name and run past
256 characters -- the Twelve Holy Doctors (289) and the Holy Fathers of Egypt (257) --
recurring on 54 dates across 2001-2027. On PostgreSQL, storing one raised ``DataError``:
the API degraded to "no feast" for that day and a range import aborted partway through.

Nothing caught it, because the test database is SQLite and SQLite does not enforce
``max_length`` -- it stores an over-long value happily and the assertion passes. These
tests therefore go through ``full_clean()``, which applies Django's validators regardless
of backend, so the limit is checked on the DB we actually test against.
"""

from django.core.exceptions import ValidationError
from django.test import TestCase

from hub.models import Church, Feast

# Verbatim from the lectionary; the longest feast names in the 2001-2027 corpus. Both are
# byte-identical to what sacredtradition.am served, which is why the retired scrape failed
# the same way.
TWELVE_HOLY_DOCTORS = (
    "The Twelve Holy Doctors of Church: Hierotheus of Athens, Dionysius the Areopagite, "
    "Sylvester of Rome, Athanasius of Alexandria, Cyril of Jerusalem, Ephraim the Syrian, "
    "Basil the Great, Gregory of Nyssa, Gregory the Theologian, Epiphanius of Cyprus, "
    "John Chrysostom, and Cyril of Alexandria"
)
HOLY_FATHERS_OF_EGYPT = (
    "The Holy Fathers of Egypt: Paul the Hermit, Paul the Simple, Macarius the Great, "
    "Evagrius Ponticus, John of the Well, John the Dwarf, Nilus of Sinai, Arsenios the "
    "Great, Sisoes the Great, Daniel of Scetis, Serapion, Macarius of Alexandria, Poimen "
    "and others"
)


class FeastNameStorageTests(TestCase):
    """The column must hold every name the lectionary can produce."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())

    def test_longest_names_are_representative(self):
        """Guard the fixtures: these must actually exceed the old 256 limit."""
        self.assertEqual(len(TWELVE_HOLY_DOCTORS), 289)
        self.assertEqual(len(HOLY_FATHERS_OF_EGYPT), 257)

    def test_column_holds_the_longest_names(self):
        """``full_clean()`` enforces max_length even on SQLite, unlike a bare save()."""
        for name in (TWELVE_HOLY_DOCTORS, HOLY_FATHERS_OF_EGYPT):
            with self.subTest(name=name[:40]):
                feast = Feast(church=self.church, name=name)
                feast.full_clean()      # raises ValidationError if the column is too narrow
                feast.save()
                feast.refresh_from_db()
                self.assertEqual(feast.name, name, "name was truncated in storage")

    def test_limit_is_wide_enough_for_the_corpus(self):
        max_length = Feast._meta.get_field("name").max_length
        self.assertGreaterEqual(
            max_length, len(TWELVE_HOLY_DOCTORS),
            "Feast.name is too narrow for the longest lectionary feast name; "
            "PostgreSQL will raise DataError on it")

    def test_over_limit_name_is_still_rejected(self):
        """Widening the column must not mean it accepts anything."""
        max_length = Feast._meta.get_field("name").max_length
        feast = Feast(church=self.church, name="x" * (max_length + 1))
        with self.assertRaises(ValidationError):
            feast.full_clean()
