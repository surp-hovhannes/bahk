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
    'create-feast-date-daily': {
        'task': 'hub.tasks.create_feast_date_task',
        'schedule': crontab(hour=0, minute=5),  # 5 minutes past midnight
        # Add Sentry Cron metadata
        'options': {
            'sentry': {
                'monitor_slug': 'daily-feast-date-creation',
            }
        }
    },
    'update-current-fast-maps-once-per-day': {
        'task': 'hub.tasks.update_current_fast_maps',
        'schedule': crontab(hour=1, minute=0),
        # Add Sentry Cron metadata
        'options': {
            'sentry': {
                'monitor_slug': 'daily-fast-map-updates',
            }
        }
    },
    'cleanup-old-activity-feed-items-daily': {
        'task': 'events.tasks.cleanup_old_activity_feed_items_task',
        'schedule': crontab(hour=2, minute=0),  # 2 AM daily
        # Add Sentry Cron metadata
        'options': {
            'sentry': {
                'monitor_slug': 'daily-activity-feed-cleanup',
            }
        }
    },
    'check-fast-beginning-events-daily': {
        'task': 'events.tasks.check_fast_beginning_events_task',
        'schedule': crontab(hour=6, minute=0),  # 6 AM daily
        # Add Sentry Cron metadata
        'options': {
            'sentry': {
                'monitor_slug': 'daily-fast-beginning-check',
            }
        }
    },
    'check-participation-milestones-daily': {
        'task': 'events.tasks.check_participation_milestones_task',
        'schedule': crontab(hour=8, minute=0),  # 8 AM daily
        # Add Sentry Cron metadata
        'options': {
            'sentry': {
                'monitor_slug': 'daily-participation-milestones-check',
            }
        }
    },
    'check-devotional-availability-daily': {
        'task': 'events.tasks.check_devotional_availability_task',
        'schedule': crontab(hour=7, minute=0),  # 7 AM daily
        # Add Sentry Cron metadata
        'options': {
            'sentry': {
                'monitor_slug': 'daily-devotional-availability-check',
            }
        }
    },
    'check-completed-fast-milestones-daily': {
        'task': 'events.tasks.check_completed_fast_milestones_task',
        'schedule': crontab(hour=3, minute=0),  # 3 AM daily
        # Add Sentry Cron metadata
        'options': {
            'sentry': {
                'monitor_slug': 'daily-completed-fast-milestones-check',
            }
        }
    },
    'send-culmination-feast-notifications-daily': {
        'task': 'notifications.tasks.send_culmination_feast_push_notification_task',
        'schedule': crontab(hour=8, minute=30),  # 8:30 AM daily
        # Add Sentry Cron metadata
        'options': {
            'sentry': {
                'monitor_slug': 'daily-culmination-feast-notifications',
            }
        }
    },
    'check-expired-prayer-requests-frequent': {
        'task': 'prayers.tasks.check_expired_prayer_requests_task',
        # Run frequently to minimize delay between expiration and completion
        'schedule': crontab(minute='*/15'),
        # Add Sentry Cron metadata
        'options': {
            'sentry': {
                'monitor_slug': 'frequent-expired-prayer-requests-check',
            }
        }
    },
    'send-daily-prayer-count-notifications': {
        'task': 'prayers.tasks.send_daily_prayer_count_notifications_task',
        'schedule': crontab(hour=23, minute=30),  # 11:30 PM daily
        # Add Sentry Cron metadata
        'options': {
            'sentry': {
                'monitor_slug': 'daily-prayer-count-notifications',
            }
        }
    },
    'send-weekly-prayer-request-notifications': {
        'task': 'notifications.tasks.send_weekly_prayer_request_push_notification_task',
        'schedule': crontab(day_of_week='sunday', hour=18, minute=0),  # Sunday 6:00 PM
        'options': {
            'sentry': {
                'monitor_slug': 'weekly-prayer-request-notifications',
            }
        }
    },
    'refresh-reading-texts-weekly': {
        'task': 'hub.tasks.refresh_all_reading_texts_task',
        'schedule': crontab(day_of_week='monday', hour=4, minute=0),  # Monday 4:00 AM
        'options': {
            'sentry': {
                'monitor_slug': 'weekly-reading-text-refresh',
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