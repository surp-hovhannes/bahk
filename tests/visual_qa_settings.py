"""Visual QA settings for disposable CI runs using the base SQLite and Redis config."""
import os

os.environ.setdefault('MAILGUN_DOMAIN', 'example.test')
os.environ.setdefault('MAILGUN_API_KEY', 'test-mailgun-api-key')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test-anthropic-api-key')
os.environ.setdefault('OPENAI_API_KEY', 'test-openai-api-key')

from bahk.settings import *

# CI has no video-upload bucket; retain every other Django system check.
SILENCED_SYSTEM_CHECKS = list(globals().get("SILENCED_SYSTEM_CHECKS", []))
if "s3_file_field.E002" not in SILENCED_SYSTEM_CHECKS:
    SILENCED_SYSTEM_CHECKS.append("s3_file_field.E002")

AWS_LOCATION_API_KEY = None
GEOCODING_ENABLED = False
SEND_PUSH_NOTIFICATIONS = False
ANTHROPIC_API_KEY = ''
OPENAI_API_KEY = ''
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
READING_FETCH_DAILY_BUDGET = 0
