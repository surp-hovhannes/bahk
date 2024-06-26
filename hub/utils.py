import datetime
from django.core.mail import EmailMultiAlternatives, send_mail
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from hub.models import Profile, Fast
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


def send_fast_reminders():
    today = datetime.today().date()
    three_days_from_now = today + timedelta(days=3)
    profiles = Profile.objects.filter(receive_upcoming_fast_reminders=True).prefetch_related('fasts__days')
    
    for profile in profiles:
        for fast in profile.fasts.all():
            next_fast_day = fast.days.filter(date__gt=today).order_by('date').first()
            # uncomment the line below before commtting
            #if next_fast_day and today <= next_fast_day.date <= three_days_from_now:
            if next_fast_day:
                subject = f'Upcoming Fast: {fast.name}'
                from_email = f"Live and Pray <{settings.EMAIL_HOST_USER}>"
                html_content = render_to_string('email/upcoming_fasts_reminder.html', {
                    'user': profile.user,
                    'upcoming_fast': next_fast_day,
                })
                text_content = strip_tags(html_content)

                email = EmailMultiAlternatives(
                    subject, text_content, from_email, [profile.user.email]
                )

                email.attach_alternative(html_content, "text/html")
                email.send()
                logger.info(f'Fast reminder sent to {profile.user.email} for {next_fast_day.date}')
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