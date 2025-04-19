"""Utilities for supporting backend."""
from datetime import datetime, timedelta
import logging
import re
import urllib

from django.core.mail import EmailMultiAlternatives, send_mail
from django.conf import settings
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.core.cache import cache

import bahk.settings as settings
from hub.models import Church, Day, Fast, Profile
from hub.serializers import FastSerializer


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PARSER_REGEX = r"^([A-za-z1-4\'\. ]+) ([0-9]+\.)?([0-9]+)\-?([0-9]+\.)?([0-9]+)?$"
SUPPORTED_CHURCHES = Church.objects.filter(name=settings.DEFAULT_CHURCH_NAME)

def invalidate_fast_participants_cache(fast_id):
    """
    Invalidate cache for a specific fast's participant list.
    This should be called whenever the participant list changes.
    """
    if hasattr(cache, 'delete_pattern'):
        # Invalidate PaginatedFastParticipantsView cache
        cache.delete_pattern(f"bahk:views.decorators.cache.cache_page.*fast.{fast_id}.participants.*")
        # Invalidate FastParticipantsView cache
        cache.delete_pattern(f"bahk:views.decorators.cache.cache_page.*{fast_id}/participants.*")
        # Invalidate our new custom cache keys
        cache.delete(f"bahk:fast_participants_view:{fast_id}")
        cache.delete(f"bahk:fast_participants_simple_view:{fast_id}")
        cache.delete(f"bahk:fast_participants_count:{fast_id}")
    else:
        # Fallback - less efficient
        cache.clear()

def scrape_readings(date_obj, church, date_format="%Y%m%d", max_num_readings=40):
    """Scrapes readings from sacredtradition.am""" 
    if church not in SUPPORTED_CHURCHES:
        logging.error("Web-scraping for readings only set up for the following churches: %r. %s not supported.", 
                      SUPPORTED_CHURCHES, church)
        return []

    date_str = date_obj.strftime(date_format)

    url = f"https://sacredtradition.am/Calendar/nter.php?NM=0&iM1103&iL=2&ymd={date_str}"
    try:
        response = urllib.request.urlopen(url)
    except urllib.error.URLError:
        logging.error("Invalid url %s", url)
        return []

    if response.status != 200:
        logging.error("Could not access readings from url %s. Failed with status %r", url, response.status)
        return []

    data = response.read()
    html_content = data.decode("utf-8")

    book_start = html_content.find("<b>")
    
    readings = []
    ct = 0
    while book_start != -1:
        # prevent infinite loop
        if ct > max_num_readings:
            logging.error("Reached maximum number of readings: %d. Breaking to avoid infinite loop.", max_num_readings)
            break
        ct += 1

        i1 = book_start + len("<b>")
        i2 = html_content.find("</b>")
        reading_str = html_content[i1:i2]

        # advance to next section of web text early to prevent infinite loop in case of failure later on
        html_content = html_content[i2 + 1:]
        book_start = html_content.find("<b>")

        if "," in reading_str:
            # TODO: if comma found, second reading appended to first, as for Daniel 3.1-23 and Azariah 1-68
            # for now, omit second reading
            reading_str = reading_str.split(",")[0]
        
        groups = re.search(PARSER_REGEX, reading_str)

        # skip reading if does not match parser regex
        if groups is None:
            logging.error("Could not parse reading %s at %s with regex %s", reading_str, url, PARSER_REGEX)
            continue

        try:
            # parse groups
            book = groups.group(1)
            # remove decimal if start chapter provided; otherwise, part of book with 1 chapter
            start_chapter = groups.group(2).strip(".") if groups.group(2) is not None else 1
            start_verse = groups.group(3)
            # remove decimal if end chapter provided; otherwise, must be the same as the start chapter
            end_chapter = groups.group(4).strip(".") if groups.group(4) is not None else start_chapter
            end_verse = groups.group(5) if groups.group(5) is not None else start_verse

            readings.append({
                "book": book,
                "start_chapter": int(start_chapter),
                "start_verse": int(start_verse),
                "end_chapter": int(end_chapter),
                "end_verse": int(end_verse)
            })
        except Exception:
            logging.error(
                "Could not parse reading with text %s with regex %s from %s. Skipping.", 
                reading_str, PARSER_REGEX, url, exc_info=True
            )
            continue
    
    return readings


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

        # Send reminder only for the earliest fast
        if earliest_fast:
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