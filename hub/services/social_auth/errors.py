"""Error codes for the social sign-in API.

Every failure is ``{"code": ..., "detail": ...}`` with a fixed, generic detail so
responses never reveal more about existing accounts than the email login and
registration flows already do. The mobile client switches on ``code``; the
table is mirrored in ``docs/SOCIAL_AUTH.md`` and must not change without a
coordinated app release.
"""
from rest_framework import status


class SocialAuthError(Exception):
    """A failure the API reports to the client as a structured error."""

    code = "social_auth_error"
    detail = "Sign-in failed. Please try again."
    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, detail=None, *, status_code=None, reason=None):
        # ``reason`` is a short internal label for logs/metrics only; it is never
        # served and must never contain token material or personal data.
        self.detail = detail or self.detail
        if status_code is not None:
            self.status_code = status_code
        self.reason = reason or self.code
        super().__init__(self.reason)

    def as_response_data(self):
        return {"code": self.code, "detail": self.detail}


class InvalidRequest(SocialAuthError):
    code = "invalid_request"
    detail = "The sign-in request was incomplete or malformed."
    status_code = status.HTTP_400_BAD_REQUEST


class InvalidProviderToken(SocialAuthError):
    code = "invalid_provider_token"
    detail = "We couldn't verify that sign-in. Please try again."
    status_code = status.HTTP_401_UNAUTHORIZED


class InvalidNonce(SocialAuthError):
    code = "invalid_nonce"
    detail = "This sign-in attempt expired. Please try again."
    status_code = status.HTTP_401_UNAUTHORIZED


class AccountLinkRequired(SocialAuthError):
    code = "account_link_required"
    detail = "Sign in to your existing account to connect this sign-in method."
    status_code = status.HTTP_409_CONFLICT


class AccountDisabled(SocialAuthError):
    code = "account_disabled"
    detail = "This account is disabled."
    status_code = status.HTTP_403_FORBIDDEN


class SocialAuthUnavailable(SocialAuthError):
    code = "social_auth_unavailable"
    detail = "This sign-in method isn't available right now. Please use email instead."
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class ProviderUnavailable(SocialAuthError):
    """The provider's key endpoint could not be reached; safe to retry."""

    code = "provider_unavailable"
    detail = "We couldn't reach the sign-in provider. Please try again."
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class IdentityInUse(SocialAuthError):
    code = "identity_in_use"
    detail = "This sign-in method is already connected to a different account."
    status_code = status.HTTP_409_CONFLICT


class ProviderAlreadyLinked(SocialAuthError):
    code = "provider_already_linked"
    detail = "Your account already has a sign-in of this type connected. Remove it first."
    status_code = status.HTTP_409_CONFLICT


class LastSignInMethod(SocialAuthError):
    code = "last_sign_in_method"
    detail = "You can't remove your only way to sign in."
    status_code = status.HTTP_409_CONFLICT


class ReauthenticationRequired(SocialAuthError):
    code = "reauthentication_required"
    detail = "For your security, sign in again before changing your sign-in methods."
    status_code = status.HTTP_403_FORBIDDEN
