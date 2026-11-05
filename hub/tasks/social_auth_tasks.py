"""Retry durable Apple revocations independently of the sign-in feature flag."""
import logging
from datetime import timedelta

from celery import shared_task
from django.db import DEFAULT_DB_ALIAS, DatabaseError, connections, transaction
from django.utils import timezone

from hub.models import PendingAppleRevocation
from hub.services.social_auth.apple import AppleTokenError, apple_key_configured, get_apple_client
from hub.services.social_auth.crypto import InvalidToken, decrypt_payload
from hub.services.social_auth.revocation import persist_revocations

logger = logging.getLogger(__name__)
MAX_BATCH_SIZE = 50


def defer_revocation(row, reason, *, retryable, using):
    row.attempts += 1
    # Keep every failed credential, including configuration errors and failures
    # beyond the old six-retry limit. Bound attempts per hour, not durability.
    delay = min(3600, 60 * (2 ** min(row.attempts - 1, 6))) if retryable else 86400
    row.next_attempt_at = timezone.now() + timedelta(seconds=delay)
    row.last_error = reason  # Fixed internal code, never an exception message.
    row.save(using=using, update_fields=["attempts", "next_attempt_at", "last_error"])
    logger.warning("social_auth.apple_revocation_pending", extra={"revocation_id": row.pk, "reason": reason})


@shared_task(acks_late=True)
def process_pending_apple_revocations_task(revocation_ids=None, *, limit=MAX_BATCH_SIZE, using=DEFAULT_DB_ALIAS):
    """Process a bounded due batch; duplicate deliveries are safe.

    The row lock is held across the bounded Apple HTTP request. A crash rolls
    back the transaction and leaves the row due. If Apple succeeded before a
    crash, repeating revocation is safe (invalid_grant/invalid_token are done).
    """
    # Beat/worker can roll before the web pre-deploy migration on Render.
    # With no outbox table yet there can be no pending rows; the next tick
    # resumes automatically after migration 0074 creates it.
    if PendingAppleRevocation._meta.db_table not in connections[using].introspection.table_names():
        return {"revoked": 0, "deferred": 0, "waiting_for_migration": True}
    limit = max(1, min(int(limit), MAX_BATCH_SIZE))
    pending = PendingAppleRevocation.objects.using(using)
    due = pending.filter(next_attempt_at__lte=timezone.now())
    if revocation_ids is not None:
        due = due.filter(pk__in=revocation_ids)
    ids = list(due.order_by("next_attempt_at", "pk").values_list("pk", flat=True)[:limit])
    revoked, deferred = 0, 0
    for pk in ids:
        with transaction.atomic(using=using):
            row = pending.select_for_update(skip_locked=True).filter(
                pk=pk, next_attempt_at__lte=timezone.now(),
            ).first()
            if row is None:
                continue
            if not apple_key_configured():
                defer_revocation(row, "apple_key_not_configured", retryable=False, using=using)
                deferred += 1
                continue
            try:
                payload = decrypt_payload(row.token_encrypted)
                token, client_id = payload["refresh_token"], payload["client_id"]
                if not isinstance(token, str) or not token or not isinstance(client_id, str) or not client_id:
                    raise ValueError
            except (InvalidToken, KeyError, TypeError, ValueError):
                defer_revocation(row, "undecryptable_token", retryable=False, using=using)
                deferred += 1
                continue
            try:
                get_apple_client().revoke_refresh_token(token, client_id=client_id)
            except AppleTokenError as exc:
                defer_revocation(
                    row, "apple_unavailable" if exc.retryable else "apple_rejected",
                    retryable=exc.retryable, using=using,
                )
                deferred += 1
                continue
            row.delete(using=using)
            revoked += 1
    return {"revoked": revoked, "deferred": deferred}


@shared_task(
    bind=True, acks_late=True, reject_on_worker_lost=True,
    autoretry_for=(DatabaseError,), retry_backoff=True, retry_backoff_max=3600, max_retries=None,
)
def revoke_apple_tokens_task(self, ciphertexts):
    """Accept ciphertext messages queued by the previous release during rollout.

    New deletion paths queue IDs only. Persist legacy messages before their
    first attempt so subsequent failures recover through the same due scanner.
    DB/migration outages retry the legacy message until its credential is saved.
    """
    with transaction.atomic():
        ids = persist_revocations(ciphertexts)
    return process_pending_apple_revocations_task(revocation_ids=ids)
