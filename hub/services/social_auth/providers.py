"""Verify Apple and Google ID tokens.

The client never tells us who the user is: it hands over the provider-issued ID
token and the raw per-attempt nonce, and everything about identity comes from a
token whose signature, issuer, audience, expiry and nonce check out here.

Checks, in order:

1. Shape: a compact JWS no larger than ``MAX_ID_TOKEN_LENGTH``.
2. Header: ``alg`` must be RS256 (both providers sign with RSA; this rejects
   ``none`` and HS256 key-confusion forgeries) and ``kid`` must be present.
3. Signature against the provider's published JWKS. Key sets are cached in the
   shared Django cache, honouring the provider's ``Cache-Control`` lifetime; an
   unknown ``kid`` triggers at most one forced refetch per provider per minute
   (key rotation without letting forged ``kid`` values hammer the provider).
4. ``iss``, ``aud`` (allowlist), ``exp``/``iat`` with bounded clock skew.
5. ``sub`` present and usable.
6. Nonce: Apple's claim is SHA-256(raw nonce) hex; Google's is the raw nonce.
7. Replay: a verified token is accepted once (``cache.add`` keyed on its hash,
   expiring with the token).
"""
import hashlib
import hmac
import logging
import re
import time
from dataclasses import dataclass

import jwt
import requests
from django.core.cache import cache

from . import config
from .errors import (
    InvalidNonce,
    InvalidProviderToken,
    InvalidRequest,
    ProviderUnavailable,
    SocialAuthUnavailable,
)

logger = logging.getLogger(__name__)

MAX_ID_TOKEN_LENGTH = 8192
MIN_NONCE_LENGTH = 16
MAX_NONCE_LENGTH = 256
MAX_SUBJECT_LENGTH = 255
JWKS_DEFAULT_TTL = 3600
JWKS_MIN_TTL = 300
JWKS_MAX_TTL = 86400
JWKS_FORCED_REFRESH_INTERVAL = 60
APPLE_PRIVATE_RELAY_DOMAIN = "privaterelay.appleid.com"
_CACHE_PREFIX = "bahk:social_auth"
_MAX_AGE_RE = re.compile(r"max-age=(\d+)")


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    issuers: tuple[str, ...]
    jwks_url: str


PROVIDER_SPECS = {
    config.APPLE: ProviderSpec(
        name=config.APPLE,
        issuers=("https://appleid.apple.com",),
        jwks_url="https://appleid.apple.com/auth/keys",
    ),
    config.GOOGLE: ProviderSpec(
        name=config.GOOGLE,
        issuers=("https://accounts.google.com", "accounts.google.com"),
        jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    ),
}


@dataclass(frozen=True)
class VerifiedIdentity:
    """Claims from a fully verified ID token. Never contains the token itself."""

    provider: str
    subject: str
    audience: str
    email: str
    email_verified: bool
    is_private_relay: bool
    name: str
    issued_at: int
    expires_at: int

    @property
    def trusted_email(self) -> str:
        """An email we may treat as belonging to this person, or ''.

        Unverified addresses are ignored outright. Apple relay addresses are
        deliverable but are never matched against existing accounts (decision
        recorded in #598); callers check ``is_private_relay`` for that.
        """
        return self.email if self.email and self.email_verified else ""


def _claim_bool(value) -> bool:
    # Apple has historically sent "true"/"false" strings for boolean claims.
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _jwks_cache_key(provider: str) -> str:
    return f"{_CACHE_PREFIX}:jwks:{provider}"


def _ttl_from_headers(headers) -> int:
    match = _MAX_AGE_RE.search(headers.get("Cache-Control", "") or "")
    ttl = int(match.group(1)) if match else JWKS_DEFAULT_TTL
    return max(JWKS_MIN_TTL, min(JWKS_MAX_TTL, ttl))


def fetch_jwks(spec: ProviderSpec, *, force: bool = False) -> dict:
    """Return the provider's JWKS, from the shared cache unless ``force``."""
    cache_key = _jwks_cache_key(spec.name)
    if not force:
        cached = cache.get(cache_key)
        if isinstance(cached, dict) and isinstance(cached.get("keys"), list):
            return cached
    try:
        response = requests.get(spec.jwks_url, timeout=config.http_timeout_seconds())
        response.raise_for_status()
        jwks = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "social_auth.jwks_fetch_failed",
            extra={"provider": spec.name, "error_type": type(exc).__name__},
        )
        raise ProviderUnavailable(reason="jwks_fetch_failed") from exc
    if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
        logger.warning("social_auth.jwks_malformed", extra={"provider": spec.name})
        raise ProviderUnavailable(reason="jwks_malformed")
    cache.set(cache_key, jwks, _ttl_from_headers(response.headers))
    return jwks


def _find_key(jwks: dict, kid: str):
    for key in jwks.get("keys", []):
        if isinstance(key, dict) and key.get("kid") == kid:
            return key
    return None


def signing_key_for(spec: ProviderSpec, kid: str):
    """Resolve ``kid`` to an RSA public key from the provider's JWKS."""
    key_data = _find_key(fetch_jwks(spec), kid)
    if key_data is None and cache.add(
        f"{_CACHE_PREFIX}:jwks_refresh:{spec.name}", 1, JWKS_FORCED_REFRESH_INTERVAL
    ):
        # Possibly a key rotation: refetch once, rate limited per provider.
        key_data = _find_key(fetch_jwks(spec, force=True), kid)
    if key_data is None:
        raise InvalidProviderToken(reason="unknown_kid")
    if key_data.get("kty") != "RSA" or key_data.get("alg", "RS256") != "RS256":
        raise InvalidProviderToken(reason="unexpected_key_type")
    try:
        return jwt.PyJWK(key_data, algorithm="RS256").key
    except jwt.PyJWTError as exc:
        raise InvalidProviderToken(reason="unusable_jwk") from exc


def validate_raw_nonce(raw_nonce) -> str:
    if raw_nonce in (None, ""):
        return ""
    if (
        not isinstance(raw_nonce, str)
        or not (MIN_NONCE_LENGTH <= len(raw_nonce) <= MAX_NONCE_LENGTH)
        or not raw_nonce.isascii()
        or not raw_nonce.isprintable()
    ):
        raise InvalidNonce(reason="malformed_nonce")
    return raw_nonce


def _check_nonce(provider: str, claims: dict, raw_nonce: str) -> None:
    claim = claims.get("nonce")
    if provider == config.APPLE:
        # The app sends SHA-256(raw) to Apple and the raw value to us.
        if not raw_nonce or not isinstance(claim, str):
            raise InvalidNonce(reason="nonce_missing")
        expected = hashlib.sha256(raw_nonce.encode("ascii")).hexdigest()
        if not hmac.compare_digest(expected, claim):
            raise InvalidNonce(reason="nonce_mismatch")
        return

    # Google echoes the raw nonce when the SDK binds one.
    if claim is None:
        if config.google_requires_nonce():
            raise InvalidNonce(reason="nonce_missing")
        return
    if not raw_nonce or not isinstance(claim, str):
        raise InvalidNonce(reason="nonce_missing")
    if not hmac.compare_digest(raw_nonce, claim):
        raise InvalidNonce(reason="nonce_mismatch")


def _mark_token_used(provider: str, id_token: str, expires_at: int) -> None:
    digest = hashlib.sha256(id_token.encode("utf-8")).hexdigest()
    ttl = max(60, expires_at - int(time.time()) + config.jwt_leeway_seconds())
    if not cache.add(f"{_CACHE_PREFIX}:used:{provider}:{digest}", 1, ttl):
        raise InvalidProviderToken(reason="token_replayed")


def verify_id_token(provider: str, id_token, *, raw_nonce=None) -> VerifiedIdentity:
    """Verify ``id_token`` for ``provider``; raise ``SocialAuthError`` on any failure."""
    spec = PROVIDER_SPECS.get(provider)
    if spec is None:
        raise InvalidRequest(reason="unsupported_provider")
    if not config.provider_enabled(provider):
        raise SocialAuthUnavailable(reason="provider_disabled")
    audiences = config.allowed_audiences(provider)
    raw_nonce = validate_raw_nonce(raw_nonce)

    if (
        not isinstance(id_token, str)
        or len(id_token) > MAX_ID_TOKEN_LENGTH
        or id_token.count(".") != 2
    ):
        raise InvalidProviderToken(reason="malformed_token")
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise InvalidProviderToken(reason="malformed_header") from exc
    if header.get("alg") != "RS256":
        raise InvalidProviderToken(reason="unexpected_alg")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise InvalidProviderToken(reason="missing_kid")

    key = signing_key_for(spec, kid)
    try:
        claims = jwt.decode(
            id_token,
            key=key,
            algorithms=["RS256"],
            audience=audiences,
            issuer=list(spec.issuers),
            leeway=config.jwt_leeway_seconds(),
            options={"require": ["iss", "aud", "exp", "iat"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidProviderToken(reason="expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise InvalidProviderToken(reason="wrong_audience") from exc
    except jwt.InvalidIssuerError as exc:
        raise InvalidProviderToken(reason="wrong_issuer") from exc
    except jwt.ImmatureSignatureError as exc:
        raise InvalidProviderToken(reason="not_yet_valid") from exc
    except jwt.PyJWTError as exc:
        raise InvalidProviderToken(reason="invalid_signature_or_claims") from exc

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > MAX_SUBJECT_LENGTH:
        raise InvalidProviderToken(status_code=400, reason="missing_subject")

    _check_nonce(provider, claims, raw_nonce)

    token_aud = claims["aud"]
    audience = token_aud if isinstance(token_aud, str) else next(
        (aud for aud in token_aud if aud in audiences), ""
    )
    email = claims.get("email")
    email = email.strip() if isinstance(email, str) else ""
    is_relay = provider == config.APPLE and (
        _claim_bool(claims.get("is_private_email"))
        or email.lower().endswith("@" + APPLE_PRIVATE_RELAY_DOMAIN)
    )
    name = claims.get("name") if provider == config.GOOGLE else ""
    identity = VerifiedIdentity(
        provider=provider,
        subject=subject,
        audience=audience,
        email=email,
        email_verified=bool(email) and _claim_bool(claims.get("email_verified")),
        is_private_relay=is_relay,
        name=name.strip() if isinstance(name, str) else "",
        issued_at=int(claims["iat"]),
        expires_at=int(claims["exp"]),
    )

    # Only fully verified tokens consume a replay slot.
    _mark_token_used(provider, id_token, identity.expires_at)
    return identity
