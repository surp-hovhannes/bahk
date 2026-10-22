"""Offline receipt-boundary tests; Redis and Expo never leave the process."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import override_settings
from exponent_server_sdk import PushTicket

from events.models import Event, EventType
from notifications.models import DeviceToken
from notifications.receipts import TTL, _apply_receipt, _key, collect_receipts, remember_ticket
from notifications.utils import EXPO_ERROR_CODES, _device_token_log_id, send_push_notification
from tests.base import BaseTestCase


@override_settings(NOTIFICATION_TELEMETRY_ENABLED=True)
class ReceiptTests(BaseTestCase):
    def setUp(self):
        self.user = self.create_user()
        self.device = DeviceToken.objects.create(user=self.user, token='ExponentPushToken[private]', device_type='ios')
        self.other = DeviceToken.objects.create(user=self.user, token='ExponentPushToken[other]', device_type='ios')
        self.row = {'device_pk': self.device.pk, 'notif_id': 'notification-123', 'kind': 'test', 'expires_at': 200000}
        self.redis_patch = patch('notifications.receipts.get_redis_connection')
        self.redis = self.redis_patch.start().return_value
        self.addCleanup(self.redis_patch.stop)
        self.redis.lock.return_value.__enter__.return_value.owned.return_value = True
        self.client_patch = patch('notifications.receipts.PushClient')
        self.client = self.client_patch.start().return_value
        self.addCleanup(self.client_patch.stop)
        self.clock_patch = patch('notifications.receipts.time.time', return_value=100000)
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)

    def events(self):
        return Event.objects.filter(event_type__code=EventType.NOTIFICATION_RECEIPT)

    def pending(self, receipt):
        self.redis.zrangebyscore.side_effect = [[b'ticket'], []]
        self.redis.mget.return_value = [json.dumps(self.row)]
        self.client.check_receipts_multiple.return_value = [SimpleNamespace(id='ticket', **receipt)] if receipt else []

    def test_sdk_ticket_inputs_and_receipt_ids(self):
        self.pending({'status': 'ok'})

        def check_receipts(tickets):
            self.assertEqual(len(tickets), 1)
            self.assertIsInstance(tickets[0], PushTicket)
            self.assertEqual(tickets[0].id, 'ticket')
            # Results need not follow request order; unrelated IDs must be ignored.
            return [
                SimpleNamespace(id='unmatched', status='error', details={'error': 'DeviceNotRegistered'}),
                SimpleNamespace(id='ticket', status='ok', details=None),
            ]

        self.client.check_receipts_multiple.side_effect = check_receipts
        collect_receipts()
        self.assertEqual(self.events().get().data['ticket_id'], 'ticket')
        self.redis.delete.assert_called_once_with(_key('ticket'))
        self.device.refresh_from_db()
        self.assertTrue(self.device.is_active)

    def test_ok_once_even_after_cache_delete_failure(self):
        self.pending({'status': 'ok'})
        self.redis.delete.side_effect = RuntimeError('private provider text')
        collect_receipts()
        self.pending({'status': 'ok'})
        collect_receipts()
        self.assertEqual(self.events().count(), 1)
        event = self.events().get()
        self.assertEqual(event.data['outcome'], 'delivered')
        self.assertEqual(event.data['kind'], 'test')
        self.assertIsNone(event.ip_address)
        self.assertEqual(event.user_agent, '')
        self.assertEqual(event.description, '')
        self.assertNotIn('device_pk', event.data)
        self.assertNotIn(self.device.token, json.dumps(event.data))

    def test_dnr_only_deactivates_mapped_row_with_flag_on_and_off(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), override_settings(NOTIFICATION_TELEMETRY_ENABLED=enabled):
                self.events().delete()
                DeviceToken.objects.filter(pk=self.device.pk).update(is_active=True)
                self.pending({'status': 'error', 'details': {'error': 'DeviceNotRegistered'}})
                with self.assertLogs('notifications.receipts', level='WARNING') as logs:
                    collect_receipts()
                self.device.refresh_from_db()
                self.other.refresh_from_db()
                self.assertFalse(self.device.is_active)
                self.assertTrue(self.other.is_active)
                self.assertEqual(self.events().count(), int(enabled))
                self.assertIn(_device_token_log_id(self.device.token), str(logs.output))
                self.assertNotIn(self.device.token, str(logs.output))

    def test_other_codes_are_bounded_and_never_deactivate(self):
        for index, code in enumerate(sorted(EXPO_ERROR_CODES - {'DeviceNotRegistered'}) + ['private-secret']):
            _apply_receipt(self.row, str(index), {'status': 'error', 'details': {'error': code}})
            event = self.events().get(data__ticket_id=str(index))
            self.assertEqual(event.data['outcome'], 'provider_error')
            self.assertEqual(event.data['error_code'], code if code in EXPO_ERROR_CODES else 'other')
        self.device.refresh_from_db()
        self.assertTrue(self.device.is_active)

    def test_missing_inactive_type_and_flag_off_skip_events(self):
        EventType.objects.filter(code=EventType.NOTIFICATION_RECEIPT).update(is_active=False)
        self.assertTrue(_apply_receipt(self.row, 'ticket', {'status': 'ok'}))
        with override_settings(NOTIFICATION_TELEMETRY_ENABLED=False), patch(
            'events.models.Event.objects.create', side_effect=AssertionError('event created'),
        ):
            self.assertTrue(_apply_receipt(self.row, 'ticket', {'status': 'ok'}))
        self.assertFalse(self.events().exists())
        EventType.objects.filter(code=EventType.NOTIFICATION_RECEIPT).delete()
        self.assertTrue(_apply_receipt(self.row, 'ticket', {'status': 'ok'}))

    def test_expired_unmatched_and_unavailable(self):
        self.pending({'status': 'ok'})
        self.row['expires_at'] = 99999
        self.redis.mget.return_value = [json.dumps(self.row), None]
        self.redis.zrangebyscore.side_effect = [[b'ticket', b'expired'], []]
        collect_receipts()
        self.client.check_receipts_multiple.assert_not_called()
        self.assertFalse(self.events().exists())
        self.row['expires_at'] = 200000
        self.pending(None)
        collect_receipts()
        self.assertFalse(self.events().exists())

    def test_batch_limit_and_sdk_exception_leave_pending(self):
        ids = [f'ticket-{i}' for i in range(1000)]
        self.redis.zrangebyscore.return_value = ids
        self.redis.mget.return_value = [json.dumps(self.row)] * 1000
        self.client.check_receipts_multiple.side_effect = RuntimeError('private token content')
        with self.assertLogs('notifications.receipts', level='WARNING') as logs:
            collect_receipts()
        self.assertEqual(self.client.check_receipts_multiple.call_count, 5)
        for call in self.client.check_receipts_multiple.call_args_list:
            self.assertLessEqual(len(call.args[0]), 1000)
            self.assertEqual([ticket.id for ticket in call.args[0]], ids)
            self.assertTrue(all(isinstance(ticket, PushTicket) for ticket in call.args[0]))
        self.redis.delete.assert_not_called()
        self.assertNotIn('private token content', str(logs.output))

    def test_lock_contention_and_lost_lease(self):
        self.redis.lock.return_value.__enter__.side_effect = RuntimeError('locked')
        collect_receipts()
        self.client.check_receipts_multiple.assert_not_called()
        self.redis.lock.return_value.__enter__.side_effect = None
        self.redis.lock.return_value.__enter__.return_value.owned.return_value = False
        self.pending({'status': 'ok'})
        collect_receipts()
        self.assertFalse(self.events().exists())

    def test_send_maps_success_with_flag_off_without_content(self):
        with override_settings(NOTIFICATION_TELEMETRY_ENABLED=False), patch('notifications.utils.PushClient') as client:
            client.return_value.publish.return_value = {'status': 'ok', 'id': 'ticket'}
            send_push_notification('private body', {'private': 'content'}, device_token_ids=[self.device.pk])
        args = self.redis.eval.call_args.args
        mapping = json.loads(args[4])
        self.assertEqual(mapping['device_pk'], self.device.pk)
        self.assertIsNone(mapping['notif_id'])
        self.assertEqual(args[5], TTL)
        self.assertNotIn('private', args[4])
        self.assertNotIn(self.device.token, args[4])
        self.assertEqual(args[3], _key('ticket'))

    def test_mapping_validation_and_cache_failure_are_safe(self):
        for ticket in ({'status': 'error', 'id': 'ticket'}, {'status': 'ok'}, {'status': 'ok', 'id': 'private/token'}):
            remember_ticket(ticket, self.device.pk, None)
        self.redis.eval.assert_not_called()
        self.redis.eval.side_effect = RuntimeError('secret')
        remember_ticket({'status': 'ok', 'id': 'ticket'}, self.device.pk, self.row)

    def test_flag_off_mapping_never_becomes_telemetry(self):
        self.row['notif_id'] = None
        self.assertTrue(_apply_receipt(self.row, 'ticket', {'status': 'ok'}))
        self.assertFalse(self.events().exists())

    def test_dnr_survives_event_write_failure(self):
        with patch('events.models.Event.objects.create', side_effect=RuntimeError('unavailable')):
            self.pending({'status': 'error', 'details': {'error': 'DeviceNotRegistered'}})
            collect_receipts()
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_active)

    def test_object_receipt_and_unknown_status(self):
        self.assertFalse(_apply_receipt(self.row, 'unknown', SimpleNamespace(status=None)))
        self.assertTrue(_apply_receipt(self.row, 'ticket', SimpleNamespace(status='ok', details=None)))
        self.assertEqual(self.events().get().data['outcome'], 'delivered')

    def test_enabled_send_preserves_correlation_and_capacity_bound(self):
        from notifications.receipts import CAPACITY

        with patch('notifications.utils.PushClient') as client:
            client.return_value.publish.return_value = {'status': 'ok', 'id': 'ticket'}
            send_push_notification('private body', device_token_ids=[self.device.pk], kind='test')
        mapping = json.loads(self.redis.eval.call_args.args[4])
        event = Event.objects.get(event_type__code=EventType.NOTIFICATION_SEND)
        self.assertEqual(mapping['notif_id'], event.data['notif_id'])
        self.assertEqual(mapping['kind'], event.data['kind'])
        self.assertEqual(self.redis.eval.call_args.args[7], CAPACITY)
        self.assertEqual(mapping['expires_at'], 100000 + TTL)
