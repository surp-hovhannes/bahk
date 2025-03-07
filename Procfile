web: newrelic-admin run-program gunicorn bahk.wsgi --log-file -
worker: newrelic-admin run-program celery -A bahk worker --loglevel=info
beat: newrelic-admin run-program celery -A bahk beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
