"""Tests for the scrape_feast utility function."""
from datetime import date
from unittest.mock import Mock, patch
import urllib.error

from django.test import TestCase

from hub.models import Church
from hub.utils import scrape_feast


class ScrapeFeastTests(TestCase):
    """Tests for the scrape_feast utility function."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)

    @patch('urllib.request.urlopen')
    def test_scrape_feast_with_both_translations(self, mock_urlopen):
        """Test scraping feast with both English and Armenian translations."""
        # Mock HTML responses for both languages
        english_html = '''
        <html>
            <div class="dname">Nativity of Jesus Christ</div>
        </html>
        '''
        armenian_html = '''
        <html>
            <div class="dname">Քրիստոսի Ծնունդ</div>
        </html>
        '''

        # Create mock response objects
        def create_mock_response(html_content):
            mock_response = Mock()
            mock_response.status = 200
            mock_response.read.return_value = html_content.encode('utf-8')
            return mock_response

        # Mock urlopen to return different responses for English and Armenian
        mock_urlopen.side_effect = [
            create_mock_response(english_html),  # English (iL=2)
            create_mock_response(armenian_html),  # Armenian (iL=3)
        ]

        result = scrape_feast(self.test_date, self.church)

        # Verify the result
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Nativity of Jesus Christ")
        self.assertEqual(result["name_en"], "Nativity of Jesus Christ")
        self.assertEqual(result["name_hy"], "Քրիստոսի Ծնունդ")

        # Verify both URLs were called (English and Armenian)
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch('urllib.request.urlopen')
    def test_scrape_feast_english_only(self, mock_urlopen):
        """Test scraping feast with only English translation."""
        english_html = '''
        <html>
            <div class="dname">Easter Sunday</div>
        </html>
        '''
        armenian_html = '''
        <html>
            <body>No feast found</body>
        </html>
        '''

        def create_mock_response(html_content):
            mock_response = Mock()
            mock_response.status = 200
            mock_response.read.return_value = html_content.encode('utf-8')
            return mock_response

        mock_urlopen.side_effect = [
            create_mock_response(english_html),
            create_mock_response(armenian_html),
        ]

        result = scrape_feast(self.test_date, self.church)

        # Verify the result
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Easter Sunday")
        self.assertEqual(result["name_en"], "Easter Sunday")
        self.assertIsNone(result["name_hy"])

    @patch('urllib.request.urlopen')
    def test_scrape_feast_armenian_only(self, mock_urlopen):
        """Test scraping feast with only Armenian translation."""
        english_html = '''
        <html>
            <body>No feast found</body>
        </html>
        '''
        armenian_html = '''
        <html>
            <span class="dname">Զատիկ</span>
        </html>
        '''

        def create_mock_response(html_content):
            mock_response = Mock()
            mock_response.status = 200
            mock_response.read.return_value = html_content.encode('utf-8')
            return mock_response

        mock_urlopen.side_effect = [
            create_mock_response(english_html),
            create_mock_response(armenian_html),
        ]

        result = scrape_feast(self.test_date, self.church)

        # Verify the result - should default to Armenian if no English
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Զատիկ")
        self.assertIsNone(result["name_en"])
        self.assertEqual(result["name_hy"], "Զատիկ")

    @patch('urllib.request.urlopen')
    def test_scrape_feast_no_feast_found(self, mock_urlopen):
        """Test scraping when no feast exists for the date."""
        no_feast_html = '''
        <html>
            <body>
                <div>Some other content</div>
            </body>
        </html>
        '''

        def create_mock_response(html_content):
            mock_response = Mock()
            mock_response.status = 200
            mock_response.read.return_value = html_content.encode('utf-8')
            return mock_response

        mock_urlopen.side_effect = [
            create_mock_response(no_feast_html),
            create_mock_response(no_feast_html),
        ]

        result = scrape_feast(self.test_date, self.church)

        # Verify None is returned when no feast found
        self.assertIsNone(result)

    @patch('urllib.request.urlopen')
    def test_scrape_feast_with_nested_tags(self, mock_urlopen):
        """Test scraping feast with nested HTML tags in dname."""
        english_html = '''
        <html>
            <a class="dname"><span>St. John</span> the Baptist</a>
        </html>
        '''
        armenian_html = '''
        <html>
            <div class="dname">Սբ. <b>Հովհաննես</b> Մկրտիչ</div>
        </html>
        '''

        def create_mock_response(html_content):
            mock_response = Mock()
            mock_response.status = 200
            mock_response.read.return_value = html_content.encode('utf-8')
            return mock_response

        mock_urlopen.side_effect = [
            create_mock_response(english_html),
            create_mock_response(armenian_html),
        ]

        result = scrape_feast(self.test_date, self.church)

        # Verify nested HTML tags are stripped
        self.assertIsNotNone(result)
        self.assertEqual(result["name_en"], "St. John the Baptist")
        self.assertEqual(result["name_hy"], "Սբ. Հովհաննես Մկրտիչ")

    @patch('urllib.request.urlopen')
    def test_scrape_feast_url_error(self, mock_urlopen):
        """Test handling of URL errors."""
        mock_urlopen.side_effect = urllib.error.URLError("Connection failed")

        result = scrape_feast(self.test_date, self.church)

        # Verify None is returned on URL error
        self.assertIsNone(result)

    @patch('urllib.request.urlopen')
    def test_scrape_feast_http_error(self, mock_urlopen):
        """Test handling of HTTP error status codes."""
        mock_response = Mock()
        mock_response.status = 404
        mock_urlopen.return_value = mock_response

        result = scrape_feast(self.test_date, self.church)

        # Verify None is returned on HTTP error
        self.assertIsNone(result)

    def test_scrape_feast_unsupported_church(self):
        """Test that unsupported churches return empty result."""
        # Create a different church that's not in SUPPORTED_CHURCHES
        unsupported_church = Church.objects.create(
            name="Unsupported Church"
        )

        result = scrape_feast(self.test_date, unsupported_church)

        # Verify None is returned for unsupported church
        self.assertIsNone(result)

    @patch('urllib.request.urlopen')
    def test_scrape_feast_empty_dname(self, mock_urlopen):
        """Test handling of empty feast name."""
        english_html = '''
        <html>
            <div class="dname">   </div>
        </html>
        '''
        armenian_html = '''
        <html>
            <div class="dname"></div>
        </html>
        '''

        def create_mock_response(html_content):
            mock_response = Mock()
            mock_response.status = 200
            mock_response.read.return_value = html_content.encode('utf-8')
            return mock_response

        mock_urlopen.side_effect = [
            create_mock_response(english_html),
            create_mock_response(armenian_html),
        ]

        result = scrape_feast(self.test_date, self.church)

        # Verify None is returned when feast name is empty
        self.assertIsNone(result)

    @patch('urllib.request.urlopen')
    def test_scrape_feast_date_format(self, mock_urlopen):
        """Test that date is formatted correctly in URL."""
        english_html = '<html><div class="dname">Test Feast</div></html>'

        def create_mock_response(html_content):
            mock_response = Mock()
            mock_response.status = 200
            mock_response.read.return_value = html_content.encode('utf-8')
            return mock_response

        mock_urlopen.side_effect = [
            create_mock_response(english_html),
            create_mock_response(english_html),
        ]

        test_date = date(2025, 1, 5)
        scrape_feast(test_date, self.church)

        # Verify the date format in URL
        calls = mock_urlopen.call_args_list
        # Check first call (English)
        request_obj = calls[0][0][0]
        self.assertIn('ymd=20250105', request_obj.full_url)
        self.assertIn('iL=2', request_obj.full_url)  # English
        
        # Check second call (Armenian)
        request_obj = calls[1][0][0]
        self.assertIn('ymd=20250105', request_obj.full_url)
        self.assertIn('iL=3', request_obj.full_url)  # Armenian

    @patch('urllib.request.urlopen')
    def test_scrape_feast_user_agent_header(self, mock_urlopen):
        """Test that User-Agent header is included in request."""
        english_html = '<html><div class="dname">Test Feast</div></html>'

        def create_mock_response(html_content):
            mock_response = Mock()
            mock_response.status = 200
            mock_response.read.return_value = html_content.encode('utf-8')
            return mock_response

        mock_urlopen.side_effect = [
            create_mock_response(english_html),
            create_mock_response(english_html),
        ]

        scrape_feast(self.test_date, self.church)

        # Verify User-Agent header is present in the request
        calls = mock_urlopen.call_args_list
        for call in calls:
            request_obj = call[0][0]
            self.assertIn('User-agent', request_obj.headers)
            self.assertTrue(request_obj.headers['User-agent'].startswith('Mozilla/5.0'))

