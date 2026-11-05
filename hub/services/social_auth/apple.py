"""Sign in with Apple server-to-server calls: code exchange and token revocation.

App Store Review Guideline 5.1.1(v) requires revoking a user's Sign in with
Apple tokens when they delete their account. That needs an Apple refresh token,
obtained by exchanging the single-use ``authorization_code`` the app receives at
sign-in, and a client secret: an ES256 JWT signed with the Sign in with Apple
private key (``APPLE_PRIVATE_KEY``, ``APPLE_KEY_ID``, ``APPLE_TEAM_ID``).

The private key, client secrets, codes and tokens are never logged. Errors carry
only Apple's short ``error`` code (for example ``invalid_grant``).

Everything here goes through ``AppleTokenClient`` so tests (and staging before
the key is installed) can substitute a fake: see ``get_apple_client``.
"""
import logging
import time
from dataclasses import dataclass

import jwt
import requests
from django.conf import settings

from . import config

logger = logging.getLogger(__name__)

APPLE_AUDIENCE = "https://appleid.apple.com"
APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
APPLE_REVOKE_URL = "https://appleid.apple.com/auth/revoke"
CLIENT_SECRET_LIFETIME_SECONDS = 300
MAX_AUTHORIZATION_CODE_LENGTH = 1024


class AppleTokenError(Exception):
    """An Apple token endpoint call failed. ``retryable`` marks transient failures."""

    def __init__(self, reason: str, *, retryable: bool = False):
        self.reason = reason
        self.retryable = retryable
        super().__init__(reason)


@dataclass(frozen=True)
class AppleTokens:
    refresh_token: str
    subject: str


def _private_key() -> str:
    key = getattr(settings, "APPLE_PRIVATE_KEY", "") or ""
    # Hosting dashboards often store multi-line PEMs with literal "\n" escapes.
    return key.replace("\\n", "\n").strip()


def apple_key_configured() -> bool:
    return bool(
        getattr(settings, "APPLE_TEAM_ID", "")
        and getattr(settings, "APPLE_KEY_ID", "")
        and _private_key()
    )


def _apple_error_code(response) -> str:
    try:
        error = response.json().get("error")
    except ValueError:
        error = None
    return error if isinstance(error, str) and len(error) <= 64 else f"http_{response.status_code}"


class AppleTokenClient:
    """Talks to Apple's token and revoke endpoints with a freshly signed client secret."""

    def __init__(self, *, session=None):
        self.session = session or requests

    def client_secret(self, client_id: str, *, now: int | None = None) -> str:
        if not apple_key_configured():
            raise AppleTokenError("apple_key_not_configured")
        issued_at = int(now if now is not None else time.time())
        try:
            return jwt.encode(
                {
                    "iss": settings.APPLE_TEAM_ID,
                    "iat": issued_at,
                    "exp": issued_at + CLIENT_SECRET_LIFETIME_SECONDS,
                    "aud": APPLE_AUDIENCE,
                    "sub": client_id,
                },
                _private_key(),
                algorithm="ES256",
                headers={"kid": settings.APPLE_KEY_ID},
            )
        except (ValueError, TypeError, jwt.PyJWTError):
            # Do not chain: the original message may quote key material.
            raise AppleTokenError("apple_key_invalid") from None

    def _post(self, url: str, data: dict):
        try:
            return self.session.post(url, data=data, timeout=config.http_timeout_seconds())
        except requests.RequestException as exc:
            raise AppleTokenError(f"network_{type(exc).__name__}", retryable=True) from None

    def exchange_authorization_code(self, code: str, *, client_id: str, expected_subject: str) -> AppleTokens:
        """Exchange the app's single-use code for a refresh token."""
        if not isinstance(code, str) or not code or len(code) > MAX_AUTHORIZATION_CODE_LENGTH:
            raise AppleTokenError("malformed_authorization_code")
        response = self._post(
            APPLE_TOKEN_URL,
            {
                "client_id": client_id,
                "client_secret": self.client_secret(client_id),
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        if response.status_code != 200:
            raise AppleTokenError(
                _apple_error_code(response), retryable=response.status_code >= 500
            )
        try:
            payload = response.json()
        except ValueError:
            raise AppleTokenError("malformed_token_response") from None
        refresh_token = payload.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise AppleTokenError("missing_refresh_token")
        # The response arrives directly from Apple over TLS; its id_token only
        # needs to name the same person the verified sign-in token did.
        subject = ""
        id_token = payload.get("id_token")
        if isinstance(id_token, str):
            try:
                subject = jwt.decode(id_token, options={"verify_signature": False}).get("sub", "")
            except jwt.PyJWTError:
                subject = ""
        if subject != expected_subject:
            raise AppleTokenError("subject_mismatch")
        return AppleTokens(refresh_token=refresh_token, subject=subject)

    def revoke_refresh_token(self, refresh_token: str, *, client_id: str) -> None:
        """Revoke a refresh token. An already-invalid token counts as revoked."""
        response = self._post(
            APPLE_REVOKE_URL,
            {
                "client_id": client_id,
                "client_secret": self.client_secret(client_id),
                "token": refresh_token,
                "token_type_hint": "refresh_token",
            },
        )
        if response.status_code == 200:
            return
        error = _apple_error_code(response)
        if error in {"invalid_grant", "invalid_token"}:
            return
        raise AppleTokenError(error, retryable=response.status_code >= 500 or response.status_code == 429)


_client_override = None


def get_apple_client() -> AppleTokenClient:
    return _client_override or AppleTokenClient()


def set_apple_client_for_tests(client) -> None:
    """Install a fake Apple client (``None`` restores the real one)."""
    global _client_override
    _client_override = client
