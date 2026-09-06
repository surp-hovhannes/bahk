"""Utilities for supporting backend."""
from datetime import datetime, timedelta
import logging

from django.core.mail import EmailMultiAlternatives, send_mail
from django.conf import settings
from django.db.models import CharField, Q, Value
from django.db.models.functions import Cast, Concat, MD5
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils import timezone
from django.core.cache import cache

import bahk.settings as settings
from hub.models import Church, Day, Fast, Feast, Profile
from hub.serializers import FastSerializer


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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


def shuffled_fast_participants(fast_id, profiles, rotation_date=None):
    """Order participant profiles by a stable, daily rotating database key.

    The key varies by fast and calendar day, so a participant's position is
    stable for pagination during one day but changes on the next day. Keeping
    the expression in SQL lets callers apply ``LIMIT`` and offsets before
    profile rows are materialized in Django.

    Args:
        fast_id: ID of the fast the profiles belong to.
        profiles: A Profile queryset (for example, ``fast.profiles.all()``).
        rotation_date: Optional date used as the rotation seed; defaults to
            the current Django-local date.
    """
    rotation_date = rotation_date or timezone.localdate()
    seed = Concat(
        Value(f"{fast_id}-{rotation_date.isoformat()}-"),
        Cast("user_id", output_field=CharField()),
    )
    return profiles.annotate(shuffle_key=MD5(seed)).order_by("shuffle_key", "id")


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


def _get_or_create_feast_for_observance(commemoration, church):
    """Resolve one commemoration to its Feast row.

    Returns ``(feast_obj, created, refreshed)`` -- ``refreshed`` being whether an existing row's
    display text had drifted from the engine and was rewritten.
    """
    observance_id = commemoration["observance_id"]
    name_en = commemoration["name_en"]

    # Look the row up by the OBSERVANCE, not by its name. An id keeps meaning the same
    # commemoration across engine releases; the name is display text the engine corrects, and
    # keying on it is what stranded 158 rows when 1.3.0 landed.
    feast_obj = Feast.objects.filter(church=church, observance_id=observance_id).first()
    if feast_obj is None:
        # Adopt an unkeyed row that already carries this name before minting a new one.
        # Migration 0067 keys every row it can resolve, but anything created since without
        # going through here -- a seed, an admin, a row the backfill could not place -- would
        # otherwise be invisible to this lookup and silently duplicated, taking its
        # designation, icon and contexts out of circulation. Adopting it is how such a row
        # rejoins, and it happens once.
        feast_obj = Feast.objects.filter(
            church=church, observance_id__isnull=True, name=name_en).first()
    if feast_obj is None:
        feast_obj = Feast(church=church, observance_id=observance_id, name=name_en)

    feast_created = feast_obj.pk is None
    adopted = not feast_created and feast_obj.observance_id != observance_id
    feast_obj.observance_id = observance_id

    # The name is derived from the id now, so it is refreshed rather than matched on -- an engine
    # release that corrects the display text updates the row in place instead of orphaning it.
    # Same for the Armenian name, on which the engine is likewise the authority.
    name_hy = commemoration.get("name_hy")
    updated_fields = ["observance_id"] if adopted else []
    if feast_obj.name != name_en:
        feast_obj.name = name_en
        updated_fields.append("name")
    if name_hy and feast_obj.name_hy != name_hy:
        feast_obj.name_hy = name_hy
        updated_fields.append("i18n")

    if feast_created:
        # A new row saves in full, so post_save sees the id, the name and its translation
        # together -- that is what the designation and icon-matching tasks read.
        feast_obj.save()
    elif updated_fields:
        feast_obj.save(update_fields=updated_fields)

    return feast_obj, feast_created, bool(updated_fields) and not feast_created


def get_or_create_feast_for_date(date_obj, church, check_fast=True):
    """Resolve the day's commemorations, and return their Feast rows.

    The day comes from the ``armenian_lectionary`` engine, recomputed per call, so nothing has to
    be imported ahead of time for a date to resolve.  A liturgical day is a LIST of observances,
    and only the ones the engine marks ``is_comm`` become feasts -- so this returns zero, one or
    two rows, and zero is the commonest answer of the three.

    Each Feast row is keyed by ``(church, observance_id)``, not by date and not by name: it is
    where the app keeps the parts the engine has no notion of -- designation, icon, generated
    contexts -- and one row serves every recurrence of that commemoration.  So "created" means
    "this commemoration was seen for the first time", not "a row was made for this date"; after
    the first year of a full cycle it is almost always false, and that is the point: the LLM
    context and icon match behind it run once, not once a year.

    Args:
        date_obj: datetime.date for the date
        church: Church object
        check_fast: If True, return no feasts when a Fast is associated with the day

    Returns:
        Tuple of (feasts, status_dict) where:
        - feasts: list of Feast instances, empty when there is nothing to record
        - status_dict: Dict with status information (status, reason, created count, etc.)
    """
    # A Fast on the day outranks the feast in the UI. Look the Day up without creating one --
    # resolving a feast name should not mint calendar rows as a side effect.
    if check_fast:
        day = Day.objects.filter(date=date_obj, church=church).select_related("fast").first()
        if day and day.fast:
            return (
                [],
                {
                    "status": "skipped",
                    "reason": "fast_associated",
                    "fast_name": day.fast.name,
                    "date": str(date_obj),
                }
            )

    # Imported lazily to avoid a circular import (feast_service imports SUPPORTED_CHURCHES here).
    from hub.services.feast_service import get_feast_for_date

    commemorations = get_feast_for_date(date_obj, church)
    if commemorations is None:
        # No answer at all -- unsupported church, out-of-range date, or a day the engine could
        # not resolve. Distinct from an answer of "nothing today", which is the empty list below.
        return (
            [],
            {"status": "skipped", "reason": "no_feast_data", "date": str(date_obj)}
        )

    if not commemorations:
        return (
            [],
            {"status": "skipped", "reason": "no_commemorations", "date": str(date_obj)}
        )

    feasts = []
    created_count = 0
    refreshed_count = 0
    for commemoration in commemorations:
        feast_obj, feast_created, refreshed = _get_or_create_feast_for_observance(
            commemoration, church)
        feasts.append(feast_obj)
        created_count += int(feast_created)
        refreshed_count += int(refreshed)

    return (
        feasts,
        {
            "status": "success",
            "created": created_count,
            "refreshed": refreshed_count,
            "feast_ids": [feast.id for feast in feasts],
            "feast_names": [feast.name for feast in feasts],
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
