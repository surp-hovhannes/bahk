"""Views for returning data pertaining to daily readings.

Currently based on the Daily Worship app's website, sacredtradition.am
"""
from datetime import datetime
import re

import urllib.request

from django.http import JsonResponse


def get_daily_readings_for_today(*_args, **_kwargs):
    """Based on https://www.geeksforgeeks.org/python-web-scraping-tutorial/"""
    today_str = datetime.strftime(datetime.today().date(), "%Y%m%d")

    url = f"https://sacredtradition.am/Calendar/nter.php?NM=0&iM1103&iL=2&ymd={today_str}"
    response = urllib.request.urlopen(url)
    data = response.read()
    html_content = data.decode("utf-8")

    readings = {}
    book_start = html_content.find("<b>")
    
    while book_start != -1:
        i1 = book_start + len("<b>")
        i2 = html_content.find("</b>")
        reading_str = html_content[i1:i2]
    
        groups = re.search("^([A-za-z\'\. ]+) ([0-9]+)\.([0-9]+)\-([0-9]+)$", reading_str)
    
        book = groups.group(1)
        chapter = groups.group(2)
        verse_start = groups.group(3)
        verse_end = groups.group(4)
    
        readings[book] = {chapter: [verse_start, verse_end]}
    
        html_content = html_content[i2 + 1:]
        book_start = html_content.find("<b>")

    return JsonResponse(readings)
