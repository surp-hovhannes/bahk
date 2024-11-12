"""Utilities for supporting backend."""
from datetime import datetime, timedelta
import logging
import re

import urllib

from django.core.mail import EmailMultiAlternatives, send_mail
from django.conf import settings
from django.db.models import OuterRef, Subquery, Q
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from hub.models import Day, Fast, Profile
from hub.serializers import FastSerializer


logger = logging.getLogger(__name__)

PARSER_REGEX = r"^([A-za-z1-4\'\. ]+) ([0-9]+\.)?([0-9]+)\-?([0-9]+\.)?([0-9]+)?$"


def scrape_readings(date_obj, church, date_format="%Y%m%d", max_num_readings=40):
    """Scrapes readings from sacredtradition.am""" 
    if "Armenian Apostolic" not in church.name:
        logging.error("Web-scraping for readings only set up for the Armenian Apostolic Church. %s not supported.", 
                      church.name)
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