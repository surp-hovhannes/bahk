import uuid

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase
from taggit.models import Tag

from hub.models import Church
from icons.models import Icon
from prayers.models import Prayer, PrayerLibraryChurchGrant, PrayerLibraryOperation, PrayerSet, PrayerSetMembership
from prayers.staff_library import digest


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}})
class StaffLibraryIconTests(APITestCase):
    def setUp(self):
        self.church = Church.objects.create(name='Icon edit church')
        self.other = Church.objects.create(name='Other icon church')
        self.staff = get_user_model().objects.create_user(username='icon-editor', is_staff=True)
        PrayerLibraryChurchGrant.objects.create(user=self.staff, church=self.church)
        self.client.force_authenticate(self.staff)
        self.url = f'/api/staff/prayer-library/{self.church.pk}/'
        self.icon = Icon.objects.create(church=self.church, title='Synthetic icon')
        self.foreign_icon = Icon.objects.create(church=self.other, title='Foreign icon')
        self.prayer = Prayer.objects.create(church=self.church, title='Synthetic prayer', text='Synthetic English', i18n={'text_hy': 'Հայերեն'})
        self.prayer.tags.add(Tag.objects.create(name='mercy', slug='mercy'))
        self.prayer_set = PrayerSet.objects.create(church=self.church, title='Synthetic set', description='Synthetic description')
        PrayerSetMembership.objects.create(prayer=self.prayer, prayer_set=self.prayer_set, order=1)

    def read(self, kind, obj):
        response = self.client.get(self.url, {'kind': kind, 'id': obj.pk})
        self.assertEqual(response.status_code, 200)
        return response.data

    def update(self, kind, obj, icon_id, revision=None, preview=False, key=None):
        envelope = {'action': f'{kind}.update', 'payload': {'id': obj.pk, 'record': {'icon_id': icon_id}}, 'if_match': revision or self.read(kind, obj)['revision']}
        if preview:
            envelope['preview'] = True
        else:
            envelope.update(preview=False, operation_key=key or str(uuid.uuid4()), digest=digest(envelope))
        return self.client.post(self.url, envelope, format='json')

    def test_assign_replace_clear_preserves_content_and_membership(self):
        replacement = Icon.objects.create(church=self.church, title='Replacement icon')
        for kind, obj in [('prayer', self.prayer), ('set', self.prayer_set)]:
            with self.subTest(kind=kind):
                before = self.read(kind, obj)['record']
                for icon_id in [self.icon.pk, replacement.pk, None]:
                    response = self.update(kind, obj, icon_id)
                    self.assertEqual(response.status_code, 200, response.data)
                    self.assertEqual(response.data['result']['icon_id'], icon_id)
                    after = self.read(kind, obj)
                    self.assertEqual(after['record'], {**before, 'icon_id': icon_id})
                    self.assertEqual(after['revision'], digest(after['record']))
                self.assertEqual(list(self.prayer_set.memberships.values_list('prayer_id', 'order')), [(self.prayer.pk, 1)])

    def test_preview_shows_current_and_requested_icon_without_writes(self):
        for kind, obj in [('prayer', self.prayer), ('set', self.prayer_set)]:
            before = self.read(kind, obj)
            response = self.update(kind, obj, self.icon.pk, preview=True)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['plan']['icon_change'], {'current_icon_id': None, 'requested_icon_id': self.icon.pk})
            self.assertEqual(response.data['plan']['changed_fields'], ['icon_id'])
            self.assertEqual(self.read(kind, obj), before)
        self.assertEqual(PrayerLibraryOperation.objects.count(), 0)

    def test_invalid_ids_and_wrong_church_are_rejected_without_receipts(self):
        for kind, obj in [('prayer', self.prayer), ('set', self.prayer_set)]:
            before = self.read(kind, obj)
            for value, status in [(True, 400), (False, 400), (0, 400), (-1, 400), ('1', 400), (1.5, 400), ([], 400), ({}, 400), (999999, 404), (self.foreign_icon.pk, 404)]:
                with self.subTest(kind=kind, value=value):
                    self.assertEqual(self.update(kind, obj, value).status_code, status)
                    self.assertEqual(self.read(kind, obj), before)
        self.assertEqual(PrayerLibraryOperation.objects.count(), 0)

    def test_admin_icon_change_invalidates_revision_including_parent_set(self):
        for kind, obj in [('prayer', self.prayer), ('set', self.prayer_set)]:
            before = self.read(kind, obj)
            parent_revision = self.read('set', self.prayer_set)['revision']
            type(obj).objects.filter(pk=obj.pk).update(icon_id=self.icon.pk)
            self.assertNotEqual(self.read(kind, obj)['revision'], before['revision'])
            self.assertNotEqual(self.read('set', self.prayer_set)['revision'], parent_revision)
            self.assertEqual(self.update(kind, obj, None, revision=before['revision']).status_code, 409)
        self.assertEqual(PrayerLibraryOperation.objects.count(), 0)

    def test_completed_icon_update_replays_receipt_before_stale_revision(self):
        key = str(uuid.uuid4())
        revision = self.read('prayer', self.prayer)['revision']
        first = self.update('prayer', self.prayer, self.icon.pk, revision=revision, key=key)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.update('prayer', self.prayer, self.icon.pk, revision=revision, key=key).data, first.data)
        self.assertEqual(self.client.get(self.url, {'operation_key': key}).data, first.data)
        self.assertEqual(self.update('prayer', self.prayer, None, revision=revision, key=key).status_code, 409)
        self.assertEqual(PrayerLibraryOperation.objects.count(), 1)

    def test_existing_authorization_still_required_for_icon_changes(self):
        revision = self.read('prayer', self.prayer)['revision']
        PrayerLibraryChurchGrant.objects.all().delete()
        self.assertEqual(self.update('prayer', self.prayer, self.icon.pk, revision=revision).status_code, 403)
        self.staff.is_staff = False
        self.staff.save()
        self.assertEqual(self.update('set', self.prayer_set, self.icon.pk, revision='unused').status_code, 403)
        self.prayer.refresh_from_db()
        self.prayer_set.refresh_from_db()
        self.assertIsNone(self.prayer.icon_id)
        self.assertIsNone(self.prayer_set.icon_id)

    def test_create_and_import_remain_content_only(self):
        prayer = {'title': 'New prayer', 'text': 'Synthetic', 'category': 'general', 'icon_id': self.icon.pk}
        for action, payload in [('prayer.create', {'record': prayer}), ('prayer.import', {'prayers': [prayer]}), ('set.create', {'record': {'title': 'New set', 'category': 'general', 'icon_id': self.icon.pk}})]:
            response = self.client.post(self.url, {'action': action, 'payload': payload}, format='json')
            self.assertTrue(response.data['plan']['blocked'])
            self.assertEqual(response.data['plan']['reason_code'], 'invalid_input')
        self.assertEqual(Prayer.objects.count(), 1)
        self.assertEqual(PrayerSet.objects.count(), 1)
        self.assertEqual(PrayerLibraryOperation.objects.count(), 0)
