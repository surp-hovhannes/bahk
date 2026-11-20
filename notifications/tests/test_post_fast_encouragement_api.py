from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from notifications.models import PostFastEncouragementEmail
from tests.fixtures.test_data import TestDataFactory


class PostFastEncouragementAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = TestDataFactory.create_user(username='email-staff')
        cls.staff.is_staff = True
        cls.staff.save(update_fields=['is_staff'])
        cls.member = TestDataFactory.create_user(username='email-member')
        cls.fast = TestDataFactory.create_fast(name='Great Lent')
        cls.other_fast = TestDataFactory.create_fast(name='Advent', church=cls.fast.church)
        cls.copy = PostFastEncouragementEmail.objects.create(
            fast=cls.fast, subject='Peace', message='Keep praying.',
        )

    def setUp(self):
        self.list_url = reverse('api_notifications:post-fast-encouragement-list')
        self.detail_url = self.detail_url_for(self.copy.pk)
        self.payload = {
            'fast': self.other_fast.pk,
            'subject': ' A blessing ',
            'message': '  Peace to you.\n\nKeep  praying. <b>Plain text</b>  ',
        }

    @staticmethod
    def detail_url_for(pk):
        return reverse('api_notifications:post-fast-encouragement-detail', args=[pk])

    def test_permissions(self):
        self.assertEqual(self.list_url, '/api/notifications/post-fast-encouragement/')
        for user in (None, self.member):
            self.client.force_authenticate(user=user)
            for method, url, data in (
                ('get', self.list_url, {}),
                ('get', self.detail_url, {}),
                ('post', self.list_url, self.payload),
                ('patch', self.detail_url, {'subject': 'Changed'}),
                ('put', self.detail_url, self.payload),
                ('delete', self.detail_url, {}),
            ):
                with self.subTest(user=user, method=method, url=url):
                    response = getattr(self.client, method)(url, data, format='json')
                    if user is None:
                        self.assertIn(response.status_code, (401, 403))
                    else:
                        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.copy.refresh_from_db()
        self.assertEqual(self.copy.subject, 'Peace')
        self.assertEqual(PostFastEncouragementEmail.objects.count(), 1)

    @patch('notifications.tasks.send_post_fast_encouragement_task')
    def test_staff_create_list_show_and_patch_without_sending(self, task):
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(self.list_url, self.payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        copy_id = response.data['id']
        expected = {
            'id': copy_id,
            'fast': self.other_fast.pk,
            'fast_id': self.other_fast.pk,
            'fast_name': self.other_fast.name,
            'subject': 'A blessing',
            'message': 'Peace to you.\n\nKeep  praying. <b>Plain text</b>',
        }
        self.assertEqual(response.data, expected)

        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)
        response = self.client.get(self.list_url, {'fast': self.other_fast.pk})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'], [expected])
        self.assertEqual(self.client.get(self.list_url, {'fast': 'invalid'}).status_code, 400)

        detail_url = self.detail_url_for(copy_id)
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, expected)
        response = self.client.patch(detail_url, {'subject': ' Updated '}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, dict(expected, subject='Updated'))
        self.assertEqual(PostFastEncouragementEmail.objects.get(pk=copy_id).subject, 'Updated')
        self.assertEqual(self.client.put(detail_url, self.payload, format='json').status_code, 405)
        self.assertEqual(self.client.delete(detail_url).status_code, 405)
        self.assertEqual(task.mock_calls, [])

    def test_duplicate_fast_on_create_and_patch(self):
        self.client.force_authenticate(user=self.staff)
        duplicate_message = ['An encouragement email already exists for this fast.']
        response = self.client.post(self.list_url, dict(self.payload, fast=self.fast.pk), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['fast'], duplicate_message)

        other_copy = PostFastEncouragementEmail.objects.create(
            fast=self.other_fast, subject='Peace', message='Keep praying.',
        )
        response = self.client.patch(self.detail_url_for(other_copy.pk), {'fast': self.fast.pk}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['fast'], duplicate_message)
        response = self.client.patch(self.detail_url, {'fast': self.fast.pk}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_invalid_fields_on_create_and_patch(self):
        self.client.force_authenticate(user=self.staff)
        for field, value in (
            ('subject', ''),
            ('subject', ' \t '),
            ('subject', 'x' * 256),
            ('message', ''),
            ('message', ' \n\t '),
            ('unknown', 'value'),
            ('fast_name', 'read-only'),
            ('fast', None),
        ):
            with self.subTest(field=field, value=value):
                response = self.client.post(self.list_url, dict(self.payload, **{field: value}), format='json')
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)
                response = self.client.patch(self.detail_url, {field: value}, format='json')
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)
        response = self.client.post(self.list_url, {'subject': 'Peace', 'message': 'Pray.'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('fast', response.data)

    def test_legacy_unassigned_copy_can_be_read_and_assigned(self):
        self.client.force_authenticate(user=self.staff)
        legacy_copy = PostFastEncouragementEmail.objects.create(subject='Legacy', message='Peace.')
        detail_url = self.detail_url_for(legacy_copy.pk)
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for field in ('fast', 'fast_id', 'fast_name'):
            self.assertIsNone(response.data[field])
        response = self.client.patch(detail_url, {'fast': self.other_fast.pk}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['fast_id'], self.other_fast.pk)
