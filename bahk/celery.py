from __future__ import absolute_import, unicode_literals
import os
from celery import Celery
from celery.schedules import crontab
from ssl import CERT_NONE
from celery.signals import celeryd_init, beat_init
import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from decouple import config

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bahk.settings')

app = Celery('bahk')

# Basic configuration from Django settings
app.config_from_object('django.conf:settings', namespace='CELERY')

# Configure SSL settings for Redis if using rediss://
broker_url = config('REDIS_URL', default='redis://localhost:6379/0')
app.conf.broker_url = broker_url

# The SSL settings will be automatically picked up from Django settings
# (CELERY_BROKER_USE_SSL and CELERY_REDIS_BACKEND_USE_SSL)

# Discover tasks automatically
app.autodiscover_tasks()

# Configure scheduled tasks with Sentry Cron monitoring
app.conf.beat_schedule = {
    'send-fast-notifications-every-day': {
        'task': 'hub.tasks.send_fast_reminder_task',
        'schedule': crontab(hour=0, minute=0),
        # Add Sentry Cron metadata
        'options': {
            'sentry': {
                'monitor_slug': 'daily-fast-notifications',
            }
        }
    },
    'update-current-fast-maps-every-hour': {
        'task': 'hub.tasks.update_current_fast_maps',
        'schedule': 60 * 60,  # Every hour
        # Add Sentry Cron metadata
        'options': {
            'sentry': {
                'monitor_slug': 'hourly-fast-map-updates',
            }
        }
    },
}

# Initialize Sentry for Celery worker processes
@celeryd_init.connect
def init_sentry_worker(**kwargs):
    """Initialize Sentry for Celery worker processes."""
    sentry_sdk.init(
        dsn=config('SENTRY_DSN', default=os.environ.get("SENTRY_DSN", "")),
        integrations=[
            CeleryIntegration(
                monitor_beat_tasks=True  # Enable Celery beat task monitoring for Sentry Crons
            ),
        ],
        environment=config('SENTRY_ENVIRONMENT', default=os.environ.get("SENTRY_ENVIRONMENT", "development")),
        release=config('SENTRY_RELEASE', default=os.environ.get("SENTRY_RELEASE", "fastandpray@1.0.0")),
        traces_sample_rate=0.2 if config('IS_PRODUCTION', default=False, cast=bool) else 1.0,
    )
    sentry_sdk.set_tag("process_type", "celery_worker")

# Initialize Sentry for Celery beat process
@beat_init.connect
def init_sentry_beat(**kwargs):
    """Initialize Sentry for Celery beat process."""
    sentry_sdk.init(
        dsn=config('SENTRY_DSN', default=os.environ.get("SENTRY_DSN", "")),
        integrations=[
            CeleryIntegration(
                monitor_beat_tasks=True  # Enable Celery beat task monitoring for Sentry Crons
            ),
        ],
        environment=config('SENTRY_ENVIRONMENT', default=os.environ.get("SENTRY_ENVIRONMENT", "development")),
        release=config('SENTRY_RELEASE', default=os.environ.get("SENTRY_RELEASE", "fastandpray@1.0.0")),
        traces_sample_rate=0.2 if config('IS_PRODUCTION', default=False, cast=bool) else 1.0,
    )
    sentry_sdk.set_tag("process_type", "celery_beat")

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')