"""Utilities for supporting backend."""
from datetime import datetime, timedelta
import logging

from django.core.mail import EmailMultiAlternatives, send_mail
from django.conf import settings
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.core.cache import cache

import bahk.settings as settings
from hub.models import Church, Day, Fast, Feast, Profile
from hub.serializers import FastSerializer


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PARSER_REGEX = r"^([\w\u0531-\u058A\u0400-\u04FF1-4\'\.\s]+) ([0-9]+\.)?([0-9]+)\-?([0-9]+\.)?([0-9]+)?$"
SUPPORTED_CHURCHES = Church.objects.filter(name=settings.DEFAULT_CHURCH_NAME)


def get_user_profile_safe(user):
    """
    Safely get a user's profile, returning None if it doesn't exist.
    
    This handles the RelatedObjectDoesNotExist exception that occurs when
    accessing user.profile on a OneToOneField where no related Profile exists.
    
    Args:
        user: Django User instance
        
    Returns:
        Profile instance if it exists, None otherwise
    """
    try:
        return user.profile
    except Exception:  # Catch any profile-related exception
        return None


def invalidate_fast_participants_cache(fast_id):
    """
    Invalidate cache for a specific fast's participant list.
    This should be called whenever the participant list changes.
    """
    cache.delete(f"bahk:fast_participants_view:{fast_id}")
    cache.delete(f"bahk:fast_participants_simple_view:{fast_id}")
    cache.delete(f"bahk:fast_participants_count:{fast_id}")

    if hasattr(cache, 'delete_pattern'):
        # Invalidate PaginatedFastParticipantsView cache
        cache.delete_pattern(f"bahk:views.decorators.cache.cache_page.*fast.{fast_id}.participants.*")
        # Invalidate FastParticipantsView cache
        cache.delete_pattern(f"bahk:views.decorators.cache.cache_page.*{fast_id}/participants.*")


def invalidate_fast_stats_cache(user):
    """
    Invalidate the cached fast stats for a specific user.
    
    This should be called when:
    - User joins or leaves a fast
    - User completes a checklist action
    - Any action that affects user's fast statistics
    
    Args:
        user: User object whose stats cache should be invalidated
    """
    # Use the same cache key format as FastStatsView
    cache_key = f"bahk:fast_stats:{user.id}"
    cache.delete(cache_key)


def send_fast_reminders():
    today = datetime.today().date()
    tomorrow = today + timedelta(days=1)
    three_days_from_now = today + timedelta(days=3)

    # Get all profiles that want reminders
    profiles = Profile.objects.filter(receive_upcoming_fast_reminders=True)

    for profile in profiles:
        # Get all fasts for this profile that:
        # 1. Have days in our date range
        # 2. Haven't started yet (earliest day is tomorrow or later)
        # 3. Aren't weekly fasts
        fasts = Fast.objects.filter(
            profiles=profile,
            days__date__gte=tomorrow,  # Only consider days from tomorrow onwards
            days__date__lte=three_days_from_now  # Changed from lt to lte to include 3 days from now
        ).filter(
            ~Q(name__icontains="Friday Fasts") & ~Q(name__icontains="Wednesday Fasts")
        ).distinct()

        # Find the earliest fast
        earliest_fast = None
        earliest_start_date = None

        for fast in fasts:
            # Get the earliest day for this fast
            earliest_day = Day.objects.filter(fast=fast).order_by('date').first()
            
            # Skip if no earliest day found or if the fast has already started
            if not earliest_day or earliest_day.date <= today:
                continue

            # Update earliest_fast if this is the first valid fast or if it starts earlier
            if earliest_fast is None or earliest_day.date < earliest_start_date:
                earliest_fast = fast
                earliest_start_date = earliest_day.date

        # Send reminder only for the earliest fast if no promotional emails have been assigned to it
        if earliest_fast and not earliest_fast.promo_emails.exists():
            subject = f'Upcoming Fast: {earliest_fast.name}'
            from_email = f"Fast and Pray <{settings.EMAIL_HOST_USER}>"
            serialized_fast = FastSerializer(earliest_fast).data
            html_content = render_to_string('email/upcoming_fasts_reminder.html', {
                'user': profile.user,
                'fast': serialized_fast,
            })
            text_content = strip_tags(html_content)

            email = EmailMultiAlternatives(
                subject, text_content, from_email, [profile.user.email]
            )

            email.attach_alternative(html_content, "text/html")
            email.send()
            logger.info(f'Reminder Email: Fast reminder sent to {profile.user.email} for {earliest_fast.name}')


def get_or_create_feast_for_date(date_obj, church, check_fast=True):
    """Resolve the commemoration for a date, and return its Feast row.

    The name of the day comes from the ``armenian_lectionary`` engine, recomputed per call, so
    nothing has to be imported ahead of time for a date to resolve.  The Feast row this returns is
    keyed by ``(church, name)``, not by date: it is where the app keeps the parts the engine has
    no notion of -- designation, icon, generated contexts -- and one row serves every recurrence
    of that commemoration.

    ``created`` therefore means "this commemoration was seen for the first time", not "a row was
    made for this date".  After the first year of a full cycle it is almost always ``False``, and
    that is the point: the LLM context and icon match behind it run once, not once a year.

    Args:
        date_obj: datetime.date for the date
        church: Church object
        check_fast: If True, return no feast when a Fast is associated with the day

    Returns:
        Tuple of (feast_obj, created, status_dict) where:
        - feast_obj: Feast instance, or None if there is no feast to record
        - created: True only when the commemoration had no row in this church yet
        - status_dict: Dict with status information (status, reason, etc.)
    """
    # A Fast on the day outranks the feast in the UI. Look the Day up without creating one --
    # resolving a feast name should not mint calendar rows as a side effect.
    if check_fast:
        day = Day.objects.filter(date=date_obj, church=church).select_related("fast").first()
        if day and day.fast:
            return (
                None,
                False,
                {
                    "status": "skipped",
                    "reason": "fast_associated",
                    "fast_name": day.fast.name,
                    "date": str(date_obj),
                }
            )

    # Imported lazily to avoid a circular import (feast_service imports SUPPORTED_CHURCHES here).
    from hub.services.feast_service import get_feast_for_date

    feast_data = get_feast_for_date(date_obj, church)
    if not feast_data:
        return (
            None,
            False,
            {"status": "skipped", "reason": "no_feast_data", "date": str(date_obj)}
        )

    name_en = feast_data.get("name_en") or feast_data.get("name")
    if not name_en:
        return (
            None,
            False,
            {"status": "skipped", "reason": "no_feast_name", "date": str(date_obj)}
        )

    feast_obj, feast_created = Feast.objects.get_or_create(church=church, name=name_en)

    # Fill in the Armenian name if the engine has one and this row does not. Engine releases add
    # translations over time, so an existing row can still be upgraded.
    name_hy = feast_data.get("name_hy")
    translation_updated = False
    if name_hy and not feast_obj.name_hy:
        feast_obj.name_hy = name_hy
        translation_updated = True
        # A freshly created row saves in full so post_save sees the name and its translation
        # together; an existing one touches only the translation column.
        feast_obj.save(**({} if feast_created else {"update_fields": ["i18n"]}))

    if feast_created:
        action = "created"
    elif translation_updated:
        action = "updated"
    else:
        return (
            feast_obj,
            False,
            {
                "status": "skipped",
                "reason": "feast_already_exists",
                "date": str(date_obj),
            }
        )

    return (
        feast_obj,
        feast_created,
        {
            "status": "success",
            "action": action,
            "feast_id": feast_obj.id,
            "feast_name": feast_obj.name,
            "date": str(date_obj),
        }
    )

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
