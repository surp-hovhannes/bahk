"""Test-specific settings to override production settings."""
from datetime import timedelta
import os
import tempfile

os.environ.setdefault('MAILGUN_DOMAIN', 'example.test')
os.environ.setdefault('MAILGUN_API_KEY', 'test-mailgun-api-key')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test-anthropic-api-key')
os.environ.setdefault('OPENAI_API_KEY', 'test-openai-api-key')

from bahk.settings import *  # Import all default settings

# Override cache to use local memory instead of Redis for tests
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'test-cache',
    }
}

# Disable Celery task execution during tests
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Use in-memory database for tests
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# Test media settings
MEDIA_ROOT = os.path.join(BASE_DIR, 'test_media')
MEDIA_URL = '/test_media/'


# Video uploads use django-s3-file-field's multipart S3 workflow. Test settings
# intentionally have no bucket credentials, so suppress its live-bucket probe;
# `learning_resources.tests.VideoStorageConfigurationTests` verifies the field's
# configured storage type without external I/O.
SILENCED_SYSTEM_CHECKS = ["s3_file_field.E002"]

# Disable email sending during tests
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

# Disable logging during tests to reduce noise
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'null': {
            'class': 'logging.NullHandler',
        },
    },
    'root': {
        'handlers': ['null'],
    },
    'loggers': {
        'django': {
            'handlers': ['null'],
            'propagate': False,
        },
        'hub': {
            'handlers': ['null'],
            'propagate': False,
        },
        'notifications': {
            'handlers': ['null'],
            'propagate': False,
        },
    }
}

# Disable debug toolbar for tests
DEBUG_TOOLBAR = False

# Use a test-specific secret key
SECRET_KEY = 'test-secret-key-for-testing-only'

# Disable external service integrations for tests
AWS_LOCATION_API_KEY = None  # Disable AWS Location Service
GEOCODING_ENABLED = False
SEND_PUSH_NOTIFICATIONS = False  # Disable push notifications

# Disable real LLM API calls during tests.
# Without these overrides, Celery tasks that fire synchronously (CELERY_TASK_ALWAYS_EAGER=True)
# can make real API calls when signals trigger LLM tasks on Feast creation.
# Tests that need to mock LLM responses already use @patch decorators and are unaffected.
ANTHROPIC_API_KEY = ''
OPENAI_API_KEY = ''

# Disable real API.Bible calls from the readings view during tests.
# The view fetches expired text on demand, and several tests exercise that path without
# patching it; a zero daily budget makes the English fetch return before the HTTP call.
# The monthly ceiling is left effectively unlimited because the refresh task charges it
# and those tests drive the task deliberately with the HTTP layer mocked.
# Note BIBLE_API_KEY='' would NOT work here: BibleAPIService reads the key via
# decouple.config, not django.conf.settings, so the setting is never consulted.
# Tests that exercise budget behaviour override these explicitly.
READING_FETCH_DAILY_BUDGET = 0
BIBLE_API_MONTHLY_BUDGET = 1_000_000

# JWT Settings for testing
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.LimitOffsetPagination',
    'PAGE_SIZE': 10,
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=5),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
    'ROTATE_REFRESH_TOKENS': False,
    'BLACKLIST_AFTER_ROTATION': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'VERIFYING_KEY': None,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    'TOKEN_TYPE_CLAIM': 'token_type',
}

# Use custom test runner that cleans up media files
TEST_RUNNER = 'tests.test_runner.MediaCleanupTestRunner'

# Enable user account creation tracking
TRACK_USER_ACCOUNT_CREATED = False

# Analytics testing settings
ANALYTICS_SESSION_TIMEOUT_MINUTES = 30

# Ensure middleware is enabled for testing
if 'events.middleware.AnalyticsTrackingMiddleware' not in MIDDLEWARE:
    # Insert after authentication middleware
    auth_index = MIDDLEWARE.index('django.contrib.auth.middleware.AuthenticationMiddleware')
    MIDDLEWARE.insert(auth_index + 1, 'events.middleware.AnalyticsTrackingMiddleware')
