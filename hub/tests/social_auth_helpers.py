"""Test doubles for social sign-in: a fake provider that signs real RS256 tokens.

Tokens are signed with locally generated keys and served from a fake JWKS
endpoint (``requests.get`` is patched), so the production verification path
(header checks, JWKS lookup, signature, claims, nonce, replay) runs unchanged.
No network access and no real provider credentials are involved.
"""
import hashlib
import json
import secrets
import time
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives import serialization
from django.core.cache import cache
from django.test import override_settings

from hub.services.social_auth import apple as apple_module
from hub.services.social_auth.apple import AppleTokenError, AppleTokens
from hub.services.social_auth.providers import PROVIDER_SPECS

APPLE_AUDIENCE = "com.stjohn.bahk"
GOOGLE_AUDIENCE = "1234-web.apps.googleusercontent.com"
GOOGLE_IOS_CLIENT = "1234-ios.apps.googleusercontent.com"

_RSA_KEYS = {}


def rsa_key(name="primary"):
    if name not in _RSA_KEYS:
        _RSA_KEYS[name] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return _RSA_KEYS[name]


def public_jwk(private_key, kid):
    data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    data.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return data


def apple_nonce_claim(raw_nonce):
    return hashlib.sha256(raw_nonce.encode("ascii")).hexdigest()


def new_nonce():
    return secrets.token_urlsafe(24)


def make_id_token(
    provider,
    sub,
    *,
    nonce=None,
    email=None,
    email_verified=True,
    is_private_email=None,
    name=None,
    aud=None,
    iss=None,
    iat=None,
    exp=None,
    kid="kid-1",
    key=None,
    alg="RS256",
    headers=None,
    drop=(),
    extra=None,
):
    """Sign a provider-shaped ID token. ``nonce`` is the RAW nonce."""
    now = int(time.time())
    claims = {
        "iss": iss or PROVIDER_SPECS[provider].issuers[0],
        "aud": aud or (APPLE_AUDIENCE if provider == "apple" else GOOGLE_AUDIENCE),
        "sub": sub,
        "iat": iat if iat is not None else now,
        "exp": exp if exp is not None else now + 600,
    }
    if provider == "google":
        claims["azp"] = GOOGLE_IOS_CLIENT
    if nonce is not None:
        claims["nonce"] = apple_nonce_claim(nonce) if provider == "apple" else nonce
    if email is not None:
        claims["email"] = email
        # Apple has historically sent these booleans as strings.
        claims["email_verified"] = (
            ("true" if email_verified else "false") if provider == "apple" else email_verified
        )
    if is_private_email is not None:
        claims["is_private_email"] = "true" if is_private_email else "false"
    if name is not None:
        claims["name"] = name
    claims.update(extra or {})
    for claim in drop:
        claims.pop(claim, None)
    signing_key = key if key is not None else rsa_key()
    return jwt.encode(claims, signing_key, algorithm=alg, headers={"kid": kid, **(headers or {})})


class FakeJWKSResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {"Cache-Control": "public, max-age=3600"}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeJWKSServer:
    """Serves JWKS for both providers; records fetches."""

    def __init__(self):
        self.keys = {
            provider: [public_jwk(rsa_key(), "kid-1")] for provider in PROVIDER_SPECS
        }
        self.fetches = []
        self.fail = False

    def get(self, url, timeout=None):
        for provider, spec in PROVIDER_SPECS.items():
            if url == spec.jwks_url:
                self.fetches.append(provider)
                if self.fail:
                    import requests

                    raise requests.ConnectionError("simulated outage")
                return FakeJWKSResponse({"keys": list(self.keys[provider])})
        raise AssertionError(f"unexpected URL {url}")


class FakeAppleClient:
    """Stands in for AppleTokenClient; never talks to Apple."""

    def __init__(self, *, fail_exchange=None, fail_revoke=None, subject_override=None):
        self.exchanged = []
        self.revoked = []
        self.fail_exchange = fail_exchange
        self.fail_revoke = fail_revoke
        self.subject_override = subject_override

    def exchange_authorization_code(self, code, *, client_id, expected_subject):
        self.exchanged.append((code, client_id))
        if self.fail_exchange:
            raise self.fail_exchange
        subject = self.subject_override or expected_subject
        if subject != expected_subject:
            raise AppleTokenError("subject_mismatch")
        return AppleTokens(refresh_token=f"apple-refresh-for-{code}", subject=subject)

    def revoke_refresh_token(self, refresh_token, *, client_id):
        if self.fail_revoke:
            error = self.fail_revoke.pop(0) if isinstance(self.fail_revoke, list) else self.fail_revoke
            if error:
                raise error
        self.revoked.append((refresh_token, client_id))


def ec_private_key_pem():
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return key, pem


SOCIAL_SETTINGS = dict(
    SOCIAL_AUTH_ENABLED=True,
    SOCIAL_AUTH_APPLE_ENABLED=True,
    SOCIAL_AUTH_GOOGLE_ENABLED=True,
    APPLE_ALLOWED_AUDIENCES=[APPLE_AUDIENCE],
    GOOGLE_ALLOWED_AUDIENCES=[GOOGLE_AUDIENCE],
    SOCIAL_AUTH_GOOGLE_REQUIRE_NONCE=False,
    SOCIAL_AUTH_REAUTH_MAX_AGE_SECONDS=900,
    SOCIAL_AUTH_THROTTLE_RATE="1000/min",
)


class SocialAuthTestMixin:
    """Enables social auth with fake providers for the duration of each test."""

    social_settings = {}

    def setUp(self):
        super().setUp()
        cache.clear()
        settings_override = override_settings(**{**SOCIAL_SETTINGS, **self.social_settings})
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.jwks = FakeJWKSServer()
        patcher = mock.patch("hub.services.social_auth.providers.requests.get", side_effect=self.jwks.get)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.apple_client = FakeAppleClient()
        apple_module.set_apple_client_for_tests(self.apple_client)
        self.addCleanup(apple_module.set_apple_client_for_tests, None)
        self.addCleanup(cache.clear)

    def social_payload(self, provider, sub, *, nonce=None, with_nonce=True, **token_kwargs):
        raw = nonce if nonce is not None else (new_nonce() if with_nonce else None)
        payload = {
            "provider": provider,
            "id_token": make_id_token(provider, sub, nonce=raw, **token_kwargs),
        }
        if raw is not None:
            payload["nonce"] = raw
        return payload
