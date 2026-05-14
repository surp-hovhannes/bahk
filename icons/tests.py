"""Tests for the icons app."""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase
from rest_framework import status

from hub.models import Church
from icons.models import Icon, IconFeedback


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


class IconFeedbackAPITests(APITestCase):
    """Tests for the Icon Feedback API endpoint."""

    def setUp(self):
        """Set up test data."""
        self.church = Church.objects.create(name="Test Church")

        test_image = SimpleUploadedFile(
            name='test_icon.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )

        self.icon = Icon.objects.create(
            title="Test Icon",
            church=self.church,
            image=test_image
        )
        self.icon.tags.add("cross", "saint")

        self.feedback_url = f'/api/icons/{self.icon.pk}/feedback/'

    def _valid_payload(self, **overrides):
        payload = {
            'feedback_type': 'mislabel',
            'description': 'This icon is incorrectly labeled.',
        }
        payload.update(overrides)
        return payload

    def test_valid_payload_returns_201(self):
        """Test that a valid feedback submission returns 201."""
        response = self.client.post(
            self.feedback_url,
            self._valid_payload(),
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('message', response.data)

    def test_missing_description_returns_400(self):
        """Test that missing description returns 400."""
        response = self.client.post(
            self.feedback_url,
            {'feedback_type': 'mislabel'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_feedback_type_returns_400(self):
        """Test that invalid feedback_type returns 400."""
        response = self.client.post(
            self.feedback_url,
            self._valid_payload(feedback_type='invalid_type'),
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nonexistent_icon_returns_404(self):
        """Test that feedback for nonexistent icon returns 404."""
        url = '/api/icons/99999/feedback/'
        response = self.client.post(url, self._valid_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_with_valid_email_returns_201(self):
        """Test that a valid email is accepted."""
        response = self.client.post(
            self.feedback_url,
            self._valid_payload(submitter_email='user@example.com'),
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_invalid_email_returns_400(self):
        """Test that invalid email is rejected."""
        response = self.client.post(
            self.feedback_url,
            self._valid_payload(submitter_email='not-an-email'),
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_snapshots_title_and_tags(self):
        """Test that icon title and tags are snapshotted at submission time."""
        response = self.client.post(
            self.feedback_url,
            self._valid_payload(),
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        feedback = IconFeedback.objects.first()
        self.assertEqual(feedback.icon_title_at_time, "Test Icon")
        self.assertIn("cross", feedback.icon_tags_at_time)
        self.assertIn("saint", feedback.icon_tags_at_time)

    def test_suggested_tags_required_when_type_is_suggested_tags(self):
        """Test that suggested_tags is required when feedback_type is 'suggested_tags'."""
        response = self.client.post(
            self.feedback_url,
            self._valid_payload(feedback_type='suggested_tags'),
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('suggested_tags', response.data)

    def test_suggested_tags_empty_string_rejected(self):
        """Test that empty string for suggested_tags is rejected when type is 'suggested_tags'."""
        response = self.client.post(
            self.feedback_url,
            self._valid_payload(
                feedback_type='suggested_tags',
                suggested_tags=''
            ),
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('suggested_tags', response.data)

    def test_ip_anonymization_ipv4(self):
        """Test that IPv4 addresses are anonymized (last octet zeroed)."""
        response = self.client.post(
            self.feedback_url,
            self._valid_payload(),
            format='json',
            REMOTE_ADDR='192.168.1.42'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        feedback = IconFeedback.objects.first()
        self.assertEqual(feedback.ip_address, '192.168.1.0')

    def test_ip_anonymization_ipv6(self):
        """Test that IPv6 addresses are anonymized (preserve /48, zero rest)."""
        response = self.client.post(
            self.feedback_url,
            self._valid_payload(),
            format='json',
            REMOTE_ADDR='2001:db8::1:2:3:4'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        feedback = IconFeedback.objects.first()
        # With /48 masking, the suffix should be zeroed
        self.assertEqual(feedback.ip_address, '2001:db8::')

    def test_no_ip_stored_when_not_provided(self):
        """Test that ip_address is None when REMOTE_ADDR is empty."""
        response = self.client.post(
            self.feedback_url,
            self._valid_payload(),
            format='json',
            REMOTE_ADDR=''
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        feedback = IconFeedback.objects.first()
        self.assertIsNone(feedback.ip_address)

    def test_user_agent_captured(self):
        """Test that HTTP_USER_AGENT is captured on submission."""
        response = self.client.post(
            self.feedback_url,
            self._valid_payload(),
            format='json',
            HTTP_USER_AGENT='TestBot/1.0'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        feedback = IconFeedback.objects.first()
        self.assertEqual(feedback.http_user_agent, 'TestBot/1.0')

    def test_user_agent_defaults_to_empty(self):
        """Test that http_user_agent defaults to empty string when not sent."""
        response = self.client.post(
            self.feedback_url,
            self._valid_payload(),
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        feedback = IconFeedback.objects.first()
        self.assertEqual(feedback.http_user_agent, '')

    @override_settings(ENABLE_FEEDBACK_THROTTLING=False)
    def test_throttling_bypassed_when_disabled(self):
        """Test that hitting the endpoint repeatedly works when throttling is off."""
        for _ in range(25):
            response = self.client.post(
                self.feedback_url,
                self._valid_payload(),
                format='json'
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
