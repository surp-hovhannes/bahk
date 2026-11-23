import os
import subprocess
import sys
import unittest
from textwrap import dedent


class TestSettingsImportTests(unittest.TestCase):
    def _settings_env(self):
        env = os.environ.copy()
        env.update(
            {
                'CI': 'true',
                'MAILGUN_DOMAIN': 'example.test',
                'MAILGUN_API_KEY': 'test-mailgun-api-key',
                'ANTHROPIC_API_KEY': 'test-anthropic-api-key',
                'OPENAI_API_KEY': 'test-openai-api-key',
            }
        )
        return env

    def test_normal_startup_initializes_sentry_with_telemetry_disabled(self):
        # Django's test command and CI=true skip Sentry initialization. Exercise
        # normal local startup too, so a compatible test install cannot conceal
        # an incompatible SDK in the actual web/worker startup path.
        env = self._settings_env()
        env.update({
            'CI': 'false',
            'DEBUG': 'false',
            'IS_PRODUCTION': 'false',
            'SENTRY_DSN': '',
            'AWS_ACCESS_KEY_ID': '',
            'AWS_SECRET_ACCESS_KEY': '',
            'AWS_STORAGE_BUCKET_NAME': '',
            'AWS_EC2_METADATA_DISABLED': 'true',
        })
        result = subprocess.run(
            [sys.executable, 'manage.py', 'check', '--settings=tests.test_settings'],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_test_settings_import_without_external_service_secrets(self):
        env = self._settings_env()
        for key in (
            'MAILGUN_DOMAIN',
            'MAILGUN_API_KEY',
            'ANTHROPIC_API_KEY',
            'OPENAI_API_KEY',
        ):
            env.pop(key, None)

        subprocess.run(
            [
                sys.executable,
                '-c',
                (
                    'import tests.test_settings as settings; '
                    'assert settings.EMAIL_HOST_USER == "postmaster@example.test"; '
                    'assert settings.ANYMAIL["MAILGUN_API_KEY"] == "test-mailgun-api-key"; '
                    'assert settings.OPENAI_API_KEY == ""; '
                    'assert settings.ANTHROPIC_API_KEY == ""'
                ),
            ],
            check=True,
            cwd=os.getcwd(),
            env=env,
        )

    def test_production_settings_require_secret_key(self):
        env = self._settings_env()
        env['IS_PRODUCTION'] = 'true'
        env.pop('SECRET_KEY', None)

        result = subprocess.run(
            [
                sys.executable,
                '-c',
                'import bahk.settings',
            ],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SECRET_KEY must be configured in production', result.stderr)

    def test_default_refresh_token_lifetime_is_bounded(self):
        env = self._settings_env()
        env.pop('SECRET_KEY', None)
        env.pop('JWT_REFRESH_TOKEN_LIFETIME_DAYS', None)

        subprocess.run(
            [
                sys.executable,
                '-c',
                dedent(
                    """
                    import bahk.settings as settings
                    assert settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].days == 30
                    """
                ),
            ],
            check=True,
            cwd=os.getcwd(),
            env=env,
        )
