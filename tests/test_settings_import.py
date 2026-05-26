import os
import subprocess
import sys
import unittest


class TestSettingsImportTests(unittest.TestCase):
    def test_test_settings_import_without_external_service_secrets(self):
        env = os.environ.copy()
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
