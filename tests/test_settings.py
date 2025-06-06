"""Test-specific settings to override production settings."""
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
SEND_PUSH_NOTIFICATIONS = False  # Disable push notifications