"""Tests for Armenian Bible text scraping from sacredtradition.am.

Tests cover:
    - scrape_armenian_reading_texts (HTML parsing, chapter/verse extraction)
    - fetch_armenian_reading_text_task (task behavior, matching, error handling)
"""
from datetime import date
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings

from hub.models import Church, Day, Reading
from hub.tasks.armenian_text_tasks import fetch_armenian_reading_text_task
from hub.utils import scrape_armenian_reading_texts


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


# Sample HTML mimicking sacredtradition.am Armenian readings page (single reading)
SINGLE_READING_HTML = """
<html><body>
<td>
<!--body-->
<div class="dsound">16 date info</div>
<div class="dname">Feast name</div>
<center><hr color="#152451" width="80%" size="1"></center>
<br><b>\u0535\u057d\u0561\u0575\u0578\u0582 \u0574\u0561\u0580\u0563\u0561\u0580\u0565\u0578\u0582\u0569\u0575\u0578\u0582\u0576\u0568 1.16-20</b><br><br>
16 \u053c\u0578\u0582\u0561\u0581\u0578\u0582\u0565\u0581\u0567\u055b\u0584, \u0574\u0561\u0584\u0580\u0578\u0582\u0565\u0581\u0567\u055b\u0584 test verse text.
<br><br><center><hr color="#152451" width="70%" size="1"></center><br><br>
<!--/body-->
</td>
</body></html>
""".encode("utf-8")

# Sample HTML with multiple readings
MULTI_READING_HTML = """
<html><body>
<td>
<!--body-->
<div class="dsound">15 date info</div>
<div class="dname">Feast name</div>
<center><hr color="#152451" width="80%" size="1"></center>
<br><b>\u0531\u057e\u0565\u057f\u0561\u0580\u0561\u0576 \u0568\u057d\u057f \u0544\u0561\u057f\u0569\u0565\u0578\u057d\u056b 6.22-33</b><br><br>
22 First reading Armenian text here.
<br><br><center><hr color="#152451" width="70%" size="1"></center><br><br>
<b>\u0535\u057d\u0561\u0575\u0578\u0582 \u0574\u0561\u0580\u0563\u0561\u0580\u0565\u0578\u0582\u0569\u0575\u0578\u0582\u0576\u0568 58.1-14</b><br><br>
1 Second reading Armenian text here.
<br><br><center><hr color="#152451" width="70%" size="1"></center><br><br>
<b>\u054a\u0578\u0572\u0578\u057d \u0561\u057c\u0561\u0584\u0575\u0561\u056c\u056b \u0569\u0578\u0582\u0572\u0569\u0568 13.11-14.26</b><br><br>
11 Third reading cross-chapter text.
<br><br>14 1 Chapter 14 continuation.
<br><br><center><hr color="#152451" width="70%" size="1"></center><br><br>
<!--/body-->
</td>
</body></html>
""".encode("utf-8")

# HTML with no readings (empty body)
EMPTY_READINGS_HTML = """
<html><body>
<td>
<!--body-->
<div class="dsound">17 date info</div>
<div class="dname">Feast name</div>
<center><hr color="#152451" width="80%" size="1"></center>
<br><br>
<!--/body-->
</td>
</body></html>
""".encode("utf-8")


def _mock_urlopen(html_bytes):
    """Create a mock response object that returns the given HTML bytes."""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = html_bytes
    return mock_response


# ------------------------------------------------------------------ #
#  scrape_armenian_reading_texts Tests
# ------------------------------------------------------------------ #

class ScrapeArmenianReadingTextsTests(TestCase):
    """Tests for the scrape_armenian_reading_texts utility function."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2026, 2, 16)

    @patch("hub.utils.urllib.request.urlopen")
    def test_single_reading_parsed(self, mock_urlopen):
        """Test that a single reading's text is extracted correctly."""
        mock_urlopen.return_value = _mock_urlopen(SINGLE_READING_HTML)

        results = scrape_armenian_reading_texts(self.test_date, self.church)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["start_chapter"], 1)
        self.assertEqual(results[0]["start_verse"], 16)
        self.assertEqual(results[0]["end_chapter"], 1)
        self.assertEqual(results[0]["end_verse"], 20)
        self.assertIn("\u053c\u0578\u0582\u0561\u0581\u0578\u0582\u0565\u0581\u0567", results[0]["text_hy"])
        # Verse number should be wrapped in brackets
        self.assertTrue(results[0]["text_hy"].startswith("[16]"))

    @patch("hub.utils.urllib.request.urlopen")
    def test_multiple_readings_parsed(self, mock_urlopen):
        """Test that multiple readings are all extracted."""
        mock_urlopen.return_value = _mock_urlopen(MULTI_READING_HTML)

        results = scrape_armenian_reading_texts(self.test_date, self.church)

        self.assertEqual(len(results), 3)

        # First reading: Matthew 6:22-33
        self.assertEqual(results[0]["start_chapter"], 6)
        self.assertEqual(results[0]["start_verse"], 22)
        self.assertEqual(results[0]["end_chapter"], 6)
        self.assertEqual(results[0]["end_verse"], 33)
        self.assertIn("First reading Armenian text", results[0]["text_hy"])

        # Second reading: Isaiah 58:1-14
        self.assertEqual(results[1]["start_chapter"], 58)
        self.assertEqual(results[1]["start_verse"], 1)
        self.assertEqual(results[1]["end_chapter"], 58)
        self.assertEqual(results[1]["end_verse"], 14)
        self.assertIn("Second reading Armenian text", results[1]["text_hy"])

        # Third reading: Romans 13:11-14:26 (cross-chapter)
        self.assertEqual(results[2]["start_chapter"], 13)
        self.assertEqual(results[2]["start_verse"], 11)
        self.assertEqual(results[2]["end_chapter"], 14)
        self.assertEqual(results[2]["end_verse"], 26)
        self.assertIn("Third reading cross-chapter text", results[2]["text_hy"])

    @patch("hub.utils.urllib.request.urlopen")
    def test_empty_page_returns_empty_list(self, mock_urlopen):
        """Test that a page with no readings returns an empty list."""
        mock_urlopen.return_value = _mock_urlopen(EMPTY_READINGS_HTML)

        results = scrape_armenian_reading_texts(self.test_date, self.church)

        self.assertEqual(results, [])

    @patch("hub.utils.urllib.request.urlopen")
    def test_url_error_returns_empty_list(self, mock_urlopen):
        """Test that a URL error returns an empty list."""
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        results = scrape_armenian_reading_texts(self.test_date, self.church)

        self.assertEqual(results, [])

    @patch("hub.utils.urllib.request.urlopen")
    def test_non_200_status_returns_empty_list(self, mock_urlopen):
        """Test that a non-200 response returns an empty list."""
        mock_response = MagicMock()
        mock_response.status = 500
        mock_urlopen.return_value = mock_response

        results = scrape_armenian_reading_texts(self.test_date, self.church)

        self.assertEqual(results, [])

    def test_unsupported_church_returns_empty_list(self):
        """Test that an unsupported church returns an empty list."""
        unsupported_church = Church.objects.create(name="Unsupported Church")

        results = scrape_armenian_reading_texts(self.test_date, unsupported_church)

        self.assertEqual(results, [])

    @patch("hub.utils.urllib.request.urlopen")
    def test_html_tags_stripped_from_text(self, mock_urlopen):
        """Test that HTML tags are properly stripped from verse text."""
        mock_urlopen.return_value = _mock_urlopen(SINGLE_READING_HTML)

        results = scrape_armenian_reading_texts(self.test_date, self.church)

        self.assertEqual(len(results), 1)
        # Should not contain any HTML tags
        self.assertNotIn("<br>", results[0]["text_hy"])
        self.assertNotIn("<b>", results[0]["text_hy"])

    @patch("hub.utils.urllib.request.urlopen")
    def test_verse_numbers_wrapped_in_brackets(self, mock_urlopen):
        """Test that verse numbers are wrapped in brackets before Armenian text."""
        # HTML with multiple Armenian verses (verse 16, 17, 18)
        multi_verse_html = (
            '<html><body><td><!--body-->'
            '<div class="dsound">16 date</div>'
            '<div class="dname">Feast</div>'
            '<center><hr color="#152451" width="80%" size="1"></center>'
            '<br><b>\u0535\u057d\u0561\u0575\u0578\u0582 1.16-18</b><br><br>'
            '16 \u053c\u0578\u0582\u0561\u0581\u0578\u0582\u0565\u0581\u0567\u055b\u0584 '
            '17 \u054d\u0578\u057e\u0578\u0580\u0565\u0581\u0567\u0584 '
            '18 \u0535\u056f\u0567\u0584'
            '<br><br><center><hr color="#152451" width="70%" size="1"></center>'
            '<br><br><!--/body--></td></body></html>'
        ).encode("utf-8")
        mock_urlopen.return_value = _mock_urlopen(multi_verse_html)

        results = scrape_armenian_reading_texts(self.test_date, self.church)

        self.assertEqual(len(results), 1)
        text = results[0]["text_hy"]
        # All verse numbers should be bracketed
        self.assertIn("[16]", text)
        self.assertIn("[17]", text)
        self.assertIn("[18]", text)
        # No bare verse numbers (number followed by Armenian without brackets)
        self.assertNotRegex(text, r"(?<!\[)16(?!\])")
        self.assertNotRegex(text, r"(?<!\[)17(?!\])")

    @patch("hub.utils.urllib.request.urlopen")
    def test_correct_url_constructed(self, mock_urlopen):
        """Test that the correct URL is constructed for the Armenian page."""
        mock_urlopen.return_value = _mock_urlopen(EMPTY_READINGS_HTML)

        scrape_armenian_reading_texts(date(2026, 2, 16), self.church)

        args = mock_urlopen.call_args
        url = args[0][0]
        self.assertIn("iL=0", url)
        self.assertIn("ymd=20260216", url)


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

    @patch("hub.utils.scrape_armenian_reading_texts")
    def test_updates_text_hy_on_matching_reading(self, mock_scrape):
        """Test that task updates text_hy and metadata when a matching reading is found."""
        mock_scrape.return_value = [
            {
                "start_chapter": 1,
                "start_verse": 16,
                "end_chapter": 1,
                "end_verse": 20,
                "text_hy": "Armenian verse text for Isaiah 1:16-20",
            }
        ]

        reading = _create_reading(
            self.day, book="Isaiah", start_ch=1, start_v=16, end_ch=1, end_v=20,
        )

        fetch_armenian_reading_text_task(reading.id)

        reading.refresh_from_db()
        self.assertEqual(reading.text_hy, "Armenian verse text for Isaiah 1:16-20")
        # Metadata should be populated
        self.assertEqual(reading.text_hy_version, "\u0546\u0578\u0580 \u0537\u057b\u0574\u056b\u0561\u056e\u056b\u0576")
        self.assertIsNotNone(reading.text_hy_fetched_at)
        self.assertEqual(reading.text_hy_copyright, "")
        self.assertEqual(reading.text_hy_fums_token, "")

    @patch("hub.utils.scrape_armenian_reading_texts")
    def test_no_match_leaves_text_hy_empty(self, mock_scrape):
        """Test that task leaves text_hy empty when no matching reading is found."""
        mock_scrape.return_value = [
            {
                "start_chapter": 5,
                "start_verse": 1,
                "end_chapter": 5,
                "end_verse": 12,
                "text_hy": "Different passage text",
            }
        ]

        reading = _create_reading(
            self.day, book="Isaiah", start_ch=1, start_v=16, end_ch=1, end_v=20,
        )

        fetch_armenian_reading_text_task(reading.id)

        reading.refresh_from_db()
        self.assertFalse(reading.text_hy)

    @patch("hub.utils.scrape_armenian_reading_texts")
    def test_empty_scrape_result(self, mock_scrape):
        """Test that task handles empty scrape results gracefully."""
        mock_scrape.return_value = []

        reading = _create_reading(
            self.day, book="Genesis", start_ch=1, start_v=1, end_ch=1, end_v=5,
        )

        fetch_armenian_reading_text_task(reading.id)

        reading.refresh_from_db()
        self.assertFalse(reading.text_hy)

    def test_nonexistent_reading_handled(self):
        """Test that task handles nonexistent reading gracefully."""
        # Should not raise
        fetch_armenian_reading_text_task(99999)

    @patch("hub.utils.scrape_armenian_reading_texts")
    def test_multiple_readings_correct_match(self, mock_scrape):
        """Test that task matches the correct reading among multiple results."""
        mock_scrape.return_value = [
            {
                "start_chapter": 6,
                "start_verse": 22,
                "end_chapter": 6,
                "end_verse": 33,
                "text_hy": "Matthew Armenian text",
            },
            {
                "start_chapter": 58,
                "start_verse": 1,
                "end_chapter": 58,
                "end_verse": 14,
                "text_hy": "Isaiah Armenian text",
            },
        ]

        reading = _create_reading(
            self.day, book="Isaiah", start_ch=58, start_v=1, end_ch=58, end_v=14,
        )

        fetch_armenian_reading_text_task(reading.id)

        reading.refresh_from_db()
        self.assertEqual(reading.text_hy, "Isaiah Armenian text")
