"""Receipt-boundary tests. ReceiptTests mock Redis and Expo; RedisLedgerTests run the
real Lua script, lock and SDK parsing against a live Redis with Expo's HTTP faked."""
import json
import os
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest import SkipTest
from unittest.mock import patch

import redis
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from exponent_server_sdk import PushClient, PushTicket
from rest_framework.test import APIClient

from events.models import Event, EventType
from notifications import receipts
from notifications.models import DeviceToken
from notifications.receipts import (
    CAPACITY, PROVIDER_TIMEOUT, RETRY_DELAY, TTL, _apply_receipt, _key, collect_receipts,
    ledger_entry, note_registration, remember_tickets,
)
from notifications.utils import EXPO_ERROR_CODES, _device_token_log_id, send_push_notification
from tests.base import BaseTestCase


@override_settings(NOTIFICATION_TELEMETRY_ENABLED=True)
class ReceiptTests(BaseTestCase):
    def setUp(self):
        # Registration markers live in the (process-wide) cache.
        cache.clear()
        self.user = self.create_user()
        self.device = DeviceToken.objects.create(user=self.user, token='ExponentPushToken[private]', device_type='ios')
        self.other = DeviceToken.objects.create(user=self.user, token='ExponentPushToken[other]', device_type='ios')
        self.row = {'device_pk': self.device.pk, 'notif_id': 'notification-123', 'kind': 'test',
                    'sent_at': 90000, 'expires_at': 200000}
        self.redis_patch = patch('notifications.receipts.get_redis_connection')
        self.redis = self.redis_patch.start().return_value
        self.addCleanup(self.redis_patch.stop)
        self.lock = self.redis.lock.return_value
        self.lock.acquire.return_value = True
        self.lock.owned.return_value = True
        self.pipe = self.redis.pipeline.return_value
        self.client_patch = patch('notifications.receipts.PushClient')
        self.client_class = self.client_patch.start()
        self.client = self.client_class.return_value
        self.addCleanup(self.client_patch.stop)
        # Patch the module's clock, not time.time for the whole process.
        self.clock_patch = patch('notifications.receipts._now', return_value=100000)
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)

    def events(self):
        return Event.objects.filter(event_type__code=EventType.NOTIFICATION_RECEIPT)

    def test_redis_outage_fails_required_suite_but_skips_locally(self):
        for required, exception in (('1', AssertionError), ('0', SkipTest)):
            with self.subTest(required=required), patch.dict(os.environ, REQUIRE_REDIS_TESTS=required), patch(
                'notifications.tests.test_receipts.redis.Redis.from_url',
            ) as connect:
                connect.return_value.ping.side_effect = redis.exceptions.ConnectionError('unreachable')
                case = RedisLedgerTests('test_held_lock_skips_the_run')
                with self.assertRaises(exception):
                    case.setUp()

    def pending(self, receipt):
        self.redis.zrangebyscore.side_effect = [[b'ticket'], []]
        self.redis.mget.return_value = [json.dumps(self.row)]
        self.client.check_receipts_multiple.return_value = [SimpleNamespace(id='ticket', **receipt)] if receipt else []

    def enqueued(self):
        """(row, args) for each script call queued by remember_tickets."""
        calls = self.redis.register_script.return_value.call_args_list
        return [(json.loads(call.kwargs['args'][0]), call.kwargs) for call in calls]

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
        self.pipe.delete.assert_called_once_with(_key('ticket'))
        self.pipe.zrem.assert_called_once_with(_key('pending'), 'ticket')
        self.client_class.assert_called_once_with(timeout=PROVIDER_TIMEOUT)
        self.device.refresh_from_db()
        self.assertTrue(self.device.is_active)
        self.lock.release.assert_called_once()

    def test_ok_once_even_after_cache_delete_failure(self):
        self.pending({'status': 'ok'})
        self.pipe.execute.side_effect = RuntimeError('private provider text')
        with self.assertRaisesMessage(RuntimeError, 'Push receipt collection failed (RuntimeError)') as raised:
            collect_receipts()
        self.assertNotIn('private provider text', str(raised.exception))
        self.pipe.execute.side_effect = None
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

    def test_dedupe_only_scans_the_ledger_horizon(self):
        _apply_receipt(self.row, 'ticket', {'status': 'ok'})
        old = self.events().get()
        Event.objects.filter(pk=old.pk).update(timestamp=timezone.now() - timedelta(seconds=TTL + 3600))
        # A row older than any live mapping cannot be its duplicate, and is not scanned.
        _apply_receipt(self.row, 'ticket', {'status': 'ok'})
        self.assertEqual(self.events().count(), 2)
        _apply_receipt(self.row, 'ticket', {'status': 'ok'})
        self.assertEqual(self.events().count(), 2)

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

    def test_dnr_receipt_older_than_reregistration_keeps_device_active(self):
        dnr = {'status': 'error', 'details': {'error': 'DeviceNotRegistered'}}
        with patch('notifications.receipts._now', return_value=self.row['sent_at'] + 60):
            note_registration(self.device.pk)
        _apply_receipt(self.row, 'ticket', dnr)
        self.device.refresh_from_db()
        self.assertTrue(self.device.is_active)
        # A registration before the send is no reason to doubt the receipt.
        with patch('notifications.receipts._now', return_value=self.row['sent_at'] - 60):
            note_registration(self.device.pk)
        _apply_receipt(self.row, 'ticket-2', dnr)
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_active)

    def test_reregistering_existing_token_marks_registration(self):
        api = APIClient()
        api.force_authenticate(self.user)
        with patch('notifications.views.note_registration') as noted:
            response = api.post('/api/notifications/register-device-token/',
                                {'token': self.device.token, 'device_type': 'ios'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        noted.assert_called_once_with(self.device.pk)

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
        self.pipe.zrem.assert_called_once_with(_key('pending'), 'ticket', 'expired')
        self.assertFalse(self.events().exists())
        self.row['expires_at'] = 200000
        self.pending(None)
        collect_receipts()
        self.assertFalse(self.events().exists())

    def test_unreadable_mapping_is_dropped_without_aborting_the_batch(self):
        self.redis.zrangebyscore.side_effect = [[b'broken', b'missing-key', b'ticket'], []]
        self.redis.mget.return_value = ['not json', json.dumps({'device_pk': 1}), json.dumps(self.row)]
        self.client.check_receipts_multiple.return_value = [SimpleNamespace(id='ticket', status='ok', details=None)]
        with self.assertLogs('notifications.receipts', level='WARNING') as logs:
            collect_receipts()
        self.assertEqual(self.events().get().data['ticket_id'], 'ticket')
        self.pipe.zrem.assert_called_once_with(_key('pending'), 'broken', 'missing-key', 'ticket')
        self.assertIn('Push receipt mappings unreadable: 2', str(logs.output))

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
        self.pipe.delete.assert_not_called()
        self.assertNotIn('private token content', str(logs.output))
        self.assertIn('Push receipt provider unavailable (RuntimeError)', str(logs.output))

    def test_soft_time_limit_stops_the_run_and_releases_the_lock(self):
        self.pending({'status': 'ok'})
        self.client.check_receipts_multiple.side_effect = SoftTimeLimitExceeded()
        with self.assertRaises(SoftTimeLimitExceeded):
            collect_receipts()
        self.lock.release.assert_called_once()
        self.assertEqual(self.client.check_receipts_multiple.call_count, 1)

    def test_cache_outage_fails_the_task_without_payload_text(self):
        self.lock.acquire.side_effect = redis.exceptions.ConnectionError('private host text')
        with self.assertLogs('notifications.receipts', level='WARNING') as logs, self.assertRaises(RuntimeError) as raised:
            collect_receipts()
        self.assertEqual(str(raised.exception), 'Push receipt collection failed (ConnectionError)')
        self.assertNotIn('private host text', str(logs.output))

    def test_lock_contention_and_lost_lease(self):
        self.lock.acquire.return_value = False
        with self.assertLogs('notifications.receipts', level='INFO') as logs:
            collect_receipts()
        self.assertIn('already running', str(logs.output))
        self.client.check_receipts_multiple.assert_not_called()
        self.lock.acquire.return_value = True
        self.lock.owned.return_value = False
        self.pending({'status': 'ok'})
        collect_receipts()
        self.client.check_receipts_multiple.assert_not_called()
        self.assertFalse(self.events().exists())

    @override_settings(PUSH_RECEIPTS_ENABLED=False)
    def test_kill_switch_stops_ledger_collection_and_cleanup(self):
        self.assertIsNone(ledger_entry({'status': 'ok', 'id': 'ticket'}, self.device.pk, None))
        # Older mappings are collected once the switch is back on, so keep marking.
        note_registration(self.device.pk)
        self.assertEqual(cache.get(receipts._registration_key(self.device.pk)), 100000)
        collect_receipts()
        self.redis.lock.assert_not_called()
        self.client.check_receipts_multiple.assert_not_called()
        with patch('notifications.utils.PushClient') as client:
            client.return_value.publish.return_value = {'status': 'ok', 'id': 'ticket'}
            send_push_notification('body', device_token_ids=[self.device.pk])
        self.redis.register_script.assert_not_called()

    def test_send_maps_success_with_flag_off_without_content(self):
        with override_settings(NOTIFICATION_TELEMETRY_ENABLED=False), patch('notifications.utils.PushClient') as client:
            client.return_value.publish.return_value = {'status': 'ok', 'id': 'ticket'}
            send_push_notification('private body', {'private': 'content'}, device_token_ids=[self.device.pk])
        [(mapping, call)] = self.enqueued()
        self.assertEqual(mapping['device_pk'], self.device.pk)
        self.assertIsNone(mapping['notif_id'])
        self.assertEqual(call['keys'], [_key('pending'), _key('ticket')])
        self.assertEqual(call['args'][1], TTL)
        # The first poll waits one interval: receipts are seldom ready at send time.
        self.assertEqual(call['args'][2], 100000 + RETRY_DELAY)
        self.assertNotIn('private', call['args'][0])
        self.assertNotIn(self.device.token, call['args'][0])
        self.assertIs(call['client'], self.pipe)
        self.pipe.execute.assert_called_once()

    def test_mapping_validation_and_cache_failure_are_safe(self):
        for ticket in ({'status': 'error', 'id': 'ticket'}, {'status': 'ok'}, {'status': 'ok', 'id': 'private/token'}):
            self.assertIsNone(ledger_entry(ticket, self.device.pk, None))
        self.redis.register_script.side_effect = RuntimeError('secret')
        with self.assertLogs('notifications.receipts', level='WARNING') as logs:
            remember_tickets([ledger_entry({'status': 'ok', 'id': 'ticket'}, self.device.pk, None)])
        self.assertNotIn('secret', str(logs.output))

    def test_capacity_warning_is_constant_and_throttled(self):
        self.pipe.execute.return_value = [0]
        entry = ledger_entry({'status': 'ok', 'id': 'ticket'}, self.device.pk, None)
        with patch.object(receipts, '_next_capacity_warning', 0), patch(
            'notifications.receipts.time.monotonic', side_effect=[100, 101, 160],
        ), patch.object(receipts.logger, 'warning') as warning:
            for _ in range(3):
                remember_tickets([entry])
        self.assertEqual(warning.call_count, 2)
        for call in warning.call_args_list:
            self.assertEqual(call.args, ('Push receipt ledger insert skipped',))

    def test_receipt_cache_has_isolated_short_timeout_pool(self):
        config = {'default': {
            'BACKEND': 'django_redis.cache.RedisCache', 'LOCATION': 'rediss://localhost/1',
            'KEY_PREFIX': 'bahk', 'OPTIONS': {
                'CONNECTION_POOL_CLASS': 'redis.BlockingConnectionPool',
                'CONNECTION_POOL_CLASS_KWARGS': {'timeout': 20},
                'CONNECTION_POOL_KWARGS': {'ssl_cert_reqs': None},
            },
        }}
        with override_settings(CACHES=config), patch.object(receipts, 'CacheHandler') as handler:
            # Bypass memoization so this test cannot replace the shared test cache.
            receipts._receipt_cache.__wrapped__()
        isolated = handler.call_args.args[0]['default']
        self.assertEqual(isolated['KEY_PREFIX'], 'bahk')
        options = isolated['OPTIONS']
        self.assertEqual(options['CONNECTION_POOL_CLASS'], 'redis.ConnectionPool')
        self.assertNotIn('CONNECTION_POOL_CLASS_KWARGS', options)
        pool = options['CONNECTION_POOL_KWARGS']
        self.assertEqual(pool['socket_timeout'], receipts.REDIS_TIMEOUT)
        self.assertEqual(pool['socket_connect_timeout'], receipts.REDIS_TIMEOUT)
        self.assertIsNone(pool['ssl_cert_reqs'])
        self.assertFalse(pool['retry_on_timeout'])
        self.assertNotIn('SOCKET_TIMEOUT', config['default']['OPTIONS'])

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

    def test_enabled_send_preserves_correlation_in_one_round_trip(self):
        with patch('notifications.utils.PushClient') as client:
            client.return_value.publish.return_value = {'status': 'ok', 'id': 'ticket'}
            send_push_notification('private body', device_token_ids=[self.device.pk, self.other.pk], kind='test')
        entries = self.enqueued()
        self.assertEqual(len(entries), 2)
        self.pipe.execute.assert_called_once()
        sends = Event.objects.filter(event_type__code=EventType.NOTIFICATION_SEND)
        self.assertEqual({mapping['notif_id'] for mapping, _ in entries},
                         {event.data['notif_id'] for event in sends})
        self.assertEqual(len(entries), sends.count())
        mapping, call = entries[0]
        self.assertEqual(mapping['kind'], 'test')
        self.assertEqual(call['args'][3], CAPACITY)
        self.assertEqual(mapping['expires_at'], 100000 + TTL)


class FakeExpoSession:
    """Stands in for requests.Session: Expo's HTTP is the only faked layer."""

    def __init__(self, receipts):
        self.receipts = receipts
        self.requests = []

    def post(self, url, json=None, timeout=None, **kwargs):
        self.requests.append((url, json, timeout))
        data = {ticket_id: self.receipts[ticket_id] for ticket_id in json['ids'] if ticket_id in self.receipts}
        return SimpleNamespace(json=lambda: {'data': data}, raise_for_status=lambda: None)


@override_settings(NOTIFICATION_TELEMETRY_ENABLED=True)
class RedisLedgerTests(BaseTestCase):
    """Real Redis; CI requires availability, local development may skip."""

    def setUp(self):
        self.redis = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=5)
        try:
            self.redis.ping()
        except redis.exceptions.RedisError:
            if os.environ.get('REQUIRE_REDIS_TESTS') == '1':
                self.fail('Redis is required but unreachable; check REDIS_URL and the CI Redis service')
            self.skipTest('Redis is not reachable')
        namespace = f'push-receipts-test:{uuid.uuid4().hex}:'
        for target, value in (('NAMESPACE', namespace), ('get_redis_connection', lambda alias: self.redis)):
            patcher = patch.object(receipts, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lambda: [self.redis.delete(key) for key in self.redis.scan_iter(match=f'*{namespace}*')])
        self.clock = 100000.0
        clock = patch('notifications.receipts._now', side_effect=lambda: self.clock)
        clock.start()
        self.addCleanup(clock.stop)
        self.device = DeviceToken.objects.create(user=self.create_user(), token='ExponentPushToken[live]', device_type='ios')

    def remember(self, *ticket_ids, telemetry=None):
        remember_tickets([ledger_entry({'status': 'ok', 'id': ticket_id}, self.device.pk, telemetry)
                          for ticket_id in ticket_ids])

    def collect(self, receipts_by_id):
        session = FakeExpoSession(receipts_by_id)
        with patch.object(receipts, 'PushClient', lambda **kwargs: PushClient(session=session, **kwargs)):
            collect_receipts()
        return session

    def pending(self):
        return {member.decode(): score for member, score in self.redis.zrange(_key('pending'), 0, -1, withscores=True)}

    def test_enqueue_is_atomic_bounded_and_never_extends_retention(self):
        with patch.object(receipts, 'CAPACITY', 2):
            self.remember('a', 'b', 'c')
        self.assertEqual(self.pending(), {'a': 100000 + RETRY_DELAY, 'b': 100000 + RETRY_DELAY})
        self.assertLessEqual(self.redis.ttl(_key('a')), TTL)
        self.assertGreater(self.redis.ttl(_key('a')), TTL - 60)
        self.clock += 600
        self.remember('a')
        self.assertEqual(self.pending()['a'], 100000 + RETRY_DELAY)
        self.assertEqual(json.loads(self.redis.get(_key('a')))['sent_at'], 100000)

    def test_unready_receipt_is_due_at_the_next_tick(self):
        tick = 1_000_000.0
        self.clock = tick - RETRY_DELAY
        self.remember('ticket')
        # Each run starts a little after its 15-minute tick.
        self.clock = tick + 0.5
        session = self.collect({})
        self.assertEqual(len(session.requests), 1)
        self.clock = tick + 900 + 0.2
        session = self.collect({})
        self.assertEqual([request[1]['ids'] for request in session.requests], [['ticket']])

    def test_real_sdk_receipts_deactivate_and_record_once(self):
        self.remember('dead', 'alive', telemetry={'notif_id': 'notification-1', 'kind': 'test'})
        self.clock += RETRY_DELAY + 1
        session = self.collect({
            'dead': {'status': 'error', 'details': {'error': 'DeviceNotRegistered'}},
            'alive': {'status': 'ok'},
        })
        url, body, timeout = session.requests[0]
        self.assertTrue(url.endswith('/push/getReceipts'))
        self.assertEqual(sorted(body['ids']), ['alive', 'dead'])
        self.assertEqual(timeout, PROVIDER_TIMEOUT)
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_active)
        outcomes = Event.objects.filter(event_type__code=EventType.NOTIFICATION_RECEIPT).values_list(
            'data__ticket_id', 'data__outcome')
        self.assertEqual(sorted(outcomes), [('alive', 'delivered'), ('dead', 'provider_error')])
        self.assertEqual(self.pending(), {})
        self.assertIsNone(self.redis.get(_key('dead')))

    def test_held_lock_skips_the_run(self):
        self.remember('ticket')
        self.clock += RETRY_DELAY + 1
        held = self.redis.lock(_key('lock'), timeout=60)
        self.assertTrue(held.acquire(blocking=False))
        self.addCleanup(held.release)
        session = self.collect({'ticket': {'status': 'ok'}})
        self.assertEqual(session.requests, [])
        self.assertIn('ticket', self.pending())
