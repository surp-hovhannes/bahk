"""Account creation and token issuance shared by every sign-up path.

Email/password registration (``RegisterSerializer``) and Sign in with Apple/Google
(``hub.services.social_auth``) both create a Django ``User`` plus its ``Profile``
here, so the two paths cannot drift apart on defaults (issue #598).
"""
import time

from django.contrib.auth.models import User
from rest_framework_simplejwt.tokens import RefreshToken

from hub.models import Profile

# Custom SimpleJWT claim: when the user last proved who they are (password, or a
# freshly verified provider credential). Refresh tokens carry it, and SimpleJWT
# copies it into every access token minted from them, so a refreshed session
# keeps its original authentication time. Security-sensitive operations (linking
# or unlinking a sign-in method) require it to be recent.
AUTH_TIME_CLAIM = "auth_time"

# How a user signed up; recorded on the USER_ACCOUNT_CREATED event.
SIGNUP_METHOD_PASSWORD = "password"


def add_auth_time(token, *, now=None):
    """Stamp a SimpleJWT token with the current authentication time."""
    token[AUTH_TIME_CLAIM] = int(now if now is not None else time.time())
    return token


def issue_tokens_for_user(user) -> dict:
    """Mint the standard SimpleJWT pair for ``user`` (lifetimes unchanged)."""
    refresh = add_auth_time(RefreshToken.for_user(user))
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
    }


def create_user_with_profile(
    *,
    username: str,
    email: str,
    password: str | None = None,
    name: str | None = None,
    church=None,
    signup_method: str = SIGNUP_METHOD_PASSWORD,
):
    """Create a ``User`` and its ``Profile`` with the app-wide defaults.

    Callers own the transaction. ``password=None`` stores an unusable password,
    which is how social-only accounts are represented: they can still set one
    later through the password-reset flow if they have an email address.
    """
    user = User(username=username, email=email)
    if password is None:
        user.set_unusable_password()
    else:
        user.set_password(password)
    user.save()

    profile = Profile(user=user, name=name, church=church)
    # Read by events.signals.track_user_account_creation (not a model field).
    profile.signup_method = signup_method
    profile.save()
    return user, profile


def profile_is_incomplete(profile) -> bool:
    """True when a field email registration requires is still missing.

    Registration requires a name and a church; a social sign-up may arrive with
    neither. The app asks for them after sign-in, never as an auth failure.
    """
    return profile is None or not (profile.name or "").strip() or profile.church_id is None
