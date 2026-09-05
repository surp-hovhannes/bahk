"""Atomic invalidation and durable outbox, shared by every write route."""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from icons.models import Icon, IconTaxonomyProjection, IconTaxonomyWork
from icons.services.taxonomy_inputs import fingerprint

logger = logging.getLogger(__name__)


def wake_dispatcher():
    if not getattr(settings, "ICON_TAXONOMY_DISPATCH_ENABLED", False):
        return
    try:
        from icons.tasks import dispatch_icon_taxonomy

        dispatch_icon_taxonomy.apply_async(countdown=3)
    except Exception:
        # No private metadata in logs; explicit dispatcher recovers this row.
        logger.warning("Icon taxonomy wake failed; durable work remains pending")


@transaction.atomic
def schedule(icon_id, *, force=False):
    icon = Icon.objects.select_for_update().filter(pk=icon_id).first()
    if icon is None:
        return
    fp = fingerprint(icon)
    work, created = IconTaxonomyWork.objects.get_or_create(icon=icon, defaults={"fingerprint": fp})
    if not created and work.fingerprint == fp and not force:
        return work
    IconTaxonomyProjection.objects.filter(icon=icon).delete()
    if not created:
        work.revision += 1
    work.fingerprint = fp
    work.state = "pending"
    work.attempts = 0
    work.error = ""
    work.lease_token = ""
    work.lease_until = None
    work.available_at = timezone.now() + timedelta(seconds=2)
    work.save()
    transaction.on_commit(wake_dispatcher)
    return work


@transaction.atomic
def ingest_icon(*, icon=None, tags=None, **fields):
    """Import/API boundary: final image and tags are committed together."""
    icon = icon or Icon()
    for key, value in fields.items():
        if key not in {"title", "church", "church_id", "image"}:
            raise ValueError("unsupported_ingestion_field")
        setattr(icon, key, value)
    icon.save()
    if tags is not None:
        icon.tags.set(tags)
    schedule(icon.pk)
    return icon


def refresh_content(icon):
    """Read storage outside locks, then advance the durable content generation.

    Supported writers lock/save Icon; untracked storage replacement is detected
    here and by reconciliation. This is not a transaction over external storage.
    """
    from icons.services.taxonomy_inputs import image_input

    try:
        actual = image_input(icon)[0]
    except (OSError, ValueError):
        actual = ""
    if actual == icon.image_content_digest:
        return actual
    with transaction.atomic():
        locked = Icon.objects.select_for_update().get(pk=icon.pk)
        if (locked.image.name, locked.image_revision) != (icon.image.name, icon.image_revision):
            icon.refresh_from_db()
            return None
        locked.image_content_digest = actual
        locked.image_revision += 1
        Icon.objects.filter(pk=icon.pk).update(image_content_digest=actual, image_revision=locked.image_revision)
        schedule(icon.pk, force=True)
        icon.image_content_digest = actual
        icon.image_revision = locked.image_revision
    return actual


def reconcile_versions(limit=100):
    """Bounded recovery for policy changes, including unavailable prior analyses."""
    from icons.services.taxonomy_inputs import versions, dependencies_current

    current_versions = versions()
    ids = []
    for work in (
        IconTaxonomyWork.objects.filter(state__in=["complete", "unavailable"])
        .select_related("icon")
        .order_by("available_at", "pk")[:limit]
    ):
        latest = work.icon.taxonomic_analyses.order_by("-created_at").first()
        if latest and (latest.versions != current_versions or not dependencies_current(latest.dependencies)):
            schedule(work.icon_id, force=True)
            ids.append(work.icon_id)
        else:
            # Rotate the bounded sweep without adding a scheduler/cursor table.
            IconTaxonomyWork.objects.filter(pk=work.pk, state=work.state, revision=work.revision).update(
                available_at=timezone.now()
            )
    return ids
