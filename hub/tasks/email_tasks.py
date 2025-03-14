"""
Email-related tasks for the hub app.
"""
from celery import shared_task
from hub.utils import test_email, send_fast_reminders

@shared_task
def test_email_task():
    """Send a test email to verify that the email configuration is working."""
    test_email()

@shared_task
def send_fast_reminder_task():
    """Send reminders about upcoming fasts."""
    send_fast_reminders() 