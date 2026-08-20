"""Clean expired, unattached objects created by the dedicated CLI upload flow."""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from learning_resources.devotional_video_uploads import DEDICATED_PREFIX, MultipartUploadAdapter
from learning_resources.models import DevotionalVideoUpload


def _is_missing(exc):
    response = getattr(exc, "response", {})
    code = str(response.get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NoSuchUpload", "NotFound"}


def _claim_cleanup(upload_id):
    """Claim an eligible row before making irreversible storage calls."""
    with transaction.atomic():
        upload = DevotionalVideoUpload.objects.select_for_update().get(pk=upload_id)
        recoverable_lease = (
            upload.state == DevotionalVideoUpload.State.CLEANED
            and upload.cleaned_at is None
        )
        eligible = (
            upload.state in {
                DevotionalVideoUpload.State.INITIALIZED,
                DevotionalVideoUpload.State.READY,
            }
            and upload.expires_at <= timezone.now()
        )
        if not (recoverable_lease or eligible) or not upload.storage_key.startswith(
            DEDICATED_PREFIX
        ):
            return None
        previous_state = upload.state
        if not recoverable_lease:
            upload.state = DevotionalVideoUpload.State.CLEANED
            upload.cleaned_at = None
            upload.save(update_fields=["state", "cleaned_at"])
        return {
            "pk": upload.pk,
            "storage_key": upload.storage_key,
            "upload_id": upload.upload_id,
            "previous_state": previous_state,
        }


def _finish_cleanup(upload_id):
    DevotionalVideoUpload.objects.filter(
        pk=upload_id,
        state=DevotionalVideoUpload.State.CLEANED,
        cleaned_at__isnull=True,
    ).update(cleaned_at=timezone.now())


def _release_cleanup(claim):
    """Make a failed storage operation eligible for a later cleanup attempt."""
    if claim["previous_state"] == DevotionalVideoUpload.State.CLEANED:
        return
    DevotionalVideoUpload.objects.filter(
        pk=claim["pk"],
        state=DevotionalVideoUpload.State.CLEANED,
        cleaned_at__isnull=True,
    ).update(state=claim["previous_state"])


def _ignore_missing(operation):
    try:
        operation()
    except Exception as exc:
        if not _is_missing(exc):
            raise


class Command(BaseCommand):
    help = "Dry-run (default) or clean expired unattached devotional CLI uploads."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true")

    def handle(self, *args, **options):
        execute = options["execute"]
        candidates = DevotionalVideoUpload.objects.filter(
            Q(
                state__in=[
                    DevotionalVideoUpload.State.INITIALIZED,
                    DevotionalVideoUpload.State.READY,
                ],
                expires_at__lte=timezone.now(),
            )
            | Q(state=DevotionalVideoUpload.State.CLEANED, cleaned_at__isnull=True),
            storage_key__startswith=DEDICATED_PREFIX,
        ).order_by("pk")
        adapter = MultipartUploadAdapter() if execute and candidates.exists() else None
        count = 0
        for candidate in candidates.iterator():
            count += 1
            self.stdout.write(
                f"{'CLEAN' if execute else 'WOULD CLEAN'} upload={candidate.pk} "
                f"state={candidate.state} key={candidate.storage_key}"
            )
            if not execute:
                continue
            try:
                claim = _claim_cleanup(candidate.pk)
                if claim is None:
                    continue
                try:
                    # Both calls are safe for every claimed dedicated key. This
                    # also lets a CLEANED/null lease recover without needing to
                    # remember whether a crash interrupted abort or delete.
                    _ignore_missing(
                        lambda: adapter.abort_for_cleanup(
                            key=claim["storage_key"], upload_id=claim["upload_id"]
                        )
                    )
                    _ignore_missing(
                        lambda: adapter.delete_object(key=claim["storage_key"])
                    )
                except Exception:
                    _release_cleanup(claim)
                    raise
                _finish_cleanup(claim["pk"])
            except Exception as exc:
                self.stderr.write(f"FAILED upload={candidate.pk}: {exc}")
                continue
        self.stdout.write(f"{'Cleaned' if execute else 'Found'} {count} expired upload(s).")
