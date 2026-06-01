"""Tests for the icons app."""

import hashlib
from io import BytesIO, StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APITestCase
from rest_framework import status

from hub.models import Church
from icons.models import Icon, IconFeedback
from icons.serializers import IconSerializer


def create_test_image(name='test_icon.png', color='white', pattern=None):
    """Create a small valid uploaded PNG for footprint tests."""
    image = Image.new('RGB', (32, 32), color)
    if pattern == 'diagonal':
        for index in range(32):
            image.putpixel((index, index), (255, 255, 255))
            image.putpixel((31 - index, index), (255, 255, 255))

    buffer = BytesIO()
    image.save(buffer, format='PNG')
    content = buffer.getvalue()
    return SimpleUploadedFile(name, content, content_type='image/png'), content


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

    def test_icon_save_accepts_model_save_positional_arguments(self):
        """Test direct compatibility with Django Model.save positional args."""
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

        icon.title = "Updated Icon"
        icon.save(False, False, None, ['title'])

        icon.refresh_from_db()
        self.assertEqual(icon.title, "Updated Icon")
    
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

    def test_icon_save_computes_image_footprints_for_valid_image(self):
        """Test saving a valid image computes SHA-256 and pHash values."""
        test_image, content = create_test_image()

        icon = Icon.objects.create(
            title="Test Icon",
            church=self.church,
            image=test_image
        )

        self.assertEqual(icon.image_hash, hashlib.sha256(content).hexdigest())
        self.assertEqual(len(icon.image_hash), 64)
        self.assertNotEqual(icon.phash, '')

    def test_icon_save_does_not_recompute_footprints_for_metadata_update(self):
        """Test metadata-only saves do not refresh image footprints."""
        test_image, _content = create_test_image()
        icon = Icon.objects.create(
            title="Test Icon",
            church=self.church,
            image=test_image
        )
        Icon.objects.filter(pk=icon.pk).update(image_hash='manual-hash', phash='manual-phash')

        icon.refresh_from_db()
        icon.title = "Updated Icon"
        icon.save(update_fields=['title'])

        icon.refresh_from_db()
        self.assertEqual(icon.title, "Updated Icon")
        self.assertEqual(icon.image_hash, 'manual-hash')
        self.assertEqual(icon.phash, 'manual-phash')

    def test_replacing_icon_image_refreshes_footprints(self):
        """Test replacing an image changes exact and perceptual hashes."""
        test_image, _content = create_test_image(color='white')
        icon = Icon.objects.create(
            title="Test Icon",
            church=self.church,
            image=test_image
        )
        original_image_hash = icon.image_hash
        original_phash = icon.phash

        replacement_image, _replacement_content = create_test_image(
            name='replacement.png',
            color='black',
            pattern='diagonal',
        )
        icon.image = replacement_image
        icon.save(update_fields=['image'])

        icon.refresh_from_db()
        self.assertNotEqual(icon.image_hash, original_image_hash)
        self.assertNotEqual(icon.phash, original_phash)

    def test_invalid_image_bytes_save_with_hash_and_blank_phash(self):
        """Test corrupt image bytes still save exact hashes and leave pHash blank."""
        content = b'fake image content'
        test_image = SimpleUploadedFile(
            name='test_icon.jpg',
            content=content,
            content_type='image/jpeg'
        )

        icon = Icon.objects.create(
            title="Test Icon",
            church=self.church,
            image=test_image
        )

        self.assertEqual(icon.image_hash, hashlib.sha256(content).hexdigest())
        self.assertEqual(icon.phash, '')


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

    def test_serializer_includes_read_only_footprints(self):
        """Test icon serializer exposes footprints and ignores client supplied values."""
        serializer = IconSerializer(self.icon1)
        self.assertIn('image_hash', serializer.data)
        self.assertIn('phash', serializer.data)

        upload, _content = create_test_image(name='serializer_icon.png')
        write_serializer = IconSerializer(data={
            'title': 'Serializer Icon',
            'church': self.church.pk,
            'image': upload,
            'image_hash': 'client-hash',
            'phash': 'client-phash',
        })
        self.assertTrue(write_serializer.is_valid(), write_serializer.errors)

        icon = write_serializer.save()
        self.assertNotEqual(icon.image_hash, 'client-hash')
        self.assertNotEqual(icon.phash, 'client-phash')
        self.assertEqual(len(icon.image_hash), 64)
        self.assertNotEqual(icon.phash, '')
    
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

    def test_icon_match_accepts_form_encoded_max_results_string(self):
        """Test that numeric form max_results is normalized before matching."""
        data = {
            'prompt': 'nativity',
            'return_format': 'id',
            'max_results': '2'
        }
        response = self.client.post('/api/icons/match/', data, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('matches', response.data)
        self.assertLessEqual(len(response.data['matches']), 2)

    def test_icon_match_rejects_negative_max_results(self):
        """Test that negative max_results is rejected."""
        data = {
            'prompt': 'nativity',
            'return_format': 'id',
            'max_results': -1
        }
        response = self.client.post('/api/icons/match/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('max_results', response.data['error'])

    def test_icon_match_rejects_non_numeric_max_results(self):
        """Test that non-numeric max_results is rejected."""
        data = {
            'prompt': 'nativity',
            'return_format': 'id',
            'max_results': 'two'
        }
        response = self.client.post('/api/icons/match/', data, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('max_results', response.data['error'])
    
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

    def test_malformed_ip_is_not_stored(self):
        """Test that malformed IP addresses are discarded."""
        response = self.client.post(
            self.feedback_url,
            self._valid_payload(),
            format='json',
            REMOTE_ADDR='not.an.ip'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        feedback = IconFeedback.objects.first()
        self.assertIsNone(feedback.ip_address)

    def test_proxy_style_ip_list_is_not_stored(self):
        """Test that comma-separated proxy-style address lists are discarded."""
        response = self.client.post(
            self.feedback_url,
            self._valid_payload(),
            format='json',
            REMOTE_ADDR='203.0.113.42, 10.0.0.5'
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


class BackfillIconFootprintsCommandTests(TestCase):
    """Tests for the icon footprint backfill command."""

    def setUp(self):
        """Set up test data."""
        self.church = Church.objects.create(name="Test Church")

    def create_icon(self, title="Test Icon"):
        test_image, _content = create_test_image(name=f'{title.lower().replace(" ", "_")}.png')
        return Icon.objects.create(
            title=title,
            church=self.church,
            image=test_image
        )

    def call_backfill(self, *args):
        out = StringIO()
        err = StringIO()
        call_command('backfill_icon_footprints', *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_backfill_skips_rows_with_existing_footprints(self):
        """Test rows with both footprint values are skipped unless forced."""
        self.create_icon()

        out, _err = self.call_backfill()

        self.assertIn('scanned=0', out)
        self.assertIn('updated=0', out)

    def test_backfill_updates_rows_missing_footprints(self):
        """Test backfill updates rows missing either footprint value."""
        icon = self.create_icon()
        Icon.objects.filter(pk=icon.pk).update(image_hash='', phash='')

        out, _err = self.call_backfill()

        icon.refresh_from_db()
        self.assertIn('scanned=1', out)
        self.assertIn('updated=1', out)
        self.assertEqual(len(icon.image_hash), 64)
        self.assertNotEqual(icon.phash, '')

    def test_backfill_honors_dry_run(self):
        """Test dry-run reports pending updates without saving."""
        icon = self.create_icon()
        Icon.objects.filter(pk=icon.pk).update(image_hash='', phash='')

        out, _err = self.call_backfill('--dry-run')

        icon.refresh_from_db()
        self.assertIn('scanned=1', out)
        self.assertIn('would_update=1', out)
        self.assertEqual(icon.image_hash, '')
        self.assertEqual(icon.phash, '')

    def test_backfill_honors_force(self):
        """Test force recomputes rows that already have both values."""
        icon = self.create_icon()
        Icon.objects.filter(pk=icon.pk).update(image_hash='client-hash', phash='client-phash')

        out, _err = self.call_backfill()
        icon.refresh_from_db()
        self.assertIn('scanned=0', out)
        self.assertEqual(icon.image_hash, 'client-hash')
        self.assertEqual(icon.phash, 'client-phash')

        out, _err = self.call_backfill('--force')
        icon.refresh_from_db()
        self.assertIn('scanned=1', out)
        self.assertIn('updated=1', out)
        self.assertNotEqual(icon.image_hash, 'client-hash')
        self.assertNotEqual(icon.phash, 'client-phash')
