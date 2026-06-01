"""Tests for icon duplicate detection and cleanup."""
from io import BytesIO, StringIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from hub.models import Church
from icons.models import Icon
from prayers.models import Prayer, PrayerRequest, PrayerSet


def create_test_image(name='icon.png', color='white'):
    """Create a valid PNG upload."""
    image = Image.new('RGB', (32, 32), color)
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    content = buffer.getvalue()
    return SimpleUploadedFile(name, content, content_type='image/png'), content


@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}
)
class IconDedupCommandTests(TestCase):
    """Tests for duplicate icon management commands."""

    duplicate_phash = '0123456789abcdef'

    def setUp(self):
        self.church = Church.objects.create(name='Test Church')

    def create_icon(self, title, color='white', phash=None):
        upload, _content = create_test_image(
            name=f"{title.lower().replace(' ', '_')}.png",
            color=color,
        )
        icon = Icon.objects.create(
            title=title,
            church=self.church,
            image=upload,
        )
        if phash is not None:
            Icon.objects.filter(pk=icon.pk).update(phash=phash)
            icon.refresh_from_db()
        return icon

    def call_icons(self, *args):
        out = StringIO()
        err = StringIO()
        call_command('icons', *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def attach_prayer(self, icon, title='Prayer'):
        return Prayer.objects.create(
            title=title,
            text='Prayer text',
            church=self.church,
            icon=icon,
        )

    def attach_prayer_set(self, icon, title='Prayer Set'):
        return PrayerSet.objects.create(
            title=title,
            description='Prayer set description',
            church=self.church,
            icon=icon,
        )

    def attach_prayer_request(self, icon, title='Prayer Request'):
        user = get_user_model().objects.create_user(
            username=f'user-{icon.pk}',
            email=f'user-{icon.pk}@example.com',
            password='password',
        )
        return PrayerRequest.objects.create(
            title=title,
            description='Prayer request description',
            requester=user,
            icon=icon,
        )

    def test_find_duplicates_one_group(self):
        """Find command reports one duplicate pHash group."""
        self.create_icon('Duplicate 1', color='white', phash=self.duplicate_phash)
        self.create_icon('Duplicate 2', color='black', phash=self.duplicate_phash)
        self.create_icon('Duplicate 3', color='red', phash=self.duplicate_phash)
        self.create_icon('Unique 1', color='green', phash='1111111111111111')
        self.create_icon('Unique 2', color='blue', phash='2222222222222222')

        out, err = self.call_icons('find-duplicates')

        self.assertEqual(err, '')
        self.assertIn(f'phash={self.duplicate_phash} count=3', out)
        self.assertIn('Total: duplicate icons=3, groups=1, merge candidates=2', out)

    def test_merge_duplicates_safe(self):
        """Merge command deletes unassociated duplicate icons."""
        canonical = self.create_icon(
            'Canonical',
            color='white',
            phash=self.duplicate_phash,
        )
        duplicate = self.create_icon(
            'Duplicate',
            color='black',
            phash=self.duplicate_phash,
        )

        out, err = self.call_icons('merge-duplicates', '--execute')

        self.assertEqual(err, '')
        self.assertIn(f'Deleted icon {duplicate.pk} (Duplicate)', out)
        self.assertTrue(Icon.objects.filter(pk=canonical.pk).exists())
        self.assertFalse(Icon.objects.filter(pk=duplicate.pk).exists())

        out, err = self.call_icons('merge-duplicates')
        self.assertEqual(err, '')
        self.assertIn('Merged 0, Skipped 0, Groups 0', out)

    def test_merge_duplicates_conflict(self):
        """Merge command skips duplicate icons that still have associations."""
        canonical = self.create_icon(
            'Canonical',
            color='white',
            phash=self.duplicate_phash,
        )
        duplicate = self.create_icon(
            'Associated Duplicate',
            color='black',
            phash=self.duplicate_phash,
        )
        self.attach_prayer(canonical)
        self.attach_prayer_set(canonical)
        self.attach_prayer_request(duplicate)

        out, err = self.call_icons('merge-duplicates', '--execute')

        self.assertIn('Merged 0, Skipped 1, Groups 1', out)
        self.assertIn(f'SKIP icon {duplicate.pk} (Associated Duplicate)', err)
        self.assertTrue(Icon.objects.filter(pk=canonical.pk).exists())
        self.assertTrue(Icon.objects.filter(pk=duplicate.pk).exists())

    def test_merge_duplicates_canonical_selection(self):
        """Icon with the most associations is selected as canonical."""
        older = self.create_icon('Older', color='white', phash=self.duplicate_phash)
        most_used = self.create_icon(
            'Most Used',
            color='black',
            phash=self.duplicate_phash,
        )
        unused = self.create_icon('Unused', color='red', phash=self.duplicate_phash)
        self.attach_prayer(most_used)
        self.attach_prayer_set(most_used)
        self.attach_prayer_request(older)

        out, err = self.call_icons('merge-duplicates', '--execute')

        self.assertIn('Merged 1, Skipped 1, Groups 1', out)
        self.assertIn(f'SKIP icon {older.pk} (Older)', err)
        self.assertTrue(Icon.objects.filter(pk=most_used.pk).exists())
        self.assertTrue(Icon.objects.filter(pk=older.pk).exists())
        self.assertFalse(Icon.objects.filter(pk=unused.pk).exists())


@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}
)
class IconDedupUploadTests(APITestCase):
    """Tests for upload-time duplicate detection."""

    def setUp(self):
        self.church = Church.objects.create(name='Test Church')
        self.user = get_user_model().objects.create_user(
            username='admin',
            email='admin@example.com',
            password='password',
            is_staff=True,
        )
        self.client.force_authenticate(user=self.user)

    def test_upload_duplicate_exact_image_hash(self):
        """Uploading identical image bytes returns a 409."""
        upload, _content = create_test_image(name='existing.png', color='white')
        existing = Icon.objects.create(
            title='Existing Icon',
            church=self.church,
            image=upload,
        )
        duplicate_upload, _content = create_test_image(
            name='duplicate.png',
            color='white',
        )

        response = self.client.post('/api/icons/', {
            'title': 'Duplicate Icon',
            'church': self.church.pk,
            'image': duplicate_upload,
        })

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['existing_icon']['id'], existing.pk)
        self.assertEqual(Icon.objects.count(), 1)

    def test_direct_model_create_allows_duplicate_image_bytes(self):
        """Internal icon creation does not fail when image bytes duplicate."""
        upload, _content = create_test_image(name='existing.png', color='white')
        existing = Icon.objects.create(
            title='Existing Icon',
            church=self.church,
            image=upload,
        )
        duplicate_upload, _content = create_test_image(
            name='duplicate.png',
            color='white',
        )

        duplicate = Icon.objects.create(
            title='Duplicate Internal Icon',
            church=self.church,
            image=duplicate_upload,
        )

        self.assertEqual(Icon.objects.count(), 2)
        existing.refresh_from_db()
        duplicate.refresh_from_db()
        self.assertNotEqual(existing.image_hash, '')
        self.assertEqual(duplicate.image_hash, '')

    def test_upload_duplicate_similar_phash(self):
        """Uploading a perceptually similar image returns a 409."""
        upload, _content = create_test_image(name='existing.png', color='white')
        existing = Icon.objects.create(
            title='Existing Icon',
            church=self.church,
            image=upload,
        )
        similar_upload, _content = create_test_image(
            name='similar.png',
            color=(250, 250, 250),
        )

        response = self.client.post('/api/icons/', {
            'title': 'Similar Icon',
            'church': self.church.pk,
            'image': similar_upload,
        })

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['existing_icon']['id'], existing.pk)
        self.assertEqual(Icon.objects.count(), 1)

    def test_upload_new_icon_no_hashes(self):
        """Uploading a new icon without client-supplied hashes succeeds."""
        upload, _content = create_test_image(name='new.png', color='purple')

        response = self.client.post('/api/icons/', {
            'title': 'New Icon',
            'church': self.church.pk,
            'image': upload,
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Icon.objects.count(), 1)
        icon = Icon.objects.get()
        self.assertEqual(icon.title, 'New Icon')
        self.assertNotEqual(icon.image_hash, '')
