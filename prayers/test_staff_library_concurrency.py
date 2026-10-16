"""PostgreSQL row-lock regressions; SQLite cannot verify select_for_update."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import connection, close_old_connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from hub.models import Church, Profile
from prayers.models import Prayer, PrayerLibraryChurchGrant, PrayerLibraryOperation, PrayerSet
from prayers.staff_library import digest


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks')
@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}})
class StaffLibraryConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.church = Church.objects.create(name='Concurrent fixture church')
        self.staff = get_user_model().objects.create_user(username='concurrent-staff', is_staff=True)
        Profile.objects.update_or_create(user=self.staff, defaults={'church': self.church})
        PrayerLibraryChurchGrant.objects.create(user=self.staff, church=self.church)
        self.url = f'/api/staff/prayer-library/{self.church.pk}/'

    def submit(self, action, payload, revision=None, key=None):
        client = APIClient()
        client.force_authenticate(self.staff)
        data = {'action': action, 'payload': payload, 'if_match': revision}
        data.update(preview=False, operation_key=key or str(uuid.uuid4()), digest=digest(data))
        return client.post(self.url, data, format='json')

    def concurrent(self, action, payloads, revision=None, keys=None):
        barrier = Barrier(2)
        def worker(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = self.submit(action, payloads[index], revision, keys[index] if keys else None)
                return response.status_code, response.data
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, range(2)))

    def test_same_import_key_serializes_to_one_receipt(self):
        payload = {'prayer_sets': [{'title': 'Synthetic Set', 'category': 'general', 'prayers': [{'title': 'Synthetic Prayer', 'text': 'Synthetic', 'category': 'general'}]}]}
        key = str(uuid.uuid4())
        results = self.concurrent('set.import', [payload, payload], keys=[key, key])
        self.assertEqual([status for status, _ in results], [200, 200])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(Prayer.objects.count(), 1)
        self.assertEqual(PrayerSet.objects.count(), 1)
        self.assertEqual(PrayerLibraryOperation.objects.count(), 1)

    def test_competing_updates_reject_the_second_revision(self):
        created = self.submit('prayer.create', {'record': {'title': 'Synthetic', 'text': 'Synthetic', 'category': 'general'}}).data['result']
        payloads = [{'id': created['id'], 'record': {'text': f'Synthetic change {index}'}} for index in range(2)]
        results = self.concurrent('prayer.update', payloads, created['revision'])
        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        self.assertEqual(PrayerLibraryOperation.objects.count(), 2)
