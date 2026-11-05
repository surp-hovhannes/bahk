"""Encryption for the one provider credential we keep (Apple's refresh token).

Fernet (AES-128-CBC + HMAC-SHA256) via ``MultiFernet`` so keys can rotate: the
first configured key encrypts, every key decrypts. Without explicit keys a key
is derived from ``SECRET_KEY`` with HKDF; set ``SOCIAL_AUTH_TOKEN_ENCRYPTION_KEYS``
before rotating ``SECRET_KEY``, or tokens stored under the old key can no longer
be revoked at account deletion.
"""
import base64
import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

_HKDF_INFO = b"bahk-social-auth-token-v1"

__all__ = ["InvalidToken", "decrypt_payload", "encrypt_payload", "generate_key"]


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


def _derived_key() -> bytes:
    secret = settings.SECRET_KEY
    if not secret:
        raise RuntimeError("SECRET_KEY is required to derive the social auth encryption key")
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(
        secret.encode("utf-8")
    )
    return base64.urlsafe_b64encode(raw)


def _fernet() -> MultiFernet:
    keys = [
        key.strip()
        for key in getattr(settings, "SOCIAL_AUTH_TOKEN_ENCRYPTION_KEYS", []) or []
        if key and key.strip()
    ]
    fernets = [Fernet(key.encode("ascii")) for key in keys] or [Fernet(_derived_key())]
    return MultiFernet(fernets)


def encrypt_payload(payload: dict) -> str:
    return _fernet().encrypt(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")


def decrypt_payload(ciphertext: str) -> dict:
    """Raise ``InvalidToken`` when the ciphertext is corrupt or the key is gone."""
    data = json.loads(_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8"))
    if not isinstance(data, dict):
        raise InvalidToken
    return data
