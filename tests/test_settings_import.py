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
