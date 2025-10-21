"""Tests for patristic quotes feature."""
import hashlib
from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status

from hub.models import Church, Fast, PatristicQuote


class PatristicQuoteModelTests(TestCase):
    """Test cases for PatristicQuote model."""
    
    def setUp(self):
        """Set up test data."""
        self.church1 = Church.objects.create(name='Test Church 1')
        self.church2 = Church.objects.create(name='Test Church 2')
        self.fast1 = Fast.objects.create(
            name='Test Fast 1',
            church=self.church1
        )
        self.fast2 = Fast.objects.create(
            name='Test Fast 2',
            church=self.church1
        )
    
    def test_create_quote(self):
        """Test creating a basic patristic quote."""
        quote = PatristicQuote.objects.create(
            text='This is a test quote about **prayer** and fasting.',
            attribution='St. John Chrysostom'
        )
        quote.churches.add(self.church1)
        
        self.assertEqual(quote.attribution, 'St. John Chrysostom')
        self.assertEqual(quote.churches.count(), 1)
        self.assertEqual(quote.fasts.count(), 0)
    
    def test_quote_multiple_churches(self):
        """Test assigning a quote to multiple churches."""
        quote = PatristicQuote.objects.create(
            text='Quote for multiple churches.',
            attribution='Desert Fathers'
        )
        quote.churches.add(self.church1, self.church2)
        
        self.assertEqual(quote.churches.count(), 2)
        self.assertIn(self.church1, quote.churches.all())
        self.assertIn(self.church2, quote.churches.all())
    
    def test_quote_multiple_fasts(self):
        """Test assigning a quote to multiple fasts."""
        quote = PatristicQuote.objects.create(
            text='Quote for multiple fasts.',
            attribution='St. Basil the Great'
        )
        quote.churches.add(self.church1)
        quote.fasts.add(self.fast1, self.fast2)
        
        self.assertEqual(quote.fasts.count(), 2)
        self.assertIn(self.fast1, quote.fasts.all())
        self.assertIn(self.fast2, quote.fasts.all())
    
    def test_quote_tags(self):
        """Test adding tags to quotes."""
        quote = PatristicQuote.objects.create(
            text='Quote with tags.',
            attribution='St. Augustine'
        )
        quote.churches.add(self.church1)
        quote.tags.add('prayer', 'fasting', 'humility')
        
        self.assertEqual(quote.tags.count(), 3)
        tag_names = [tag.name for tag in quote.tags.all()]
        self.assertIn('prayer', tag_names)
        self.assertIn('fasting', tag_names)
        self.assertIn('humility', tag_names)
    
    def test_quote_translations(self):
        """Test quote translation fields."""
        quote = PatristicQuote.objects.create(
            text='The prayer of the humble pierces the clouds.',
            attribution='Sirach'
        )
        quote.churches.add(self.church1)
        
        # Set Armenian translation
        quote.text_hy = 'Խոնարհի աղոթքը ամպերը ծակում է։'
        quote.attribution_hy = 'Սիրաք'
        quote.save()
        
        # Retrieve and verify
        quote_retrieved = PatristicQuote.objects.get(pk=quote.pk)
        self.assertEqual(quote_retrieved.text_hy, 'Խոնարհի աղոթքը ամպերը ծակում է։')
        self.assertEqual(quote_retrieved.attribution_hy, 'Սիրաք')
    
    def test_quote_str(self):
        """Test string representation of quote."""
        quote = PatristicQuote.objects.create(
            text='This is a longer quote that should be truncated in the string representation to show only the first fifty characters.',
            attribution='St. Gregory'
        )
        quote.churches.add(self.church1)
        
        str_repr = str(quote)
        self.assertIn('St. Gregory', str_repr)
        self.assertLess(len(str_repr.split(' - ')[0]), 60)  # First 50 chars plus ellipsis


class PatristicQuoteAPITests(APITestCase):
    """Test cases for PatristicQuote API endpoints."""
    
    def setUp(self):
        """Set up test data."""
        self.church1 = Church.objects.create(name='Test Church 1')
        self.church2 = Church.objects.create(name='Test Church 2')
        self.fast1 = Fast.objects.create(
            name='Great Fast',
            church=self.church1
        )
        self.fast2 = Fast.objects.create(
            name='Lent',
            church=self.church2
        )
        
        # Create test quotes
        self.quote1 = PatristicQuote.objects.create(
            text='Prayer is the key of the morning and the bolt of the evening.',
            attribution='Mahatma Gandhi'
        )
        self.quote1.churches.add(self.church1)
        self.quote1.fasts.add(self.fast1)
        self.quote1.tags.add('prayer', 'discipline')
        
        self.quote2 = PatristicQuote.objects.create(
            text='Fasting is the first weapon to fight the devil.',
            attribution='St. John Chrysostom'
        )
        self.quote2.churches.add(self.church1, self.church2)
        self.quote2.fasts.add(self.fast1, self.fast2)
        self.quote2.tags.add('fasting', 'spiritual-warfare')
        
        self.quote3 = PatristicQuote.objects.create(
            text='Humility is the foundation of all virtues.',
            attribution='St. Augustine'
        )
        self.quote3.churches.add(self.church2)
        self.quote3.tags.add('humility', 'virtue')
    
    def test_list_quotes(self):
        """Test listing all patristic quotes."""
        url = reverse('patristic-quote-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 3)
    
    def test_retrieve_quote(self):
        """Test retrieving a single quote."""
        url = reverse('patristic-quote-detail', kwargs={'pk': self.quote1.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['attribution'], 'Mahatma Gandhi')
        self.assertIn('prayer', response.data['tags'])
    
    def test_filter_by_church(self):
        """Test filtering quotes by church."""
        url = reverse('patristic-quotes-by-church', kwargs={'church_id': self.church1.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)  # quote1 and quote2
    
    def test_filter_by_fast(self):
        """Test filtering quotes by fast."""
        url = reverse('patristic-quotes-by-fast', kwargs={'fast_id': self.fast1.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)  # quote1 and quote2
    
    def test_filter_by_tags(self):
        """Test filtering quotes by tags."""
        url = reverse('patristic-quote-list')
        response = self.client.get(url, {'tags': 'prayer'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['id'], self.quote1.pk)
    
    def test_filter_by_multiple_tags(self):
        """Test filtering quotes by multiple tags."""
        url = reverse('patristic-quote-list')
        response = self.client.get(url, {'tags': 'fasting,spiritual-warfare'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['id'], self.quote2.pk)
    
    def test_search_quotes(self):
        """Test searching quotes by text."""
        url = reverse('patristic-quote-list')
        response = self.client.get(url, {'search': 'prayer'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['id'], self.quote1.pk)
    
    def test_search_by_attribution(self):
        """Test searching quotes by attribution."""
        url = reverse('patristic-quote-list')
        response = self.client.get(url, {'search': 'Augustine'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['id'], self.quote3.pk)


class PatristicQuoteOfTheDayTests(APITestCase):
    """Test cases for the quote of the day endpoint."""
    
    def setUp(self):
        """Set up test data."""
        # Clear cache before each test
        cache.clear()
        
        self.church = Church.objects.create(name='Test Church')
        self.fast = Fast.objects.create(
            name='Great Fast',
            church=self.church
        )
        
        # Create 10 test quotes
        self.quotes = []
        for i in range(10):
            quote = PatristicQuote.objects.create(
                text=f'Test quote number {i} about prayer and fasting.',
                attribution=f'Saint {i}'
            )
            quote.churches.add(self.church)
            quote.fasts.add(self.fast)
            quote.tags.add('prayer', 'fasting')
            self.quotes.append(quote)
    
    def test_quote_of_the_day_basic(self):
        """Test basic quote of the day functionality."""
        url = reverse('patristic-quote-of-the-day')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('text', response.data)
        self.assertIn('attribution', response.data)
    
    def test_quote_of_the_day_deterministic(self):
        """Test that the same quote is returned for the same day."""
        url = reverse('patristic-quote-of-the-day')
        
        # Make multiple requests
        response1 = self.client.get(url)
        response2 = self.client.get(url)
        response3 = self.client.get(url)
        
        # All responses should return the same quote
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        self.assertEqual(response1.data['id'], response2.data['id'])
        self.assertEqual(response1.data['id'], response3.data['id'])
    
    def test_quote_of_the_day_with_fast_filter(self):
        """Test quote of the day filtered by fast."""
        url = reverse('patristic-quote-of-the-day')
        response = self.client.get(url, {'fast_id': self.fast.pk})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(self.fast.pk, response.data['fasts'])
    
    def test_quote_of_the_day_with_tags_filter(self):
        """Test quote of the day filtered by tags."""
        url = reverse('patristic-quote-of-the-day')
        response = self.client.get(url, {'tags': 'prayer'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('prayer', response.data['tags'])
    
    def test_quote_of_the_day_caching(self):
        """Test that quote of the day is properly cached."""
        url = reverse('patristic-quote-of-the-day')
        
        # First request - should hit database
        response1 = self.client.get(url, {'fast_id': self.fast.pk})
        quote_id_1 = response1.data['id']
        
        # Second request - should come from cache
        response2 = self.client.get(url, {'fast_id': self.fast.pk})
        quote_id_2 = response2.data['id']
        
        # Should be the same quote
        self.assertEqual(quote_id_1, quote_id_2)
    
    @patch('hub.views.patristic_quotes.datetime')
    def test_quote_changes_with_different_dates(self, mock_datetime):
        """Test that different dates return potentially different quotes."""
        url = reverse('patristic-quote-of-the-day')
        
        # Mock today's date
        today = datetime(2024, 1, 1, 12, 0, 0)
        mock_datetime.now.return_value = today
        
        response1 = self.client.get(url, {'fast_id': self.fast.pk})
        quote_id_day1 = response1.data['id']
        
        # Clear cache and mock tomorrow's date
        cache.clear()
        tomorrow = datetime(2024, 1, 2, 12, 0, 0)
        mock_datetime.now.return_value = tomorrow
        
        response2 = self.client.get(url, {'fast_id': self.fast.pk})
        quote_id_day2 = response2.data['id']
        
        # The algorithm might return the same quote by chance, but the mechanism should work
        # We're just verifying that both requests succeed
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
    
    def test_quote_of_the_day_no_matching_quotes(self):
        """Test quote of the day when no quotes match the criteria."""
        url = reverse('patristic-quote-of-the-day')
        
        # Request with tags that don't exist
        response = self.client.get(url, {'tags': 'nonexistent-tag'})
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('detail', response.data)
    
    def test_quote_of_the_day_invalid_fast_id(self):
        """Test quote of the day with invalid fast_id."""
        url = reverse('patristic-quote-of-the-day')
        response = self.client.get(url, {'fast_id': 'invalid'})
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    def test_quote_of_the_day_with_language(self):
        """Test quote of the day with language parameter."""
        # Add Armenian translation to first quote
        quote = self.quotes[0]
        quote.text_hy = 'Թեստ քօթ նում֊բր զէրօ աբոութ փրէյր ընդ ֆաստինգ։'
        quote.attribution_hy = 'Սուրբ զէրօ'
        quote.save()
        
        url = reverse('patristic-quote-of-the-day')
        response = self.client.get(url, {'lang': 'hy'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_deterministic_selection_algorithm(self):
        """Test that the deterministic algorithm works correctly."""
        # Create a specific seed for testing
        date_str = '2024-01-01'
        fast_str = str(self.fast.pk)
        tags_str = 'fasting,prayer'  # Sorted
        
        # Calculate expected index
        seed = f"{date_str}-{fast_str}-{tags_str}"
        hash_object = hashlib.md5(seed.encode())
        hash_int = int(hash_object.hexdigest(), 16)
        
        # Get quotes matching criteria
        queryset = PatristicQuote.objects.filter(
            fasts__id=self.fast.pk,
            tags__name__iexact='prayer'
        ).filter(
            tags__name__iexact='fasting'
        ).distinct().order_by('id')
        
        count = queryset.count()
        expected_index = hash_int % count
        expected_quote = queryset[expected_index]
        
        # Now test the endpoint with mocked date
        from unittest.mock import MagicMock
        with patch('hub.views.patristic_quotes.datetime') as mock_datetime:
            mock_date = datetime(2024, 1, 1, 12, 0, 0)
            mock_now = MagicMock()
            mock_now.date.return_value = mock_date.date()
            mock_datetime.now.return_value = mock_now
            
            url = reverse('patristic-quote-of-the-day')
            response = self.client.get(url, {
                'fast_id': self.fast.pk,
                'tags': 'prayer,fasting'
            })
            
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data['id'], expected_quote.id)

