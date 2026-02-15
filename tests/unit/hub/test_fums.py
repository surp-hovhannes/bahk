"""Tests for FUMS (Fair Use Management System) token capture and serving."""
import datetime
from unittest.mock import Mock, patch

from django.db.models.signals import post_save
from django.test import TestCase
from django.utils import timezone

from hub.admin import ReadingAdmin
from hub.models import Church, Day, Fast, Reading
from hub.services.bible_api_service import BibleAPIService
from hub.signals import handle_reading_save


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
        return Reading.objects.create(
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


class BibleAPIServiceFumsTests(TestCase):
    """Tests for FUMS token extraction in BibleAPIService."""

    def setUp(self):
        self.mock_response_json = {
            "data": {
                "reference": "Genesis 1:1-5",
                "content": "In the beginning God created...",
                "copyright": "Scripture taken from NKJV...",
            },
            "meta": {
                "fumsToken": "test-fums-token-abc123",
            },
        }

    @patch("hub.services.bible_api_service.requests.Session")
    def test_get_passage_extracts_fums_token(self, mock_session_cls):
        """get_passage should return fums_token from meta.fumsToken."""
        mock_resp = Mock()
        mock_resp.json.return_value = self.mock_response_json
        mock_resp.raise_for_status = Mock()
        mock_session_cls.return_value.get.return_value = mock_resp

        service = BibleAPIService(api_key="fake-key")
        result = service.get_passage("GEN", 1, 1, 1, 5)

        self.assertEqual(result["fums_token"], "test-fums-token-abc123")

    @patch("hub.services.bible_api_service.requests.Session")
    def test_get_passage_fums_token_missing_returns_empty(self, mock_session_cls):
        """get_passage should return empty string when meta.fumsToken is absent."""
        response_no_meta = {
            "data": {
                "reference": "Genesis 1:1",
                "content": "In the beginning...",
                "copyright": "Copyright...",
            },
        }
        mock_resp = Mock()
        mock_resp.json.return_value = response_no_meta
        mock_resp.raise_for_status = Mock()
        mock_session_cls.return_value.get.return_value = mock_resp

        service = BibleAPIService(api_key="fake-key")
        result = service.get_passage("GEN", 1, 1, 1, 1)

        self.assertEqual(result["fums_token"], "")

    @patch("hub.services.bible_api_service.requests.Session")
    def test_get_passage_sends_fums_version_param(self, mock_session_cls):
        """get_passage should include fums-version=3 in request params."""
        mock_resp = Mock()
        mock_resp.json.return_value = self.mock_response_json
        mock_resp.raise_for_status = Mock()
        mock_session = mock_session_cls.return_value
        mock_session.get.return_value = mock_resp

        service = BibleAPIService(api_key="fake-key")
        service.get_passage("GEN", 1, 1, 1, 5)

        call_kwargs = mock_session.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        self.assertEqual(params["fums-version"], "3")


class ReadingFumsTokenAPITests(TestCase):
    """Tests for FUMS token in the readings API response."""

    def setUp(self):
        # Use the default church so the anonymous readings view finds our Day
        self.church = Church.objects.get_or_create(
            name="Armenian Apostolic Church"
        )[0]
        self.day = Day.objects.create(
            date=timezone.now().date(), church=self.church
        )

    @patch("hub.views.readings.scrape_readings")
    @patch("hub.views.readings.generate_reading_context_task")
    def test_readings_api_includes_fums_token(self, mock_gen_task, mock_scrape):
        """The readings API should include fumsToken in each reading."""
        mock_scrape.return_value = []
        _create_reading(
            day=self.day,
            book="Genesis",
            text="In the beginning...",
            text_copyright="Copyright NKJV",
            text_version="NKJV",
            fums_token="api-fums-token-xyz",
        )

        date_str = self.day.date.strftime("%Y-%m-%d")
        response = self.client.get(f"/api/readings/?date={date_str}")

        self.assertEqual(response.status_code, 200)
        readings = response.json()["readings"]
        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0]["fumsToken"], "api-fums-token-xyz")

    @patch("hub.views.readings.scrape_readings")
    @patch("hub.views.readings.generate_reading_context_task")
    def test_readings_api_fums_token_empty_when_not_set(self, mock_gen_task, mock_scrape):
        """fumsToken should be empty string when no FUMS token is stored."""
        mock_scrape.return_value = []
        other_date = timezone.now().date() - datetime.timedelta(days=1)
        other_day = Day.objects.create(date=other_date, church=self.church)
        _create_reading(day=other_day, book="Psalms", start_ch=23, end_ch=23, end_v=6)

        date_str = other_day.date.strftime("%Y-%m-%d")
        response = self.client.get(f"/api/readings/?date={date_str}")

        self.assertEqual(response.status_code, 200)
        readings = response.json()["readings"]
        self.assertEqual(readings[0]["fumsToken"], "")


class ReadingAdminFumsTests(TestCase):
    """Tests for the has_fums_token admin display method."""

    def setUp(self):
        self.admin = ReadingAdmin(model=Reading, admin_site=None)

    def test_has_fums_token_true(self):
        """has_fums_token returns True when a token is present."""
        reading = Reading(fums_token="some-token")
        self.assertTrue(self.admin.has_fums_token(reading))

    def test_has_fums_token_false_when_empty(self):
        """has_fums_token returns False when token is empty string."""
        reading = Reading(fums_token="")
        self.assertFalse(self.admin.has_fums_token(reading))

    def test_has_fums_token_false_when_default(self):
        """has_fums_token returns False for a new Reading with default token."""
        church = Church.objects.create(name="Admin Test Church")
        day = Day.objects.create(
            date=timezone.now().date(), church=church
        )
        reading = _create_reading(day=day, book="Psalms", start_ch=23, end_ch=23, end_v=6)
        self.assertFalse(self.admin.has_fums_token(reading))


class FetchAndUpdateReadingFumsTests(TestCase):
    """Tests for FUMS token storage in bible_api_tasks."""

    def setUp(self):
        self.church = Church.objects.create(name="Task Test Church")
        self.fast = Fast.objects.create(
            name="Test Fast", church=self.church, description="desc"
        )
        self.day = Day.objects.create(
            date=timezone.now().date(), fast=self.fast, church=self.church
        )

    def test_fetch_and_update_reading_stores_fums_token(self):
        """_fetch_and_update_reading should store the FUMS token on the reading."""
        from hub.tasks.bible_api_tasks import _fetch_and_update_reading

        mock_service = Mock()
        mock_service.resolve_book_name.return_value = "GEN"
        mock_service.get_passage.return_value = {
            "reference": "Genesis 1:1-5",
            "content": "In the beginning...",
            "copyright": "Copyright NKJV",
            "version": "NKJV",
            "fums_token": "task-fums-token-999",
        }

        reading = _create_reading(day=self.day)

        _fetch_and_update_reading(mock_service, reading)

        reading.refresh_from_db()
        self.assertEqual(reading.fums_token, "task-fums-token-999")

    @patch("hub.tasks.bible_api_tasks.BibleAPIService")
    def test_each_reading_gets_own_fums_token(self, MockService):
        """Each reading should get its own API call and unique FUMS token."""
        from hub.tasks.bible_api_tasks import fetch_reading_text_task

        mock_service = Mock()
        mock_service.get_passage.return_value = {
            "reference": "Genesis 1:1-5",
            "content": "In the beginning...",
            "copyright": "Copyright NKJV",
            "version": "NKJV",
            "fums_token": "new-unique-fums-token",
        }
        MockService.return_value = mock_service

        # Create an existing reading with text already fetched
        _create_reading(
            day=self.day,
            text="In the beginning...",
            text_copyright="Copyright",
            text_version="NKJV",
            text_fetched_at=timezone.now(),
            fums_token="existing-fums-token",
        )

        # Create a second day/reading with same passage
        day2 = Day.objects.create(
            date=timezone.now().date() + datetime.timedelta(days=1),
            fast=self.fast,
            church=self.church,
        )
        new_reading = _create_reading(day=day2)

        fetch_reading_text_task(new_reading.pk)

        new_reading.refresh_from_db()
        # Should have its own token from the API call, not the copied one
        self.assertEqual(new_reading.fums_token, "new-unique-fums-token")
        mock_service.get_passage.assert_called_once()
