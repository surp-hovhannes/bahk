import datetime
from django.core.mail import EmailMultiAlternatives, send_mail
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from hub.models import Profile, Fast, Day
from datetime import datetime, timedelta
from .serializers import FastSerializer
from django.db.models import OuterRef, Subquery, Q

import logging

logger = logging.getLogger(__name__)


def send_fast_reminders():
    today = datetime.today().date()
    three_days_from_now = today + timedelta(days=3)

    # Subquery to find the next fast for each profile, excluding those with "Friday Fasts" or "Wednesday Fasts" in the name
    # TODO: Find a better way to handle weekly fasts
    next_fast_subquery = Day.objects.filter(
        fast__profiles__id=OuterRef('pk'),
        date__gt=today,
        date__lt=three_days_from_now
    ).filter(
        ~Q(fast__name__icontains="Friday Fasts") & ~Q(fast__name__icontains="Wednesday Fasts")
    ).order_by('date').values('fast__id')[:1]

    # Filter profiles based on the subquery
    profiles = Profile.objects.filter(receive_upcoming_fast_reminders=True).annotate(
        next_fast_id=Subquery(next_fast_subquery)
    ).filter(next_fast_id__isnull=False)

    for profile in profiles:
        if profile.next_fast_id:
            try:
                next_fast = Fast.objects.get(id=profile.next_fast_id)
            except Fast.DoesNotExist:
                logger.warning(f"Reminder Email: No Fast found with ID {profile.next_fast_id} for profile {profile.user.email}")
                continue

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
            logger.info(f'Reminder Email: Fast reminder sent to {profile.user.email} for {next_fast.name}')
        else:
            logger.info(f"Reminder Email: No upcoming fasts found for profile {profile.user.email}")


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