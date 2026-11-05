"""Sign in with Apple / Google endpoints (issue #598; contract in docs/SOCIAL_AUTH.md).

- ``GET    /api/auth/social/config/``           which provider buttons the app may show
- ``POST   /api/auth/social/``                  exchange a provider ID token for app JWTs
- ``POST   /api/auth/social/link/``             link a provider to the signed-in user
- ``GET    /api/auth/social/identities/``       list the caller's sign-in methods
- ``DELETE /api/auth/social/identities/<id>/``  unlink one of them

The app's own tokens are the unchanged SimpleJWT pair; refresh and logout keep
using ``/api/token/refresh/``. Provider tokens are never stored (except Apple's
encrypted refresh token, kept only for revocation), echoed, or logged.
"""
import logging
import time

from django.conf import settings
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from hub.services.accounts import AUTH_TIME_CLAIM, issue_tokens_for_user
from hub.services.social_auth import config
from hub.services.social_auth.accounts import (
    IdentityNotFound,
    link_identity,
    sign_in_methods,
    sign_in_with_identity,
    unlink_identity,
)
from hub.services.social_auth.apple import MAX_AUTHORIZATION_CODE_LENGTH
from hub.services.social_auth.errors import (
    InvalidRequest,
    ReauthenticationRequired,
    SocialAuthError,
    SocialAuthUnavailable,
)
from hub.services.social_auth.providers import (
    MAX_ID_TOKEN_LENGTH,
    MAX_NONCE_LENGTH,
    verify_id_token,
)

logger = logging.getLogger(__name__)


class SocialCredentialSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=config.PROVIDERS)
    id_token = serializers.CharField(max_length=MAX_ID_TOKEN_LENGTH)
    # Raw per-attempt nonce (Apple was given its SHA-256; Google the raw value).
    nonce = serializers.CharField(required=False, allow_blank=True, max_length=MAX_NONCE_LENGTH)
    # Apple only: single-use code exchanged for the refresh token we revoke at deletion.
    authorization_code = serializers.CharField(
        required=False, allow_blank=True, max_length=MAX_AUTHORIZATION_CODE_LENGTH
    )


class SocialSignInSerializer(SocialCredentialSerializer):
    # Apple sends the user's name to the app only on the very first sign-in and
    # never puts it in the ID token, so the app forwards it here when present.
    name = serializers.CharField(required=False, allow_blank=True, max_length=256)


def serialize_identity(identity) -> dict:
    return {
        "id": identity.pk,
        "provider": identity.provider,
        "email": identity.email_at_link or None,
        "is_private_email": identity.email_is_private_relay,
        "created_at": identity.created_at,
        "last_login_at": identity.last_login_at,
    }


def serialize_sign_in_methods(user) -> dict:
    methods = sign_in_methods(user)
    return {
        "password": methods["password"],
        "identities": [serialize_identity(identity) for identity in methods["identities"]],
    }


def error_response(error: SocialAuthError, *, provider=None, endpoint: str) -> Response:
    # Reason labels are fixed internal strings; no token or personal data.
    logger.info(
        "social_auth.request_rejected",
        extra={
            "endpoint": endpoint,
            "provider": provider,
            "code": error.code,
            "reason": error.reason,
            "status_code": error.status_code,
        },
    )
    return Response(error.as_response_data(), status=error.status_code)


def validated_or_error(serializer_class, data):
    serializer = serializer_class(data=data)
    if not serializer.is_valid():
        error = InvalidRequest(reason="invalid_payload")
        payload = error.as_response_data()
        payload["errors"] = serializer.errors
        return None, Response(payload, status=error.status_code)
    return serializer.validated_data, None


class SocialAuthThrottle(ScopedRateThrottle):
    """Per-IP for anonymous sign-in, per-user for linking (scope ``social_auth``).

    The rate comes from ``SOCIAL_AUTH_THROTTLE_RATE`` at request time rather than
    DRF's import-time ``DEFAULT_THROTTLE_RATES`` snapshot.
    """

    def get_rate(self):
        return getattr(settings, "SOCIAL_AUTH_THROTTLE_RATE", "20/min")


def require_recent_authentication(request) -> None:
    """Link/unlink need an app sign-in no older than SOCIAL_AUTH_REAUTH_MAX_AGE_SECONDS."""
    max_age = config.reauth_max_age_seconds()
    if max_age <= 0:
        return
    payload = getattr(request.auth, "payload", None)
    auth_time = payload.get(AUTH_TIME_CLAIM) if isinstance(payload, dict) else None
    if not isinstance(auth_time, (int, float)) or time.time() - auth_time > max_age:
        raise ReauthenticationRequired(reason="stale_or_missing_auth_time")


def record_login_event(request, user, provider: str, *, is_new_user: bool) -> None:
    try:
        from events.models import Event, EventType

        Event.create_event(
            event_type_code=EventType.USER_LOGGED_IN,
            user=user,
            title=f"User logged in ({provider.title()})",
            data={"method": provider, "is_new_user": is_new_user},
            request=request,
        )
    except Exception:
        logger.exception("Failed to record social login audit event", extra={"user_id": user.pk})


class SocialAuthConfigView(APIView):
    """Public: lets the app hide social buttons remotely (the kill switch)."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response(config.public_config())


class SocialSignInView(APIView):
    """Exchange a verified Apple/Google ID token for the app's SimpleJWT pair."""

    # Unauthenticated by design; a stale bearer token must not turn into a 401.
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [SocialAuthThrottle]
    throttle_scope = "social_auth"

    def post(self, request):
        if not config.social_auth_enabled():
            return error_response(SocialAuthUnavailable(reason="feature_disabled"), endpoint="sign_in")
        data, invalid = validated_or_error(SocialSignInSerializer, request.data)
        if invalid is not None:
            return invalid
        provider = data["provider"]
        try:
            identity = verify_id_token(provider, data["id_token"], raw_nonce=data.get("nonce"))
            result = sign_in_with_identity(
                identity,
                display_name=data.get("name", ""),
                authorization_code=data.get("authorization_code", ""),
            )
        except SocialAuthError as error:
            return error_response(error, provider=provider, endpoint="sign_in")

        record_login_event(request, result.user, provider, is_new_user=result.is_new_user)
        return Response(
            {
                "tokens": issue_tokens_for_user(result.user),
                "user": {"id": result.user.pk},
                "is_new_user": result.is_new_user,
                "profile_incomplete": result.profile_incomplete,
                "provider": provider,
            },
            status=status.HTTP_200_OK,
        )


class SocialLinkView(APIView):
    """Link a freshly verified provider identity to the signed-in user."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [SocialAuthThrottle]
    throttle_scope = "social_auth"

    def post(self, request):
        if not config.social_auth_enabled():
            return error_response(SocialAuthUnavailable(reason="feature_disabled"), endpoint="link")
        data, invalid = validated_or_error(SocialCredentialSerializer, request.data)
        if invalid is not None:
            return invalid
        provider = data["provider"]
        try:
            require_recent_authentication(request)
            identity = verify_id_token(provider, data["id_token"], raw_nonce=data.get("nonce"))
            social, created = link_identity(
                request.user,
                identity,
                authorization_code=data.get("authorization_code", ""),
            )
        except SocialAuthError as error:
            return error_response(error, provider=provider, endpoint="link")

        return Response(
            {
                "identity": serialize_identity(social),
                "created": created,
                **serialize_sign_in_methods(request.user),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SocialIdentityListView(APIView):
    """The caller's sign-in methods: password availability plus linked providers."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(serialize_sign_in_methods(request.user))


class SocialIdentityDetailView(APIView):
    """Unlink one of the caller's providers (never the last way to sign in)."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [SocialAuthThrottle]
    throttle_scope = "social_auth"

    def delete(self, request, pk):
        try:
            require_recent_authentication(request)
            unlink_identity(request.user, pk)
        except IdentityNotFound:
            # Same answer for "not yours" and "does not exist".
            return Response(
                {"code": "not_found", "detail": "That sign-in method was not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SocialAuthError as error:
            return error_response(error, endpoint="unlink")
        return Response(serialize_sign_in_methods(request.user), status=status.HTTP_200_OK)
