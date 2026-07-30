"""Tests for Bible API text retrieval functionality.

Tests cover:
    - BibleAPIService (book name resolution, bible ID selection)
    - fetch_english_text (synchronous single-reading fetch, unique FUMS token)
    - fetch_reading_text_task (Celery wrapper, used for management commands)
    - refresh_all_reading_texts_task (nearest-first refresh, spend limits, error summary)
    - Synchronous text fetch in GetDailyReadingsForDate view
    - API response (includes text fields)
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
from hub.models import Church, Day, Reading
from hub.services.bible_api_service import BibleAPIService
from hub.services.reading_text_service import bible_api_budgets, fetch_english_text
from hub.tasks.bible_api_tasks import (
    fetch_reading_text_task,
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
        _create_reading(
            day,
            book="Genesis",
            start_ch=1,
            start_v=1,
            end_ch=1,
            end_v=5,
            text="Old text",
            text_fetched_at=timezone.now() - timedelta(days=25),
        )

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
#  fetch_english_text (synchronous) Tests
# ------------------------------------------------------------------ #

class FetchEnglishTextTests(TestCase):
    """Tests for the fetch_english_text synchronous helper."""

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

        result = fetch_english_text(reading)

        self.assertTrue(result)
        reading.refresh_from_db()
        self.assertEqual(reading.text, self.mock_api_response["content"])
        self.assertEqual(reading.text_copyright, self.mock_api_response["copyright"])
        self.assertEqual(reading.text_version, "NKJV")
        self.assertEqual(reading.fums_token, "test-fums-token")
        self.assertIsNotNone(reading.text_fetched_at)

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_fetch_updates_only_target_reading(self, mock_config, mock_resolve, mock_get_passage):
        """Test that fetching text updates only the target reading, not duplicates."""
        mock_get_passage.return_value = self.mock_api_response

        # Create two readings with the same passage on different days
        day2 = Day.objects.create(date=date(2026, 3, 15), church=self.church)
        reading1 = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)
        reading2 = _create_reading(day2, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        fetch_english_text(reading1)

        # Only the target reading should have text
        reading1.refresh_from_db()
        reading2.refresh_from_db()
        self.assertEqual(reading1.text, self.mock_api_response["content"])
        self.assertEqual(reading2.text, "")

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_fetch_always_calls_api_even_when_existing_text(self, mock_config, mock_resolve, mock_get_passage):
        """Test that an API call is made even when an existing reading has the same passage.

        Each reading needs its own FUMS token, so we never skip the API call.
        """
        mock_get_passage.return_value = self.mock_api_response

        # Create a reading that already has text
        day2 = Day.objects.create(date=date(2026, 3, 15), church=self.church)
        _create_reading(
            self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5,
            text="Existing text",
            text_copyright="Existing copyright",
            text_version="NKJV",
            text_fetched_at=timezone.now(),
        )
        reading_new = _create_reading(day2, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        result = fetch_english_text(reading_new)

        self.assertTrue(result)
        reading_new.refresh_from_db()
        # Should have text from the API call, not the existing reading
        self.assertEqual(reading_new.text, self.mock_api_response["content"])
        # API SHOULD have been called (one call per reading for FUMS compliance)
        mock_get_passage.assert_called_once()

    @patch('hub.services.bible_api_service.config', return_value="")
    def test_fetch_no_api_key_returns_false(self, mock_config):
        """Test that fetch returns False when API key is not configured."""
        reading = _create_reading(self.day)

        result = fetch_english_text(reading)

        self.assertFalse(result)
        reading.refresh_from_db()
        self.assertEqual(reading.text, "")

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_fetch_with_pre_initialized_service(self, mock_config, mock_resolve, mock_get_passage):
        """Test fetching with a pre-initialized service (as used by the view)."""
        mock_get_passage.return_value = self.mock_api_response

        reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)
        service = BibleAPIService()

        result = fetch_english_text(reading, service=service)

        self.assertTrue(result)
        reading.refresh_from_db()
        self.assertEqual(reading.text, self.mock_api_response["content"])

    def test_fetch_unknown_book_returns_false(self):
        """Test that fetch returns False for unknown book names."""
        reading = _create_reading(self.day, book="Nonexistent Book")
        service = MagicMock(spec=BibleAPIService)

        result = fetch_english_text(reading, service=service)

        self.assertFalse(result)


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
    def test_task_delegates_to_fetch_english_text(self, mock_config, mock_resolve, mock_get_passage):
        """Test that the Celery task delegates to fetch_english_text."""
        mock_get_passage.return_value = {
            "content": "Test content.",
            "copyright": "Test copyright.",
            "version": "NKJV",
            "reference": "Genesis 1:1-5",
            "fums_token": "test-token",
        }

        reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        fetch_reading_text_task(reading.id)

        reading.refresh_from_db()
        self.assertEqual(reading.text, "Test content.")


# ------------------------------------------------------------------ #
#  refresh_all_reading_texts_task Tests
# ------------------------------------------------------------------ #

@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    READING_TEXT_REFRESH_DAYS=23,
)
class RefreshAllReadingTextsTaskTests(TestCase):
    """Tests for the refresh_all_reading_texts_task Celery task."""

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

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_refresh_stale_readings(self, mock_config, mock_resolve, mock_get_passage):
        """Test that stale readings (text_fetched_at is NULL) are refreshed."""
        mock_get_passage.return_value = self.mock_api_response

        day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        reading = _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        refresh_all_reading_texts_task()

        reading.refresh_from_db()
        self.assertEqual(reading.text, "Test verse content.")
        self.assertIsNotNone(reading.text_fetched_at)

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_refresh_old_readings(self, mock_config, mock_resolve, mock_get_passage):
        """Test that readings older than READING_TEXT_REFRESH_DAYS are refreshed."""
        mock_get_passage.return_value = self.mock_api_response

        day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        reading = _create_reading(
            day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5,
            text="Old text",
            text_fetched_at=timezone.now() - timedelta(days=25),
        )

        refresh_all_reading_texts_task()

        reading.refresh_from_db()
        self.assertEqual(reading.text, "Test verse content.")

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_skip_recent_readings(self, mock_config, mock_resolve, mock_get_passage):
        """Test that recently fetched readings are not refreshed."""
        day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        _create_reading(
            day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5,
            text="Recent text",
            text_fetched_at=timezone.now() - timedelta(days=5),
        )

        refresh_all_reading_texts_task()

        # API should not have been called since reading is recent
        mock_get_passage.assert_not_called()

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_one_api_call_per_reading_for_fums_compliance(self, mock_config, mock_resolve, mock_get_passage):
        """Test that each reading gets its own API call for a unique FUMS token."""
        mock_get_passage.return_value = self.mock_api_response

        # Create three readings with the same passage on different days
        for i in range(3):
            day = Day.objects.create(
                date=date(2025, 3, 15) + timedelta(days=i * 365),
                church=self.church,
            )
            _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        refresh_all_reading_texts_task()

        # Three API calls should have been made (one per reading)
        self.assertEqual(mock_get_passage.call_count, 3)

        # All three readings should have text
        readings = Reading.objects.filter(book="Genesis", start_chapter=1, start_verse=1)
        for reading in readings:
            self.assertEqual(reading.text, "Test verse content.")

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_refresh_only_updates_stale_readings(self, mock_config, mock_resolve, mock_get_passage):
        """Test that refresh only updates stale readings, leaving fresh ones unchanged."""
        mock_get_passage.return_value = self.mock_api_response

        # Create a stale reading and a fresh reading with the same passage
        day1 = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        day2 = Day.objects.create(date=date(2026, 3, 15), church=self.church)

        stale = _create_reading(day1, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)
        fresh = _create_reading(
            day2, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5,
            text="Old text from last refresh",
            text_fetched_at=timezone.now() - timedelta(days=5),
        )

        refresh_all_reading_texts_task()

        # Only stale reading should be updated
        stale.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(stale.text, "Test verse content.")
        self.assertEqual(fresh.text, "Old text from last refresh")

    @override_settings(READING_REFRESH_LIMIT=10)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_refresh_never_deletes_readings(self, mock_config, mock_resolve, mock_get_passage):
        """Refresh caps API spend at READING_REFRESH_LIMIT without deleting any readings."""
        mock_get_passage.return_value = self.mock_api_response

        # Create 15 readings (exceeds READING_REFRESH_LIMIT=10)
        base_date = date(2020, 1, 1)
        for i in range(15):
            day = Day.objects.create(
                date=base_date + timedelta(days=i),
                church=self.church,
            )
            _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=i + 1)

        self.assertEqual(Reading.objects.count(), 15)

        refresh_all_reading_texts_task()

        # Pruning is what caused rows to be re-created and re-fetched; nothing is deleted.
        self.assertEqual(Reading.objects.count(), 15)
        # Only the limit's worth of API calls are made.
        self.assertEqual(mock_get_passage.call_count, 10)

    @override_settings(READING_REFRESH_LIMIT=3)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_refresh_prefers_readings_nearest_to_today(self, mock_config, mock_resolve, mock_get_passage):
        """Upcoming readings are refreshed before past ones, nearest first."""
        mock_get_passage.return_value = self.mock_api_response

        today = timezone.localdate()
        readings = {}
        for offset in (-10, -1, 0, 1, 10):
            day = Day.objects.create(date=today + timedelta(days=offset), church=self.church)
            readings[offset] = _create_reading(
                day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5,
            )

        refresh_all_reading_texts_task()

        # today, +1, +10 are selected; the past dates are left for a later run.
        refreshed = {o for o, r in readings.items() if Reading.objects.get(pk=r.pk).text}
        self.assertEqual(refreshed, {0, 1, 10})

    @override_settings(READING_REFRESH_LIMIT=3)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_refresh_backfills_with_most_recent_past_readings(self, mock_config, mock_resolve, mock_get_passage):
        """When upcoming readings do not fill the limit, the most recent past ones follow."""
        mock_get_passage.return_value = self.mock_api_response

        today = timezone.localdate()
        readings = {}
        for offset in (-30, -2, -1, 5):
            day = Day.objects.create(date=today + timedelta(days=offset), church=self.church)
            readings[offset] = _create_reading(
                day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5,
            )

        refresh_all_reading_texts_task()

        refreshed = {o for o, r in readings.items() if Reading.objects.get(pk=r.pk).text}
        self.assertEqual(refreshed, {5, -1, -2})

    @override_settings(READING_REFRESH_MAX_CONSECUTIVE_FAILURES=3)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_refresh_aborts_after_consecutive_failures(self, mock_config, mock_resolve, mock_get_passage):
        """A run of API failures aborts the task instead of burning the whole quota.

        A failed fetch never records text_fetched_at, so without this circuit breaker a
        quota rejection leaves every reading permanently stale and the weekly run
        re-attempts all of them forever.
        """
        mock_get_passage.side_effect = Exception("429 Too Many Requests")

        today = timezone.localdate()
        for i in range(10):
            day = Day.objects.create(date=today + timedelta(days=i), church=self.church)
            _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        refresh_all_reading_texts_task()

        self.assertEqual(mock_get_passage.call_count, 3)

    @override_settings(READING_REFRESH_MAX_CONSECUTIVE_FAILURES=3)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_unmappable_books_do_not_trip_the_circuit_breaker(self, mock_config, mock_get_passage):
        """Unresolvable book names must not look like API rejection.

        They fail before any HTTP request and can never succeed, so they stay stale and
        are re-selected every run.  Once everything else is fresh a run selects *only*
        these — the steady state — and counting them would abort the task every week.
        """
        mock_get_passage.return_value = self.mock_api_response

        today = timezone.localdate()
        for i in range(6):
            day = Day.objects.create(date=today + timedelta(days=i), church=self.church)
            _create_reading(day, book="Not A Real Book", start_ch=1, start_v=1, end_ch=1, end_v=5)
        # A resolvable reading after them, to prove the run kept going.
        last_day = Day.objects.create(date=today + timedelta(days=6), church=self.church)
        good = _create_reading(last_day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        refresh_all_reading_texts_task()

        good.refresh_from_db()
        self.assertEqual(good.text, "Test verse content.")
        self.assertEqual(mock_get_passage.call_count, 1)

    @override_settings(READING_REFRESH_MAX_CONSECUTIVE_FAILURES=3)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_real_api_failures_after_unmappable_books_still_abort(
        self, mock_config, mock_resolve, mock_get_passage,
    ):
        """Ignoring unmappable readings must not blunt the breaker for genuine failures."""
        mock_get_passage.side_effect = Exception("429 Too Many Requests")

        today = timezone.localdate()
        for i in range(10):
            day = Day.objects.create(date=today + timedelta(days=i), church=self.church)
            _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        refresh_all_reading_texts_task()

        self.assertEqual(mock_get_passage.call_count, 3)

    @override_settings(READING_REFRESH_MAX_CONSECUTIVE_FAILURES=3)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_breaker_trip_reports_attempts_not_zero(self, mock_config, mock_resolve, mock_get_passage):
        """A circuit-breaker trip must report the attempts that reached API.Bible.

        Counting only successes made a rejection spiral log "0 API calls," hiding the
        exact event the telemetry exists to surface.
        """
        mock_get_passage.side_effect = Exception("429 Too Many Requests")

        today = timezone.localdate()
        for i in range(10):
            day = Day.objects.create(date=today + timedelta(days=i), church=self.church)
            _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        with self.assertLogs("hub.tasks.bible_api_tasks", level="ERROR") as log:
            refresh_all_reading_texts_task()

        abort_messages = [m for m in log.output if "Aborting refresh" in m]
        self.assertEqual(len(abort_messages), 1)
        self.assertIn("3 English API attempts", abort_messages[0])

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_readings_processed_does_not_double_count_partial_language_failure(
        self, mock_config, mock_resolve, mock_get_passage,
    ):
        """A reading whose English fetch succeeds but Armenian fetch fails must count
        once in readings processed, not twice — the old ``api_calls + len(failures)``
        expression double-counted exactly this case.

        No BibleVerse rows are seeded for this passage, so the Armenian composer
        naturally returns no text without needing to mock anything.
        """
        mock_get_passage.return_value = self.mock_api_response

        day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        with self.assertLogs("hub.tasks.bible_api_tasks", level="INFO") as log:
            refresh_all_reading_texts_task()

        summary = [m for m in log.output if "Refresh complete" in m][0]
        self.assertIn("1 readings processed", summary)
        self.assertIn("1 English API attempts", summary)
        self.assertIn("1 successes", summary)
        self.assertIn("{'hy': 1}", summary)

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_refresh_stops_when_monthly_budget_is_exhausted(self, mock_config, mock_resolve, mock_get_passage):
        """The monthly ceiling caps the task's spend even when the per-run limit is higher."""
        mock_get_passage.return_value = self.mock_api_response

        today = timezone.localdate()
        for i in range(6):
            day = Day.objects.create(date=today + timedelta(days=i), church=self.church)
            _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        with override_settings(BIBLE_API_MONTHLY_BUDGET=2):
            refresh_all_reading_texts_task()

        self.assertEqual(mock_get_passage.call_count, 2)

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_no_stale_readings_skips_refresh(self, mock_config, mock_resolve, mock_get_passage):
        """Test that task exits early when no stale readings exist."""
        # Create a recently fetched reading
        day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        _create_reading(
            day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5,
            text="Fresh text",
            text_fetched_at=timezone.now(),
        )

        refresh_all_reading_texts_task()

        mock_get_passage.assert_not_called()

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_api_failure_logged_in_summary(self, mock_config, mock_resolve, mock_get_passage):
        """Test that API failures are collected and logged in the error summary."""
        mock_get_passage.side_effect = Exception("API timeout")

        day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        # Should not raise despite API failure
        with self.assertLogs("hub.services.reading_text_service", level="ERROR") as log:
            refresh_all_reading_texts_task()

        # Check that the failure was logged
        log_output = "\n".join(log.output)
        self.assertIn("API call failed", log_output)

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_refresh_maps_esther_greek_addition_to_esg(self, mock_config, mock_get_passage):
        """Esther 10:4-9 should fetch from KJVAIC's ESG 1:4-9."""
        mock_get_passage.return_value = {
            "content": "Then Mordecai said...",
            "copyright": "KJVAIC copyright",
            "version": "KJVAIC",
            "reference": "Esther (Additions) 1:4-9",
            "fums_token": "test-fums-token",
        }

        day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        reading = _create_reading(
            day,
            book="Esther",
            start_ch=10,
            start_v=4,
            end_ch=10,
            end_v=9,
        )

        refresh_all_reading_texts_task()

        mock_get_passage.assert_called_once_with("ESG", 1, 4, 1, 9)
        reading.refresh_from_db()
        self.assertEqual(reading.text, "Then Mordecai said...")
        self.assertEqual(reading.text_version, "KJVAIC")

    @patch('hub.services.bible_api_service.config', return_value="")
    def test_no_api_key_aborts_refresh(self, mock_config):
        """Test that refresh aborts gracefully when API key is missing."""
        day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        # Should not raise
        refresh_all_reading_texts_task()


# ------------------------------------------------------------------ #
#  View Synchronous Text Fetch Tests
# ------------------------------------------------------------------ #

class ViewSynchronousTextFetchTests(TestCase):
    """Tests that GetDailyReadingsForDate fetches Bible text synchronously."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 4, 1)

    @patch('hub.views.readings.fetch_all_reading_texts')
    @patch('hub.views.readings.prepare_shared_resources')
    @patch('hub.views.readings.get_daily_readings')
    @patch('hub.views.readings.generate_reading_context_task')
    def test_view_calls_fetch_all_for_new_readings(
        self, mock_context_task, mock_scrape, mock_prepare, mock_fetch_all,
    ):
        """Test that the view fetches text (all languages) for newly scraped readings."""
        from rest_framework.test import APIRequestFactory
        from hub.views.readings import GetDailyReadingsForDate

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
        mock_prepare.return_value = {"service": "mock_svc"}

        factory = APIRequestFactory()
        request = factory.get(f'/readings/?date={self.test_date}')
        view = GetDailyReadingsForDate.as_view()

        response = view(request)

        self.assertEqual(response.status_code, 200)
        # prepare_shared_resources should have been called once for the batch
        mock_prepare.assert_called_once()
        # fetch_all_reading_texts should have been called for the new reading
        mock_fetch_all.assert_called_once()
        # The call should include the shared resources from prepare
        call_kwargs = mock_fetch_all.call_args
        self.assertEqual(call_kwargs.kwargs.get('service'), "mock_svc")

    @patch('hub.views.readings.fetch_all_reading_texts')
    @patch('hub.views.readings.prepare_shared_resources')
    @patch('hub.views.readings.get_daily_readings')
    @patch('hub.views.readings.generate_reading_context_task')
    def test_view_does_not_fetch_text_for_existing_readings(
        self, mock_context_task, mock_scrape, mock_prepare, mock_fetch_all,
    ):
        """Test that the view does not re-fetch text for readings that already exist."""
        from rest_framework.test import APIRequestFactory
        from hub.views.readings import GetDailyReadingsForDate

        mock_scrape.return_value = []

        # Pre-create the day and reading
        day = Day.objects.create(date=self.test_date, church=self.church)
        _create_reading(
            day, book="Matthew", start_ch=5, start_v=1, end_ch=5, end_v=12,
            text="Existing text",
            text_fetched_at=timezone.now(),
        )

        factory = APIRequestFactory()
        request = factory.get(f'/readings/?date={self.test_date}')
        view = GetDailyReadingsForDate.as_view()

        response = view(request)

        self.assertEqual(response.status_code, 200)
        # Should NOT have called fetch since readings already exist
        mock_fetch_all.assert_not_called()
        mock_prepare.assert_not_called()

    @patch('hub.views.readings.fetch_all_reading_texts')
    @patch('hub.views.readings.prepare_shared_resources')
    @patch('hub.views.readings.get_daily_readings')
    @patch('hub.views.readings.generate_reading_context_task')
    def test_view_graceful_when_prepare_partial(
        self, mock_context_task, mock_scrape, mock_prepare, mock_fetch_all,
    ):
        """Test that the view still returns readings when some resources fail to prepare."""
        from rest_framework.test import APIRequestFactory
        from hub.views.readings import GetDailyReadingsForDate

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
        # Simulate partial preparation (e.g. API key missing, Armenian scrape failed)
        mock_prepare.return_value = {}

        factory = APIRequestFactory()
        request = factory.get(f'/readings/?date={self.test_date}')
        view = GetDailyReadingsForDate.as_view()

        response = view(request)

        # View should still succeed, just without text
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["readings"]), 1)
        # fetch_all was still called (with empty shared resources)
        mock_fetch_all.assert_called_once()


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
        _create_reading(
            day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5,
            text="In the beginning God created the heavens and the earth.",
            text_copyright="NKJV (c) 1982 Thomas Nelson.",
            text_version="NKJV",
            text_fetched_at=timezone.now(),
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

    @patch('hub.views.readings.fetch_all_reading_texts')
    @patch('hub.views.readings.prepare_shared_resources', return_value={})
    @patch('hub.views.readings.get_daily_readings', return_value=[])
    @patch('hub.views.readings.generate_reading_context_task')
    def test_refetches_expired_reading(self, mock_ctx, mock_scrape, mock_prepare, mock_fetch):
        _create_reading(
            self.day, book="Matthew", start_ch=5, start_v=1, end_ch=5, end_v=12,
            text="Stale text", text_fetched_at=timezone.now() - timedelta(days=31),
        )

        self.assertEqual(self._get().status_code, 200)

        mock_prepare.assert_called_once()
        mock_fetch.assert_called_once()

    @patch('hub.views.readings.fetch_all_reading_texts')
    @patch('hub.views.readings.prepare_shared_resources', return_value={})
    @patch('hub.views.readings.get_daily_readings', return_value=[])
    @patch('hub.views.readings.generate_reading_context_task')
    def test_does_not_refetch_just_inside_max_age(self, mock_ctx, mock_scrape, mock_prepare, mock_fetch):
        _create_reading(
            self.day, book="Matthew", start_ch=5, start_v=1, end_ch=5, end_v=12,
            text="Fresh enough", text_fetched_at=timezone.now() - timedelta(days=29),
        )

        self.assertEqual(self._get().status_code, 200)

        mock_fetch.assert_not_called()
        mock_prepare.assert_not_called()

    @patch('hub.views.readings.fetch_all_reading_texts')
    @patch('hub.views.readings.prepare_shared_resources', return_value={})
    @patch('hub.views.readings.get_daily_readings', return_value=[])
    @patch('hub.views.readings.generate_reading_context_task')
    def test_prepares_shared_resources_once_for_many_expired(self, mock_ctx, mock_scrape, mock_prepare, mock_fetch):
        """Shared resources open an HTTP session and scrape a page; build them once."""
        for verse in range(1, 4):
            _create_reading(
                self.day, book="Matthew", start_ch=5, start_v=verse, end_ch=5, end_v=verse,
                text="Stale", text_fetched_at=timezone.now() - timedelta(days=31),
            )

        self.assertEqual(self._get().status_code, 200)

        mock_prepare.assert_called_once()
        self.assertEqual(mock_fetch.call_count, 3)


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
        from hub.services.reading_text_service import bible_api_budgets, fetch_english_text

        mock_get_passage.return_value = self.mock_api_response
        budgets = bible_api_budgets()
        readings = [
            _create_reading(self.day, book="Genesis", start_ch=1, start_v=v, end_ch=1, end_v=v)
            for v in range(1, 4)
        ]

        results = [fetch_english_text(r, budgets=budgets) for r in readings]

        self.assertEqual(results, [True, True, False])
        self.assertEqual(mock_get_passage.call_count, 2)
        readings[2].refresh_from_db()
        self.assertIsNone(readings[2].text_fetched_at)

    @override_settings(READING_FETCH_DAILY_BUDGET=1, BIBLE_API_MONTHLY_BUDGET=1000)
    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_unmappable_book_does_not_consume_budget(self, mock_config, mock_get_passage):
        """Resolution failures must not burn a token — they never reach the API.

        This is why the budget is consumed after resolve_reading_passage rather than at
        the top of the fetcher: a day of unmappable book names would otherwise drain the
        whole allowance without a single call being made.
        """
        from hub.services.reading_text_service import bible_api_budgets, fetch_english_text

        mock_get_passage.return_value = self.mock_api_response
        budgets = bible_api_budgets()
        bad = _create_reading(self.day, book="Not A Real Book", start_ch=1, start_v=1, end_ch=1, end_v=1)

        self.assertFalse(fetch_english_text(bad, budgets=budgets))
        mock_get_passage.assert_not_called()
        self.assertEqual(budgets[0].used(), 0)

        # The allowance is still intact for a reading we can actually resolve.
        good = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)
        self.assertTrue(fetch_english_text(good, budgets=budgets))

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
    """hub.admin.ReadingAdmin used to call fetch_text_for_reading, a separate helper
    with no budget awareness — an admin bulk action could burn a whole month's
    API.Bible quota outside the ceiling this module otherwise enforces everywhere else.
    It now goes through fetch_english_text with budgets, same as the refresh task and
    the public view.
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
        self.assertEqual(Reading.objects.filter(text_fetched_at__isnull=False).count(), 2)

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
        reading.refresh_from_db()
        self.assertIsNone(reading.text_fetched_at)
