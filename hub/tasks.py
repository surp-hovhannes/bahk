from celery import shared_task
from hub.utils import send_fast_reminders, test_email

# this is for testing purposes to verify that Celery & Redis are working
@shared_task
def add(x, y):
    return x + y

@shared_task
def test_email_task():
    test_email()

@shared_task
def send_fast_reminder_task():
    send_fast_reminders()