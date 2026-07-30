"""Tests for Armenian Bible text composition from the offline ``BibleVerse`` corpus.

Tests cover:
    - fetch_armenian_text (USFM lookup, passage composition, book_hy population)
    - fetch_armenian_reading_text_task (task behavior, metadata, error handling)

Armenian reading text is now served from the local ``BibleVerse`` table (loaded from the
git-tracked corpus), not scraped from sacredtradition.am.
"""
from datetime import date

from django.test import TestCase, override_settings

from hub.constants import BOOK_NAME_TO_USFM_NORMALIZED, normalize_book_name
from hub.models import BibleVerse, Church, Day, Reading
from hub.services.reading_text_service import (
    ARMENIAN_TEXT_VERSION,
    usfm_to_hy_book_name,
    fetch_armenian_text,
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


# ------------------------------------------------------------------ #
#  fetch_armenian_text (corpus composition) Tests
# ------------------------------------------------------------------ #

class ComposeArmenianTextTests(TestCase):
    """Tests for composing Armenian text from the BibleVerse corpus."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2026, 2, 16), church=self.church)

    def test_single_chapter_passage_composed_with_markers(self):
        usfm = _usfm_for("Genesis")
        _add_verses(usfm, 1, {1: "Skizb" + "y", 2: "verse two", 3: "v3", 4: "v4", 5: "v5"})
        reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        self.assertTrue(fetch_armenian_text(reading))

        reading.refresh_from_db()
        # Inline [verse] markers, only the requested range, in order.
        self.assertTrue(reading.text_hy.startswith("[1] "))
        self.assertIn("[5] v5", reading.text_hy)
        self.assertEqual(reading.text_hy_version, ARMENIAN_TEXT_VERSION)
        self.assertIsNotNone(reading.text_hy_fetched_at)

    def test_cross_chapter_passage_composed(self):
        usfm = _usfm_for("Romans")
        _add_verses(usfm, 13, {11: "r13_11", 12: "r13_12", 13: "r13_13", 14: "r13_14"})
        _add_verses(usfm, 14, {1: "r14_1", 2: "r14_2"})
        reading = _create_reading(self.day, book="Romans", start_ch=13, start_v=11, end_ch=14, end_v=2)

        self.assertTrue(fetch_armenian_text(reading))

        reading.refresh_from_db()
        self.assertIn("[11] r13_11", reading.text_hy)
        self.assertIn("[14] r13_14", reading.text_hy)
        self.assertIn("[1] r14_1", reading.text_hy)
        self.assertIn("[2] r14_2", reading.text_hy)

    def test_range_bounds_respected(self):
        usfm = _usfm_for("Genesis")
        _add_verses(usfm, 1, {1: "v1", 2: "v2", 3: "v3", 4: "v4", 5: "v5"})
        reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=2, end_ch=1, end_v=4)

        self.assertTrue(fetch_armenian_text(reading))

        reading.refresh_from_db()
        self.assertNotIn("v1", reading.text_hy)
        self.assertNotIn("v5", reading.text_hy)
        self.assertIn("[2] v2", reading.text_hy)
        self.assertIn("[4] v4", reading.text_hy)

    def test_book_hy_populated_from_corpus_mapping(self):
        usfm = _usfm_for("Genesis")
        _add_verses(usfm, 1, {1: "v1"})
        reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=1)

        self.assertTrue(fetch_armenian_text(reading))

        reading.refresh_from_db()
        self.assertEqual(reading.book_hy, usfm_to_hy_book_name()[usfm])

    def test_missing_verses_returns_false(self):
        """No corpus rows for the passage → returns False, text_hy stays empty."""
        reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        self.assertFalse(fetch_armenian_text(reading))

        reading.refresh_from_db()
        self.assertFalse(reading.text_hy)

    def test_unmapped_book_returns_false(self):
        """A book with no USFM mapping is skipped without error."""
        reading = _create_reading(self.day, book="Totally Made Up Book", start_ch=1, start_v=1, end_ch=1, end_v=5)

        self.assertFalse(fetch_armenian_text(reading))

        reading.refresh_from_db()
        self.assertFalse(reading.text_hy)


# ------------------------------------------------------------------ #
#  Azariah composite (embedded in Armenian Daniel 3) Tests
# ------------------------------------------------------------------ #

class AzariahCompositeTests(TestCase):
    """Azariah composes from the standalone S3Y corpus rows derived by
    scripts/derive_azariah_hy.py, with gap-awareness for the unreconciled EN/HY tail
    (Armenian 67 verses vs English KJVAIC S3Y's 68)."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2026, 4, 4), church=self.church)
        self.usfm = _usfm_for("Azariah")
        _add_verses(self.usfm, 1, {v: f"az{v}" for v in range(1, 68)})

    def test_composes_from_standalone_s3y_corpus(self):
        reading = _create_reading(self.day, book="Azariah", start_ch=1, start_v=1, end_ch=1, end_v=67)

        self.assertTrue(fetch_armenian_text(reading))

        reading.refresh_from_db()
        self.assertIn("[1] az1", reading.text_hy)
        self.assertIn("[67] az67", reading.text_hy)
        self.assertEqual(reading.book_hy, usfm_to_hy_book_name()[self.usfm])

    def test_range_touching_verse_68_logs_gap_warning(self):
        """A request reaching the unreconciled tail (verse 68) still composes what exists,
        but logs a structured warning instead of silently returning short."""
        reading = _create_reading(self.day, book="Azariah", start_ch=1, start_v=1, end_ch=1, end_v=68)

        with self.assertLogs("hub.services.reading_text_service", level="WARNING") as log:
            result = fetch_armenian_text(reading)

        self.assertTrue(result)
        reading.refresh_from_db()
        self.assertIn("[67] az67", reading.text_hy)
        log_output = "\n".join(log.output)
        self.assertIn("azariah-in-daniel", log_output)
        self.assertIn("verse-numbering gap", log_output)


# ------------------------------------------------------------------ #
#  fetch_armenian_reading_text_task Tests
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

    def test_updates_text_hy_on_matching_reading(self):
        usfm = _usfm_for("Isaiah")
        _add_verses(usfm, 1, {16: "i16", 17: "i17", 18: "i18", 19: "i19", 20: "i20"})
        reading = _create_reading(self.day, book="Isaiah", start_ch=1, start_v=16, end_ch=1, end_v=20)

        fetch_armenian_reading_text_task(reading.id)

        reading.refresh_from_db()
        self.assertIn("[16] i16", reading.text_hy)
        self.assertIn("[20] i20", reading.text_hy)
        # Metadata should be populated / left at defaults.
        self.assertEqual(reading.text_hy_version, ARMENIAN_TEXT_VERSION)
        self.assertIsNotNone(reading.text_hy_fetched_at)
        self.assertEqual(reading.text_hy_copyright, "")
        self.assertEqual(reading.text_hy_fums_token, "")

    def test_no_corpus_verses_leaves_text_hy_empty(self):
        reading = _create_reading(self.day, book="Isaiah", start_ch=1, start_v=16, end_ch=1, end_v=20)

        fetch_armenian_reading_text_task(reading.id)

        reading.refresh_from_db()
        self.assertFalse(reading.text_hy)

    def test_nonexistent_reading_handled(self):
        """Task handles a nonexistent reading id gracefully (no raise)."""
        fetch_armenian_reading_text_task(99999)
