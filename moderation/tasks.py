"""Scheduled moderation notices."""

from datetime import timedelta

from celery import shared_task
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from icons.models import IconFeedback
from moderation.notifications import deliver, site_link

ICON_DIGEST_SENT_AT = "bahk:moderation:icon-feedback-digest-at"


@shared_task
def send_icon_feedback_digest():
    """Tell General reviewers, once a day, how many icon reports arrived since the last digest.

    Icon feedback is anonymous, so emailing every reviewer per submission let anyone fill
    their inboxes. Nothing is sent on a day with no new reports.
    """
    now = timezone.now()
    since = cache.get(ICON_DIGEST_SENT_AT) or now - timedelta(days=1)
    open_reports = IconFeedback.objects.filter(is_resolved=False)
    new = open_reports.filter(created_at__gte=since, created_at__lt=now).count()
    if not new:
        cache.set(ICON_DIGEST_SENT_AT, now, timeout=None)
        return {"new": 0}
    total = open_reports.count()
    reports = f"{new} new icon report{'s' if new != 1 else ''}"
    url = site_link(reverse("moderation-dashboard") + "?type=icon")
    deliver(
        f"{reports} to review",
        f"{reports} since the last digest; {total} open in total. Sign in to review them: {url}",
        "general",
        "icon feedback digest",
    )
    # Marked only after sending, so a run that fails before this point is covered by the next one.
    cache.set(ICON_DIGEST_SENT_AT, now, timeout=None)
    return {"new": new, "open": total}
