"""Feature flags and per-provider configuration for social sign-in.

Read through ``django.conf.settings`` at call time so ``override_settings`` and
environment changes (after a restart) take effect without import-time caching.
"""
from django.conf import settings

APPLE = "apple"
GOOGLE = "google"
PROVIDERS = (APPLE, GOOGLE)


def _clean_list(values) -> list[str]:
    return [value.strip() for value in (values or []) if value and value.strip()]


def allowed_audiences(provider: str) -> list[str]:
    if provider == APPLE:
        return _clean_list(getattr(settings, "APPLE_ALLOWED_AUDIENCES", []))
    if provider == GOOGLE:
        return _clean_list(getattr(settings, "GOOGLE_ALLOWED_AUDIENCES", []))
    return []


def social_auth_enabled() -> bool:
    return bool(getattr(settings, "SOCIAL_AUTH_ENABLED", False))


def provider_enabled(provider: str) -> bool:
    """A provider is live only with the master switch, its own switch and an audience."""
    if provider not in PROVIDERS or not social_auth_enabled():
        return False
    switch = {
        APPLE: "SOCIAL_AUTH_APPLE_ENABLED",
        GOOGLE: "SOCIAL_AUTH_GOOGLE_ENABLED",
    }[provider]
    return bool(getattr(settings, switch, True)) and bool(allowed_audiences(provider))


def google_requires_nonce() -> bool:
    return bool(getattr(settings, "SOCIAL_AUTH_GOOGLE_REQUIRE_NONCE", False))


def jwt_leeway_seconds() -> int:
    return int(getattr(settings, "SOCIAL_AUTH_JWT_LEEWAY_SECONDS", 60))


def http_timeout_seconds() -> float:
    return float(getattr(settings, "SOCIAL_AUTH_HTTP_TIMEOUT_SECONDS", 5))


def reauth_max_age_seconds() -> int:
    return int(getattr(settings, "SOCIAL_AUTH_REAUTH_MAX_AGE_SECONDS", 900))


def public_config() -> dict:
    """What the app may know: which buttons to show. No IDs, no secrets."""
    return {
        "enabled": social_auth_enabled(),
        "providers": {provider: provider_enabled(provider) for provider in PROVIDERS},
    }
