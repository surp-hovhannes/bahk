import datetime
from django.core.mail import send_mail
from django.conf import settings
from hub.models import Profile, Fast
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


def send_fast_reminders():
    today = datetime.today().date()
    three_days_from_now = today + timedelta(days=3)
    profiles = Profile.objects.prefetch_related('fasts__days')
    
    for profile in profiles:
        for fast in profile.fasts.all():
            next_fast_day = fast.days.filter(date__gt=today).order_by('date').first()
            # uncomment the line below before commtting
            #if next_fast_day and today <= next_fast_day.date <= three_days_from_now:
            if next_fast_day:
                send_mail(
                    subject=f'Upcoming Fast: {fast.culmination_feast}',
                    message=f'Dear {profile.user.username},\n\nYou have an upcoming fast: {fast.culmination_feast} on {next_fast_day.date}.',
                    from_email=f"Live and Pray <{settings.EMAIL_HOST_USER}>",
                    recipient_list=[profile.user.email],
                )
                break

def test_email():
    try:
        send_mail(
            'Test Email',
            'This is a test email sent from Celery.',
            settings.EMAIL_HOST_USER,  # Replace with your sender email
            ['matt.ash@gmail.com'],  # Replace with the recipient email
            fail_silently=False,
        )
        logger.info('Email sent successfully.')
    except Exception as e:
        logger.error(f'Failed to send email: {e}')
        raise e