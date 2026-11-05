"""Durable Apple revocation scheduling; never send credentials to a new task."""
import hashlib
import logging

from django.db import DEFAULT_DB_ALIAS, transaction

from hub.models import PendingAppleRevocation

logger = logging.getLogger(__name__)


def persist_revocations(ciphertexts, *, using=DEFAULT_DB_ALIAS):
    """Call inside the deletion transaction so rollback restores both sides."""
    ids = []
    for ciphertext in ciphertexts:
        if not ciphertext:
            continue
        row, _ = PendingAppleRevocation.objects.using(using).get_or_create(
            ciphertext_digest=hashlib.sha256(ciphertext.encode("ascii")).hexdigest(),
            defaults={"token_encrypted": ciphertext},
        )
        ids.append(row.pk)
    return ids


def wake_revocation_worker(ids, *, using=DEFAULT_DB_ALIAS):
    """Best-effort latency optimization; beat recovers a lost wakeup."""
    if not ids:
        return
    from hub.tasks.social_auth_tasks import process_pending_apple_revocations_task

    try:
        process_pending_apple_revocations_task.apply_async(
            args=[ids], kwargs={"using": using}, retry=False,
        )
    except Exception:
        # Do not execute inline or log task arguments/connection credentials.
        logger.warning("social_auth.apple_revocation_wakeup_failed", extra={"pending": len(ids)})


def preserve_revocations(ciphertexts, *, using=DEFAULT_DB_ALIAS):
    ids = persist_revocations(ciphertexts, using=using)
    if ids:
        transaction.on_commit(lambda: wake_revocation_worker(ids, using=using), using=using)
    return ids
