"""Mocked Expo transport regressions; never contact the provider."""
from unittest.mock import Mock, patch

from django.test import override_settings
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
            self.assertEqual(self.send()['failed'], 1)
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
        self.assertEqual(result['failed'], 1)
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
