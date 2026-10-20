"""Provider-boundary tests: no network, client sessions, or navigation persistence."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.db import IntegrityError
from django.test import override_settings
from exponent_server_sdk import DeviceNotRegisteredError, PushTicket

from events.event_schema import EVENT_SCHEMA_VERSION, NOTIFICATION_DESTINATIONS, NOTIFICATION_KINDS
from events.models import Event, EventType
from notifications.models import DeviceToken
from notifications.tasks import send_push_notification_task, send_push_notification_to_users_task
from notifications.utils import notification_destination, send_push_notification
from tests.base import BaseTestCase


@override_settings(NOTIFICATION_TELEMETRY_ENABLED=True)
class SendTelemetryTests(BaseTestCase):
    def setUp(self):
        self.user = self.create_user()
        self.device = DeviceToken.objects.create(
            user=self.user, token='ExponentPushToken[telemetry-private]', device_type='ios',
        )
        self.data = {'screen': 'prayer-request/12345', 'private': 'private-data-marker'}
        self.message = 'private-message-marker'
        self.client_patch = patch('notifications.utils.PushClient')
        self.publish = self.client_patch.start().return_value.publish
        self.addCleanup(self.client_patch.stop)
        self.publish.return_value = self.ticket()

    def ticket(self, status='ok', code=None, ticket_id='opaque-ticket-123'):
        return PushTicket(
            push_message=None, status=status, message=None,
            details={'error': code} if code else None, id=ticket_id,
        )

    def send(self, **kwargs):
        return send_push_notification(
            self.message, self.data, users=[self.user], kind='prayer_thanks', **kwargs,
        )

    def rows(self):
        return Event.objects.filter(event_type__code=EventType.NOTIFICATION_SEND)

    def assert_private(self, row):
        serialized = json.dumps(row.data)
        for secret in (self.device.token, self.message, 'private-data-marker', '12345', 'screen'):
            self.assertNotIn(secret, serialized)
        self.assertIsNone(row.ip_address)
        self.assertEqual(row.user_agent, '')
        self.assertEqual(row.description, '')
        self.assertEqual(row.title, 'Notification send')
        self.assertEqual(row.user, self.user)
        self.assertEqual(row.data['schema_version'], EVENT_SCHEMA_VERSION)

    def test_accepted_ticket(self):
        result = self.send()
        self.assertEqual((result['sent'], result['failed'], result['success']), (1, 0, True))
        row = self.rows().get()
        self.assertEqual(row.data['outcome'], 'provider_accepted')
        self.assertEqual(row.data['kind'], 'prayer_thanks')
        self.assertEqual(row.data['destination'], 'prayer_request')
        self.assertEqual(row.data['platform'], 'ios')
        self.assertEqual(row.data['source'], 'server_domain')
        self.assertEqual(row.data['ticket_id'], 'opaque-ticket-123')
        payload = self.publish.call_args.args[0].data
        self.assertEqual(payload['fp_nid'], row.data['notif_id'])
        self.assertRegex(payload['fp_nid'], r'^[A-Za-z0-9_-]{16}$')
        self.assertEqual(payload['fp_kind'], 'prayer_thanks')
        self.assert_private(row)

    def test_success_warning_and_telemetry_logs_are_private(self):
        with self.assertLogs('notifications.utils', level='INFO') as logs:
            self.send()
            self.publish.side_effect = DeviceNotRegisteredError(self.ticket('error', 'DeviceNotRegistered'))
            with patch.object(Event, 'create_event', side_effect=ValueError(self.device.token)):
                self.send()
        for secret in (self.device.token, self.message, 'private-data-marker'):
            self.assertNotIn(secret, '\n'.join(logs.output))

    def test_rejected_ticket_keeps_legacy_count_and_token(self):
        for code, expected in [('DeviceNotRegistered', 'DeviceNotRegistered'), ('private-error', 'other')]:
            with self.subTest(code=code):
                self.rows().delete()
                self.publish.return_value = self.ticket('error', code)
                result = self.send()
                self.assertEqual((result['sent'], result['failed']), (1, 0))
                row = self.rows().get()
                self.assertEqual(row.data['outcome'], 'provider_rejected')
                self.assertEqual(row.data['error_code'], expected)
                self.assert_private(row)
                self.device.refresh_from_db()
                self.assertTrue(self.device.is_active)

    def test_send_exception_and_logs_are_private(self):
        self.publish.side_effect = RuntimeError(f'{self.device.token} {self.message} private-data-marker')
        with self.assertLogs('notifications.utils', level='INFO') as logs:
            result = self.send()
        self.assertEqual((result['sent'], result['failed']), (0, 1))
        row = self.rows().get()
        self.assertEqual(row.data['outcome'], 'send_error')
        self.assertNotIn('ticket_id', row.data)
        self.assert_private(row)
        for secret in (self.device.token, self.message, 'private-data-marker'):
            self.assertNotIn(secret, '\n'.join(logs.output))

    def test_device_not_registered_exception_preserves_deactivation(self):
        self.publish.side_effect = DeviceNotRegisteredError(self.ticket('error', 'DeviceNotRegistered'))
        result = self.send()
        self.assertEqual((result['sent'], result['failed']), (0, 1))
        self.assertEqual(result['invalid_tokens'], [self.device.token])
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_active)
        row = self.rows().get()
        self.assertEqual(row.data['outcome'], 'provider_rejected')
        self.assertEqual(row.data['error_code'], 'DeviceNotRegistered')
        self.assert_private(row)

    @override_settings(NOTIFICATION_TELEMETRY_ENABLED=False)
    def test_flag_off(self):
        self.send()
        self.assertFalse(self.rows().exists())
        self.assertEqual(self.publish.call_args.args[0].data, self.data)
        self.assertFalse({'fp_nid', 'fp_kind'} & self.publish.call_args.args[0].data.keys())

    def test_recipients_get_distinct_copies_and_ids(self):
        DeviceToken.objects.create(user=self.user, token='ExponentPushToken[second-private]', device_type='android')
        original = self.data.copy()
        result = self.send()
        self.assertEqual(result['sent'], 2)
        payloads = [call.args[0].data for call in self.publish.call_args_list]
        self.assertIsNot(payloads[0], payloads[1])
        self.assertNotEqual(payloads[0]['fp_nid'], payloads[1]['fp_nid'])
        self.assertEqual(self.data, original)
        self.assertEqual(self.rows().count(), 2)
        self.assertEqual(set(self.rows().values_list('data__platform', flat=True)), {'ios', 'android'})

    def test_telemetry_failures_do_not_change_counts(self):
        for error in [ValueError('inactive'), IntegrityError('write failed')]:
            for failed in (False, True):
                with self.subTest(error=type(error).__name__, failed=failed):
                    self.publish.side_effect = RuntimeError('send failed') if failed else None
                    with patch.object(Event, 'create_event', side_effect=error):
                        result = self.send()
                    self.assertEqual((result['sent'], result['failed']), (0, 1) if failed else (1, 0))
                    self.assertFalse(self.rows().exists())

    def test_database_error_isolated_by_savepoint(self):
        with patch.object(Event.objects, 'create', side_effect=IntegrityError('write failed')):
            self.assertEqual(self.send()['sent'], 1)
        self.assertTrue(DeviceToken.objects.filter(pk=self.device.pk).exists())
        self.assertFalse(self.rows().exists())

    def test_missing_and_inactive_type_skip(self):
        row = EventType.objects.get(code=EventType.NOTIFICATION_SEND)
        row.is_active = False
        row.save()
        self.assertEqual(self.send()['sent'], 1)
        row.delete()
        self.assertEqual(self.send()['sent'], 1)
        self.assertFalse(self.rows().exists())

    def test_missing_ticket_id_and_unknown_ticket_shape(self):
        for ticket in [self.ticket(ticket_id=None), {'status': 'ok'}, SimpleNamespace()]:
            with self.subTest(ticket=ticket):
                self.rows().delete()
                self.publish.return_value = ticket
                self.assertEqual(self.send()['sent'], 1)
                row = self.rows().get()
                self.assertNotIn('ticket_id', row.data)
                self.assert_private(row)

    def test_unknown_kind_platform_and_oversize_payload(self):
        DeviceToken.objects.filter(pk=self.device.pk).update(device_type=None)
        self.data['private'] = 'x' * 4096
        result = send_push_notification(self.message, self.data, users=[self.user], kind='private-kind')
        self.assertEqual(result['sent'], 1)
        self.assertEqual(self.publish.call_args.args[0].data, self.data)
        row = self.rows().get()
        self.assertEqual(row.data['kind'], 'unclassified')
        self.assertEqual(row.data['platform'], 'unknown')
        self.assertNotIn('private-kind', json.dumps(row.data))

    def test_destination_only_returns_families(self):
        examples = [
            ({}, 'none'), ({'screen': 'fast/12345'}, 'fast'),
            ({'screen': 'devotional/12345'}, 'devotional'),
            ({'screen': 'prayer-set/12345'}, 'prayer_set'),
            ({'screen': 'prayer-request/12345'}, 'prayer_request'),
            ({'screen': 'prayer-requests'}, 'prayer_requests'), ({'screen': 'learn'}, 'learn'),
            ({'fast_id': 12345}, 'fast'), ({'devotional_id': 12345}, 'devotional'),
            ({'prayer_set_id': 12345}, 'prayer_set'), ({'prayer_request_id': 12345}, 'prayer_request'),
            ({'screen': 'activity'}, 'other'), ({'screen': 'fast/secret'}, 'other'),
            ({'screen': 'fast/12345/private'}, 'other'), ({'screen': {}}, 'other'),
        ]
        for data, expected in examples:
            with self.subTest(data=data):
                self.assertEqual(notification_destination(data), expected)
                self.assertIn(notification_destination(data), NOTIFICATION_DESTINATIONS)

    def test_tasks_forward_kind_and_preserve_returns(self):
        result = {'sent': 1, 'failed': 0, 'invalid_tokens': []}
        with patch('notifications.tasks.send_push_notification', return_value=result) as send:
            self.assertEqual(send_push_notification_task(
                'message', user_ids=[self.user.id], notification_type='daily_fast', kind='daily_fast',
            ), result)
            self.assertEqual(send.call_args.kwargs['kind'], 'daily_fast')
            self.assertEqual(send.call_args.kwargs['notification_type'], 'daily_fast')
            self.assertIsNone(send_push_notification_to_users_task(
                'message', device_token_ids=list(range(1, 102)), kind='admin_manual',
            ))
            self.assertEqual(send.call_count, 3)
            self.assertEqual(send.call_args.kwargs['kind'], 'admin_manual')
            self.assertEqual(send.call_args.kwargs['device_token_ids'], [101])

    def test_all_production_callers_pass_allowlisted_kind(self):
        root = Path(__file__).resolve().parents[2]
        names = {'send_push_notification', 'send_push_notification_task', 'send_push_notification_to_users_task'}
        count = 0
        for package in ['notifications', 'events', 'prayers']:
            for path in (root / package).glob('*.py'):
                for node in ast.walk(ast.parse(path.read_text())):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    if isinstance(func, ast.Attribute) and func.attr == 'delay':
                        func = func.value
                    if not isinstance(func, ast.Name) or func.id not in names:
                        continue
                    count += 1
                    kinds = [kw.value for kw in node.keywords if kw.arg == 'kind']
                    self.assertEqual(len(kinds), 1, f'{path}:{node.lineno}')
                    kind = kinds[0]
                    if isinstance(kind, ast.Name):
                        self.assertEqual((path.name, func.id, kind.id), ('tasks.py', 'send_push_notification', 'kind'))
                    else:
                        self.assertIsInstance(kind, ast.Constant)
                        self.assertIn(kind.value, NOTIFICATION_KINDS)
        self.assertEqual(count, 18)
