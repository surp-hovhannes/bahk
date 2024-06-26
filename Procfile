web: gunicorn bahk.wsgi --log-file -
worker: celery -A bahk worker --loglevel=info
beat: celery -A bahk beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
