from __future__ import absolute_import, unicode_literals
import os
from celery import Celery
from celery.schedules import crontab
from ssl import CERT_NONE

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bahk.settings')

app = Celery('bahk')

# Basic configuration from Django settings
app.config_from_object('django.conf:settings', namespace='CELERY')

# Configure SSL settings for Redis if using rediss://
broker_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
app.conf.broker_url = broker_url

# The SSL settings will be automatically picked up from Django settings
# (CELERY_BROKER_USE_SSL and CELERY_REDIS_BACKEND_USE_SSL)

# Discover tasks automatically
app.autodiscover_tasks()

# Configure scheduled tasks
app.conf.beat_schedule = {
    'send-fast-notifications-every-day': {
        'task': 'hub.tasks.send_fast_notifications',
        'schedule': crontab(hour=0, minute=0),
    },
}

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')