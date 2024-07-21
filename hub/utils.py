import datetime
from django.core.mail import EmailMultiAlternatives, send_mail
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from hub.models import Profile, Fast, Day
from datetime import datetime, timedelta
from .serializers import FastSerializer
from django.db.models import OuterRef, Subquery

import logging

logger = logging.getLogger(__name__)


def send_fast_reminders():
    today = datetime.today().date()
    three_days_from_now = today + timedelta(days=3)
    
    next_fast_subquery = Day.objects.filter(
        fasts__profiles__id=OuterRef('pk'),
        date__gt=today,
        date__lt=three_days_from_now
    ).order_by('date').values('id')[:1]

    profiles = Profile.objects.filter(receive_upcoming_fast_reminders=True).annotate(
        next_fast_id=Subquery(next_fast_subquery)
    ).filter(next_fast_id__isnull=False)

    for profile in profiles:
        next_fast = Fast.objects.get(id=profile.next_fast_id)

        if next_fast:
            subject = f'Upcoming Fast: {next_fast.name}'
            from_email = f"Live and Pray <{settings.EMAIL_HOST_USER}>"
            serialized_next_fast = FastSerializer(next_fast).data
            html_content = render_to_string('email/upcoming_fasts_reminder.html', {
                'user': profile.user,
                'fast': serialized_next_fast,
            })
            text_content = strip_tags(html_content)

            email = EmailMultiAlternatives(
                subject, text_content, from_email, [profile.user.email]
            )

            email.attach_alternative(html_content, "text/html")
            email.send()
            logger.info(f'Fast reminder sent to {profile.user.email} for {next_fast.name}')

def test_email():
    try:
        send_mail(
            'Test Email',
            'This is a test email sent from Celery.',
            settings.EMAIL_HOST_USER,  # Replace with your sender email
            [settings.EMAIL_TEST_ADDRESS],  # Replace with the recipient email
            fail_silently=False,
        )
        logger.info('Email sent successfully.')
    except Exception as e:
        logger.error(f'Failed to send email: {e}')
        raise e