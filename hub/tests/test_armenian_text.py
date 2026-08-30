"""Tests for Armenian Bible text composition from the offline ``BibleVerse`` corpus.

Tests cover:
    - the "hy" fetcher (USFM lookup, passage composition, range bounds)
    - ensure_book_hy (book_hy population from the corpus mapping)
    - fetch_armenian_reading_text_task (task behavior, metadata, error handling)

Armenian reading text is served from the local ``BibleVerse`` table (loaded from the
git-tracked corpus), not scraped from sacredtradition.am, and is stored per passage in
``PassageText`` rather than on each ``Reading`` row.
"""
from datetime import date

from django.test import TestCase, override_settings

from hub.constants import BOOK_NAME_TO_USFM_NORMALIZED, normalize_book_name
from hub.models import BibleVerse, Church, Day, PassageText, Reading
from hub.services.reading_text_service import (
    ARMENIAN_TEXT_VERSION,
    usfm_to_hy_book_name,
    ensure_book_hy,
    fetch_all_reading_texts,
)
from hub.tasks.armenian_text_tasks import fetch_armenian_reading_text_task


def _usfm_for(book: str) -> str:
    """Resolve the USFM id the production code would map ``book`` to."""
    return BOOK_NAME_TO_USFM_NORMALIZED[normalize_book_name(book)]


def _add_verses(usfm, chapter, verses, version=BibleVerse.NOR_EJMIATSIN):
    """Create BibleVerse rows for ``{verse_number: text}`` in a single chapter."""
    BibleVerse.objects.bulk_create([
        BibleVerse(version=version, book=usfm, chapter=chapter, verse=v, text=t)
        for v, t in verses.items()
    ])


def _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5, **kwargs):
    """Helper to create a Reading."""
    return Reading.objects.create(
        day=day,
        book=book,
        start_chapter=start_ch,
        start_verse=start_v,
        end_chapter=end_ch,
        end_verse=end_v,
        **kwargs,
    )


def _compose_hy(reading) -> bool:
    """Run just the Armenian fetcher for a reading's passage."""
    return fetch_all_reading_texts(reading, langs=["hy"]).get("hy", False)


def _hy(reading):
    """The stored Armenian PassageText for a reading's passage, or None."""
    return PassageText.objects.filter(
        passage_key=reading.passage_key, language="hy",
    ).first()


# ------------------------------------------------------------------ #
#  Armenian composition from the corpus
# ------------------------------------------------------------------ #

class ComposeArmenianTextTests(TestCase):
    """Tests for composing Armenian text from the BibleVerse corpus."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2026, 2, 16), church=self.church)

    def test_single_chapter_passage_composed_with_markers(self):
        usfm = _usfm_for("Genesis")
        _add_verses(usfm, 1, {1: "Skizby", 2: "verse two", 3: "v3", 4: "v4", 5: "v5"})
        reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        self.assertTrue(_compose_hy(reading))

        stored = _hy(reading)
        # Inline [verse] markers, only the requested range, in order.
        self.assertTrue(stored.text.startswith("[1] "))
        self.assertIn("[5] v5", stored.text)
        self.assertEqual(stored.version, ARMENIAN_TEXT_VERSION)
        self.assertIsNotNone(stored.fetched_at)

    def test_cross_chapter_passage_composed(self):
        usfm = _usfm_for("Romans")
        _add_verses(usfm, 13, {11: "r13_11", 12: "r13_12", 13: "r13_13", 14: "r13_14"})
        _add_verses(usfm, 14, {1: "r14_1", 2: "r14_2"})
        reading = _create_reading(self.day, book="Romans", start_ch=13, start_v=11, end_ch=14, end_v=2)

        self.assertTrue(_compose_hy(reading))

        text = _hy(reading).text
        self.assertIn("[11] r13_11", text)
        self.assertIn("[14] r13_14", text)
        self.assertIn("[1] r14_1", text)
        self.assertIn("[2] r14_2", text)

    def test_range_bounds_respected(self):
        usfm = _usfm_for("Genesis")
        _add_verses(usfm, 1, {1: "v1", 2: "v2", 3: "v3", 4: "v4", 5: "v5"})
        reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=2, end_ch=1, end_v=4)

        self.assertTrue(_compose_hy(reading))

        text = _hy(reading).text
        self.assertNotIn("v1", text)
        self.assertNotIn("v5", text)
        self.assertIn("[2] v2", text)
        self.assertIn("[4] v4", text)

    def test_composed_text_is_shared_by_every_reading_of_the_passage(self):
        """The point of passage keying: one composition serves every date."""
        usfm = _usfm_for("Genesis")
        _add_verses(usfm, 1, {1: "v1", 2: "v2", 3: "v3", 4: "v4", 5: "v5"})
        first = _create_reading(self.day, book="Genesis")
        other_day = Day.objects.create(date=date(2027, 5, 3), church=self.church)
        second = _create_reading(other_day, book="Genesis")

        self.assertTrue(_compose_hy(first))

        self.assertEqual(first.passage_key, second.passage_key)
        self.assertIn("[1] v1", _hy(second).text)
        self.assertEqual(PassageText.objects.filter(language="hy").count(), 1)

    def test_missing_verses_returns_false(self):
        """No corpus rows for the passage -> returns False, nothing stored."""
        reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        self.assertFalse(_compose_hy(reading))

        self.assertIsNone(_hy(reading))

    def test_unmapped_book_returns_false(self):
        """A book with no USFM mapping has no passage key, so nothing is attempted."""
        reading = _create_reading(
            self.day, book="Totally Made Up Book", start_ch=1, start_v=1, end_ch=1, end_v=5,
        )

        self.assertEqual(reading.passage_key, "")
        self.assertFalse(_compose_hy(reading))
        self.assertFalse(PassageText.objects.exists())


class EnsureBookHyTests(TestCase):
    """book_hy is a per-reading display name, so it stays on Reading, not PassageText."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2026, 2, 16), church=self.church)

    def test_book_hy_populated_from_corpus_mapping(self):
        reading = _create_reading(self.day, book="Genesis")

        self.assertTrue(ensure_book_hy(reading))

        reading.refresh_from_db()
        self.assertEqual(reading.book_hy, usfm_to_hy_book_name()[_usfm_for("Genesis")])

    def test_no_change_when_already_correct(self):
        reading = _create_reading(self.day, book="Genesis")
        ensure_book_hy(reading)

        self.assertFalse(ensure_book_hy(reading))

    def test_unmapped_book_is_left_alone(self):
        reading = _create_reading(self.day, book="Totally Made Up Book")

        self.assertFalse(ensure_book_hy(reading))


# ------------------------------------------------------------------ #
#  fetch_armenian_reading_text_task
# ------------------------------------------------------------------ #

@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class FetchArmenianReadingTextTaskTests(TestCase):
    """Tests for the fetch_armenian_reading_text_task Celery task."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2026, 2, 16), church=self.church)

    def test_composes_text_for_the_passage(self):
        usfm = _usfm_for("Isaiah")
        _add_verses(usfm, 1, {16: "i16", 17: "i17", 18: "i18", 19: "i19", 20: "i20"})
        reading = _create_reading(self.day, book="Isaiah", start_ch=1, start_v=16, end_ch=1, end_v=20)

        fetch_armenian_reading_text_task(reading.id)

        stored = _hy(reading)
        self.assertIn("[16] i16", stored.text)
        self.assertIn("[20] i20", stored.text)
        self.assertEqual(stored.version, ARMENIAN_TEXT_VERSION)
        self.assertIsNotNone(stored.fetched_at)
        # Locally composed text carries no copyright string and no usage-tracking token.
        self.assertEqual(stored.copyright, "")
        self.assertEqual(stored.fums_token, "")

    def test_armenian_text_never_expires(self):
        """No licence clock on a local corpus, so it is never withheld."""
        usfm = _usfm_for("Isaiah")
        _add_verses(usfm, 1, {16: "i16"})
        reading = _create_reading(self.day, book="Isaiah", start_ch=1, start_v=16, end_ch=1, end_v=16)
        fetch_armenian_reading_text_task(reading.id)

        stored = _hy(reading)
        stored.fetched_at = stored.fetched_at.replace(year=stored.fetched_at.year - 5)
        self.assertFalse(stored.is_expired())

    def test_no_corpus_verses_stores_nothing(self):
        reading = _create_reading(self.day, book="Isaiah", start_ch=1, start_v=16, end_ch=1, end_v=20)

        fetch_armenian_reading_text_task(reading.id)

        self.assertIsNone(_hy(reading))

    def test_nonexistent_reading_handled(self):
        """Task handles a nonexistent reading id gracefully (no raise)."""
        fetch_armenian_reading_text_task(99999)
