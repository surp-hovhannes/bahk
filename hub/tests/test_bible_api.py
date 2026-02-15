"""Tests for Bible API text retrieval functionality.

Tests cover:
    - BibleAPIService (book name resolution, bible ID selection)
    - fetch_reading_text_task (single reading fetch with unique FUMS token)
    - refresh_all_reading_texts_task (per-reading refresh, cleanup, error summary)
    - Reading post_save signal (triggers text fetch on creation)
    - API response (includes text fields)
"""
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from django.db.models.signals import post_save
from django.test import TestCase, override_settings
from django.utils import timezone

from hub.constants import BOOK_NAME_TO_USFM, APOCRYPHA_USFM_IDS, CATENA_ABBREV_FOR_BOOK
from hub.models import Church, Day, Reading
from hub.services.bible_api_service import BibleAPIService
from hub.signals import handle_reading_save
from hub.tasks.bible_api_tasks import (
    fetch_reading_text_task,
    refresh_all_reading_texts_task,
    _fetch_and_update_reading,
)


def _disconnect_reading_signal():
    """Disconnect the Reading post_save signal to avoid side effects in tests."""
    post_save.disconnect(handle_reading_save, sender=Reading)


def _reconnect_reading_signal():
    """Reconnect the Reading post_save signal."""
    post_save.connect(handle_reading_save, sender=Reading)


def _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5, **kwargs):
    """Helper to create a Reading with signals disconnected."""
    _disconnect_reading_signal()
    try:
        reading = Reading.objects.create(
            day=day,
            book=book,
            start_chapter=start_ch,
            start_verse=start_v,
            end_chapter=end_ch,
            end_verse=end_v,
            **kwargs,
        )
    finally:
        _reconnect_reading_signal()
    return reading


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
#  fetch_reading_text_task Tests
# ------------------------------------------------------------------ #

@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class FetchReadingTextTaskTests(TestCase):
    """Tests for the fetch_reading_text_task Celery task."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        self.mock_api_response = {
            "content": "[1] In the beginning God created the heavens and the earth.",
            "copyright": "Scripture taken from the NKJV. Copyright 1982 Thomas Nelson.",
            "version": "NKJV",
            "reference": "Genesis 1:1-5",
        }

    @patch('hub.tasks.bible_api_tasks.BibleAPIService')
    def test_fetch_text_for_new_reading(self, MockService):
        """Test fetching text for a reading that has no text yet."""
        mock_service = MagicMock()
        mock_service.get_passage.return_value = self.mock_api_response
        MockService.return_value = mock_service

        reading = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        fetch_reading_text_task(reading.id)

        reading.refresh_from_db()
        self.assertEqual(reading.text, self.mock_api_response["content"])
        self.assertEqual(reading.text_copyright, self.mock_api_response["copyright"])
        self.assertEqual(reading.text_version, "NKJV")
        self.assertIsNotNone(reading.text_fetched_at)

    @patch('hub.tasks.bible_api_tasks.BibleAPIService')
    def test_fetch_updates_only_target_reading(self, MockService):
        """Test that fetching text updates only the target reading, not duplicates."""
        mock_service = MagicMock()
        mock_service.get_passage.return_value = self.mock_api_response
        MockService.return_value = mock_service

        # Create two readings with the same passage on different days
        day2 = Day.objects.create(date=date(2026, 3, 15), church=self.church)
        reading1 = _create_reading(self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)
        reading2 = _create_reading(day2, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        fetch_reading_text_task(reading1.id)

        # Only the target reading should have text
        reading1.refresh_from_db()
        reading2.refresh_from_db()
        self.assertEqual(reading1.text, self.mock_api_response["content"])
        self.assertEqual(reading2.text, "")

    @patch('hub.tasks.bible_api_tasks.BibleAPIService')
    def test_fetch_always_calls_api_even_when_existing_text(self, MockService):
        """Test that an API call is made even when an existing reading has the same passage.

        Each reading needs its own FUMS token, so we never skip the API call.
        """
        mock_service = MagicMock()
        mock_service.get_passage.return_value = self.mock_api_response
        MockService.return_value = mock_service

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

        fetch_reading_text_task(reading_new.id)

        reading_new.refresh_from_db()
        # Should have text from the API call, not the existing reading
        self.assertEqual(reading_new.text, self.mock_api_response["content"])
        # API SHOULD have been called (one call per reading for FUMS compliance)
        mock_service.get_passage.assert_called_once()

    def test_fetch_nonexistent_reading(self):
        """Test that task handles nonexistent reading gracefully."""
        # Should not raise an exception
        fetch_reading_text_task(99999)

    @patch('hub.tasks.bible_api_tasks.BibleAPIService')
    def test_fetch_no_api_key_logs_error(self, MockService):
        """Test that task handles missing API key gracefully."""
        MockService.side_effect = ValueError("API key required.")

        reading = _create_reading(self.day)

        # Should not raise, just log the error
        fetch_reading_text_task(reading.id)

        reading.refresh_from_db()
        self.assertEqual(reading.text, "")


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
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.mock_api_response = {
            "content": "Test verse content.",
            "copyright": "Test copyright.",
            "version": "NKJV",
            "reference": "Genesis 1:1-5",
        }

    @patch('hub.tasks.bible_api_tasks.BibleAPIService')
    def test_refresh_stale_readings(self, MockService):
        """Test that stale readings (text_fetched_at is NULL) are refreshed."""
        mock_service = MagicMock()
        mock_service.get_passage.return_value = self.mock_api_response
        MockService.return_value = mock_service

        day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        reading = _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        refresh_all_reading_texts_task()

        reading.refresh_from_db()
        self.assertEqual(reading.text, "Test verse content.")
        self.assertIsNotNone(reading.text_fetched_at)

    @patch('hub.tasks.bible_api_tasks.BibleAPIService')
    def test_refresh_old_readings(self, MockService):
        """Test that readings older than READING_TEXT_REFRESH_DAYS are refreshed."""
        mock_service = MagicMock()
        mock_service.get_passage.return_value = self.mock_api_response
        MockService.return_value = mock_service

        day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        reading = _create_reading(
            day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5,
            text="Old text",
            text_fetched_at=timezone.now() - timedelta(days=25),
        )

        refresh_all_reading_texts_task()

        reading.refresh_from_db()
        self.assertEqual(reading.text, "Test verse content.")

    @patch('hub.tasks.bible_api_tasks.BibleAPIService')
    def test_skip_recent_readings(self, MockService):
        """Test that recently fetched readings are not refreshed."""
        mock_service = MagicMock()
        MockService.return_value = mock_service

        day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        _create_reading(
            day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5,
            text="Recent text",
            text_fetched_at=timezone.now() - timedelta(days=5),
        )

        refresh_all_reading_texts_task()

        # API should not have been called since reading is recent
        mock_service.get_passage.assert_not_called()

    @patch('hub.tasks.bible_api_tasks.BibleAPIService')
    def test_one_api_call_per_reading_for_fums_compliance(self, MockService):
        """Test that each reading gets its own API call for a unique FUMS token."""
        mock_service = MagicMock()
        mock_service.get_passage.return_value = self.mock_api_response
        MockService.return_value = mock_service

        # Create three readings with the same passage on different days
        for i in range(3):
            day = Day.objects.create(
                date=date(2025, 3, 15) + timedelta(days=i * 365),
                church=self.church,
            )
            _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        refresh_all_reading_texts_task()

        # Three API calls should have been made (one per reading)
        self.assertEqual(mock_service.get_passage.call_count, 3)

        # All three readings should have text
        readings = Reading.objects.filter(book="Genesis", start_chapter=1, start_verse=1)
        for reading in readings:
            self.assertEqual(reading.text, "Test verse content.")

    @patch('hub.tasks.bible_api_tasks.BibleAPIService')
    def test_refresh_only_updates_stale_readings(self, MockService):
        """Test that refresh only updates stale readings, leaving fresh ones unchanged."""
        mock_service = MagicMock()
        mock_service.get_passage.return_value = self.mock_api_response
        MockService.return_value = mock_service

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

    @override_settings(MAX_READINGS=10)
    @patch('hub.tasks.bible_api_tasks.BibleAPIService')
    def test_cleanup_old_readings_when_over_max(self, MockService):
        """Test that oldest readings are deleted when count exceeds MAX_READINGS."""
        mock_service = MagicMock()
        mock_service.get_passage.return_value = self.mock_api_response
        MockService.return_value = mock_service

        # Create 15 readings (exceeds MAX_READINGS=10)
        base_date = date(2020, 1, 1)
        for i in range(15):
            day = Day.objects.create(
                date=base_date + timedelta(days=i),
                church=self.church,
            )
            _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=i + 1)

        self.assertEqual(Reading.objects.count(), 15)

        refresh_all_reading_texts_task()

        # Should have cleaned up to MAX_READINGS (10)
        self.assertLessEqual(Reading.objects.count(), 10)

    @patch('hub.tasks.bible_api_tasks.BibleAPIService')
    def test_no_stale_readings_skips_refresh(self, MockService):
        """Test that task exits early when no stale readings exist."""
        mock_service = MagicMock()
        MockService.return_value = mock_service

        # Create a recently fetched reading
        day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        _create_reading(
            day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5,
            text="Fresh text",
            text_fetched_at=timezone.now(),
        )

        refresh_all_reading_texts_task()

        mock_service.get_passage.assert_not_called()

    @patch('hub.tasks.bible_api_tasks.BibleAPIService')
    def test_api_failure_logged_in_summary(self, MockService):
        """Test that API failures are collected and logged in the error summary."""
        mock_service = MagicMock()
        mock_service.get_passage.side_effect = Exception("API timeout")
        MockService.return_value = mock_service

        day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        # Should not raise despite API failure
        with self.assertLogs("hub.tasks.bible_api_tasks", level="WARNING") as log:
            refresh_all_reading_texts_task()

        # Check that the failure was logged
        log_output = "\n".join(log.output)
        self.assertIn("Failed to fetch", log_output)

    @patch('hub.tasks.bible_api_tasks.BibleAPIService')
    def test_no_api_key_aborts_refresh(self, MockService):
        """Test that refresh aborts gracefully when API key is missing."""
        MockService.side_effect = ValueError("API key required.")

        day = Day.objects.create(date=date(2025, 3, 15), church=self.church)
        _create_reading(day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5)

        # Should not raise
        refresh_all_reading_texts_task()


# ------------------------------------------------------------------ #
#  Signal Tests
# ------------------------------------------------------------------ #

@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class ReadingTextSignalTests(TestCase):
    """Tests for the Reading post_save signal that triggers text fetch."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2025, 4, 1), church=self.church)

    @patch('hub.signals.fetch_reading_text_task.delay')
    def test_signal_triggers_on_reading_creation(self, mock_task_delay):
        """Test that signal triggers fetch task when Reading is created."""
        # Disconnect and manually trigger to isolate signal behavior
        _disconnect_reading_signal()

        reading = Reading.objects.create(
            day=self.day,
            book="Matthew",
            start_chapter=5,
            start_verse=1,
            end_chapter=5,
            end_verse=12,
        )

        # Manually trigger the signal handler
        handle_reading_save(sender=Reading, instance=reading, created=True)

        _reconnect_reading_signal()

        mock_task_delay.assert_called_once_with(reading.id)

    @patch('hub.signals.fetch_reading_text_task.delay')
    def test_signal_does_not_trigger_if_text_exists(self, mock_task_delay):
        """Test that signal does not trigger if reading already has text."""
        _disconnect_reading_signal()

        reading = Reading.objects.create(
            day=self.day,
            book="Matthew",
            start_chapter=5,
            start_verse=1,
            end_chapter=5,
            end_verse=12,
            text="Pre-existing text",
        )

        handle_reading_save(sender=Reading, instance=reading, created=True)

        _reconnect_reading_signal()

        mock_task_delay.assert_not_called()

    @patch('hub.signals.fetch_reading_text_task.delay')
    def test_signal_does_not_trigger_on_update(self, mock_task_delay):
        """Test that signal does not trigger when existing reading is updated."""
        _disconnect_reading_signal()

        reading = Reading.objects.create(
            day=self.day,
            book="Matthew",
            start_chapter=5,
            start_verse=1,
            end_chapter=5,
            end_verse=12,
        )

        mock_task_delay.reset_mock()

        # Simulate an update (created=False)
        handle_reading_save(sender=Reading, instance=reading, created=False)

        _reconnect_reading_signal()

        mock_task_delay.assert_not_called()


# ------------------------------------------------------------------ #
#  API Response Tests
# ------------------------------------------------------------------ #

class ReadingTextAPIResponseTests(TestCase):
    """Tests for text fields in the GetDailyReadingsForDate API response."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 5, 1)

    @patch('hub.views.readings.scrape_readings')
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

    @patch('hub.views.readings.scrape_readings')
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


# ------------------------------------------------------------------ #
#  _fetch_and_update_reading unit tests
# ------------------------------------------------------------------ #

class FetchAndUpdateReadingTests(TestCase):
    """Tests for the _fetch_and_update_reading helper function."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.day = Day.objects.create(date=date(2025, 6, 1), church=self.church)

    @patch('hub.tasks.bible_api_tasks.BibleAPIService.resolve_book_name', return_value="GEN")
    def test_updates_single_reading(self, mock_resolve):
        """Test that _fetch_and_update_reading updates only the given reading."""
        mock_api_response = {
            "content": "Verse text here.",
            "copyright": "Copyright info.",
            "version": "NKJV",
            "reference": "Genesis 1:1-5",
            "fums_token": "unique-fums-token",
        }

        reading = _create_reading(self.day, book="Genesis")

        service = MagicMock(spec=BibleAPIService)
        service.get_passage.return_value = mock_api_response

        _fetch_and_update_reading(service, reading)

        reading.refresh_from_db()
        self.assertEqual(reading.text, "Verse text here.")
        self.assertEqual(reading.fums_token, "unique-fums-token")

    @patch('hub.tasks.bible_api_tasks.BibleAPIService.resolve_book_name', return_value="GEN")
    def test_does_not_update_other_readings(self, mock_resolve):
        """Test that _fetch_and_update_reading does not touch other readings with the same passage."""
        mock_api_response = {
            "content": "Verse text here.",
            "copyright": "Copyright info.",
            "version": "NKJV",
            "reference": "Genesis 1:1-5",
            "fums_token": "unique-fums-token",
        }

        day2 = Day.objects.create(date=date(2026, 6, 1), church=self.church)
        reading1 = _create_reading(self.day, book="Genesis")
        reading2 = _create_reading(day2, book="Genesis")

        service = MagicMock(spec=BibleAPIService)
        service.get_passage.return_value = mock_api_response

        _fetch_and_update_reading(service, reading1)

        reading1.refresh_from_db()
        reading2.refresh_from_db()
        self.assertEqual(reading1.text, "Verse text here.")
        self.assertEqual(reading2.text, "")

    def test_unknown_book_raises_value_error(self):
        """Test that _fetch_and_update_reading raises ValueError for unknown books."""
        reading = _create_reading(self.day, book="Nonexistent Book")
        service = MagicMock(spec=BibleAPIService)

        with self.assertRaises(ValueError):
            _fetch_and_update_reading(service, reading)
