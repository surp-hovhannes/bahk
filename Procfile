web: bash -c '[ "$USE_NEW_RELIC" = "true" ] && newrelic-admin run-program gunicorn bahk.wsgi --log-file - || gunicorn bahk.wsgi --log-file -'
worker: bash -c '[ "$USE_NEW_RELIC_WORKER" = "true" ] && newrelic-admin run-program celery -A bahk worker --loglevel=info || celery -A bahk worker --loglevel=info'
beat: bash -c '[ "$USE_NEW_RELIC_BEAT" = "true" ] && newrelic-admin run-program celery -A bahk beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler || celery -A bahk beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler'
