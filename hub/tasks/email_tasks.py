"""
Email-related tasks for the hub app.
"""
from celery import shared_task
from hub.utils import test_email, test_mailgun_api, send_fast_reminders
import sentry_sdk

@shared_task
def test_email_task():
    """Send a test email to verify that the email configuration is working."""
    test_email()

@shared_task
def test_mailgun_api_task():
    """Send a test email specifically for testing Mailgun API integration."""
    return test_mailgun_api()

@shared_task(name='hub.tasks.send_fast_reminder_task')
@sentry_sdk.monitor(monitor_slug='daily-fast-notifications')
def send_fast_reminder_task():
    """Send reminders about upcoming fasts."""
    # Add additional context for Sentry
    sentry_sdk.set_context("email_task", {
        "type": "fast_reminder",
        "scheduled": "daily"
    })
    
    # Add breadcrumb for monitoring the flow
    sentry_sdk.add_breadcrumb(
        category="task",
        message="Starting send_fast_reminders process",
        level="info"
    )
    
    send_fast_reminders() 