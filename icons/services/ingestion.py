"""Atomic invalidation and durable outbox, shared by every write route."""

import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from icons.models import Icon, IconTaxonomyProjection, IconTaxonomyWork
from icons.services.taxonomy_inputs import fingerprint

logger = logging.getLogger(__name__)
INLINE_OWNED = ContextVar("taxonomy_inline_owned", default=False)


@contextmanager
def inline_owned():
    token = INLINE_OWNED.set(str(uuid.uuid4()))
    try:
        yield
    finally:
        INLINE_OWNED.reset(token)


def own_inline_work(ids, *, resume_budget_blocked=False, resume_inline=False):
    """Atomically reserve selected work, including explicit resumes, before I/O.

    No intermediate ordinary pending state is exposed to background dispatchers.
    A crashed invocation remains owned until explicit bounded recovery.
    """
    from django.db.models import Q, Case, When, F, Value, IntegerField

    owner = INLINE_OWNED.get()
    if not owner:
        raise ValueError("inline_owner_required")
    now = timezone.now()
    eligible = Q(state="pending") | Q(state="retry", available_at__lte=now) | Q(state="running", lease_until__lte=now)
    if resume_budget_blocked:
        eligible |= Q(state="budget_blocked")
    if resume_inline:
        eligible |= Q(state__in=["inline_pending", "inline_running"], lease_until__lte=now) | Q(
            state="inline_retry", available_at__lte=now
        )
    return (
        IconTaxonomyWork.objects.filter(icon_id__in=ids)
        .filter(eligible)
        .update(
            state="inline_pending",
            lease_token=owner,
            lease_until=now + timedelta(seconds=600),
            attempts=Case(
                When(state="budget_blocked", then=Value(0)), default=F("attempts"), output_field=IntegerField()
            ),
        )
    )


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
    work.state = "inline_pending" if INLINE_OWNED.get() else "pending"
    work.attempts = 0
    work.error = ""
    work.lease_token = INLINE_OWNED.get() or ""
    work.lease_until = timezone.now() + timedelta(seconds=600) if INLINE_OWNED.get() else None
    work.available_at = timezone.now() + timedelta(seconds=2)
    work.save()
    if not INLINE_OWNED.get():
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


def reconcile_versions(limit=100, *, church_id=None, icon_ids=None):
    """Bounded recovery for policy changes, including unavailable prior analyses."""
    from icons.services.taxonomy_inputs import versions, dependencies_current

    current_versions = versions()
    ids = []
    qs = IconTaxonomyWork.objects.filter(state__in=["complete", "unavailable"])
    if church_id is not None:
        qs = qs.filter(icon__church_id=church_id)
    if icon_ids is not None:
        qs = qs.filter(icon_id__in=icon_ids)
    for work in qs.select_related("icon").order_by("available_at", "pk")[:limit]:
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
