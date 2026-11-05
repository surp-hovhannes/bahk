"""Celery tasks for Sign in with Apple token revocation (issue #598)."""
import logging

from celery import shared_task

from hub.services.social_auth.apple import AppleTokenError, apple_key_configured, get_apple_client
from hub.services.social_auth.crypto import InvalidToken, decrypt_payload

logger = logging.getLogger(__name__)

REVOCATION_MAX_RETRIES = 6


@shared_task(bind=True, max_retries=REVOCATION_MAX_RETRIES, acks_late=True)
def revoke_apple_tokens_task(self, ciphertexts):
    """Revoke stored Apple refresh tokens after account deletion or unlink.

    Arguments stay encrypted in the broker; plaintext exists only in this
    worker's memory for the duration of the HTTP call. Transient failures
    retry with exponential backoff; permanent ones are logged (to Sentry) with
    Apple's error code only.
    """
    if not apple_key_configured():
        logger.error(
            "social_auth.apple_revocation_not_configured",
            extra={"pending": len(ciphertexts)},
        )
        return {"revoked": 0, "failed": len(ciphertexts), "retrying": 0}

    client = get_apple_client()
    revoked, failed, retry = 0, 0, []
    for ciphertext in ciphertexts:
        try:
            payload = decrypt_payload(ciphertext)
            refresh_token = payload["refresh_token"]
            client_id = payload["client_id"]
        except (InvalidToken, KeyError, TypeError, ValueError):
            failed += 1
            logger.error("social_auth.apple_revocation_undecryptable")
            continue
        try:
            client.revoke_refresh_token(refresh_token, client_id=client_id)
            revoked += 1
        except AppleTokenError as exc:
            if exc.retryable:
                retry.append(ciphertext)
            else:
                failed += 1
                logger.error("social_auth.apple_revocation_failed", extra={"reason": exc.reason})

    if retry:
        if self.request.retries < REVOCATION_MAX_RETRIES:
            raise self.retry(args=[retry], countdown=60 * (2 ** self.request.retries))
        failed += len(retry)
        logger.error("social_auth.apple_revocation_gave_up", extra={"pending": len(retry)})
    return {"revoked": revoked, "failed": failed, "retrying": 0}
