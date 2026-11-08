"""Mocked Expo transport regressions; never contact the provider."""
from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import override_settings
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from exponent_server_sdk import PushClient, PushServerError
from requests import Response
from requests.exceptions import ConnectionError, HTTPError, ReadTimeout

from notifications.models import DeviceToken
from events.models import Event, EventType
from tests.base import BaseTestCase
from notifications.utils import send_push_notification


@override_settings(NOTIFICATION_TELEMETRY_ENABLED=True)
@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}})
class TransportTests(BaseTestCase):
    def setUp(self):
        self.user = self.create_user()
        self.device = DeviceToken.objects.create(
            user=self.user, token='ExponentPushToken[transport-private]', device_type='ios',
        )
        client_patch = patch('notifications.utils.PushClient')
        self.publish = client_patch.start().return_value.publish
        self.addCleanup(client_patch.stop)

    def ticket(self, ticket_id='opaque-ticket'):
        from exponent_server_sdk import PushTicket
        return PushTicket(push_message=None, status='ok', message=None, details=None, id=ticket_id)

    def send(self):
        return send_push_notification('private-message', users=[self.user], kind='prayer_thanks')

    def rows(self):
        return Event.objects.filter(event_type__code=EventType.NOTIFICATION_SEND)

    def rejection(self, status, structured=True, data=False):
        response = Response()
        response.status_code = status
        response._content = b'private-response ExponentPushToken[secret]'
        if not structured:
            return HTTPError('private-response', response=response)
        body = {'errors': [{'message': 'private-response'}]}
        if data:
            body['data'] = [{'status': 'ok', 'id': 'accepted'}]
        return PushServerError('private-response', response, response_data=body, errors=body['errors'])

    def test_safe_recovery_preserves_one_accepted_event_and_receipt(self):
        self.publish.side_effect = [self.rejection(429), self.rejection(503), self.ticket()]
        with patch('notifications.utils.time.sleep') as sleep, patch('notifications.receipts.remember_tickets') as remember:
            result = self.send()
        self.assertEqual((result['sent'], result['failed']), (1, 0))
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])
        self.assertEqual(self.publish.call_count, 3)
        self.assertEqual(len(remember.call_args.args[0]), 1)
        self.assertEqual(self.rows().get().data['outcome'], 'provider_accepted')
        self.assertEqual(len({c.args[0].data['fp_nid'] for c in self.publish.call_args_list}), 1)

    def test_retry_exhaustion_is_bounded(self):
        self.publish.side_effect = self.rejection(503)
        with patch('notifications.utils.time.sleep') as sleep:
            result = self.send()
        self.assertEqual((result['sent'], result['failed']), (0, 1))
        self.assertEqual(self.publish.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(result['transport_errors'][-1]['attempt'], 3)
        self.assertEqual(self.rows().get().data['outcome'], 'send_error')

    def test_ambiguous_and_permanent_failures_never_retry(self):
        cases = [
            (self.rejection(400), 'permanent_reject', 400),
            (self.rejection(401, False), 'permanent_reject', 401),
            (self.rejection(503, False), 'ambiguous_acceptance', 503),
            (self.rejection(429, False), 'ambiguous_acceptance', 429),
            (self.rejection(503, data=True), 'ambiguous_acceptance', 503),
            (ReadTimeout('private-response'), 'ambiguous_acceptance', None),
            (ConnectionError('private-response'), 'ambiguous_acceptance', None),
            (self.rejection(200, False), 'ambiguous_acceptance', 200),
        ]
        for error, classification, status in cases:
            with self.subTest(classification=classification, status=status):
                self.publish.reset_mock()
                self.rows().delete()
                self.publish.side_effect = error
                with patch('notifications.utils.time.sleep') as sleep, self.assertLogs('notifications.utils') as logs:
                    result = self.send()
                self.publish.assert_called_once()
                sleep.assert_not_called()
                self.assertEqual(result['transport_errors'], [{
                    'operation': 'publish', 'classification': classification, 'http_status': status, 'attempt': 1,
                }])
                self.assertNotIn('private-response', str(result) + str(logs.output))
                self.assertNotIn(self.device.token, str(result) + str(logs.output))
                self.device.refresh_from_db()
                self.assertTrue(self.device.is_active)

    def test_consent_is_rechecked_after_backoff(self):
        self.publish.side_effect = self.rejection(503)
        def revoke(_delay):
            DeviceToken.objects.filter(pk=self.device.pk).update(is_active=False)
        with patch('notifications.utils.time.sleep', side_effect=revoke):
            result = self.send()
        self.assertEqual((result['failed'], result['skipped']), (0, 1))
        self.assertFalse(self.rows().exists())
        self.publish.assert_called_once()

    def test_accepted_device_is_not_replayed_for_next_device_failure(self):
        DeviceToken.objects.create(user=self.user, token='ExponentPushToken[second]', device_type='ios')
        self.publish.side_effect = [self.ticket(), self.rejection(503), self.ticket(ticket_id='second')]
        with patch('notifications.utils.time.sleep'):
            result = self.send()
        self.assertEqual(result['sent'], 2)
        tokens = [c.args[0].to for c in self.publish.call_args_list]
        self.assertNotEqual(tokens[0], tokens[1])
        self.assertEqual(tokens[1], tokens[2])

    def test_later_device_revoked_during_earlier_backoff_is_never_attempted(self):
        from hub.models import Profile

        Profile.objects.create(user=self.user, receive_daily_fast_push_notifications=True)
        later_user = self.create_user()
        later_profile = Profile.objects.create(user=later_user, receive_daily_fast_push_notifications=True)
        later_device = DeviceToken.objects.create(
            user=later_user, token='ExponentPushToken[later-private]', device_type='ios',
        )
        # DeviceToken sends newest first; make this device deterministically later.
        DeviceToken.objects.filter(pk=later_device.pk).update(created_at=self.device.created_at - timedelta(seconds=1))
        for revocation in ('preference', 'inactive', 'replacement'):
            with self.subTest(revocation=revocation):
                later_profile.receive_daily_fast_push_notifications = True
                later_profile.save()
                DeviceToken.objects.filter(pk=later_device.pk).update(is_active=True, token=later_device.token)
                self.rows().delete()
                self.publish.reset_mock()
                self.publish.side_effect = [self.rejection(503), self.ticket()]

                def revoke(_delay):
                    if revocation == 'preference':
                        later_profile.receive_daily_fast_push_notifications = False
                        later_profile.save()
                    elif revocation == 'inactive':
                        DeviceToken.objects.filter(pk=later_device.pk).update(is_active=False)
                    else:
                        DeviceToken.objects.filter(pk=later_device.pk).update(token='ExponentPushToken[replacement]')

                with patch('notifications.utils.time.sleep', side_effect=revoke), \
                        patch('notifications.receipts.remember_tickets') as remember:
                    result = send_push_notification(
                        'test', users=[self.user, later_user], notification_type='daily_fast',
                    )
                self.assertEqual((result['sent'], result['failed']), (1, 0))
                self.assertEqual(result['skipped'], 1)
                self.assertEqual(result['errors'], [])
                self.assertEqual(len(result['transport_errors']), 1)
                self.assertEqual([call.args[0].to for call in self.publish.call_args_list], [self.device.token] * 2)
                self.assertEqual(self.rows().get().user_id, self.user.pk)
                self.assertEqual(len(remember.call_args.args[0]), 1)

    def test_real_sdk_json_and_non_json_failure_shapes(self):
        # Exercise the pinned SDK parser instead of assuming HTTPError for JSON.
        for structured in (True, False):
            response = Response()
            response.status_code = 503
            response._content = b'{"errors":[{"message":"private-response"}]}' if structured else b'private-response'
            session = Mock()
            session.post.return_value = response
            client = PushClient(session=session)
            from exponent_server_sdk import PushMessage
            with self.assertRaises(PushServerError if structured else HTTPError):
                client.publish(PushMessage(to=self.device.token, body='test'))

    def test_ambiguous_after_safe_rejection_stops_immediately(self):
        self.publish.side_effect = [self.rejection(503), ReadTimeout('private-response'), self.ticket()]
        with patch('notifications.utils.time.sleep') as sleep:
            result = self.send()
        self.assertEqual(self.publish.call_count, 2)
        sleep.assert_called_once_with(1)
        self.assertEqual(result['transport_errors'][-1]['attempt'], 2)
        self.assertEqual(result['transport_errors'][-1]['classification'], 'ambiguous_acceptance')

    @override_settings(NOTIFICATION_TELEMETRY_ENABLED=False)
    def test_flag_off_still_recovers_without_events(self):
        self.publish.side_effect = [self.rejection(429), self.ticket()]
        with patch('notifications.utils.time.sleep'):
            self.assertEqual(self.send()['sent'], 1)
        self.assertFalse(self.rows().exists())

    def test_malformed_status_and_errors_are_not_retry_evidence(self):
        for status, body in [('503 private-response', {'errors': [{}]}), (503, {'errors': 'private-response'})]:
            error = PushServerError('private-response', Mock(status_code=status), response_data=body)
            self.publish.reset_mock()
            self.publish.side_effect = error
            with patch('notifications.utils.time.sleep') as sleep:
                result = self.send()
            self.publish.assert_called_once()
            sleep.assert_not_called()
            self.assertEqual(result['transport_errors'][0]['classification'], 'ambiguous_acceptance')
            self.assertNotIn('private-response', str(result))

    def test_preference_revocation_during_backoff_stops_retry(self):
        from hub.models import Profile
        Profile.objects.create(user=self.user, receive_daily_fast_push_notifications=True)
        self.publish.side_effect = self.rejection(503)
        def revoke(_delay):
            self.user.profile.receive_daily_fast_push_notifications = False
            self.user.profile.save()
        with patch('notifications.utils.time.sleep', side_effect=revoke):
            result = send_push_notification('test', users=[self.user], notification_type='daily_fast')
        self.assertEqual((result['failed'], result['skipped']), (0, 1))
        self.assertFalse(self.rows().exists())
        self.publish.assert_called_once()

    def test_successful_send_never_retries_local_bookkeeping_failure(self):
        self.publish.return_value = self.ticket()
        with patch('django.db.models.query.QuerySet.update', side_effect=RuntimeError('private-response')):
            with patch('notifications.utils.time.sleep') as sleep:
                result = self.send()
        self.publish.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(result['transport_errors'], [])
        self.assertEqual(self.rows().get().data['outcome'], 'provider_accepted')

    def add_devices(self, count):
        return DeviceToken.objects.bulk_create([
            DeviceToken(user=self.user, token=f'ExponentPushToken[extra-{i}]', device_type='ios')
            for i in range(count)
        ])

    @override_settings(NOTIFICATION_TELEMETRY_ENABLED=False)
    def test_no_backoff_has_no_eligibility_queries(self):
        self.add_devices(19)
        self.publish.side_effect = lambda message: self.ticket()
        with patch('notifications.utils._still_eligible') as eligible, \
                patch('notifications.receipts.remember_tickets'), CaptureQueriesContext(connection) as queries:
            result = self.send()
        eligible.assert_not_called()
        selects = [q['sql'] for q in queries if q['sql'].lstrip().startswith('SELECT')]
        self.assertEqual(len(selects), 1)
        self.assertEqual(result['sent'], 20)
        self.assertEqual(sum(q['sql'].lstrip().startswith('UPDATE') for q in queries), 20)

    def test_delayed_checks_do_not_repeat_audience_in_sql(self):
        from hub.models import Profile
        from notifications.utils import _still_eligible
        Profile.objects.create(user=self.user, receive_daily_fast_push_notifications=True)
        self.add_devices(4)
        self.publish.side_effect = [self.rejection(503)] + [self.ticket()] * 5
        with patch('notifications.utils.time.sleep'), CaptureQueriesContext(connection) as queries, \
                patch('notifications.receipts.remember_tickets'), \
                patch('notifications.utils._still_eligible', wraps=_still_eligible) as eligible:
            result = send_push_notification(
                'test', users=[self.user.pk] + list(range(100000, 100200)), notification_type='daily_fast',
            )
        self.assertEqual(result['sent'], 5)
        self.assertEqual(eligible.call_count, 5)
        checks = [q['sql'] for q in queries if 'SELECT 1 AS' in q['sql']]
        self.assertEqual(len(checks), 5)
        self.assertTrue(all(' IN (' not in sql and '100199' not in sql for sql in checks))

    def test_circuit_stops_sustained_outage_and_preserves_accepted_ticket(self):
        self.add_devices(9)
        self.publish.side_effect = [self.ticket()] + [self.rejection(503)] * 3
        with patch('notifications.utils.time.sleep') as sleep, \
                patch('notifications.receipts.remember_tickets') as remember:
            result = self.send()
        self.assertEqual((result['sent'], result['failed'], result['skipped']), (1, 1, 8))
        self.assertTrue(result['circuit_open'])
        self.assertEqual(self.publish.call_count, 4)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])
        self.assertEqual(len(remember.call_args.args[0]), 1)
        self.assertEqual(self.rows().count(), 2)

    def test_call_wide_budget_survives_success_between_rejections(self):
        self.add_devices(4)
        self.publish.side_effect = [
            self.rejection(429), self.ticket(), self.rejection(503), self.ticket(), self.rejection(503),
        ]
        with patch('notifications.utils.time.sleep') as sleep:
            result = self.send()
        self.assertEqual((result['sent'], result['failed'], result['skipped']), (2, 1, 2))
        self.assertTrue(result['circuit_open'])
        self.assertEqual(sleep.call_count, 2)

    def test_mixed_ambiguous_accepted_results_are_bounded(self):
        self.add_devices(29)
        self.publish.side_effect = [ReadTimeout('private-response')] * 25 + [self.ticket()] * 5
        with patch('notifications.utils.time.sleep') as sleep, \
                patch('notifications.receipts.remember_tickets') as remember:
            result = self.send()
        self.assertEqual((result['sent'], result['failed'], result['skipped']), (5, 25, 0))
        self.assertFalse(result['circuit_open'])
        self.assertEqual(self.publish.call_count, 30)
        sleep.assert_not_called()
        self.assertEqual(len(result['transport_errors']), 20)
        self.assertEqual(result['transport_errors_omitted'], 5)
        self.assertEqual(len(result['errors']), 20)
        self.assertNotIn('private-response', str(result))
        self.assertEqual(len(remember.call_args.args[0]), 5)

    def test_database_recheck_failure_keeps_previous_acceptance_and_continues(self):
        self.add_devices(2)
        self.publish.side_effect = [self.rejection(503), self.ticket(), self.ticket()]
        from notifications.utils import _still_eligible
        calls = 0
        def check(device, notification_type):
            nonlocal calls
            calls += 1
            if calls == 2:
                # A real SQL failure inside the helper's savepoint must leave
                # the caller's outer transaction usable.
                with connection.execute_wrapper(fail_select):
                    return _still_eligible(device, notification_type)
            return _still_eligible(device, notification_type)
        def fail_select(execute, sql, params, many, context):
            if sql.startswith('SELECT'):
                return execute('SELECT * FROM nonexistent_transport_table', (), False, context)
            return execute(sql, params, many, context)
        with transaction.atomic(), patch('notifications.utils._still_eligible', side_effect=check), \
                patch('notifications.utils.time.sleep'), patch('notifications.receipts.remember_tickets') as remember:
            result = self.send()
            self.assertEqual(self.rows().count(), 2)
        self.assertEqual((result['sent'], result['failed'], result['skipped']), (2, 1, 0))
        self.assertEqual(len(remember.call_args.args[0]), 2)
        self.assertTrue(all(row.data['outcome'] == 'provider_accepted' for row in self.rows()))

    def test_database_failure_after_acceptance_rolls_back_bookkeeping_only(self):
        self.add_devices(1)
        self.publish.side_effect = lambda message: self.ticket()
        failed = False
        def fail_first_update(execute, sql, params, many, context):
            nonlocal failed
            if sql.startswith('UPDATE') and not failed:
                failed = True
                # SQLite marks a genuine failed statement within the savepoint.
                return execute('UPDATE nonexistent_transport_table SET missing=1', (), False, context)
            return execute(sql, params, many, context)
        with transaction.atomic(), connection.execute_wrapper(fail_first_update), \
                patch('notifications.receipts.remember_tickets') as remember:
            result = self.send()
            self.assertEqual(self.rows().count(), 2)
        self.assertEqual((result['sent'], result['failed']), (2, 0))
        self.assertEqual(self.publish.call_count, 2)
        self.assertEqual(len(remember.call_args.args[0]), 2)
        self.assertTrue(all(row.data['outcome'] == 'provider_accepted' for row in self.rows()))

    def test_dead_token_cleanup_database_failure_does_not_abandon_batch(self):
        from exponent_server_sdk import DeviceNotRegisteredError, PushTicket
        self.add_devices(2)
        for raises in (False, True):
            with self.subTest(raises=raises):
                self.rows().delete()
                self.publish.reset_mock()
                dead = PushTicket(None, 'error', None, {'error': 'DeviceNotRegistered'}, None)
                self.publish.side_effect = [
                    self.ticket(), DeviceNotRegisteredError(dead) if raises else dead, self.ticket(),
                ]
                def fail_cleanup(execute, sql, params, many, context):
                    if sql.startswith('UPDATE') and 'is_active' in sql:
                        return execute('UPDATE nonexistent_transport_table SET missing=1', (), False, context)
                    return execute(sql, params, many, context)
                with transaction.atomic(), connection.execute_wrapper(fail_cleanup), \
                        patch('notifications.receipts.remember_tickets') as remember:
                    result = self.send()
                    self.assertEqual(self.rows().count(), 3)
                self.assertEqual((result['sent'], result['failed'], result['skipped']), (2, 1, 0))
                self.assertEqual(len(remember.call_args.args[0]), 2)
                self.assertEqual(self.publish.call_count, 3)
                outcomes = list(self.rows().values_list('data__outcome', flat=True))
                self.assertEqual(outcomes.count('provider_accepted'), 2)
                self.assertEqual(outcomes.count('provider_rejected'), 1)
