"""Human publication decisions, shared by the admin actions and the moderation dashboard."""

from datetime import timedelta

from django.utils import timezone

from events.models import Event, EventType, UserMilestone
from prayers.models import PrayerRequestAcceptance


def publish_prayer_requests(queryset, *, restart_expiration=False):
    """Approve every request in ``queryset`` the way automated moderation does; return the count.

    Callers choose which requests are eligible. ``restart_expiration`` gives an
    overturned rejection its full duration from now instead of from submission.
    """
    count = 0
    now = timezone.now()
    for prayer_request in queryset:
        prayer_request.status = "approved"
        prayer_request.reviewed = True
        prayer_request.moderated_at = now
        prayer_request.requires_human_review = False
        if restart_expiration:
            prayer_request.expiration_date = now + timedelta(days=prayer_request.duration_days)
        prayer_request.save()

        Event.create_event(
            event_type_code=EventType.PRAYER_REQUEST_CREATED,
            user=prayer_request.requester,
            target=prayer_request,
            title=f"Prayer request created: {prayer_request.title}",
            data={
                "prayer_request_id": prayer_request.id,
                "is_anonymous": prayer_request.is_anonymous,
            },
        )

        if prayer_request.requester.prayer_requests.filter(status="approved").count() == 1:
            UserMilestone.create_milestone(
                user=prayer_request.requester,
                milestone_type="first_prayer_request_created",
                related_object=prayer_request,
                data={
                    "prayer_request_id": prayer_request.id,
                    "title": prayer_request.title,
                },
            )

        # Automatically accept own prayer request
        PrayerRequestAcceptance.objects.get_or_create(
            prayer_request=prayer_request, user=prayer_request.requester, defaults={"counts_for_milestones": False}
        )
        count += 1
    return count


def reject_prayer_requests(queryset):
    """Reject every request in ``queryset``; return the count."""
    count = 0
    now = timezone.now()
    for prayer_request in queryset:
        prayer_request.status = "rejected"
        prayer_request.reviewed = True
        prayer_request.moderated_at = now
        prayer_request.requires_human_review = False
        prayer_request.save()
        count += 1
    return count
