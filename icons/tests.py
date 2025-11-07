"""Tests for the icons app."""
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase
from rest_framework import status

from hub.models import Church
from icons.models import Icon


class IconModelTests(TestCase):
    """Tests for the Icon model."""
    
    def setUp(self):
        """Set up test data."""
        self.church = Church.objects.create(name="Test Church")
    
    def test_icon_creation(self):
        """Test creating an icon."""
        # Create a simple test image
        test_image = SimpleUploadedFile(
            name='test_icon.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        
        icon = Icon.objects.create(
            title="Test Icon",
            church=self.church,
            image=test_image
        )
        
        self.assertEqual(icon.title, "Test Icon")
        self.assertEqual(icon.church, self.church)
        self.assertIsNotNone(icon.created_at)
        self.assertIsNotNone(icon.updated_at)
    
    def test_icon_string_representation(self):
        """Test the string representation of an icon."""
        test_image = SimpleUploadedFile(
            name='test_icon.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        
        icon = Icon.objects.create(
            title="Test Icon",
            church=self.church,
            image=test_image
        )
        
        self.assertEqual(str(icon), "Test Icon")
    
    def test_icon_tags(self):
        """Test adding tags to an icon."""
        test_image = SimpleUploadedFile(
            name='test_icon.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        
        icon = Icon.objects.create(
            title="Test Icon",
            church=self.church,
            image=test_image
        )
        icon.tags.add("cross", "saint")
        
        self.assertEqual(icon.tags.count(), 2)
        self.assertIn("cross", [tag.name for tag in icon.tags.all()])


class IconAPITests(APITestCase):
    """Tests for the Icon API endpoints."""
    
    def setUp(self):
        """Set up test data."""
        self.church = Church.objects.create(name="Test Church")
        
        # Create test icons
        test_image = SimpleUploadedFile(
            name='test_icon.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        
        self.icon1 = Icon.objects.create(
            title="Nativity Icon",
            church=self.church,
            image=test_image
        )
        self.icon1.tags.add("nativity", "christmas")
        
        test_image2 = SimpleUploadedFile(
            name='test_icon2.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        
        self.icon2 = Icon.objects.create(
            title="Resurrection Icon",
            church=self.church,
            image=test_image2
        )
        self.icon2.tags.add("resurrection", "easter")
    
    def test_list_icons(self):
        """Test listing all icons."""
        response = self.client.get('/api/icons/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)
    
    def test_retrieve_icon(self):
        """Test retrieving a specific icon."""
        response = self.client.get(f'/api/icons/{self.icon1.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], "Nativity Icon")
    
    def test_filter_by_church(self):
        """Test filtering icons by church."""
        response = self.client.get(f'/api/icons/?church={self.church.id}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)
    
    def test_filter_by_tags(self):
        """Test filtering icons by tags."""
        response = self.client.get('/api/icons/?tags=nativity')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['title'], "Nativity Icon")
    
    def test_search_icons(self):
        """Test searching icons by title."""
        response = self.client.get('/api/icons/?search=nativity')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
    
    def test_icon_match_endpoint(self):
        """Test the AI-powered icon matching endpoint."""
        data = {
            'prompt': 'Icon showing the birth of Jesus',
            'return_format': 'id',
            'max_results': 1
        }
        response = self.client.post('/api/icons/match/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('matches', response.data)
    
    def test_icon_match_requires_prompt(self):
        """Test that icon matching requires a prompt."""
        data = {
            'return_format': 'id'
        }
        response = self.client.post('/api/icons/match/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
