"""Tests for FUMS (Fair Use Management System) token capture and serving."""
import datetime
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from hub.admin import ReadingAdmin
from hub.models import Church, Day, Fast, PassageText, Reading
from hub.services.bible_api_service import BibleAPIService
from hub.services.reading_text_service import (
    fetch_all_reading_texts,
    get_reading_text_fields,
    load_passage_texts,
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


def _store_text(reading, language="en", **kwargs):
    """Store PassageText for a reading's passage, defaulting to fresh English."""
    defaults = {
        "text": "In the beginning...",
        "version": "NKJV",
        "copyright": "Copyright NKJV",
        "fums_token": "api-fums-token-xyz",
        "fetched_at": timezone.now(),
    }
    defaults.update(kwargs)
    return PassageText.objects.create(
        passage_key=reading.passage_key, language=language, **defaults,
    )


def _fields(reading, lang):
    """Resolve response fields the way the view does."""
    return get_reading_text_fields(
        reading, lang, passage_texts=load_passage_texts([reading.passage_key]),
    )


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

    @patch("hub.services.bible_api_service.requests.Session")
    def test_get_passage_uses_configured_timeout(self, mock_session_cls):
        """get_passage should pass a timeout to the external API call."""
        mock_resp = Mock()
        mock_resp.json.return_value = self.mock_response_json
        mock_resp.raise_for_status = Mock()
        mock_session = mock_session_cls.return_value
        mock_session.get.return_value = mock_resp

        service = BibleAPIService(api_key="fake-key")
        service.get_passage("GEN", 1, 1, 1, 5)

        self.assertEqual(mock_session.get.call_args.kwargs["timeout"], 10)


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

    @patch("hub.views.readings.get_daily_readings")
    @patch("hub.views.readings.generate_reading_context_task")
    def test_readings_api_includes_fums_token(self, mock_gen_task, mock_scrape):
        """The readings API should include fumsToken in each reading."""
        mock_scrape.return_value = []
        reading = _create_reading(day=self.day, book="Genesis")
        # Text older than the freshness cap (or with no timestamp at all) is blanked in
        # the response, so a stored fetched_at is required for the token to be served.
        _store_text(reading)

        date_str = self.day.date.strftime("%Y-%m-%d")
        response = self.client.get(f"/api/readings/?date={date_str}")

        self.assertEqual(response.status_code, 200)
        readings = response.json()["readings"]
        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0]["fumsToken"], "api-fums-token-xyz")

    @patch("hub.views.readings.get_daily_readings")
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


class ReadingAdminPassageTextTests(TestCase):
    """The admin surfaces the PassageText rows a reading is served from."""

    def setUp(self):
        self.admin = ReadingAdmin(model=Reading, admin_site=None)
        self.church = Church.objects.get_or_create(name="Armenian Apostolic Church")[0]
        self.day = Day.objects.create(date=timezone.now().date(), church=self.church)

    def test_summary_reports_token_and_sharing(self):
        reading = _create_reading(day=self.day)
        other_day = Day.objects.create(
            date=timezone.now().date() + datetime.timedelta(days=1), church=self.church,
        )
        _create_reading(day=other_day)  # same passage
        _store_text(reading)

        summary = self.admin.passage_text_summary(reading)

        self.assertIn("FUMS token: yes", summary)
        self.assertIn("Shared by 2 reading(s).", summary)

    def test_summary_when_nothing_retrieved(self):
        reading = _create_reading(day=self.day)
        self.assertIn("not yet retrieved", self.admin.passage_text_summary(reading))

    def test_summary_flags_expired_text(self):
        reading = _create_reading(day=self.day)
        _store_text(reading, fetched_at=timezone.now() - datetime.timedelta(days=31))

        self.assertIn("EXPIRED", self.admin.passage_text_summary(reading))

    def test_summary_when_book_is_unmappable(self):
        reading = _create_reading(day=self.day, book="Not A Real Book")
        self.assertIn("no USFM mapping", self.admin.passage_text_summary(reading))


class FetchEnglishTextFumsTests(TestCase):
    """Tests for FUMS token storage via the English fetch."""

    def setUp(self):
        self.church = Church.objects.create(name="Task Test Church")
        self.fast = Fast.objects.create(
            name="Test Fast", church=self.church, description="desc"
        )
        self.day = Day.objects.create(
            date=timezone.now().date(), fast=self.fast, church=self.church
        )

    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    def test_english_fetch_stores_fums_token(self, mock_resolve):
        """The English fetch should store the FUMS token against the passage."""
        mock_service = Mock(spec=BibleAPIService)
        mock_service.get_passage.return_value = {
            "reference": "Genesis 1:1-5",
            "content": "In the beginning...",
            "copyright": "Copyright NKJV",
            "version": "NKJV",
            "fums_token": "task-fums-token-999",
        }

        reading = _create_reading(day=self.day)

        result = fetch_all_reading_texts(reading, langs=["en"], service=mock_service)

        self.assertTrue(result["en"])
        stored = PassageText.objects.get(passage_key=reading.passage_key, language="en")
        self.assertEqual(stored.fums_token, "task-fums-token-999")

    @patch('hub.services.bible_api_service.BibleAPIService.get_passage')
    @patch('hub.services.bible_api_service.BibleAPIService.resolve_book_name', return_value="GEN")
    @patch('hub.services.bible_api_service.config', return_value="test-key")
    def test_readings_of_one_passage_share_a_token(self, mock_config, mock_resolve, mock_get_passage):
        """Deliberate behaviour change -- do not "fix" this back to one call per row.

        FUMS tracks *displays*, and the app already serves one stored token to unlimited
        users of a reading across the 30-day cache window; the frontend deduplicates per
        device and session, sending a distinct dId/sId/uId with each report.  Sharing a
        token across rows that cite the same passage is the same category of reuse, and
        it is what turns retrieval cost from O(readings) into O(distinct passages).
        """
        mock_get_passage.return_value = {
            "reference": "Genesis 1:1-5",
            "content": "In the beginning...",
            "copyright": "Copyright NKJV",
            "version": "NKJV",
            "fums_token": "shared-fums-token",
        }

        first = _create_reading(day=self.day)
        day2 = Day.objects.create(
            date=timezone.now().date() + datetime.timedelta(days=1),
            fast=self.fast,
            church=self.church,
        )
        second = _create_reading(day=day2)

        self.assertTrue(fetch_all_reading_texts(first, langs=["en"])["en"])

        # One call covers both readings, and both serve the same token.
        mock_get_passage.assert_called_once()
        self.assertEqual(first.passage_key, second.passage_key)
        self.assertEqual(_fields(second, "en")["fumsToken"], "shared-fums-token")
        self.assertEqual(PassageText.objects.filter(language="en").count(), 1)


class ReadingTextExpiryTests(TestCase):
    """Tests for withholding text that is past its source licence's freshness cap.

    API.Bible forbids displaying content cached for more than 30 days.  Text can outlive
    that window whenever a refresh run does not reach a passage or the on-demand spend
    budget is exhausted, so the serve path — not just the refresh path — has to enforce
    the cap.
    """

    def setUp(self):
        self.church = Church.objects.get_or_create(name="Armenian Apostolic Church")[0]
        self.day = Day.objects.create(date=timezone.now().date(), church=self.church)

    def test_fresh_english_text_is_served(self):
        reading = _create_reading(day=self.day)
        _store_text(reading)

        fields = _fields(reading, "en")

        self.assertEqual(fields["text"], "In the beginning...")
        self.assertEqual(fields["fumsToken"], "api-fums-token-xyz")

    def test_expired_english_text_is_blanked(self):
        reading = _create_reading(day=self.day)
        _store_text(reading, fetched_at=timezone.now() - datetime.timedelta(days=31))

        self.assertEqual(
            _fields(reading, "en"),
            {"text": "", "textVersion": "", "textCopyright": "", "fumsToken": ""},
        )

    def test_english_text_just_inside_max_age_is_served(self):
        reading = _create_reading(day=self.day)
        _store_text(reading, fetched_at=timezone.now() - datetime.timedelta(days=29))

        self.assertEqual(_fields(reading, "en")["text"], "In the beginning...")

    def test_missing_timestamp_is_treated_as_expired(self):
        """We cannot show the text is fresh enough to serve, so we do not serve it."""
        reading = _create_reading(day=self.day)
        _store_text(reading, fetched_at=None)

        self.assertEqual(_fields(reading, "en")["text"], "")

    def test_never_retrieved_passage_serves_blank(self):
        reading = _create_reading(day=self.day)

        self.assertEqual(
            _fields(reading, "en"),
            {"text": "", "textVersion": "", "textCopyright": "", "fumsToken": ""},
        )

    def test_armenian_text_never_expires(self):
        """Armenian is composed from a local corpus, so no licence clock applies."""
        reading = _create_reading(day=self.day)
        _store_text(
            reading, language="hy", text="Ի սկզբանէ...", version="Նոր Էջմիածին",
            copyright="", fums_token="",
            fetched_at=timezone.now() - datetime.timedelta(days=400),
        )

        self.assertEqual(_fields(reading, "hy")["text"], "Ի սկզբանէ...")

    def test_expired_english_does_not_suppress_armenian(self):
        """Freshness is tracked per (passage, language), so one cannot mask the other."""
        reading = _create_reading(day=self.day)
        _store_text(reading, fetched_at=timezone.now() - datetime.timedelta(days=31))
        _store_text(reading, language="hy", text="Ի սկզբանէ...", fetched_at=timezone.now())

        self.assertEqual(_fields(reading, "en")["text"], "")
        self.assertEqual(_fields(reading, "hy")["text"], "Ի սկզբանէ...")

    def test_unknown_language_falls_back_to_english_including_expiry(self):
        """An unknown code must not sidestep the English freshness rule."""
        reading = _create_reading(day=self.day)
        _store_text(reading, fetched_at=timezone.now() - datetime.timedelta(days=31))

        self.assertEqual(_fields(reading, "fr")["text"], "")

    @patch("hub.views.readings.fetch_passage_text")
    @patch("hub.views.readings.get_daily_readings", return_value=[])
    @patch("hub.views.readings.generate_reading_context_task")
    def test_readings_api_blanks_expired_text(self, mock_gen_task, mock_scrape, mock_fetch):
        reading = _create_reading(day=self.day)
        _store_text(reading, fetched_at=timezone.now() - datetime.timedelta(days=31))

        response = self.client.get(f"/api/readings/?date={self.day.date:%Y-%m-%d}")

        self.assertEqual(response.status_code, 200)
        reading_data = response.json()["readings"][0]
        self.assertEqual(reading_data["text"], "")
        self.assertEqual(reading_data["fumsToken"], "")
