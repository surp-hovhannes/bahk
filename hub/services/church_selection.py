"""Resolve incomplete profiles without guessing between multiple churches."""
from rest_framework.exceptions import APIException

from hub.models import Church, Profile


class ChurchSelectionRequired(APIException):
    status_code = 400
    default_code = "church_selection_required"
    default_detail = "Select a church before continuing."

    def __init__(self):
        super().__init__({"code": self.default_code, "detail": self.default_detail})


def only_church():
    churches = list(Church.objects.order_by("pk")[:2])
    return churches[0] if len(churches) == 1 else None


def assign_only_church(profile):
    """Persist the sole choice, preserving a selection made concurrently."""
    if profile is not None and profile.church_id is None:
        church = only_church()
        if church is not None:
            Profile.objects.filter(pk=profile.pk, church__isnull=True).update(church=church)
            profile.refresh_from_db(fields=["church"])
    return profile


def content_church(profile):
    profile = assign_only_church(profile)
    if profile is None or profile.church_id is None:
        raise ChurchSelectionRequired()
    return profile.church
