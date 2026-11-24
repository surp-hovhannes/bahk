"""Feature flags and per-provider configuration for social sign-in.

Read through ``django.conf.settings`` at call time so ``override_settings`` and
environment changes (after a restart) take effect without import-time caching.
"""
from ipaddress import ip_address
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

APPLE = "apple"
GOOGLE = "google"
PROVIDERS = (APPLE, GOOGLE)
_validate_https_url = URLValidator(schemes=["https"])


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


def apple_web_audience() -> str:
    return getattr(settings, "APPLE_WEB_AUDIENCE", "").strip()


def apple_web_audience_conflicts() -> bool:
    web_audience = apple_web_audience()
    return bool(web_audience) and (
        web_audience in _clean_list(getattr(settings, "APPLE_NATIVE_AUDIENCES", []))
        or allowed_audiences(APPLE).count(web_audience) > 1
    )


def is_apple_web_audience(audience: str) -> bool:
    return bool(audience) and audience == apple_web_audience() and not apple_web_audience_conflicts()


def apple_web_redirect_uri() -> str:
    """Return the exact configured callback if it is usable by the browser and Apple."""
    uri = getattr(settings, "APPLE_WEB_REDIRECT_URI", "")
    if not uri or any(char.isspace() or char in "\\#" for char in uri):
        return ""
    try:
        _validate_https_url(uri)
        parts = urlsplit(uri)
        if parts.hostname is None or parts.username is not None or parts.port == 0:
            return ""
    except (ValidationError, ValueError):
        return ""

    hostname = parts.hostname.rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return ""
    try:
        ip_address(hostname)
    except ValueError:
        return uri
    return ""


def accepted_audiences(provider: str) -> list[str]:
    audiences = allowed_audiences(provider)
    if provider == APPLE and not apple_web_redirect_uri():
        return [audience for audience in audiences if not is_apple_web_audience(audience)]
    return audiences


def provider_switch_enabled(provider: str) -> bool:
    if provider not in PROVIDERS or not social_auth_enabled():
        return False
    switch = {
        APPLE: "SOCIAL_AUTH_APPLE_ENABLED",
        GOOGLE: "SOCIAL_AUTH_GOOGLE_ENABLED",
    }[provider]
    return bool(getattr(settings, switch, True))


def provider_enabled(provider: str) -> bool:
    """A provider is live only with both switches and an accepted audience."""
    return provider_switch_enabled(provider) and bool(accepted_audiences(provider))


def google_requires_nonce() -> bool:
    return bool(getattr(settings, "SOCIAL_AUTH_GOOGLE_REQUIRE_NONCE", False))


def jwt_leeway_seconds() -> int:
    return int(getattr(settings, "SOCIAL_AUTH_JWT_LEEWAY_SECONDS", 60))


def http_timeout_seconds() -> float:
    return float(getattr(settings, "SOCIAL_AUTH_HTTP_TIMEOUT_SECONDS", 5))


def reauth_max_age_seconds() -> int:
    return int(getattr(settings, "SOCIAL_AUTH_REAUTH_MAX_AGE_SECONDS", 900))


def public_config() -> dict:
    """Report provider availability and public OAuth configuration."""
    providers = {provider: provider_enabled(provider) for provider in PROVIDERS}
    audiences = {provider: accepted_audiences(provider) for provider in PROVIDERS}
    web_audience = apple_web_audience()
    redirect_uri = apple_web_redirect_uri()
    apple_web = None
    if providers[APPLE] and is_apple_web_audience(web_audience) and web_audience in audiences[APPLE] and redirect_uri:
        apple_web = {"audience": web_audience, "redirect_uri": redirect_uri}
    return {
        "enabled": social_auth_enabled(),
        "providers": providers,
        "audiences": audiences,
        "apple_web": apple_web,
    }
