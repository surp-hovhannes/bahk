from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from bahk.celery import _redact_url_for_logs, check_redis_connectivity, log_worker_ready


class RedisConnectivityLoggingTests(SimpleTestCase):
    def test_redact_url_for_logs_strips_credentials(self):
        safe_url = _redact_url_for_logs("rediss://user:secret@example.test:6380/1")

        self.assertEqual(safe_url, "rediss://example.test:6380/1")
        self.assertNotIn("user", safe_url)
        self.assertNotIn("secret", safe_url)

    @patch("bahk.celery.sentry_sdk.capture_exception")
    @patch("bahk.celery.sentry_sdk.capture_message")
    @patch("bahk.celery.app")
    @patch("redis.from_url")
    def test_redis_success_log_redacts_broker_credentials(
        self,
        mock_from_url,
        mock_app,
        mock_capture_message,
        mock_capture_exception,
    ):
        mock_app.conf.broker_url = "redis://:super-secret@example.test:6379/0"
        mock_app.conf.get.return_value = None
        mock_connection = Mock()
        mock_from_url.return_value = mock_connection

        with self.assertLogs("bahk.celery", level="INFO") as captured_logs:
            check_redis_connectivity()

        self.assertIn("redis://example.test:6379/0", "\n".join(captured_logs.output))
        self.assertNotIn("super-secret", "\n".join(captured_logs.output))
        mock_connection.ping.assert_called_once()
        mock_capture_message.assert_not_called()
        mock_capture_exception.assert_not_called()

    @patch("bahk.celery.sentry_sdk.capture_exception")
    @patch("bahk.celery.sentry_sdk.capture_message")
    @patch("bahk.celery.app")
    @patch("redis.from_url", side_effect=RuntimeError("connection failed for super-secret"))
    def test_redis_failure_log_redacts_broker_credentials(
        self,
        mock_from_url,
        mock_app,
        mock_capture_message,
        mock_capture_exception,
    ):
        mock_app.conf.broker_url = "redis://:super-secret@example.test:6379/0"
        mock_app.conf.get.return_value = None

        with self.assertLogs("bahk.celery", level="ERROR") as captured_logs:
            check_redis_connectivity()

        self.assertIn("redis://example.test:6379/0", "\n".join(captured_logs.output))
        self.assertNotIn("super-secret", "\n".join(captured_logs.output))
        mock_from_url.assert_called_once()
        mock_capture_message.assert_called_once_with("Redis connection failed", level="error")
        mock_capture_exception.assert_not_called()

    @patch("bahk.celery.app")
    def test_worker_ready_log_redacts_broker_credentials(self, mock_app):
        mock_app.conf.broker_url = "redis://:super-secret@example.test:6379/0"

        with self.assertLogs("bahk.celery", level="INFO") as captured_logs:
            log_worker_ready()

        self.assertIn("redis://example.test:6379/0", "\n".join(captured_logs.output))
        self.assertNotIn("super-secret", "\n".join(captured_logs.output))
