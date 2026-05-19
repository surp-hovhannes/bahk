from __future__ import absolute_import, unicode_literals
import logging
import os
from celery import Celery
from celery.schedules import crontab
from celery.signals import celeryd_init, beat_init, worker_ready
import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from decouple import config

logger = logging.getLogger('bahk.celery')

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
    'send-fast-nonjoin-nudge-daily': {
        'task': 'notifications.tasks.send_fast_nonjoin_nudge_task',
        'schedule': crontab(hour=9, minute=0),  # 9 AM daily
        'options': {
            'sentry': {
                'monitor_slug': 'daily-fast-nonjoin-nudge',
            }
        }
    },
    'send-inactive-fast-member-nudge-daily': {
        'task': 'notifications.tasks.send_inactive_fast_member_nudge_task',
        'schedule': crontab(hour=10, minute=0),  # 10 AM daily
        'options': {
            'sentry': {
                'monitor_slug': 'daily-inactive-fast-member-nudge',
            }
        }
    },
    'send-activity-feed-nudge-daily': {
        'task': 'notifications.tasks.send_activity_feed_nudge_task',
        'schedule': crontab(hour=11, minute=0),  # 11 AM daily
        'options': {
            'sentry': {
                'monitor_slug': 'daily-activity-feed-nudge',
            }
        }
    },
    'send-prayer-acceptance-nudge-daily': {
        'task': 'notifications.tasks.send_prayer_acceptance_nudge_task',
        'schedule': crontab(hour=19, minute=0),  # 7 PM daily
        'options': {
            'sentry': {
                'monitor_slug': 'daily-prayer-acceptance-nudge',
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

# ── Startup: Redis connectivity check ─────────────────────────────────────────────

@celeryd_init.connect
def check_redis_connectivity(**kwargs):
    """Verify Redis is reachable before the worker starts accepting tasks."""
    broker_url = app.conf.broker_url
    try:
        import redis
        redis_kwargs = {'socket_connect_timeout': 5}
        # Match Celery's SSL config so rediss:// isn't misdiagnosed as failure.
        broker_ssl = app.conf.get('broker_use_ssl')
        if broker_ssl:
            redis_kwargs['ssl_cert_reqs'] = None  # mirrors CELERY_BROKER_USE_SSL
        r = redis.from_url(broker_url, **redis_kwargs)
        r.ping()
        logger.info("✅ Redis connection OK: %s", broker_url)
    except Exception as exc:
        logger.error("❌ Redis connection FAILED: %s — %s", broker_url, exc)
        sentry_sdk.capture_exception(exc)


# ── Startup: Sync inline beat_schedule → django_celery_beat DB ─────────────────────
# When the Procfile uses DatabaseScheduler, Celery Beat reads its schedule from the
# django_celery_beat PeriodicTask model, NOT from app.conf.beat_schedule.
# If no rows exist in the DB, Beat dispatches nothing — even though 17+ tasks are
# defined inline.  This handler ensures the DB stays in sync with code.

@beat_init.connect
def sync_beat_schedule_to_db(**kwargs):
    """Mirror app.conf.beat_schedule into django_celery_beat PeriodicTask rows.

    Uses update_or_create so it is safe to run on every Beat restart:
    - Creates any entries missing from the DB (the common case).
    - Updates task path & crontab if they drifted (e.g. after a code deploy).
    - Does NOT re-enable entries that were manually disabled in Django admin.
    """
    try:
        from django_celery_beat.models import CrontabSchedule, PeriodicTask
    except ImportError:
        logger.warning("django_celery_beat not installed — skipping beat sync")
        return

    schedule = app.conf.beat_schedule or {}

    CODE_MANAGED_MARKER = '[code-managed]'

    try:
        synced = 0
        seen_names = set()

        for name, entry in schedule.items():
            task_path = entry.get('task')
            schedule_def = entry.get('schedule')

            if not task_path or not schedule_def:
                logger.warning("Skipping invalid beat entry '%s': missing task or schedule", name)
                continue

            if not isinstance(schedule_def, crontab):
                logger.warning(
                    "Skipping beat entry '%s': schedule is %s, only crontab is supported",
                    name, type(schedule_def).__name__,
                )
                continue

            # Build crontab kwargs from the Celery crontab instance.
            cron_kwargs = {
                'minute': _crontab_field(schedule_def, 'minute'),
                'hour': _crontab_field(schedule_def, 'hour'),
                'day_of_week': _crontab_field(schedule_def, 'day_of_week'),
                'day_of_month': _crontab_field(schedule_def, 'day_of_month'),
                'month_of_year': _crontab_field(schedule_def, 'month_of_year'),
                'timezone': getattr(schedule_def, 'tz', None) or 'America/Los_Angeles',
            }

            cron_schedule, _ = CrontabSchedule.objects.get_or_create(**cron_kwargs)

            defaults = {
                'task': task_path,
                'crontab': cron_schedule,
                'interval': None,
                'solar': None,
                'clocked': None,
                'name': name,
                'kwargs': '{}',
                'description': CODE_MANAGED_MARKER,
                'enabled': True,
            }
            pt, created = PeriodicTask.objects.update_or_create(
                name=name,
                defaults=defaults,
            )

            seen_names.add(name)
            synced += 1
            logger.info(
                "%s periodic task: %s → %s",
                'Created' if created else 'Updated',
                name,
                task_path,
            )

        # Disable stale code-managed entries that were removed from beat_schedule.
        stale = PeriodicTask.objects.filter(
            description=CODE_MANAGED_MARKER,
        ).exclude(name__in=seen_names).exclude(enabled=False)
        stale_count = stale.update(enabled=False)
        if stale_count:
            # Bulk update bypasses django-celery-beat's change-tracking signal,
            # so the scheduler's in-memory view would still see the old entries.
            # Force a change event to refresh the scheduler's schedule cache.
            from django_celery_beat.models import PeriodicTasks
            PeriodicTasks.update_changed()
            logger.info(
                "Beat sync: disabled %d stale code-managed periodic task(s) "
                "no longer in beat_schedule",
                stale_count,
            )

        logger.info("Beat sync complete: %d schedule entries mirrored to DB", synced)
    except Exception as exc:
        # Covers missing tables (migrations not applied), DB connection
        # issues, etc.  The Beat process will continue with whatever was
        # already in the DB (if anything).
        logger.warning("Beat sync failed (%s) — beat may use stale or empty schedule", exc)
        sentry_sdk.capture_exception(exc)


def _crontab_field(schedule_def, field):
    """Return the original crontab expression string, falling back to '*'.

    Celery crontab internally expands expressions into sets, but the original
    expressions are preserved as private ``_orig_<field>`` attributes.
    The Django CrontabSchedule model expects the original expression string
    (e.g. '*/15', 'sunday', '0'), not the expanded set.
    """
    orig_attr = f'_orig_{field}'
    val = getattr(schedule_def, orig_attr, None)
    if val is None:
        return '*'
    return str(val)


# ── Startup: worker-ready log ──────────────────────────────────────────────────────

@worker_ready.connect
def log_worker_ready(**kwargs):
    """Confirm the worker is ready to process tasks."""
    logger.info("🚀 Celery worker ready — listening for tasks on %s", app.conf.broker_url)


# ── Sentry initialization ──────────────────────────────────────────────────────────

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