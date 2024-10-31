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
if broker_url.startswith('rediss://'):
    app.conf.broker_transport_options = {
        'ssl_cert_reqs': CERT_NONE,
        'ssl_ca_certs': None,
        'ssl_certfile': None,
        'ssl_keyfile': None,
    }
    app.conf.redis_backend_use_ssl = {
        'ssl_cert_reqs': CERT_NONE,
        'ssl_ca_certs': None,
        'ssl_certfile': None,
        'ssl_keyfile': None,
    }

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