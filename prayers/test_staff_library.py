import json
import uuid

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase
from taggit.models import Tag

from hub.models import Church, Profile
from prayers.models import Prayer, PrayerLibraryOperation, PrayerSet
from prayers.staff_library import digest


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}})
class StaffLibraryTests(APITestCase):
    def setUp(self):
        self.church = Church.objects.create(name='Library church')
        self.other = Church.objects.create(name='Other church')
        self.staff = get_user_model().objects.create_user(username='library-staff', is_staff=True)
        Profile.objects.update_or_create(user=self.staff, defaults={'church': self.church})
        self.client.force_authenticate(self.staff)
        self.url = f'/api/staff/prayer-library/{self.church.pk}/'
        Tag.objects.create(name='mercy', slug='mercy')

    def prayer(self, number=1):
        return {'title': f'Synthetic Prayer {number:03}', 'title_hy': f'Սինթետիկ {number:03}', 'text': f'Synthetic English {number}', 'text_hy': f'Սինթետիկ հայերեն {number}', 'category': 'general', 'tags': ['mercy']}

    def execute(self, action, payload, revision=None, key=None):
        envelope = {'action': action, 'payload': payload, 'if_match': revision}
        envelope.update(preview=False, operation_key=key or str(uuid.uuid4()), digest=digest(envelope))
        return self.client.post(self.url, envelope, format='json')

    def read(self, kind, pk):
        response = self.client.get(self.url, {'kind': kind, 'id': pk})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_permissions_and_cross_church(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))
        self.staff.is_staff = False
        self.staff.save()
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.execute('prayer.create', {'record': self.prayer()}).status_code, 403)
        self.staff.is_staff = True
        self.staff.save()
        self.assertEqual(self.client.get(f'/api/staff/prayer-library/{self.other.pk}/').status_code, 403)
        foreign = Prayer.objects.create(church=self.other, title='Foreign', text='Synthetic')
        data = {'prayer_sets': [{'title': 'Set', 'category': 'general', 'prayers': [{'prayer_id': foreign.pk}]}]}
        self.assertEqual(self.execute('set.import', data).status_code, 404)
        self.assertEqual(PrayerSet.objects.count(), 0)
        self.assertEqual(PrayerLibraryOperation.objects.count(), 0)

    def test_one_95_bilingual_set_roundtrip_and_idempotency(self):
        payload = {'prayer_sets': [{'title': 'Synthetic Narek', 'title_hy': 'Սինթետիկ հավաքածու', 'description': 'Synthetic', 'description_hy': 'Սինթետիկ', 'category': 'general', 'prayers': [self.prayer(i) for i in range(1, 96)]}]}
        key = str(uuid.uuid4())
        response = self.execute('set.import', payload, key=key)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(PrayerSet.objects.count(), 1)
        self.assertEqual(Prayer.objects.count(), 95)
        self.assertEqual(self.execute('set.import', payload, key=key).data, response.data)
        self.assertEqual(Prayer.objects.count(), 95)
        result = response.data['result']
        pk = result['created_set_ids'][0]
        exported = self.read('set', pk)['record']
        self.assertEqual(exported['description_hy'], 'Սինթետիկ')
        fields = ['title', 'title_hy', 'text', 'text_hy', 'category', 'tags']
        self.assertEqual([{field: prayer[field] for field in fields} for prayer in exported['prayers']], payload['prayer_sets'][0]['prayers'])
        self.assertEqual([item['order'] for item in result['sets'][0]['positions']], list(range(1, 96)))
        status = self.client.get(self.url, {'operation_key': key})
        self.assertEqual(status.data, response.data)
        payload['prayer_sets'][0]['title'] = 'Changed'
        self.assertEqual(self.execute('set.import', payload, key=key).status_code, 409)
        self.assertEqual(Prayer.objects.count(), 95)
        receipt = PrayerLibraryOperation.objects.get()
        self.assertNotIn('Synthetic English', json.dumps(receipt.result))

    def test_preview_and_atomic_failure(self):
        payload = {'prayers': [self.prayer(1), {**self.prayer(2), 'tags': ['unknown']}]}
        self.assertEqual(self.execute('prayer.import', payload).status_code, 409)
        self.assertEqual(Prayer.objects.count(), 0)
        response = self.client.post(self.url, {'action': 'prayer.import', 'payload': {'prayers': [self.prayer()]}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn('Synthetic English', json.dumps(response.data))
        self.assertEqual(Prayer.objects.count(), 0)
        self.assertEqual(PrayerLibraryOperation.objects.count(), 0)
        self.assertEqual(Tag.objects.count(), 1)

    def test_crud_revision_and_preservation(self):
        created = self.execute('prayer.create', {'record': self.prayer()}).data['result']
        pk = created['id']
        self.assertEqual(self.execute('prayer.update', {'id': pk, 'record': {'title': 'Changed'}}, 'stale').status_code, 409)
        updated = self.execute('prayer.update', {'id': pk, 'record': {'title': 'Changed'}}, created['revision'])
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(self.read('prayer', pk)['record']['text_hy'], self.prayer()['text_hy'])
        self.assertEqual(self.execute('prayer.delete', {'id': pk}, updated.data['result']['revision']).status_code, 200)
        self.assertEqual(Prayer.objects.count(), 0)

    def test_membership_reorder_and_deletion_acknowledgement(self):
        result = self.execute('set.import', {'prayer_sets': [{'title': 'Set', 'category': 'general', 'prayers': [self.prayer(1), self.prayer(2)]}]}).data['result']
        pk = result['created_set_ids'][0]
        ids = result['created_prayer_ids']
        rev = self.read('set', pk)['revision']
        self.assertEqual(self.execute('members.reorder', {'id': pk, 'prayer_ids': [ids[0], ids[0]]}, rev).status_code, 409)
        changed = self.execute('members.reorder', {'id': pk, 'prayer_ids': ids[::-1]}, rev)
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(self.execute('members.remove', {'id': pk, 'prayer_id': ids[0]}, rev).status_code, 409)
        removed = self.execute('members.remove', {'id': pk, 'prayer_id': ids[0]}, changed.data['result']['revision'])
        self.assertEqual(removed.status_code, 200, removed.data)
        added = self.execute('members.add', {'id': pk, 'prayer_id': ids[0], 'position': 1}, removed.data['result']['revision'])
        self.assertEqual(added.status_code, 200, added.data)
        self.assertEqual([item['order'] for item in added.data['result']['positions']], [1, 2])
        prayer_rev = self.read('prayer', ids[0])['revision']
        self.assertEqual(self.execute('prayer.delete', {'id': ids[0]}, prayer_rev).status_code, 409)
        self.assertEqual(self.execute('prayer.delete', {'id': ids[0], 'affected_set_ids': [pk]}, prayer_rev).status_code, 200)
        self.assertEqual(list(PrayerSet.objects.get(pk=pk).memberships.values_list('order', flat=True)), [1])
        self.assertEqual(self.execute('set.delete', {'id': pk}, self.read('set', pk)['revision']).status_code, 200)
        self.assertEqual(Prayer.objects.count(), 1)

    def test_existing_reference_and_strict_parser(self):
        created = self.execute('prayer.create', {'record': self.prayer()}).data['result']
        response = self.execute('set.import', {'prayer_sets': [{'title': 'Set', 'category': 'general', 'prayers': [{'prayer_id': created['id']}]}]})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Prayer.objects.count(), 1)
        for raw in ('{"action":"x","action":"y"}', '{"x":NaN}', '[' * 2000 + '0' + ']' * 2000):
            self.assertEqual(self.client.post(self.url, raw, content_type='application/json').status_code, 400)

    def test_invalid_digest_and_unknown_fields_do_not_write(self):
        response = self.client.post(self.url, {'action': 'prayer.create', 'payload': {'record': self.prayer()}, 'preview': False, 'operation_key': str(uuid.uuid4()), 'digest': 'invalid'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.execute('prayer.create', {'record': {**self.prayer(), 'source_url': 'unknown'}}).status_code, 400)
        self.assertEqual(Prayer.objects.count(), 0)

    def test_blank_translation_and_empty_set_roundtrip(self):
        created = self.execute('set.create', {'record': {'title': 'Empty', 'category': 'general', 'description_hy': 'Սինթետիկ'}}).data['result']
        response = self.execute('set.update', {'id': created['id'], 'record': {'description_hy': ''}}, created['revision'])
        self.assertEqual(response.status_code, 200)
        record = self.read('set', created['id'])['record']
        self.assertEqual(record['description_hy'], '')
        self.assertEqual(record['prayers'], [])
        imported = self.execute('set.import', {'prayer_sets': [{'title': 'Other empty', 'category': 'general', 'prayers': []}]})
        self.assertEqual(imported.status_code, 200, imported.data)

    def test_read_revision_hashes_exact_returned_snapshot(self):
        from unittest.mock import patch
        from prayers.staff_library import snapshot
        created = self.execute('set.create', {'record': {'title': 'Empty', 'category': 'general'}}).data['result']
        with patch('prayers.staff_library.snapshot', wraps=snapshot) as mocked:
            result = self.read('set', created['id'])
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result['revision'], digest(result['record']))

    def test_mid_persistence_failure_rolls_back_all_rows_and_receipt(self):
        from unittest.mock import patch
        from prayers.staff_library import apply
        calls = 0
        def fail_second(obj, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('Synthetic persistence failure')
            return apply(obj, data)
        with patch('prayers.staff_library.apply', side_effect=fail_second), self.assertRaises(RuntimeError):
            self.execute('prayer.import', {'prayers': [self.prayer(1), self.prayer(2)]})
        self.assertEqual(Prayer.objects.count(), 0)
        self.assertEqual(PrayerLibraryOperation.objects.count(), 0)

    def test_receipts_are_account_bound(self):
        key = str(uuid.uuid4())
        payload = {'record': self.prayer()}
        self.assertEqual(self.execute('prayer.create', payload, key=key).status_code, 200)
        other_staff = get_user_model().objects.create_user(username='other-staff', is_staff=True)
        Profile.objects.update_or_create(user=other_staff, defaults={'church': self.church})
        self.client.force_authenticate(other_staff)
        self.assertEqual(self.client.get(self.url, {'operation_key': key}).status_code, 404)
        self.assertEqual(self.execute('prayer.create', payload, key=key).status_code, 409)
        self.assertEqual(Prayer.objects.count(), 1)

    def test_expired_token_does_not_write(self):
        from datetime import timedelta
        from django.utils import timezone
        from rest_framework_simplejwt.tokens import AccessToken
        token = AccessToken.for_user(self.staff)
        token.set_exp(from_time=timezone.now() - timedelta(hours=1), lifetime=timedelta(seconds=1))
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(self.execute('prayer.create', {'record': self.prayer()}).status_code, 401)
        self.assertEqual(Prayer.objects.count(), 0)

    def test_blocked_conflict_preview_includes_ids_without_prose(self):
        result = self.execute('prayer.create', {'record': self.prayer()}).data['result']
        preview = self.client.post(self.url, {'action': 'prayer.create', 'payload': {'record': self.prayer()}}, format='json')
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.data['plan']['blocked'])
        self.assertEqual(preview.data['plan']['existing_ids'], [result['id']])
        self.assertNotIn('Synthetic English', json.dumps(preview.data))
