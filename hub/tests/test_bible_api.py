"""Tests for Bible API text retrieval functionality.

Tests cover:
    - BibleAPIService (book name resolution, bible ID selection)
    - passage_key derivation and the dedup it enables
    - fetch_all_reading_texts (synchronous fetch for a reading's passage)
    - admin fetch actions charging the same budgets as every other path
    - fetch_reading_text_task (Celery wrapper, used for management commands)
    - refresh_all_reading_texts_task (per-passage refresh, spend limits, error summary)
    - the readings view handing missing text to fetch_missing_passage_texts_task (issue #506)
    - prewarm_next_day_readings_task (tomorrow's readings and text, before first request)
    - API response (includes text fields)

Text is stored per passage in ``PassageText``, not per ``Reading`` row, so most assertions
here are about how many API calls a set of readings costs — not about what each row holds.
"""
from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch, MagicMock

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from hub.admin import ReadingAdmin
from hub.constants import (
    BOOK_NAME_TO_USFM,
    APOCRYPHA_USFM_IDS,
    CATENA_ABBREV_FOR_BOOK,
    CATENA_ABBREV_FOR_BOOK_NORMALIZED,
    normalize_book_name,
    CATENA_HOME_PAGE_URL,
)
from hub.models import Church, Day, PassageText, Reading
from hub.services.bible_api_service import BibleAPIService
from hub.services.reading_text_service import bible_api_budgets, fetch_all_reading_texts
from hub.tasks.bible_api_tasks import (
    fetch_missing_passage_texts_task,
    fetch_reading_text_task,
    prewarm_next_day_readings_task,
    refresh_all_reading_texts_task,
)


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


def _text_for(reading, language="en"):
    """The stored PassageText for a reading's passage, or None."""
    return PassageText.objects.filter(
        passage_key=reading.passage_key, language=language,
    ).first()


def _store_text(reading, language="en", **kwargs):
    """Store PassageText for a reading's passage, defaulting to fresh English."""
    defaults = {
        "text": "Existing text",
        "version": "NKJV",
        "copyright": "Existing copyright",
        "fums_token": "existing-token",
        "fetched_at": timezone.now(),
    }
    defaults.update(kwargs)
    return PassageText.objects.update_or_create(
        passage_key=reading.passage_key, language=language, defaults=defaults,
    )[0]


# ------------------------------------------------------------------ #
#  fetch_reading_texts Management Command Tests
# ------------------------------------------------------------------ #

@override_settings(READING_TEXT_REFRESH_DAYS=23)
class FetchReadingTextsCommandTests(TestCase):
    """Tests for the fetch_reading_texts management command."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())

    @patch("hub.management.commands.fetch_reading_texts.refresh_all_reading_texts_task")
    def test_stale_populated_reading_runs_refresh(self, mock_refresh_task):
        """Stale readings with existing text should still trigger refresh."""
        day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        reading = _create_reading(
            day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5,
        )
        _store_text(reading, text="Old text", fetched_at=timezone.now() - timedelta(days=25))

        call_command("fetch_reading_texts", stdout=StringIO())

        mock_refresh_task.assert_called_once_with()


# ------------------------------------------------------------------ #
#  Constants Tests
# ------------------------------------------------------------------ #

class BookNameMappingTests(TestCase):
    """Tests for the BOOK_NAME_TO_USFM mapping completeness."""

    def test_every_catena_book_has_usfm_mapping(self):
        """Every key in CATENA_ABBREV_FOR_BOOK must have a USFM mapping."""
        missing = []
        for book_name in CATENA_ABBREV_FOR_BOOK:
            if book_name not in BOOK_NAME_TO_USFM:
                missing.append(book_name)
        self.assertEqual(
            missing, [],
            f"These book names in CATENA_ABBREV_FOR_BOOK lack a USFM mapping: {missing}"
        )

    def test_apocrypha_books_are_in_apocrypha_set(self):
        """Apocrypha book USFM IDs should be in APOCRYPHA_USFM_IDS."""
        apocrypha_names = ["Tobit", "Judith", "Wisdom of Solomon", "Sirach",
                           "Baruch", "Epistle of Jeremiah", "1 Maccabees", "2 Maccabees"]
        for name in apocrypha_names:
            usfm_id = BOOK_NAME_TO_USFM[name]
            self.assertIn(
                usfm_id, APOCRYPHA_USFM_IDS,
                f"{name} -> {usfm_id} should be in APOCRYPHA_USFM_IDS"
            )

    def test_canonical_books_not_in_apocrypha_set(self):
        """Canonical OT/NT books should NOT be in APOCRYPHA_USFM_IDS."""
        canonical_names = ["Genesis", "Matthew", "Romans", "Revelation", "Psalms"]
        for name in canonical_names:
            usfm_id = BOOK_NAME_TO_USFM[name]
            self.assertNotIn(
                usfm_id, APOCRYPHA_USFM_IDS,
                f"{name} -> {usfm_id} should NOT be in APOCRYPHA_USFM_IDS"
            )

    # ── normalize_book_name tests ────────────────────────────────── #

    def test_normalize_book_name_with_curly_quote(self):
        """Curly single quote (U+2019) should normalize to straight apostrophe."""
        curly_hebrews = "St. Paul\u2019s Epistle to the Hebrews"
        result = normalize_book_name(curly_hebrews)
        self.assertEqual(result, "St. Paul's Epistle to the Hebrews")

    def test_normalize_book_name_already_straight(self):
        """Already straight apostrophe should remain unchanged."""
        straight = "St. Paul's Epistle to the Hebrews"
        result = normalize_book_name(straight)
        self.assertEqual(result, straight)

    def test_normalize_book_name_none(self):
        """None input should return None."""
        self.assertIsNone(normalize_book_name(None))

    def test_normalize_book_name_empty_string(self):
        """Empty or whitespace-only string should return None."""
        self.assertIsNone(normalize_book_name(""))
        self.assertIsNone(normalize_book_name("   "))

    # ── CATENA_ABBREV_FOR_BOOK_NORMALIZED tests ──────────────────── #

    def test_normalized_dict_matches_curly_quote_book_name(self):
        """Normalized dict should resolve a curly-quote book name."""
        curly_hebrews = "St. Paul\u2019s Epistle to the Hebrews"
        self.assertEqual(
            CATENA_ABBREV_FOR_BOOK_NORMALIZED.get(normalize_book_name(curly_hebrews)),
            "heb",
        )

    def test_normalized_dict_has_same_entries_as_original(self):
        """All original keys should be present in the normalized dict."""
        for k, v in CATENA_ABBREV_FOR_BOOK.items():
            normalized_k = normalize_book_name(k)
            self.assertIn(normalized_k, CATENA_ABBREV_FOR_BOOK_NORMALIZED)
            self.assertEqual(
                CATENA_ABBREV_FOR_BOOK_NORMALIZED[normalized_k], v
            )

    # ── create_url tests ─────────────────────────────────────────── #

    def test_create_url_with_curly_quote_book_name(self):
        """Reading.create_url() should handle curly-quote book names."""

        # Create a reading with curly-quote book name
        reading = Reading(
            book="St. Paul\u2019s Epistle to the Hebrews",

            start_chapter=1,
            start_verse=1,
            end_chapter=1,
            end_verse=5,
        )
        url = reading.create_url()
        self.assertIn("heb", url)
        self.assertNotEqual(url, CATENA_HOME_PAGE_URL)

    def test_create_url_with_straight_apostrophe_book_name(self):
        """Reading.create_url() should handle standard apostrophe book names."""
        reading = Reading(
            book="St. Paul's Epistle to the Hebrews",
            start_chapter=1,
            start_verse=1,
            end_chapter=1,
            end_verse=5,
        )
        url = reading.create_url()
        self.assertIn("heb", url)
        self.assertNotEqual(url, CATENA_HOME_PAGE_URL)

    def test_create_url_with_azariah_book_name(self):
        """Reading.create_url() should remap Azariah onto Catena's Daniel 3 page.

        Catena has no standalone "Azariah" section; the Prayer of Azariah / Song of
        the Three Young Men is embedded inline in Daniel 3 (verses 24-90). Azariah
        verse 1 == Daniel 3:24, verified live against catenabible.com/dn/3.
        """
        reading = Reading(
            book="Azariah",
            start_chapter=1,
            start_verse=1,
            end_chapter=1,
            end_verse=68,
        )
        url = reading.create_url()
        self.assertEqual(url, "https://catenabible.com/dn/3/#dn003022")

    def test_create_url_with_unknown_book_returns_home(self):
        """Unknown book should return CATENA_HOME_PAGE_URL."""
        reading = Reading(
            book="Completely Made Up Book",
            start_chapter=1,
            start_verse=1,
            end_chapter=1,
            end_verse=5,
        )
        url = reading.create_url()
        self.assertEqual(url, CATENA_HOME_PAGE_URL)


# ------------------------------------------------------------------ #
#  BibleAPIService Tests
# ------------------------------------------------------------------ #

class BibleAPIServiceResolveBookNameTests(TestCase):
    """Tests for BibleAPIService.resolve_book_name."""

    def test_resolve_standard_book_name(self):
        """Test resolving a standard book name."""
        self.assertEqual(BibleAPIService.resolve_book_name("Genesis"), "GEN")
        self.assertEqual(BibleAPIService.resolve_book_name("Matthew"), "MAT")
        self.assertEqual(BibleAPIService.resolve_book_name("Revelation"), "REV")

    def test_resolve_liturgical_book_name(self):
        """Test resolving a liturgical book name variant."""
        self.assertEqual(
            BibleAPIService.resolve_book_name("St. Paul's Epistle to the Romans"),
            "ROM",
        )
        self.assertEqual(
            BibleAPIService.resolve_book_name("St. James' Epistle General"),
            "JAS",
        )

    def test_resolve_apocrypha_book_name(self):
        """Test resolving an Apocrypha book name."""
        self.assertEqual(BibleAPIService.resolve_book_name("Tobit"), "TOB")
        self.assertEqual(BibleAPIService.resolve_book_name("Wisdom of Solomon"), "WIS")
        self.assertEqual(BibleAPIService.resolve_book_name("Wisdom"), "WIS")

    def test_resolve_unknown_book_raises_error(self):
        """Test that resolving an unknown book name raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            BibleAPIService.resolve_book_name("Nonexistent Book")
        self.assertIn("Unknown book name", str(ctx.exception))
        self.assertIn("Nonexistent Book", str(ctx.exception))


class BibleAPIServiceResolveReadingPassageTests(TestCase):
    """Tests for Reading reference resolution into API.Bible passage IDs."""

    def test_esther_greek_addition_uses_esg_numbering(self):
        """Esther 10:4-9 maps to the KJVAIC Esther Additions book."""
        self.assertEqual(
            BibleAPIService.resolve_reading_passage("Esther", 10, 4, 10, 9),
            ("ESG", 1, 4, 1, 9),
        )

    def test_regular_esther_stays_canonical(self):
        """Canonical Esther references should continue to use EST."""
        self.assertEqual(
            BibleAPIService.resolve_reading_passage("Esther", 10, 1, 10, 3),
            ("EST", 10, 1, 10, 3),
        )

    def test_esther_addition_spanning_chapters(self):
        """Addition C (Esther 13:8-14:19) maps across two ESG chapters.

        Chapters 11-16 are additions in full, so both ends shift by the same offset and
        the verse numbers are untouched.  Without this the fetcher would ask for EST 13,
        which does not exist in either NKJV or KJVAIC -- canonical Esther ends at 10.
        """
        self.assertEqual(
            BibleAPIService.resolve_reading_passage("Esther", 13, 8, 14, 19),
            ("ESG", 4, 8, 5, 19),
        )

    def test_esther_last_addition_chapter(self):
        """EST 16, the final addition chapter, maps to the last ESG chapter."""
        self.assertEqual(
            BibleAPIService.resolve_reading_passage("Esther", 16, 1, 16, 24),
            ("ESG", 7, 1, 7, 24),
        )

    def test_esther_range_straddling_additions_stays_canonical(self):
        """A range crossing from canonical Esther into the additions is left on EST.

        KJVAIC splits those into separate books, so no single passage covers the range.
        It degrades to the canonical part and warns rather than raising, so a citation
        like this appearing in the lectionary is loud but not fatal.
        """
        with self.assertLogs("hub.services.bible_api_service", level="WARNING") as logs:
            resolved = BibleAPIService.resolve_reading_passage("Esther", 10, 1, 10, 13)
        self.assertEqual(resolved, ("EST", 10, 1, 10, 13))
        self.assertIn("straddles", logs.output[0])

    def test_canonical_esther_range_does_not_warn(self):
        """A wholly canonical Esther range resolves quietly, with no straddle warning."""
        with self.assertNoLogs("hub.services.bible_api_service", level="WARNING"):
            resolved = BibleAPIService.resolve_reading_passage("Esther", 9, 1, 10, 3)
        self.assertEqual(resolved, ("EST", 9, 1, 10, 3))


class BibleAPIServiceBibleIdSelectionTests(TestCase):
    """Tests for BibleAPIService._bible_id_for_book."""

    def test_canonical_book_uses_nkjv(self):
        """Test that canonical books use NKJV bible ID."""
        bible_id, version = BibleAPIService._bible_id_for_book("GEN")
        self.assertEqual(version, "NKJV")

    def test_apocrypha_book_uses_kjvaic(self):
        """Test that Apocrypha books use KJVAIC bible ID."""
        bible_id, version = BibleAPIService._bible_id_for_book("TOB")
        self.assertEqual(version, "KJVAIC")

    def test_new_testament_book_uses_nkjv(self):
        """Test that New Testament books use NKJV."""
        bible_id, version = BibleAPIService._bible_id_for_book("MAT")
        self.assertEqual(version, "NKJV")


class BibleAPIServiceBuildPassageIdTests(TestCase):
    """Tests for BibleAPIService._build_passage_id."""

    def test_build_range_passage(self):
        """Test building a passage ID for a verse range."""
        result = BibleAPIService._build_passage_id("GEN", 1, 1, 1, 5)
        self.assertEqual(result, "GEN.1.1-GEN.1.5")

    def test_build_single_verse_passage(self):
        """Test building a passage ID for a single verse."""
        result = BibleAPIService._build_passage_id("JHN", 3, 16, 3, 16)
        self.assertEqual(result, "JHN.3.16")

    def test_build_cross_chapter_passage(self):
        """Test building a passage ID spanning multiple chapters."""
        result = BibleAPIService._build_passage_id("PSA", 22, 1, 23, 6)
        self.assertEqual(result, "PSA.22.1-PSA.23.6")


class BibleAPIServiceInitTests(TestCase):
    """Tests for BibleAPIService initialization."""

    @patch('hub.services.bible_api_service.config')
    def test_init_without_api_key_raises_error(self, mock_config):
        """Test that initialization without API key raises ValueError."""
        mock_config.return_value = ""
        with self.assertRaises(ValueError) as ctx:
            BibleAPIService()
        self.assertIn("API key required", str(ctx.exception))

    @patch('hub.services.bible_api_service.config')
    def test_init_with_api_key(self, mock_config):
        """Test that initialization with API key succeeds."""
        mock_config.return_value = "test-api-key"
        service = BibleAPIService()
        self.assertEqual(service.api_key, "test-api-key")


# ------------------------------------------------------------------ #
#  Synchronous English fetch Tests
# ------------------------------------------------------------------ #

class FetchEnglishTextTests(TestCase):
    """Tests for the synchronous English fetch, which stores against the passage."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        self.mock_api_response = {
            "content": "[1] In the beginning God created the heavens and the earth.",
            "copyright": "Scripture taken from the NKJV. Copyright 1982 Thomas Nelson.",
            "version": "NKJV",
            "reference": "Genesis 1:1-5",
            "fums_token": "test-fums-token",
        }

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_fetch_text_for_new_reading(self, mock_config, mock_resolve, mock_get_passage):
        """Test fetching text for a reading that has no text yet."""
        mock_get_passage.return_value = self.mock_api_response

        reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        result = fetch_all_reading_texts(reading, langs=["en"])

        self.assertTrue(result["en"])
        stored = _text_for(reading)
        self.assertEqual(stored.text, self.mock_api_response["content"])
        self.assertEqual(stored.copyright, self.mock_api_response["copyright"])
        self.assertEqual(stored.version, "NKJV")
        self.assertEqual(stored.fums_token, "test-fums-token")
        self.assertIsNotNone(stored.fetched_at)

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_fetch_serves_every_reading_of_the_same_passage(self, mock_config, mock_resolve, mock_get_passage):
        """One retrieval covers every date citing the passage. The whole point."""
        mock_get_passage.return_value = self.mock_api_response

        day2 = Day.objects.create(date=date(2026, 3, 15), church=self.church)
        reading1 = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)
        reading2 = _create_reading(day2, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        fetch_all_reading_texts(reading1, langs=["en"])

        self.assertEqual(reading1.passage_key, reading2.passage_key)
        self.assertEqual(_text_for(reading2).text, self.mock_api_response["content"])
        mock_get_passage.assert_called_once()

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_fetch_does_not_touch_other_passages(self, mock_config, mock_resolve, mock_get_passage):
        """Sharing is scoped to the passage key, not applied indiscriminately."""
        mock_get_passage.return_value = self.mock_api_response

        target = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)
        other = _create_reading(self.day, book="Genesis", start_ch=2, start_v=1, end_ch=2, end_v=5)

        fetch_all_reading_texts(target, langs=["en"])

        self.assertNotEqual(target.passage_key, other.passage_key)
        self.assertIsNone(_text_for(other))

    @patch('hub.services.bible_api_service.config', return_value="")
    def test_fetch_no_api_key_returns_false(self, mock_config):
        """Test that fetch returns False when API key is not configured."""
        reading = _create_reading(self.day)

        result = fetch_all_reading_texts(reading, langs=["en"])

        self.assertFalse(result["en"])
        self.assertIsNone(_text_for(reading))

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_fetch_with_pre_initialized_service(self, mock_config, mock_resolve, mock_get_passage):
        """Test fetching with a pre-initialized service (as used by the view)."""
        mock_get_passage.return_value = self.mock_api_response

        reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)
        service = BibleAPIService()

        result = fetch_all_reading_texts(reading, langs=["en"], service=service)

        self.assertTrue(result["en"])
        self.assertEqual(_text_for(reading).text, self.mock_api_response["content"])

    def test_fetch_unknown_book_returns_false(self):
        """Test that fetch returns False for unknown book names."""
        reading = _create_reading(self.day, book="Nonexistent Book")
        service = MagicMock(spec=BibleAPIService)

        result = fetch_all_reading_texts(reading, langs=["en"], service=service)

        self.assertFalse(result["en"])


# ------------------------------------------------------------------ #
#  fetch_reading_text_task (Celery wrapper) Tests
# ------------------------------------------------------------------ #

@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class FetchReadingTextTaskTests(TestCase):
    """Tests for the fetch_reading_text_task Celery task (management command use)."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2025, 3, 15), church=self.church)

    def test_fetch_nonexistent_reading(self):
        """Test that task handles nonexistent reading gracefully."""
        # Should not raise an exception
        fetch_reading_text_task(99999)

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_task_stores_text_for_the_passage(self, mock_config, mock_resolve, mock_get_passage):
        """The Celery task retrieves text for the passage the reading cites."""
        mock_get_passage.return_value = {
            "content": "Test content.",
            "copyright": "Test copyright.",
            "version": "NKJV",
            "reference": "Genesis 1:1-5",
            "fums_token": "test-token",
        }

        reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        fetch_reading_text_task(reading.id)

        self.assertEqual(_text_for(reading).text, "Test content.")

    def test_task_skips_readings_with_no_passage_key(self):
        """An unmappable book cannot be retrieved, so the task must not try."""
        reading = _create_reading(self.day, book="Not A Real Book")

        with self.assertLogs("hub.tasks.bible_api_tasks", level="WARNING") as log:
            fetch_reading_text_task(reading.id)

        self.assertFalse(PassageText.objects.exists())
        self.assertIn("no passage key", "\n".join(log.output))


# ------------------------------------------------------------------ #
#  refresh_all_reading_texts_task Tests
# ------------------------------------------------------------------ #

@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    READING_TEXT_REFRESH_DAYS=23,
)
class RefreshAllReadingTextsTaskTests(TestCase):
    """Tests for the refresh_all_reading_texts_task Celery task.

    The unit of work is a passage, not a reading, so these assert on how many API calls a
    set of readings costs.  Several tests deliberately vary ``end_verse`` to create
    *distinct* passages: with identical citations they would collapse to one retrieval,
    which is the behaviour under test elsewhere.
    """

    def setUp(self):
        # Spend budgets live in the cache, and LocMemCache persists across tests within a
        # process, so counters must be reset or budget-sensitive assertions bleed together.
        cache.clear()
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.mock_api_response = {
            "content": "Test verse content.",
            "copyright": "Test copyright.",
            "version": "NKJV",
            "reference": "Genesis 1:1-5",
            "fums_token": "test-fums-token",
        }

    def _reading_on(self, day_offset, **kwargs):
        """Create a reading on a day offset from today."""
        day = Day.objects.create(
            date=timezone.localdate() + timedelta(days=day_offset), church=self.church,
        )
        return _create_reading(day, **kwargs)

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_refresh_never_retrieved_passages(self, mock_config, mock_resolve, mock_get_passage):
        """A passage with no PassageText row at all is retrieved."""
        mock_get_passage.return_value = self.mock_api_response

        reading = self._reading_on(0)

        refresh_all_reading_texts_task()

        stored = _text_for(reading)
        self.assertEqual(stored.text, "Test verse content.")
        self.assertIsNotNone(stored.fetched_at)

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_refresh_stale_passages(self, mock_config, mock_resolve, mock_get_passage):
        """Text older than READING_TEXT_REFRESH_DAYS is re-retrieved."""
        mock_get_passage.return_value = self.mock_api_response

        reading = self._reading_on(0)
        _store_text(reading, text="Old text", fetched_at=timezone.now() - timedelta(days=25))

        refresh_all_reading_texts_task()

        self.assertEqual(_text_for(reading).text, "Test verse content.")

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_skip_recent_passages(self, mock_config, mock_resolve, mock_get_passage):
        """Recently retrieved text is left alone."""
        reading = self._reading_on(0)
        _store_text(reading, text="Recent text", fetched_at=timezone.now() - timedelta(days=5))
        _store_text(reading, language="hy", text="Recent hy")

        refresh_all_reading_texts_task()

        mock_get_passage.assert_not_called()

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_one_api_call_per_distinct_passage(self, mock_config, mock_resolve, mock_get_passage):
        """The headline behaviour: N dates citing one passage cost ONE call.

        This deliberately inverts the old one-call-per-reading rule.  FUMS tracks
        displays, and the app already serves a single stored token to unlimited users of
        a reading across the 30-day cache window, so sharing across rows that cite the
        same passage is the same category of reuse -- and it is what keeps a lectionary of
        ~15,000 readings inside a 5,000-call monthly quota.
        """
        mock_get_passage.return_value = self.mock_api_response

        readings = [self._reading_on(i * 365) for i in range(3)]

        refresh_all_reading_texts_task()

        self.assertEqual(mock_get_passage.call_count, 1)
        self.assertEqual(PassageText.objects.filter(language="en").count(), 1)
        for reading in readings:
            self.assertEqual(_text_for(reading).text, "Test verse content.")

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_refresh_only_updates_stale_passages(self, mock_config, mock_resolve, mock_get_passage):
        """Fresh passages are untouched while stale ones are re-retrieved."""
        mock_get_passage.return_value = self.mock_api_response

        stale = self._reading_on(0, end_v=5)
        fresh = self._reading_on(1, end_v=9)
        _store_text(fresh, text="Old text from last refresh")
        _store_text(fresh, language="hy", text="hy text")

        refresh_all_reading_texts_task()

        self.assertEqual(_text_for(stale).text, "Test verse content.")
        self.assertEqual(_text_for(fresh).text, "Old text from last refresh")

    @override_settings(READING_REFRESH_LIMIT=10)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_refresh_never_deletes_readings(self, mock_config, mock_resolve, mock_get_passage):
        """Spend is capped at READING_REFRESH_LIMIT distinct passages, deleting nothing."""
        mock_get_passage.return_value = self.mock_api_response

        for i in range(15):
            self._reading_on(i, end_v=i + 1)  # 15 distinct passages

        self.assertEqual(Reading.objects.count(), 15)

        refresh_all_reading_texts_task()

        # Pruning is what caused rows to be re-created and re-fetched; nothing is deleted.
        self.assertEqual(Reading.objects.count(), 15)
        self.assertEqual(mock_get_passage.call_count, 10)

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_spend_tracks_passages_not_rows(self, mock_config, mock_resolve, mock_get_passage):
        """Adding dates that cite known passages costs nothing.

        This is the property the whole design rests on: the reading table grows forever,
        but the distinct-passage count saturates, so retrieval cost stops growing with it.
        """
        mock_get_passage.return_value = self.mock_api_response

        for i in range(3):
            self._reading_on(i, end_v=i + 1)  # 3 distinct passages
        refresh_all_reading_texts_task()
        self.assertEqual(mock_get_passage.call_count, 3)

        # 30 more readings, all re-citing the same 3 passages.
        for i in range(30):
            self._reading_on(100 + i, end_v=(i % 3) + 1)

        mock_get_passage.reset_mock()
        refresh_all_reading_texts_task()

        self.assertEqual(mock_get_passage.call_count, 0)

    @override_settings(READING_REFRESH_MAX_CONSECUTIVE_FAILURES=3)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_refresh_aborts_after_consecutive_failures(self, mock_config, mock_resolve, mock_get_passage):
        """A run of API failures aborts the task instead of burning the whole quota.

        A failed fetch stores no timestamp, so without this circuit breaker a quota
        rejection leaves every passage permanently stale and each run re-attempts all of
        them against a wall.
        """
        mock_get_passage.side_effect = Exception("429 Too Many Requests")

        for i in range(10):
            self._reading_on(i, end_v=i + 1)  # distinct passages

        refresh_all_reading_texts_task()

        self.assertEqual(mock_get_passage.call_count, 3)

    @override_settings(READING_REFRESH_MAX_CONSECUTIVE_FAILURES=3)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_unmappable_books_are_excluded_and_reported(self, mock_config, mock_get_passage):
        """Books with no USFM mapping get no passage key, so they never reach the API.

        They cannot succeed and would otherwise be retried every run, so they are
        excluded from selection outright and reported separately -- rather than counted
        as failures, which would trip the circuit breaker once everything else is fresh.
        """
        mock_get_passage.return_value = self.mock_api_response

        for i in range(6):
            self._reading_on(i, book="Not A Real Book", end_v=i + 1)
        good = self._reading_on(6, book="Genesis")

        with self.assertLogs("hub.tasks.bible_api_tasks", level="WARNING") as log:
            refresh_all_reading_texts_task()

        self.assertEqual(_text_for(good).text, "Test verse content.")
        self.assertEqual(mock_get_passage.call_count, 1)
        self.assertIn("no USFM mapping", "\n".join(log.output))

    @override_settings(READING_REFRESH_MAX_CONSECUTIVE_FAILURES=3)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_real_api_failures_after_unmappable_books_still_abort(
        self, mock_config, mock_resolve, mock_get_passage,
    ):
        """Excluding unmappable readings must not blunt the breaker for genuine failures."""
        mock_get_passage.side_effect = Exception("429 Too Many Requests")

        for i in range(10):
            self._reading_on(i, end_v=i + 1)

        refresh_all_reading_texts_task()

        self.assertEqual(mock_get_passage.call_count, 3)

    @override_settings(READING_REFRESH_MAX_CONSECUTIVE_FAILURES=3)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_breaker_trip_reports_attempts_not_zero(self, mock_config, mock_resolve, mock_get_passage):
        """A circuit-breaker trip must report the calls that reached API.Bible.

        Counting only successes made a rejection spiral log "0 API calls," hiding the
        exact event the telemetry exists to surface.  Passages processed is not the same
        number: a refused budget or an unmappable book never reaches the API.
        """
        mock_get_passage.side_effect = Exception("429 Too Many Requests")

        # Distinct passages: ten readings of *one* passage is a single retrieval now, so
        # they could never trip a breaker set at three.
        for i in range(10):
            self._reading_on(i, end_v=i + 1)

        with self.assertLogs("hub.tasks.bible_api_tasks", level="ERROR") as log:
            refresh_all_reading_texts_task()

        abort_messages = [m for m in log.output if "Aborting en refresh" in m]
        self.assertEqual(len(abort_messages), 1)
        self.assertIn("3 API calls made", abort_messages[0])

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_readings_processed_does_not_double_count_partial_language_failure(
        self, mock_config, mock_resolve, mock_get_passage,
    ):
        """A passage whose English fetch succeeds but Armenian fetch fails must not be
        counted twice — the old ``api_calls + len(failures)`` expression double-counted
        exactly this case.  Per-language summaries make the two independent by
        construction; this guards the property rather than the old expression.

        No BibleVerse rows are seeded for this passage, so the Armenian composer
        naturally returns no text without needing to mock anything.
        """
        mock_get_passage.return_value = self.mock_api_response

        self._reading_on(0)

        with self.assertLogs("hub.tasks.bible_api_tasks", level="INFO") as log:
            refresh_all_reading_texts_task()

        english = [m for m in log.output if "en refresh complete" in m][0]
        self.assertIn("1 passages retrieved, 0 failed (of 1 selected)", english)
        self.assertIn("1 API call(s) made", english)

        # The same passage failing in Armenian is counted only against Armenian, and it
        # costs no API call: the composer is local.
        armenian = [m for m in log.output if "hy refresh complete" in m][0]
        self.assertIn("0 passages retrieved, 1 failed (of 1 selected)", armenian)
        self.assertIn("0 API call(s) made", armenian)

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_refresh_stops_when_monthly_budget_is_exhausted(self, mock_config, mock_resolve, mock_get_passage):
        """The monthly ceiling caps the task's spend even when the per-run limit is higher."""
        mock_get_passage.return_value = self.mock_api_response

        for i in range(6):
            self._reading_on(i, end_v=i + 1)

        with override_settings(BIBLE_API_MONTHLY_BUDGET=2):
            refresh_all_reading_texts_task()

        self.assertEqual(mock_get_passage.call_count, 2)

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_skips_run_when_ceiling_already_spent(self, mock_config, mock_resolve, mock_get_passage):
        """An exhausted ceiling is reported as our own limit, not as API rejection."""
        self._reading_on(0)

        with override_settings(BIBLE_API_MONTHLY_BUDGET=0):
            with self.assertLogs("hub.tasks.bible_api_tasks", level="ERROR") as log:
                refresh_all_reading_texts_task()

        mock_get_passage.assert_not_called()
        self.assertIn("monthly ceiling", "\n".join(log.output))

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_no_readings_skips_refresh(self, mock_config, mock_resolve, mock_get_passage):
        """Nothing to do when every passage is fresh."""
        reading = self._reading_on(0)
        _store_text(reading, text="Fresh text")
        _store_text(reading, language="hy", text="Fresh hy text")

        refresh_all_reading_texts_task()

        mock_get_passage.assert_not_called()

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_logs_dedup_ratio(self, mock_config, mock_resolve, mock_get_passage):
        """The dedup ratio is the only signal that passage keying still works."""
        mock_get_passage.return_value = self.mock_api_response

        for i in range(6):
            self._reading_on(i, end_v=(i % 2) + 1)  # 6 readings, 2 passages

        with self.assertLogs("hub.tasks.bible_api_tasks", level="INFO") as log:
            refresh_all_reading_texts_task()

        self.assertIn("6 readings resolve to 2 distinct passages (3.0x dedup)", "\n".join(log.output))

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_repairs_keys_when_book_becomes_mappable(self, mock_config, mock_resolve, mock_get_passage):
        """A BOOK_NAME_TO_USFM fix must not leave already-created rows stranded.

        Rows with an empty key are excluded from retrieval, so without the repair step
        they would stay excluded forever and the fix would be inert.
        """
        mock_get_passage.return_value = self.mock_api_response

        reading = self._reading_on(0)
        Reading.objects.filter(pk=reading.pk).update(passage_key="")

        refresh_all_reading_texts_task()

        reading.refresh_from_db()
        self.assertEqual(reading.passage_key, "GEN.1.1-1.5")
        self.assertEqual(_text_for(reading).text, "Test verse content.")

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_api_failure_logged_in_summary(self, mock_config, mock_resolve, mock_get_passage):
        """Test that API failures are collected and logged in the error summary."""
        mock_get_passage.side_effect = Exception("API timeout")

        self._reading_on(0)

        # Should not raise despite API failure
        with self.assertLogs("hub.services.reading_text_service", level="ERROR") as log:
            refresh_all_reading_texts_task()

        self.assertIn("API call failed", "\n".join(log.output))

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_esther_versification_is_applied_by_the_fetcher_not_the_key(
        self, mock_config, mock_get_passage,
    ):
        """Esther 10:4-9 is ESG 1:4-9 in KJVAIC but EST 10:4-9 in the Armenian corpus.

        That difference is per-edition versification, not passage identity, so it lives
        in the English fetcher and the shared key stays the citation as written.  Were
        the remap in the key, the Armenian fetcher would have to undo it.
        """
        mock_get_passage.return_value = {
            "content": "Then Mordecai said...",
            "copyright": "KJVAIC copyright",
            "version": "KJVAIC",
            "reference": "Esther (Additions) 1:4-9",
            "fums_token": "test-fums-token",
        }

        reading = self._reading_on(0, book="Esther", start_ch=10, start_v=4, end_ch=10, end_v=9)

        # The key is the citation, un-remapped.
        self.assertEqual(reading.passage_key, "EST.10.4-10.9")

        refresh_all_reading_texts_task()

        # But API.Bible is asked for its own address for the same passage.
        mock_get_passage.assert_called_once_with("ESG", 1, 4, 1, 9)
        stored = _text_for(reading)
        self.assertEqual(stored.text, "Then Mordecai said...")
        self.assertEqual(stored.version, "KJVAIC")

    @patch('hub.services.bible_api_service.config', return_value="")
    def test_no_api_key_aborts_refresh(self, mock_config):
        """Test that refresh aborts gracefully when API key is missing."""
        self._reading_on(0)

        # Should not raise
        refresh_all_reading_texts_task()


# ------------------------------------------------------------------ #
#  View Async Text Fetch Tests
# ------------------------------------------------------------------ #

class ViewAsyncTextFetchTests(TestCase):
    """The readings view never calls API.Bible in the request cycle (issue #506).

    Missing text is served blank (the long-standing partial contract) and the missing
    set is handed to fetch_missing_passage_texts_task in one enqueue, so a cold date
    costs one task -- not a burst of synchronous 10s-timeout calls inside the request.
    """

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 4, 1)

    def _get(self):
        from rest_framework.test import APIRequestFactory
        from hub.views.readings import GetDailyReadingsForDate

        request = APIRequestFactory().get(f'/readings/?date={self.test_date}')
        return GetDailyReadingsForDate.as_view()(request)

    @patch('hub.views.readings.fetch_missing_passage_texts_task')
    @patch('hub.views.readings.get_daily_readings')
    @patch('hub.views.readings.generate_reading_context_task')
    def test_view_enqueues_task_for_new_readings(
        self, mock_context_task, mock_scrape, mock_text_task,
    ):
        """The view hands the missing set to the follow-up task in one enqueue."""
        mock_scrape.return_value = [
            {
                "book": "Matthew",
                "book_en": "Matthew",
                "start_chapter": 5,
                "start_verse": 1,
                "end_chapter": 5,
                "end_verse": 12,
            },
        ]

        response = self._get()

        self.assertEqual(response.status_code, 200)
        mock_text_task.delay.assert_called_once()
        reading = Reading.objects.get(day__date=self.test_date, book="Matthew")
        self.assertEqual(
            mock_text_task.delay.call_args.args[0],
            [{
                "key": reading.passage_key,
                "citation": ["Matthew", 5, 1, 5, 12],
                "langs": ["en", "hy"],
            }],
        )
        # The response still honours the partial contract: served blank, not blocked.
        self.assertEqual(response.data["readings"][0]["text"], "")

    @patch('hub.views.readings.fetch_missing_passage_texts_task')
    @patch('hub.views.readings.get_daily_readings')
    @patch('hub.views.readings.generate_reading_context_task')
    def test_view_does_not_enqueue_for_existing_readings(
        self, mock_context_task, mock_scrape, mock_text_task,
    ):
        """Test that the view does not enqueue for readings whose text is servable."""
        mock_scrape.return_value = []

        # Pre-create the day and reading, with text already stored for its passage.
        # Both languages must be present: the gate is per (passage, language), so a
        # missing one would legitimately trigger an enqueue.
        day = Day.objects.create(date=self.test_date, church=self.church)
        reading = _create_reading(day, book="Matthew", start_ch=5, start_v=1, end_ch=5, end_v=12)
        _store_text(reading)
        _store_text(reading, language="hy", text="Existing hy text")

        self.assertEqual(self._get().status_code, 200)
        mock_text_task.delay.assert_not_called()

    @patch('hub.views.readings.fetch_missing_passage_texts_task')
    @patch('hub.views.readings.get_daily_readings')
    @patch('hub.views.readings.generate_reading_context_task')
    def test_view_makes_no_enqueue_for_a_new_date_citing_a_known_passage(
        self, mock_context_task, mock_scrape, mock_text_task,
    ):
        """A date never requested before is free if its passages are already stored.

        This is what the passage-keyed store buys at serve time: browsing to an unseen
        date costs no quota and no enqueue, because text is not keyed by date.
        """
        mock_scrape.return_value = []

        # A different date already cites this passage and has text stored.
        old_day = Day.objects.create(date=date(2024, 1, 1), church=self.church)
        old_reading = _create_reading(old_day, book="Matthew", start_ch=5, start_v=1, end_ch=5, end_v=12)
        _store_text(old_reading)
        _store_text(old_reading, language="hy", text="Existing hy text")

        new_day = Day.objects.create(date=self.test_date, church=self.church)
        _create_reading(new_day, book="Matthew", start_ch=5, start_v=1, end_ch=5, end_v=12)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        mock_text_task.delay.assert_not_called()
        self.assertEqual(response.data["readings"][0]["text"], "Existing text")

    @patch('hub.views.readings.fetch_missing_passage_texts_task')
    @patch('hub.views.readings.get_daily_readings')
    @patch('hub.views.readings.generate_reading_context_task')
    def test_view_serves_partial_response_when_enqueue_fails(
        self, mock_context_task, mock_scrape, mock_text_task,
    ):
        """A broker hiccup must not turn a servable response into an error."""
        mock_scrape.return_value = [
            {
                "book": "Matthew",
                "book_en": "Matthew",
                "start_chapter": 5,
                "start_verse": 1,
                "end_chapter": 5,
                "end_verse": 12,
            },
        ]
        mock_text_task.delay.side_effect = OSError("broker unavailable")

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["readings"]), 1)
        self.assertEqual(response.data["readings"][0]["text"], "")


# ------------------------------------------------------------------ #
#  API Response Tests
# ------------------------------------------------------------------ #

class ReadingTextAPIResponseTests(TestCase):
    """Tests for text fields in the GetDailyReadingsForDate API response."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 5, 1)

    @patch('hub.views.readings.get_daily_readings')
    @patch('hub.views.readings.generate_reading_context_task')
    def test_response_includes_text_fields(self, mock_context_task, mock_scrape):
        """Test that API response includes text, textCopyright, textVersion fields."""
        from rest_framework.test import APIRequestFactory
        from hub.views.readings import GetDailyReadingsForDate

        mock_scrape.return_value = []

        day = Day.objects.create(date=self.test_date, church=self.church)
        reading = _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)
        _store_text(
            reading,
            text="In the beginning God created the heavens and the earth.",
            copyright="NKJV (c) 1982 Thomas Nelson.",
            version="NKJV",
        )

        factory = APIRequestFactory()
        request = factory.get(f'/readings/?date={self.test_date}')
        view = GetDailyReadingsForDate.as_view()

        response = view(request)
        self.assertEqual(response.status_code, 200)

        readings = response.data["readings"]
        self.assertEqual(len(readings), 1)

        reading_data = readings[0]
        self.assertEqual(reading_data["text"], "In the beginning God created the heavens and the earth.")
        self.assertEqual(reading_data["textCopyright"], "NKJV (c) 1982 Thomas Nelson.")
        self.assertEqual(reading_data["textVersion"], "NKJV")

    @patch('hub.views.readings.get_daily_readings')
    @patch('hub.views.readings.generate_reading_context_task')
    def test_response_empty_text_when_not_fetched(self, mock_context_task, mock_scrape):
        """Test that API response returns empty strings when text has not been fetched."""
        from rest_framework.test import APIRequestFactory
        from hub.views.readings import GetDailyReadingsForDate

        mock_scrape.return_value = []

        day = Day.objects.create(date=self.test_date, church=self.church)
        _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        factory = APIRequestFactory()
        request = factory.get(f'/readings/?date={self.test_date}')
        view = GetDailyReadingsForDate.as_view()

        response = view(request)
        self.assertEqual(response.status_code, 200)

        readings = response.data["readings"]
        self.assertEqual(len(readings), 1)

        reading_data = readings[0]
        self.assertEqual(reading_data["text"], "")
        self.assertEqual(reading_data["textCopyright"], "")
        self.assertEqual(reading_data["textVersion"], "")


class ViewOnDemandRefetchTests(TestCase):
    """The readings view re-fetches text that has passed READING_TEXT_MAX_AGE_DAYS.

    Expired text is blanked in the response, so without this the page would show a bare
    citation forever for any date the weekly refresh does not reach.
    """

    def setUp(self):
        cache.clear()
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 6, 15)
        self.day = Day.objects.create(date=self.test_date, church=self.church)

    def _get(self):
        from rest_framework.test import APIRequestFactory
        from hub.views.readings import GetDailyReadingsForDate

        request = APIRequestFactory().get(f'/readings/?date={self.test_date}')
        return GetDailyReadingsForDate.as_view()(request)

    @patch('hub.views.readings.fetch_missing_passage_texts_task')
    @patch('hub.views.readings.get_daily_readings', return_value=[])
    @patch('hub.views.readings.generate_reading_context_task')
    def test_enqueues_refetch_for_expired_reading(self, mock_ctx, mock_scrape, mock_text_task):
        reading = _create_reading(self.day, book="Matthew", start_ch=5, start_v=1, end_ch=5, end_v=12)
        _store_text(reading, text="Stale text", fetched_at=timezone.now() - timedelta(days=31))
        _store_text(reading, language="hy", text="hy text")

        response = self._get()

        self.assertEqual(response.status_code, 200)
        mock_text_task.delay.assert_called_once()
        # Only the expired English is re-requested; Armenian is still servable.
        self.assertEqual(
            mock_text_task.delay.call_args.args[0],
            [{
                "key": reading.passage_key,
                "citation": ["Matthew", 5, 1, 5, 12],
                "langs": ["en"],
            }],
        )
        # Expired text is blanked in the response until the task lands.
        self.assertEqual(response.data["readings"][0]["text"], "")

    @patch('hub.views.readings.fetch_missing_passage_texts_task')
    @patch('hub.views.readings.get_daily_readings', return_value=[])
    @patch('hub.views.readings.generate_reading_context_task')
    def test_does_not_refetch_just_inside_max_age(self, mock_ctx, mock_scrape, mock_text_task):
        reading = _create_reading(self.day, book="Matthew", start_ch=5, start_v=1, end_ch=5, end_v=12)
        _store_text(reading, text="Fresh enough", fetched_at=timezone.now() - timedelta(days=29))
        _store_text(reading, language="hy", text="hy text")

        self.assertEqual(self._get().status_code, 200)

        mock_text_task.delay.assert_not_called()

    @patch('hub.views.readings.fetch_missing_passage_texts_task')
    @patch('hub.views.readings.get_daily_readings', return_value=[])
    @patch('hub.views.readings.generate_reading_context_task')
    def test_enqueues_once_for_many_expired(self, mock_ctx, mock_scrape, mock_text_task):
        """A burst of expired passages is one enqueue, not one per passage."""
        readings = []
        for verse in range(1, 4):
            reading = _create_reading(
                self.day, book="Matthew", start_ch=5, start_v=verse, end_ch=5, end_v=verse,
            )
            _store_text(reading, text="Stale", fetched_at=timezone.now() - timedelta(days=31))
            _store_text(reading, language="hy", text="hy text")
            readings.append(reading)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        mock_text_task.delay.assert_called_once()
        self.assertEqual(
            {item["key"] for item in mock_text_task.delay.call_args.args[0]},
            {r.passage_key for r in readings},
        )


class ReadingFetchBudgetTests(TestCase):
    """Tests for the API.Bible spend budgets guarding the public on-demand path."""

    def setUp(self):
        cache.clear()
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2025, 6, 15), church=self.church)
        self.mock_api_response = {
            "content": "Test verse content.",
            "copyright": "Test copyright.",
            "version": "NKJV",
            "reference": "Genesis 1:1-5",
            "fums_token": "test-fums-token",
        }

    @override_settings(READING_FETCH_DAILY_BUDGET=2, BIBLE_API_MONTHLY_BUDGET=1000)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_daily_budget_caps_fetches(self, mock_config, mock_resolve, mock_get_passage):
        mock_get_passage.return_value = self.mock_api_response
        budgets = bible_api_budgets()
        readings = [
            _create_reading(self.day, book="Genesis", start_ch=1, start_v=v, end_ch=1, end_v=v)
            for v in range(1, 4)
        ]

        results = [
            fetch_all_reading_texts(r, langs=["en"], budgets=budgets).get("en")
            for r in readings
        ]

        self.assertEqual(results, [True, True, False])
        self.assertEqual(mock_get_passage.call_count, 2)
        # A refused fetch stores nothing, so the passage stays due for a later run.
        self.assertIsNone(_text_for(readings[2]))

    @override_settings(READING_FETCH_DAILY_BUDGET=1, BIBLE_API_MONTHLY_BUDGET=1000)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_unmappable_book_does_not_consume_budget(self, mock_config, mock_get_passage):
        """Resolution failures must not burn a token — they never reach the API.

        This is why the budget is consumed after resolve_reading_passage rather than at
        the top of the fetcher: a day of unmappable book names would otherwise drain the
        whole allowance without a single call being made.
        """
        mock_get_passage.return_value = self.mock_api_response
        budgets = bible_api_budgets()
        bad = _create_reading(self.day, book="Not A Real Book", start_ch=1, start_v=1, end_ch=1, end_v=1)

        self.assertFalse(fetch_all_reading_texts(bad, langs=["en"], budgets=budgets).get("en"))
        mock_get_passage.assert_not_called()
        self.assertEqual(budgets[0].used(), 0)

        # The allowance is still intact for a reading we can actually resolve.
        good = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)
        self.assertTrue(fetch_all_reading_texts(good, langs=["en"], budgets=budgets).get("en"))

    @override_settings(READING_FETCH_DAILY_BUDGET=0)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_row_is_created_and_text_blank_when_budget_exhausted(
        self, mock_config, mock_resolve, mock_get_passage,
    ):
        """A spent budget degrades the response; it must not break reading creation."""
        from rest_framework.test import APIRequestFactory
        from hub.views.readings import GetDailyReadingsForDate

        with patch('hub.views.readings.get_daily_readings') as mock_scrape, \
                patch('hub.views.readings.generate_reading_context_task'):
            mock_scrape.return_value = [{
                "book": "Genesis", "book_en": "Genesis",
                "start_chapter": 1, "start_verse": 1, "end_chapter": 1, "end_verse": 5,
            }]
            request = APIRequestFactory().get('/readings/?date=2025-06-15')
            response = GetDailyReadingsForDate.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Reading.objects.filter(day=self.day).count(), 1)
        self.assertEqual(response.data["readings"][0]["text"], "")
        mock_get_passage.assert_not_called()

    def test_budget_key_is_namespaced_per_period(self):
        from hub.services.api_budget import DAY, MONTH, APIBudget

        daily = APIBudget("bible_api", 10, period=DAY)
        monthly = APIBudget("bible_api", 10, period=MONTH)

        self.assertNotEqual(daily.key(date(2026, 1, 1)), daily.key(date(2026, 1, 2)))
        self.assertEqual(monthly.key(date(2026, 1, 1)), monthly.key(date(2026, 1, 31)))
        self.assertNotEqual(monthly.key(date(2026, 1, 1)), monthly.key(date(2026, 2, 1)))

    def test_budget_counts_from_zero_when_key_absent(self):
        """Exercises the incr-raises-then-add path for a period's first call."""
        from hub.services.api_budget import APIBudget

        budget = APIBudget("bible_api_test", 2)

        self.assertEqual(budget.used(), 0)
        self.assertTrue(budget.consume())
        self.assertEqual(budget.used(), 1)
        self.assertTrue(budget.consume())
        self.assertFalse(budget.consume())
        self.assertEqual(budget.remaining(), 0)

    def test_zero_limit_refuses_everything(self):
        from hub.services.api_budget import APIBudget

        self.assertFalse(APIBudget("bible_api_test", 0).consume())


# ------------------------------------------------------------------ #
#  Admin fetch actions respect the monthly budget too
# ------------------------------------------------------------------ #

class AdminFetchBudgetTests(TestCase):
    """hub.admin.ReadingAdmin used to call a separate helper with no budget awareness,
    so an admin bulk action could burn a whole month's API.Bible quota outside the ceiling
    this module otherwise enforces everywhere else.  It now goes through
    fetch_all_reading_texts with budgets, same as the refresh task and the public view.
    """

    def setUp(self):
        cache.clear()
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2025, 6, 15), church=self.church)
        self.admin_user = get_user_model().objects.create_superuser(
            username="bible-admin", email="bible-admin@example.com", password="password",
        )
        self.mock_api_response = {
            "content": "Test verse content.",
            "copyright": "Test copyright.",
            "version": "NKJV",
            "reference": "Genesis 1:1-5",
            "fums_token": "test-fums-token",
        }

    def _admin_request(self):
        """A RequestFactory request wired with session + message storage, matching
        what admin views/actions expect."""
        request = RequestFactory().get("/admin/")
        request.user = self.admin_user
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    @override_settings(BIBLE_API_MONTHLY_BUDGET=2)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_bulk_fetch_action_stops_at_monthly_budget(self, mock_config, mock_resolve, mock_get_passage):
        mock_get_passage.return_value = self.mock_api_response
        readings = [
            _create_reading(self.day, book="Genesis", start_ch=1, start_v=v, end_ch=1, end_v=v)
            for v in range(1, 4)
        ]
        reading_admin = ReadingAdmin(Reading, AdminSite())

        reading_admin.fetch_bible_text(
            self._admin_request(), Reading.objects.filter(pk__in=[r.pk for r in readings]),
        )

        self.assertEqual(mock_get_passage.call_count, 2)
        # Three distinct passages, a ceiling of two: the third is never retrieved.
        self.assertEqual(PassageText.objects.filter(language="en").count(), 2)

    @override_settings(BIBLE_API_MONTHLY_BUDGET=0)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_single_fetch_view_refuses_when_monthly_budget_exhausted(
        self, mock_config, mock_resolve, mock_get_passage,
    ):
        reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)
        reading_admin = ReadingAdmin(Reading, AdminSite())

        reading_admin.fetch_bible_text_view(self._admin_request(), reading.pk)

        mock_get_passage.assert_not_called()
        self.assertFalse(
            PassageText.objects.filter(passage_key=reading.passage_key, language="en").exists()
        )

    @override_settings(BIBLE_API_MONTHLY_BUDGET=0)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_bulk_fetch_makes_zero_calls_when_budget_exhausted(
        self, mock_config, mock_resolve, mock_get_passage,
    ):
        """An exhausted ceiling stops the bulk action outright, not after the first row."""
        readings = [
            _create_reading(self.day, book="Genesis", start_ch=1, start_v=v, end_ch=1, end_v=v)
            for v in range(1, 4)
        ]
        reading_admin = ReadingAdmin(Reading, AdminSite())

        reading_admin.fetch_bible_text(
            self._admin_request(), Reading.objects.filter(pk__in=[r.pk for r in readings]),
        )

        mock_get_passage.assert_not_called()
        self.assertEqual(PassageText.objects.filter(language="en").count(), 0)

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_bulk_fetch_makes_one_call_per_distinct_passage(
        self, mock_config, mock_resolve, mock_get_passage,
    ):
        """Selecting every row that cites a passage must cost one call, not one per row.

        This is the whole point of passage keying, and the admin bulk action is the path
        most likely to be pointed at a large selection by hand.
        """
        mock_get_passage.return_value = self.mock_api_response
        other_day = Day.objects.create(date=date(2025, 6, 22), church=self.church)
        third_day = Day.objects.create(date=date(2025, 6, 29), church=self.church)
        readings = [
            _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5),
            _create_reading(other_day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5),
            _create_reading(
                self.day, book="St. Paul’s Epistle to the Romans",
                start_ch=1, start_v=1, end_ch=1, end_v=7,
            ),
            # The same passage spelled with a straight apostrophe: the key normalises the
            # book name, so this must not open a second dedup group.
            _create_reading(
                third_day, book="St. Paul's Epistle to the Romans",
                start_ch=1, start_v=1, end_ch=1, end_v=7,
            ),
        ]
        reading_admin = ReadingAdmin(Reading, AdminSite())

        reading_admin.fetch_bible_text(
            self._admin_request(), Reading.objects.filter(pk__in=[r.pk for r in readings]),
        )

        self.assertEqual(mock_get_passage.call_count, 2)
        self.assertEqual(PassageText.objects.filter(language="en").count(), 2)
        # Every selected reading is served, including the three that shared one call.
        for reading in readings:
            self.assertTrue(_text_for(reading).text)

    @patch('hub.admin.fetch_armenian_reading_text_task')
    def test_bulk_armenian_fetch_enqueues_one_task_per_passage(self, mock_task):
        """Armenian composition is local and unmetered, but repeating it per row would
        still recompose the same passage once per date that cites it."""
        other_day = Day.objects.create(date=date(2025, 6, 22), church=self.church)
        readings = [
            _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5),
            _create_reading(other_day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5),
            _create_reading(self.day, book="Genesis", start_ch=2, start_v=1, end_ch=2, end_v=3),
        ]
        reading_admin = ReadingAdmin(Reading, AdminSite())

        reading_admin.fetch_armenian_text(
            self._admin_request(), Reading.objects.filter(pk__in=[r.pk for r in readings]),
        )

        self.assertEqual(mock_task.delay.call_count, 2)

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_bulk_fetch_skips_unmappable_readings_without_calling(
        self, mock_config, mock_resolve, mock_get_passage,
    ):
        """No key means no fetcher can address the passage; it must not reach the API,
        and must not be stored under the empty key shared by every unmappable book."""
        mock_get_passage.return_value = self.mock_api_response
        unmappable = _create_reading(self.day, book="Book of Nowhere")
        self.assertEqual(unmappable.passage_key, "")
        reading_admin = ReadingAdmin(Reading, AdminSite())

        reading_admin.fetch_bible_text(
            self._admin_request(), Reading.objects.filter(pk=unmappable.pk),
        )

        mock_get_passage.assert_not_called()
        self.assertEqual(PassageText.objects.count(), 0)


# ------------------------------------------------------------------ #
#  passage_key derivation
# ------------------------------------------------------------------ #

class PassageKeyTests(TestCase):
    """The key is the unit of retrieval, so what collapses onto one key is load-bearing."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2026, 3, 15), church=self.church)

    def test_key_derived_on_save(self):
        reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)
        self.assertEqual(reading.passage_key, "GEN.1.1-1.5")

    def test_same_passage_on_different_days_shares_a_key(self):
        other = Day.objects.create(date=date(2027, 8, 2), church=self.church)
        self.assertEqual(
            _create_reading(self.day, book="Genesis").passage_key,
            _create_reading(other, book="Genesis").passage_key,
        )

    def test_same_passage_in_different_churches_shares_a_key(self):
        """Text is the same regardless of whose lectionary asks for it."""
        other_church = Church.objects.create(name="Another Church")
        other_day = Day.objects.create(date=date(2026, 3, 15), church=other_church)
        self.assertEqual(
            _create_reading(self.day, book="Genesis").passage_key,
            _create_reading(other_day, book="Genesis").passage_key,
        )

    def test_book_name_variants_collapse_to_one_key(self):
        """Curly and straight apostrophes must not split a dedup group in two."""
        curly = _create_reading(self.day, book="St. Paul’s Epistle to the Romans", start_ch=1, start_v=1, end_ch=1, end_v=7)
        other = Day.objects.create(date=date(2027, 1, 4), church=self.church)
        straight = _create_reading(other, book="St. Paul's Epistle to the Romans", start_ch=1, start_v=1, end_ch=1, end_v=7)

        self.assertEqual(curly.passage_key, straight.passage_key)
        self.assertEqual(curly.passage_key, "ROM.1.1-1.7")

    def test_different_verses_are_different_keys(self):
        self.assertNotEqual(
            _create_reading(self.day, book="Genesis", end_v=5).passage_key,
            _create_reading(self.day, book="Genesis", end_v=9).passage_key,
        )

    def test_apocrypha_key_is_language_neutral(self):
        """The KJVAIC/NKJV split is an API.Bible detail and stays out of the key."""
        reading = _create_reading(self.day, book="Tobit", start_ch=1, start_v=1, end_ch=1, end_v=3)
        self.assertEqual(reading.passage_key, "TOB.1.1-1.3")

    def test_esther_key_is_the_citation_not_the_kjvaic_address(self):
        """Per-edition versification belongs in the fetcher, not the shared key."""
        reading = _create_reading(self.day, book="Esther", start_ch=10, start_v=4, end_ch=10, end_v=13)
        self.assertEqual(reading.passage_key, "EST.10.4-10.13")

    def test_unmappable_book_gets_empty_key(self):
        self.assertEqual(_create_reading(self.day, book="Not A Real Book").passage_key, "")

    def test_editing_the_book_recomputes_the_key(self):
        """Covers the admin path, which saves through ModelAdmin.save_model."""
        reading = _create_reading(self.day, book="Genesis")
        reading.book = "Matthew"
        reading.save()

        reading.refresh_from_db()
        self.assertEqual(reading.passage_key, "MAT.1.1-1.5")

    def test_narrow_update_fields_still_repairs_a_stale_key(self):
        """The book_hy writes in the view pass update_fields=['i18n']."""
        reading = _create_reading(self.day, book="Genesis")
        Reading.objects.filter(pk=reading.pk).update(passage_key="")

        reading = Reading.objects.get(pk=reading.pk)
        reading.book_hy = "Ծննդոց"
        reading.save(update_fields=["i18n"])

        reading.refresh_from_db()
        self.assertEqual(reading.passage_key, "GEN.1.1-1.5")


class BackfillPassageKeysCommandTests(TestCase):
    """The escape hatch for a changed key derivation."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2026, 3, 15), church=self.church)

    def test_fills_empty_keys(self):
        reading = _create_reading(self.day, book="Genesis")
        Reading.objects.filter(pk=reading.pk).update(passage_key="")

        call_command("backfill_reading_passage_keys", stdout=StringIO())

        reading.refresh_from_db()
        self.assertEqual(reading.passage_key, "GEN.1.1-1.5")

    def test_dry_run_writes_nothing(self):
        reading = _create_reading(self.day, book="Genesis")
        Reading.objects.filter(pk=reading.pk).update(passage_key="")

        call_command("backfill_reading_passage_keys", "--dry-run", stdout=StringIO())

        reading.refresh_from_db()
        self.assertEqual(reading.passage_key, "")

    def test_is_idempotent(self):
        _create_reading(self.day, book="Genesis")
        out = StringIO()

        call_command("backfill_reading_passage_keys", "--all", stdout=out)

        self.assertIn("changed passage_key on 0 reading(s)", out.getvalue())

    def _backfill_queries(self, rows, passages):
        """Run the backfill over *rows* readings spanning *passages* citations."""
        Reading.objects.all().delete()
        Day.objects.all().delete()
        for i in range(rows):
            day = Day.objects.create(
                date=date(2026, 1, 1) + timedelta(days=i), church=self.church,
            )
            _create_reading(day, book="Genesis", end_v=(i % passages) + 1)
        Reading.objects.update(passage_key="")

        with CaptureQueriesContext(connection) as ctx:
            call_command("backfill_reading_passage_keys", stdout=StringIO())

        self.assertEqual(Reading.objects.exclude(passage_key="").count(), rows)
        return len(ctx)

    def test_query_count_tracks_passages_not_rows(self):
        """The property that keeps the migration safe on a large table.

        Cost must track the ~1,124 distinct passages in the corpus, not the row count --
        otherwise the backfill would scale with a table that grows forever.
        """
        few_rows = self._backfill_queries(rows=12, passages=3)
        many_rows = self._backfill_queries(rows=36, passages=3)
        more_passages = self._backfill_queries(rows=36, passages=6)

        self.assertEqual(few_rows, many_rows, "query count must not grow with row count")
        self.assertGreater(more_passages, many_rows, "it should grow with passage count")


class WarmPassageTextsCommandTests(TestCase):
    """The one-time enumeration that makes every date cheap forever."""

    def test_dry_run_reports_without_writing(self):
        out = StringIO()

        call_command(
            "warm_passage_texts", "--dry-run",
            "--start-date", "2026-01-01", "--end-date", "2026-01-07",
            stdout=out,
        )

        self.assertIn("distinct passages", out.getvalue())
        self.assertFalse(PassageText.objects.exists())

    def test_creates_placeholder_rows_for_the_refresh_task(self):
        call_command(
            "warm_passage_texts",
            "--start-date", "2026-01-01", "--end-date", "2026-01-07",
            "--language", "en",
            stdout=StringIO(),
        )

        rows = PassageText.objects.filter(language="en")
        self.assertGreater(rows.count(), 0)
        # NULL fetched_at is how the refresh task recognises unretrieved work.
        self.assertTrue(all(row.fetched_at is None for row in rows))
        self.assertTrue(all(row.is_expired() for row in rows))

    def test_rejects_an_unknown_language(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("warm_passage_texts", "--language", "xx", stdout=StringIO())


class ViewPassageTextQueryTests(TestCase):
    """Serving a day must cost one passage-text query, not one per reading."""

    def setUp(self):
        cache.clear()
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2026, 4, 1), church=self.church)

    @patch('hub.views.readings.get_daily_readings', return_value=[])
    @patch('hub.views.readings.generate_reading_context_task')
    def test_passage_texts_loaded_in_one_query(self, mock_ctx, mock_scrape):
        from rest_framework.test import APIRequestFactory
        from hub.views.readings import GetDailyReadingsForDate

        for verse in range(1, 7):
            reading = _create_reading(
                self.day, book="Genesis", start_ch=1, start_v=verse, end_ch=1, end_v=verse,
            )
            _store_text(reading)
            _store_text(reading, language="hy", text="hy text")

        with CaptureQueriesContext(connection) as ctx:
            response = GetDailyReadingsForDate.as_view()(
                APIRequestFactory().get(f'/readings/?date={self.day.date}')
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["readings"]), 6)
        passage_queries = [q for q in ctx.captured_queries if "hub_passagetext" in q["sql"]]
        self.assertEqual(
            len(passage_queries), 1,
            f"expected one passagetext query, got {len(passage_queries)}",
        )


# ------------------------------------------------------------------ #
#  fetch_missing_passage_texts_task (the view's off-thread continuation)
# ------------------------------------------------------------------ #

class FetchMissingPassageTextsTaskTests(TestCase):
    """The task the readings view enqueues instead of calling API.Bible inline (#506)."""

    def setUp(self):
        cache.clear()
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2025, 7, 1), church=self.church)
        self.reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)
        self.mock_api_response = {
            "content": "Test verse content.",
            "copyright": "Test copyright.",
            "version": "NKJV",
            "reference": "Genesis 1:1-5",
            "fums_token": "test-fums-token",
        }
        self.item = {
            "key": self.reading.passage_key,
            "citation": ["Genesis", 1, 1, 1, 5],
            "langs": ["en", "hy"],
        }

    @override_settings(READING_FETCH_DAILY_BUDGET=2, BIBLE_API_MONTHLY_BUDGET=1000)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_stores_text_for_enqueued_passages(self, mock_config, mock_resolve, mock_get_passage):
        mock_get_passage.return_value = self.mock_api_response

        fetch_missing_passage_texts_task([self.item])

        stored = _text_for(self.reading)
        self.assertEqual(stored.text, "Test verse content.")
        mock_get_passage.assert_called_once()

    def test_empty_payload_is_a_noop(self):
        fetch_missing_passage_texts_task([])

    @override_settings(READING_FETCH_DAILY_BUDGET=2, BIBLE_API_MONTHLY_BUDGET=1000)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_duplicate_enqueue_fetches_only_once(self, mock_config, mock_resolve, mock_get_passage):
        """A client retry enqueues the same set twice; the second run must no-op."""
        mock_get_passage.return_value = self.mock_api_response

        fetch_missing_passage_texts_task([self.item])
        fetch_missing_passage_texts_task([self.item])

        mock_get_passage.assert_called_once()

    @override_settings(READING_FETCH_DAILY_BUDGET=2, BIBLE_API_MONTHLY_BUDGET=1000)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_fetches_only_the_language_still_missing(self, mock_config, mock_resolve, mock_get_passage):
        """Armenian stored since the enqueue means the run fetches English only."""
        mock_get_passage.return_value = self.mock_api_response
        _store_text(self.reading, language="hy", text="hy text")

        fetch_missing_passage_texts_task([self.item])

        mock_get_passage.assert_called_once()
        self.assertEqual(_text_for(self.reading).text, "Test verse content.")

    @override_settings(READING_FETCH_DAILY_BUDGET=0)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_charges_the_daily_budget(self, mock_config, mock_resolve, mock_get_passage):
        """The follow-up task IS the on-demand path: a zero daily budget stops it.

        READING_FETCH_DAILY_BUDGET=0 is the test suite's only guard keeping eager runs
        of this task from reaching API.Bible, so it must stay on the daily envelope.
        """
        mock_get_passage.return_value = self.mock_api_response

        fetch_missing_passage_texts_task([self.item])

        mock_get_passage.assert_not_called()
        self.assertIsNone(_text_for(self.reading))

    @override_settings(READING_FETCH_DAILY_BUDGET=2, BIBLE_API_MONTHLY_BUDGET=1000)
    @patch('hub.tasks.bible_api_tasks.time.sleep')
    @patch('hub.tasks.bible_api_tasks.fetch_passage_text')
    def test_sleeps_only_after_successful_metered_fetch_and_not_on_last_item(
        self, mock_fetch, mock_sleep,
    ):
        """The 0.5s spacing must fire only when a metered fetch actually hit API.Bible,
        and must be skipped after the last pending item (nothing to space out).

        ``fetch_passage_text`` is mocked directly so the per-item result is
        fully controlled: the first item reports a successful metered fetch,
        the second reports a failed one (and is the last item).
        """
        other_day = Day.objects.create(date=date(2025, 7, 2), church=self.church)
        other_reading = _create_reading(
            other_day, book="Genesis", start_ch=1, start_v=6, end_ch=1, end_v=10,
        )
        other_item = {
            "key": other_reading.passage_key,
            "citation": ["Genesis", 1, 6, 1, 10],
            "langs": ["en", "hy"],
        }
        mock_fetch.side_effect = [
            {"en": True, "hy": True},  # item 1: metered success -> sleep
            {"en": False, "hy": False},  # item 2: metered failure, also last -> no sleep
        ]

        fetch_missing_passage_texts_task([self.item, other_item])

        self.assertEqual(mock_sleep.call_count, 1)

    @override_settings(READING_FETCH_DAILY_BUDGET=0, BIBLE_API_MONTHLY_BUDGET=1000)
    @patch('hub.tasks.bible_api_tasks.time.sleep')
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_no_sleep_when_every_metered_fetch_is_refused(
        self, mock_config, mock_resolve, mock_get_passage, mock_sleep,
    ):
        """Budget-refused fetches never reach API.Bible -- no point waiting."""
        other_day = Day.objects.create(date=date(2025, 7, 2), church=self.church)
        other_reading = _create_reading(
            other_day, book="Genesis", start_ch=1, start_v=6, end_ch=1, end_v=10,
        )
        other_item = {
            "key": other_reading.passage_key,
            "citation": ["Genesis", 1, 6, 1, 10],
            "langs": ["en"],
        }
        mock_get_passage.return_value = self.mock_api_response

        fetch_missing_passage_texts_task([self.item, other_item])

        mock_get_passage.assert_not_called()
        mock_sleep.assert_not_called()


# ------------------------------------------------------------------ #
#  prewarm_next_day_readings_task
# ------------------------------------------------------------------ #

class PrewarmNextDayReadingsTaskTests(TestCase):
    """Tomorrow's readings are created and warmed before the first request (#506)."""

    def setUp(self):
        cache.clear()
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.other_church = Church.objects.create(name="St. Other Church")
        self.tomorrow = timezone.localdate() + timedelta(days=1)
        self.readings_payload = [
            {
                "book": "Genesis",
                "book_en": "Genesis",
                "start_chapter": 1,
                "start_verse": 1,
                "end_chapter": 1,
                "end_verse": 5,
            },
        ]
        self.mock_api_response = {
            "content": "Test verse content.",
            "copyright": "Test copyright.",
            "version": "NKJV",
            "reference": "Genesis 1:1-5",
            "fums_token": "test-fums-token",
        }

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    @patch('hub.services.lectionary_service.get_daily_readings')
    def test_creates_tomorrows_readings_for_every_church(
        self, mock_scrape, mock_config, mock_resolve, mock_get_passage,
    ):
        mock_scrape.return_value = self.readings_payload
        mock_get_passage.return_value = self.mock_api_response

        prewarm_next_day_readings_task()

        for church in (self.church, self.other_church):
            day = Day.objects.get(date=self.tomorrow, church=church)
            self.assertEqual(day.readings.count(), 1)
        # The same passage shared by both churches is fetched once.
        self.assertEqual(mock_get_passage.call_count, 1)
        self.assertEqual(
            PassageText.objects.get(passage_key="GEN.1.1-1.5", language="en").text,
            "Test verse content.",
        )

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    @patch('hub.services.lectionary_service.get_daily_readings')
    def test_rerun_is_a_noop(self, mock_scrape, mock_config, mock_resolve, mock_get_passage):
        """Beat firing twice must not duplicate rows or re-spend quota."""
        mock_scrape.return_value = self.readings_payload
        mock_get_passage.return_value = self.mock_api_response

        prewarm_next_day_readings_task()
        prewarm_next_day_readings_task()

        self.assertEqual(Reading.objects.filter(day__date=self.tomorrow).count(), 2)
        mock_get_passage.assert_called_once()

    @override_settings(READING_FETCH_DAILY_BUDGET=0)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    @patch('hub.services.lectionary_service.get_daily_readings')
    def test_charges_monthly_ceiling_only(
        self, mock_scrape, mock_config, mock_resolve, mock_get_passage,
    ):
        """Background warm-up must not eat the public daily allowance: even with the
        daily budget pinned to zero, the passages are fetched."""
        mock_scrape.return_value = self.readings_payload
        mock_get_passage.return_value = self.mock_api_response

        prewarm_next_day_readings_task()

        self.assertEqual(mock_get_passage.call_count, 1)

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    @patch('hub.services.lectionary_service.get_daily_readings')
    def test_one_church_failure_does_not_block_the_rest(
        self, mock_scrape, mock_config, mock_resolve, mock_get_passage,
    ):
        def explode_for_default_church(target, church):
            if church.pk == Church.get_default_pk():
                raise RuntimeError("lectionary exploded")
            return self.readings_payload

        mock_scrape.side_effect = explode_for_default_church
        mock_get_passage.return_value = self.mock_api_response

        prewarm_next_day_readings_task()
        self.assertEqual(Day.objects.get(date=self.tomorrow, church=self.church).readings.count(), 0)
        self.assertEqual(Day.objects.get(date=self.tomorrow, church=self.other_church).readings.count(), 1)
        self.assertEqual(mock_get_passage.call_count, 1)

    @patch('hub.tasks.bible_api_tasks.time.sleep')
    @patch('hub.tasks.bible_api_tasks.fetch_passage_text')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    @patch('hub.services.lectionary_service.get_daily_readings')
    def test_sleeps_only_between_metered_fetches_and_skips_the_last(
        self, mock_scrape, mock_config, mock_resolve, mock_fetch, mock_sleep,
    ):
        """Two distinct metered passages: sleep fires once (between them), not after the last."""
        mock_scrape.side_effect = [
            [
                {
                    "book": "Genesis", "book_en": "Genesis",
                    "start_chapter": 1, "start_verse": 1, "end_chapter": 1, "end_verse": 5,
                },
                {
                    "book": "Genesis", "book_en": "Genesis",
                    "start_chapter": 1, "start_verse": 6, "end_chapter": 1, "end_verse": 10,
                },
            ],
        ]
        mock_fetch.side_effect = [{"en": True}, {"en": True}]

        prewarm_next_day_readings_task()

        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)
