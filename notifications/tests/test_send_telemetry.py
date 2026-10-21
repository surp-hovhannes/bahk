"""Provider-boundary tests: no network, client sessions, or navigation persistence."""
import ast
import builtins
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from exponent_server_sdk import DeviceNotRegisteredError, PushTicket

from events.event_schema import EVENT_SCHEMA_VERSION, NOTIFICATION_DESTINATIONS, NOTIFICATION_KINDS
from events.models import Event, EventType
from notifications.models import DeviceToken
from notifications.tasks import send_push_notification_task, send_push_notification_to_users_task
from notifications.utils import EXPO_ERROR_CODES, _device_token_log_id, notification_destination, send_push_notification
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
        self.assertTrue(row.data['correlatable'])
        self.assert_private(row)

    def test_success_warning_and_telemetry_logs_are_private(self):
        with self.assertLogs('notifications.utils', level='INFO') as logs:
            self.send()
            self.publish.side_effect = DeviceNotRegisteredError(self.ticket('error', 'DeviceNotRegistered'))
            with patch.object(Event.objects, 'bulk_create', side_effect=ValueError(self.device.token)):
                self.send()
        for secret in (self.device.token, self.message, 'private-data-marker'):
            self.assertNotIn(secret, '\n'.join(logs.output))

    def test_other_rejected_tickets_keep_legacy_count_and_token(self):
        for code in sorted(EXPO_ERROR_CODES - {'DeviceNotRegistered'}) + ['private-error']:
            expected = code if code in EXPO_ERROR_CODES else 'other'
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

    def test_returned_dead_device_ticket_deactivates_token(self):
        for enabled in (True, False):
            for dictionary in (False, True):
                with self.subTest(telemetry=enabled, dictionary=dictionary), override_settings(
                    NOTIFICATION_TELEMETRY_ENABLED=enabled,
                ):
                    self.rows().delete()
                    DeviceToken.objects.filter(pk=self.device.pk).update(is_active=True)
                    self.publish.return_value = (
                        {'status': 'error', 'details': {'error': 'DeviceNotRegistered'}}
                        if dictionary else self.ticket('error', 'DeviceNotRegistered')
                    )
                    with self.assertLogs('notifications.utils', level='WARNING') as logs:
                        result = self.send()
                    self.assertEqual((result['sent'], result['failed'], result['success']), (0, 1, False))
                    token_id = _device_token_log_id(self.device.token)
                    self.assertEqual(result['invalid_tokens'], [token_id])
                    self.assertEqual(result['errors'], [])
                    self.assertIn(f'Device not registered: {token_id}', '\n'.join(logs.output))
                    self.assertNotIn(self.device.token, '\n'.join(logs.output))
                    self.device.refresh_from_db()
                    self.assertFalse(self.device.is_active)
                    if enabled:
                        row = self.rows().get()
                        self.assertEqual(row.data['outcome'], 'provider_rejected')
                        self.assertEqual(row.data['error_code'], 'DeviceNotRegistered')
                        self.assert_private(row)
                    else:
                        self.assertFalse(self.rows().exists())
                    self.publish.reset_mock()
                    self.send()
                    self.publish.assert_not_called()

    def test_mixed_batch_deactivates_only_dead_token(self):
        healthy = DeviceToken.objects.create(
            user=self.user, token='ExponentPushToken[healthy]', device_type='android',
        )
        self.publish.side_effect = lambda message: (
            self.ticket('error', 'DeviceNotRegistered')
            if message.to == self.device.token else self.ticket()
        )
        result = self.send()
        self.assertEqual((result['sent'], result['failed'], result['success']), (1, 1, True))
        self.assertEqual(result['invalid_tokens'], [_device_token_log_id(self.device.token)])
        self.device.refresh_from_db()
        healthy.refresh_from_db()
        self.assertFalse(self.device.is_active)
        self.assertTrue(healthy.is_active)
        self.assertEqual(self.rows().count(), 2)

    def test_send_exception_and_logs_are_private(self):
        self.publish.side_effect = RuntimeError(f'{self.device.token} {self.message} private-data-marker')
        with self.assertLogs('notifications.utils', level='INFO') as logs:
            result = self.send()
        self.assertEqual((result['sent'], result['failed']), (0, 1))
        self.assertEqual(result['errors'], ['RuntimeError'])
        row = self.rows().get()
        self.assertEqual(row.data['outcome'], 'send_error')
        self.assertNotIn('ticket_id', row.data)
        self.assert_private(row)
        for secret in (self.device.token, self.message, 'private-data-marker'):
            self.assertNotIn(secret, '\n'.join(logs.output))

    def test_legacy_device_not_registered_exception_branch(self):
        self.publish.side_effect = DeviceNotRegisteredError(self.ticket('error', 'DeviceNotRegistered'))
        result = self.send()
        self.assertEqual((result['sent'], result['failed']), (0, 1))
        self.assertEqual(result['invalid_tokens'], [_device_token_log_id(self.device.token)])
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

    @override_settings(NOTIFICATION_TELEMETRY_ENABLED=False)
    def test_flag_off_does_not_import_events_or_prepare_telemetry(self):
        original_import = builtins.__import__

        def without_events(name, *args, **kwargs):
            if name == 'events' or name.startswith('events.'):
                raise AssertionError(f'Flag-off send imported {name}')
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=without_events), patch(
            'notifications.utils._prepare_telemetry', side_effect=AssertionError('Telemetry prepared'),
        ), patch('notifications.utils.notification_destination', side_effect=AssertionError('Destination computed')):
            result = send_push_notification(
                self.message, self.data, users=[self.user], notification_type='culmination_feast',
            )
        self.assertEqual(result['sent'], 1)
        self.assertEqual(result['errors'], [])
        self.assertEqual(self.publish.call_args.args[0].data, self.data)
        self.assertFalse(self.rows().exists())

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
                    with patch.object(Event.objects, 'bulk_create', side_effect=error):
                        result = self.send()
                    self.assertEqual((result['sent'], result['failed']), (0, 1) if failed else (1, 0))
                    self.assertFalse(self.rows().exists())

    def test_database_error_isolated_by_savepoint(self):
        with patch.object(Event.objects, 'bulk_create', side_effect=IntegrityError('write failed')):
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
        self.assertFalse(row.data['correlatable'])

    def test_destination_only_returns_families(self):
        examples = [
            ({}, 'none'), ({'screen': 'fast/12345'}, 'fast'),
            ({'screen': 'devotional/12345'}, 'devotional'),
            ({'screen': 'prayer-set/12345'}, 'prayer_set'),
            ({'screen': 'prayer-request/12345'}, 'prayer_request'),
            ({'screen': 'prayer-requests'}, 'prayer_requests'), ({'screen': 'learn'}, 'other'),
            ({'fast_id': 12345}, 'fast'), ({'devotional_id': 12345}, 'devotional'),
            ({'prayer_set_id': 12345}, 'prayer_set'), ({'prayer_request_id': 12345}, 'prayer_request'),
            ({'screen': 'activity'}, 'activity'), ({'screen': 'fast/secret'}, 'other'),
            ({'screen': 'fast/12345/private'}, 'other'), ({'screen': {}}, 'other'),
        ]
        examples += [({'screen': route}, family) for route, family in [
            ('fast/48', 'fast'), ('devotional/123', 'devotional'), ('prayer/77', 'prayer'),
            ('prayer-request/123', 'prayer_request'), ('prayer-requests', 'prayer_requests'),
            ('learn/video/902', 'learn'), ('learn/article/305', 'learn'),
            ('learn/recipe/120', 'learn'), ('prayer-set/44', 'prayer_set'), ('activity', 'activity'),
        ]]
        examples += [({'url': 'https://example.com/announcement'}, 'url'),
                     ({'screen': 'fast/48', 'params': {'source': 'push', 'ref': 'notification'}}, 'fast')]
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

    def production_notification_calls(self, source, path):
        names = {'send_push_notification', 'send_push_notification_task', 'send_push_notification_to_users_task'}
        calls = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            queued = isinstance(func, ast.Attribute) and func.attr in {'delay', 'apply_async'}
            if queued:
                func = func.value
            if not isinstance(func, ast.Name) or func.id not in names:
                continue
            if queued and func.id != 'send_push_notification':
                self.assertFalse(
                    any(kw.arg == 'kind' for kw in node.keywords),
                    f'{path}:{node.lineno}: queued kinds must use telemetry_task_kwargs',
                )
            calls.append((path, node))
        return calls

    def test_audit_rejects_raw_kind_on_queued_tasks(self):
        for task in ('send_push_notification_task', 'send_push_notification_to_users_task'):
            for method in ('delay', 'apply_async'):
                with self.subTest(task=task, method=method):
                    with self.assertRaisesRegex(AssertionError, 'queued kinds must use telemetry_task_kwargs'):
                        self.production_notification_calls(
                            f"{task}.{method}('message', kind='admin_manual')", Path('synthetic.py'),
                        )

    def test_all_production_callers_pass_allowlisted_kind(self):
        root = Path(__file__).resolve().parents[2]
        calls = []
        packages = [path for path in root.iterdir() if path.is_dir() and (path / 'apps.py').exists()]
        for package in packages:
            for path in package.rglob('*.py'):
                if any(part in {'tests', 'migrations', 'venv', '.venv'} for part in path.parts) or path.name.startswith('test'):
                    continue
                calls.extend(self.production_notification_calls(path.read_text(), path))
        self.assertTrue(calls)
        validated = []
        for path, node in calls:
            kinds = [kw.value for kw in node.keywords if kw.arg == 'kind']
            for kw in node.keywords:
                if kw.arg is None and isinstance(kw.value, ast.Call):
                    self.assertEqual(ast.unparse(kw.value.func), 'telemetry_task_kwargs')
                    kinds.extend(kw.value.args)
            if not kinds:
                kinds = [kw.value for kw in node.keywords if kw.arg == 'notification_type']
                if not kinds and len(node.args) >= 4:
                    kinds = [node.args[3]]
            self.assertEqual(len(kinds), 1, f'{path}:{node.lineno}')
            kind = kinds[0]
            if isinstance(kind, ast.Name):
                self.assertEqual((path.name, ast.unparse(node.func), kind.id), ('tasks.py', 'send_push_notification', 'kind'))
            else:
                self.assertIsInstance(kind, ast.Constant)
                self.assertIn(kind.value, NOTIFICATION_KINDS)
            validated.append(node)
        self.assertEqual(len(validated), len(calls))

    def test_preparation_failure_keeps_uncorrelatable_outcome(self):
        dumps = json.dumps

        def envelope_failure(value, *args, **kwargs):
            if isinstance(value, dict) and 'body' in value:
                raise ValueError('private')
            return dumps(value, *args, **kwargs)

        with patch('notifications.utils.json.dumps', side_effect=envelope_failure):
            self.assertEqual(self.send()['sent'], 1)
        self.assertFalse(self.rows().get().data['correlatable'])
        self.assertEqual(self.publish.call_args.args[0].data, self.data)

    def test_one_type_lookup_and_bulk_write_for_multiple_tokens(self):
        DeviceToken.objects.create(user=self.user, token='ExponentPushToken[second]', device_type='android')
        with patch.object(EventType.objects, 'filter', wraps=EventType.objects.filter) as lookup:
            with patch.object(Event.objects, 'bulk_create', wraps=Event.objects.bulk_create) as write:
                self.assertEqual(self.send()['sent'], 2)
        lookup.assert_called_once_with(code=EventType.NOTIFICATION_SEND, is_active=True)
        write.assert_called_once()
        self.assertEqual(len(write.call_args.args[0]), 2)

    def test_type_lookup_database_error_preserves_caller_transaction(self):
        def failed_lookup():
            # A real SQL error aborts PostgreSQL transactions until rollback.
            with connection.cursor() as cursor:
                cursor.execute('SELECT bahk_telemetry_missing_column FROM events_eventtype')

        DeviceToken.objects.filter(pk=self.device.pk).update(last_used=None)
        with transaction.atomic():
            with patch.object(EventType.objects, 'filter') as lookup:
                lookup.return_value.first.side_effect = failed_lookup
                with self.assertLogs('notifications.utils', level='WARNING') as logs:
                    result = self.send()
            self.assertEqual((result['sent'], result['failed'], result['success']), (1, 0, True))
            self.assertEqual(result['errors'], [])
            self.device.refresh_from_db()
            self.assertIsNotNone(self.device.last_used)
            self.assertFalse(self.rows().exists())
            self.assertEqual(self.publish.call_args.args[0].data, self.data)
            self.assertFalse(connection.needs_rollback)
        self.assertEqual(logs.output, ['WARNING:notifications.utils:Push telemetry type unavailable'])
        self.publish.assert_called_once()

    def test_notification_type_supplies_default_kind(self):
        send_push_notification(self.message, self.data, users=[self.user], notification_type='culmination_feast')
        self.assertEqual(self.rows().get().data['kind'], 'culmination_feast')
