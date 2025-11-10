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


def scrape_readings(date_obj, church, date_format="%Y%m%d", max_num_readings=40):
    """Scrapes readings from sacredtradition.am in both English and Armenian."""
    if church not in SUPPORTED_CHURCHES:
        logging.error("Web-scraping for readings only set up for the following churches: %r. %s not supported.",
                      SUPPORTED_CHURCHES, church)
        return []

    date_str = date_obj.strftime(date_format)

    def scrape_language(language_code):
        """Helper function to scrape readings for a specific language.

        Args:
            language_code: 2 for English, 3 for Armenian
        """
        url = f"https://sacredtradition.am/Calendar/nter.php?NM=0&iM=1103&iL={language_code}&ymd={date_str}"
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

    # Scrape English (iL=2) and Armenian (iL=3)
    english_readings = scrape_language(2)
    armenian_readings = scrape_language(3)

    # Combine the readings with both translations
    # Assuming the readings are in the same order, we match them by index
    combined_readings = []
    for i, en_reading in enumerate(english_readings):
        reading_data = {
            "book": en_reading["book"],
            "book_en": en_reading["book"],
            "start_chapter": en_reading["start_chapter"],
            "start_verse": en_reading["start_verse"],
            "end_chapter": en_reading["end_chapter"],
            "end_verse": en_reading["end_verse"]
        }

        # Add Armenian translation if available
        if i < len(armenian_readings):
            hy_reading = armenian_readings[i]
            # Verify the readings match (same chapter/verse structure)
            if (hy_reading["start_chapter"] == en_reading["start_chapter"] and
                hy_reading["start_verse"] == en_reading["start_verse"] and
                hy_reading["end_chapter"] == en_reading["end_chapter"] and
                hy_reading["end_verse"] == en_reading["end_verse"]):
                reading_data["book_hy"] = hy_reading["book"]
            else:
                logging.warning(
                    "Armenian reading %d does not match English reading structure. Skipping Armenian translation.",
                    i
                )

        combined_readings.append(reading_data)

    return combined_readings


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


def scrape_feast(date_obj, church, date_format="%Y%m%d"):
    """Scrapes feast name from sacredtradition.am in both English and Armenian.
    
    Args:
        date_obj: datetime.date object for the date to scrape
        church: Church object
        date_format: Format string for date in URL
        
    Returns:
        Dict with 'name', 'name_en', 'name_hy' keys or None if no feast found
    """
    if church not in SUPPORTED_CHURCHES:
        logging.error("Web-scraping for feasts only set up for the following churches: %r. %s not supported.",
                      SUPPORTED_CHURCHES, church)
        return None

    date_str = date_obj.strftime(date_format)
    
    def scrape_feast_for_language(language_code):
        """Helper function to scrape feast for a specific language.
        
        Args:
            language_code: 2 for English, 3 for Armenian
        """
        url = f"https://sacredtradition.am/Calendar/nter.php?NM=0&iM=1103&iL={language_code}&ymd={date_str}"
        
        req = urllib.request.Request(url, headers={'User-agent': 'Mozilla/5.0'})
        
        try:
            response = urllib.request.urlopen(req)
        except urllib.error.URLError:
            logging.error("Invalid url %s", url)
            return None

        if response.status != 200:
            logging.error("Could not access feast from url %s. Failed with status %r", url, response.status)
            return None

        data = response.read()
        html_content = data.decode("utf-8")

        # Look for elements with class="dname" or class=dname (with or without quotes)
        # Match various HTML tags with class dname
        import re
        
        # First, find the opening tag with class=dname (handles both quoted and unquoted)
        opening_pattern = r'<([a-z]+)[^>]*class=["\']?dname["\']?[^>]*>'
        opening_match = re.search(opening_pattern, html_content, re.DOTALL | re.IGNORECASE)
        
        if not opening_match:
            return None
        
        tag_name = opening_match.group(1)
        start_pos = opening_match.end()
        
        # Find the corresponding closing tag
        closing_pattern = f'</{tag_name}>'
        closing_match = re.search(closing_pattern, html_content[start_pos:], re.IGNORECASE)
        
        if not closing_match:
            return None
        
        # Extract content between opening and closing tags
        feast_html = html_content[start_pos:start_pos + closing_match.start()]
        
        # Remove any nested HTML tags
        feast_name = re.sub(r'<[^>]+>', '', feast_html).strip()
        
        return feast_name if feast_name else None
    
    # Scrape both English and Armenian
    name_en = scrape_feast_for_language(2)  # English
    name_hy = scrape_feast_for_language(3)  # Armenian
    
    # If no feast found in either language, return None
    if not name_en and not name_hy:
        return None
    
    # Use English as default, fallback to Armenian if English not available
    default_name = name_en if name_en else name_hy
    
    return {
        "name": default_name,
        "name_en": name_en,
        "name_hy": name_hy,
    }


def get_or_create_feast_for_date(date_obj, church, check_fast=True):
    """
    Get or create a Feast for a given date and church.
    
    This function handles the common logic for creating feasts:
    1. Gets or creates a Day for the date and church
    2. Optionally checks if a Fast is associated (skips feast lookup if so)
    3. Checks if a Feast already exists
    4. If not, scrapes and creates the feast
    
    Args:
        date_obj: datetime.date object for the date
        church: Church object
        check_fast: If True, skip feast lookup if Day has a Fast associated
        
    Returns:
        Tuple of (feast_obj, created, status_dict) where:
        - feast_obj: Feast instance or None if no feast found/created
        - created: Boolean indicating if feast was created (False if existed or skipped)
        - status_dict: Dict with status information (status, reason, etc.)
    """
    # Get or create Day for the date
    day, day_created = Day.objects.get_or_create(
        date=date_obj,
        church=church,
        defaults={}
    )
    
    # Check if a Fast is associated with this day - if so, skip feast lookup
    if check_fast and day.fast:
        return (
            None,
            False,
            {
                "status": "skipped",
                "reason": "fast_associated",
                "fast_name": day.fast.name,
                "date": str(date_obj)
            }
        )
    
    # Check if feast already exists for this day
    existing_feast = day.feasts.first() if day.feasts.exists() else None
    
    # Scrape feast data (always scrape to potentially update existing feast with missing translations)
    feast_data = scrape_feast(date_obj, church)
    
    if not feast_data:
        # If no feast data and feast already exists, return existing feast
        if existing_feast:
            return (
                existing_feast,
                False,
                {
                    "status": "skipped",
                    "reason": "feast_already_exists",
                    "date": str(date_obj)
                }
            )
        return (
            None,
            False,
            {
                "status": "skipped",
                "reason": "no_feast_data",
                "date": str(date_obj)
            }
        )
    
    # Extract name fields
    name_en = feast_data.get("name_en", feast_data.get("name"))
    name_hy = feast_data.get("name_hy", None)
    
    if not name_en:
        # If no English name, try to use name field directly
        name_en = feast_data.get("name")
    
    if not name_en:
        # If no feast name and feast already exists, return existing feast
        if existing_feast:
            return (
                existing_feast,
                False,
                {
                    "status": "skipped",
                    "reason": "feast_already_exists",
                    "date": str(date_obj)
                }
            )
        return (
            None,
            False,
            {
                "status": "skipped",
                "reason": "no_feast_name",
                "date": str(date_obj)
            }
        )
    
    # Get or create feast with English name
    feast_obj, feast_created = Feast.objects.get_or_create(
        day=day,
        defaults={"name": name_en}
    )
    
    # Set translation if available and missing
    # For new feasts, set it immediately after creation to avoid second save
    # For existing feasts, only update if translation is missing
    translation_updated = False
    if name_hy and not feast_obj.name_hy:
        feast_obj.name_hy = name_hy
        translation_updated = True
        # Only save if feast was just created (to set translation) 
        # or if it existed and needs translation update
        if feast_created:
            # For new feasts, save immediately after setting translation
            # This triggers post_save once with both name and translation set
            feast_obj.save()
        else:
            # For existing feasts, save with update_fields to only update i18n
            feast_obj.save(update_fields=['i18n'])
    
    # Determine action: created, updated (with translation), or skipped (no changes)
    if feast_created:
        action = "created"
        status = "success"
    elif translation_updated:
        action = "updated"
        status = "success"
    else:
        # Feast existed and no updates were made
        # feast_created=False means the feast already existed
        action = "skipped"
        status = "skipped"
    
    if status == "skipped":
        return (
            feast_obj,
            False,
            {
                "status": "skipped",
                "reason": "feast_already_exists",
                "date": str(date_obj)
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
            "date": str(date_obj)
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