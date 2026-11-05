"""Account resolution for verified social identities.

Rules (issue #598 and the decisions recorded on it):

- An identity is ``(provider, sub)``. A known identity signs in its own user,
  whatever email the provider now reports.
- A new identity never attaches to an existing account by email. If its
  verified, non-relay email belongs to an existing user (case-insensitively), the
  response is ``account_link_required``: the person signs in to that account and
  links explicitly. Apple relay and unverified addresses are never matched.
- Otherwise a new ``User`` + ``Profile`` + ``SocialIdentity`` is created in one
  transaction, with an unusable password. Missing name/church is completed in
  the app after sign-in.
- Concurrent first sign-ins and links are settled by the unique constraints;
  after an ``IntegrityError`` the winner's row is re-read, so tokens are only
  ever minted for the user that actually owns the identity.
- Unlinking never removes the last usable way to sign in.

Security-sensitive changes are written to the ``hub.social_auth.audit`` logger
with user/identity IDs and the provider only: no emails, names or tokens.
"""
import hashlib
import logging
import secrets
from dataclasses import dataclass

from better_profanity import profanity
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone

from hub.models import Profile, SocialIdentity
from hub.services.accounts import create_user_with_profile, profile_is_incomplete

from .apple import AppleTokenError, apple_key_configured, get_apple_client
from .config import APPLE
from .crypto import encrypt_payload
from .errors import (
    AccountDisabled,
    AccountLinkRequired,
    IdentityInUse,
    LastSignInMethod,
    ProviderAlreadyLinked,
)
from .providers import VerifiedIdentity

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("hub.social_auth.audit")

PROFILE_NAME_MAX_LENGTH = Profile._meta.get_field("name").max_length


class IdentityNotFound(Exception):
    """No identity with that ID belongs to the caller."""


@dataclass
class SignInResult:
    user: User
    profile: Profile | None
    identity: SocialIdentity
    is_new_user: bool

    @property
    def profile_incomplete(self) -> bool:
        return profile_is_incomplete(self.profile)


def audit(action: str, *, user_id, provider: str, identity_id=None, **extra) -> None:
    audit_logger.info(
        "social_auth.%s",
        action,
        extra={
            "social_auth_action": action,
            "user_id": user_id,
            "provider": provider,
            "identity_id": identity_id,
            **extra,
        },
    )


def clean_display_name(value) -> str:
    """A name we can store on the profile, or '' (the app asks for one later)."""
    if not isinstance(value, str):
        return ""
    name = " ".join(value.split())[:PROFILE_NAME_MAX_LENGTH].strip()
    if not name or profanity.contains_profanity(name):
        return ""
    return name


def _username_for(identity: VerifiedIdentity, *, randomize: bool = False) -> str:
    # Opaque and deterministic per identity: no PII in usernames or logs, and a
    # concurrent duplicate sign-up collides on the unique username as well.
    if randomize:
        return f"{identity.provider}-{secrets.token_hex(12)}"
    digest = hashlib.sha256(f"{identity.provider}:{identity.subject}".encode()).hexdigest()
    return f"{identity.provider}-{digest[:24]}"


def _find_identity(identity: VerifiedIdentity):
    return (
        SocialIdentity.objects.select_related("user")
        .filter(provider=identity.provider, provider_subject=identity.subject)
        .first()
    )


def _profile_for(user):
    return Profile.objects.filter(user=user).select_related("church").first()


def _email_taken(email: str) -> bool:
    return bool(email) and User.objects.filter(email__iexact=email).exists()


def store_apple_refresh_token(social: SocialIdentity, identity: VerifiedIdentity, authorization_code) -> bool:
    """Best effort: exchange the Apple code and keep the refresh token for revocation.

    Never fails sign-in. Returns True when a token was stored.
    """
    if identity.provider != APPLE or not authorization_code:
        return False
    if not apple_key_configured():
        logger.info("social_auth.apple_code_exchange_skipped", extra={"reason": "apple_key_not_configured"})
        return False
    try:
        tokens = get_apple_client().exchange_authorization_code(
            authorization_code,
            client_id=identity.audience,
            expected_subject=identity.subject,
        )
    except AppleTokenError as exc:
        logger.warning(
            "social_auth.apple_code_exchange_failed",
            extra={"reason": exc.reason, "identity_id": social.pk},
        )
        return False
    social.apple_refresh_token_encrypted = encrypt_payload(
        {"client_id": identity.audience, "refresh_token": tokens.refresh_token}
    )
    social.save(update_fields=["apple_refresh_token_encrypted", "updated_at"])
    return True


def _returning(social: SocialIdentity, identity: VerifiedIdentity, authorization_code) -> SignInResult:
    user = social.user
    if not user.is_active:
        audit("sign_in_rejected_inactive", user_id=user.pk, provider=identity.provider, identity_id=social.pk)
        raise AccountDisabled(reason="inactive_user")
    social.last_login_at = timezone.now()
    social.save(update_fields=["last_login_at", "updated_at"])
    store_apple_refresh_token(social, identity, authorization_code)
    return SignInResult(user=user, profile=_profile_for(user), identity=social, is_new_user=False)


def _create_account(identity: VerifiedIdentity, *, email: str, name: str, username: str):
    with transaction.atomic():
        user, profile = create_user_with_profile(
            username=username,
            email=email,
            password=None,
            name=name or None,
            church=None,
            signup_method=identity.provider,
        )
        social = SocialIdentity.objects.create(
            user=user,
            provider=identity.provider,
            provider_subject=identity.subject,
            email_at_link=identity.email if identity.email_verified else "",
            email_is_private_relay=identity.is_private_relay,
            last_login_at=timezone.now(),
        )
    return user, profile, social


def sign_in_with_identity(
    identity: VerifiedIdentity,
    *,
    display_name: str = "",
    authorization_code: str = "",
) -> SignInResult:
    """Resolve a verified identity to a user, creating the account if it is new."""
    social = _find_identity(identity)
    if social is not None:
        return _returning(social, identity, authorization_code)

    email = identity.trusted_email
    if email and not identity.is_private_relay and _email_taken(email):
        # The address may belong to an account a concurrent first sign-in of
        # this same identity just created; only a different account conflicts.
        winner = _find_identity(identity)
        if winner is not None:
            return _returning(winner, identity, authorization_code)
        audit("link_required", user_id=None, provider=identity.provider)
        raise AccountLinkRequired(reason="email_matches_existing_account")
    # A relay address that happens to collide is stored nowhere rather than
    # creating a second account that shares a login email.
    stored_email = "" if _email_taken(email) else email
    name = clean_display_name(display_name) or clean_display_name(identity.name)

    for attempt in range(2):
        try:
            user, profile, social = _create_account(
                identity,
                email=stored_email,
                name=name,
                username=_username_for(identity, randomize=attempt > 0),
            )
            break
        except IntegrityError:
            winner = _find_identity(identity)
            if winner is not None:
                # A concurrent first sign-in for the same identity committed first.
                return _returning(winner, identity, authorization_code)
            if attempt:
                raise
            # The deterministic username belongs to an older account whose
            # identity was since unlinked; retry once with a random username.
    audit("account_created", user_id=user.pk, provider=identity.provider, identity_id=social.pk)
    store_apple_refresh_token(social, identity, authorization_code)
    return SignInResult(user=user, profile=profile, identity=social, is_new_user=True)


def link_identity(user, identity: VerifiedIdentity, *, authorization_code: str = ""):
    """Attach a verified identity to ``user``. Returns ``(identity, created)``."""
    social = _find_identity(identity)
    if social is not None:
        if social.user_id != user.pk:
            audit("link_rejected_identity_in_use", user_id=user.pk, provider=identity.provider)
            raise IdentityInUse(reason="identity_owned_by_other_user")
        store_apple_refresh_token(social, identity, authorization_code)
        return social, False

    current = SocialIdentity.objects.filter(user=user, provider=identity.provider).first()
    if current is not None:
        if current.provider_subject == identity.subject:
            # A concurrent link of this same identity committed in between.
            return current, False
        raise ProviderAlreadyLinked(reason="provider_already_linked")
    try:
        with transaction.atomic():
            social = SocialIdentity.objects.create(
                user=user,
                provider=identity.provider,
                provider_subject=identity.subject,
                email_at_link=identity.email if identity.email_verified else "",
                email_is_private_relay=identity.is_private_relay,
            )
    except IntegrityError:
        winner = _find_identity(identity)
        if winner is not None and winner.user_id == user.pk:
            return winner, False
        if winner is not None:
            raise IdentityInUse(reason="identity_owned_by_other_user") from None
        raise ProviderAlreadyLinked(reason="provider_already_linked") from None
    audit("identity_linked", user_id=user.pk, provider=identity.provider, identity_id=social.pk)
    store_apple_refresh_token(social, identity, authorization_code)
    return social, True


def has_usable_password_login(user) -> bool:
    """Password login needs both a usable password and an email to log in with."""
    return user.has_usable_password() and bool((user.email or "").strip())


def sign_in_methods(user) -> dict:
    identities = list(SocialIdentity.objects.filter(user=user).order_by("created_at", "pk"))
    return {"password": has_usable_password_login(user), "identities": identities}


def unlink_identity(user, identity_id) -> None:
    """Remove one of ``user``'s identities, keeping at least one way to sign in."""
    with transaction.atomic():
        # Serialise concurrent unlinks for this user (no-op on SQLite).
        User.objects.select_for_update().filter(pk=user.pk).first()
        social = SocialIdentity.objects.filter(pk=identity_id, user=user).first()
        if social is None:
            raise IdentityNotFound
        others = SocialIdentity.objects.filter(user=user).exclude(pk=social.pk).exists()
        if not others and not has_usable_password_login(user):
            audit("unlink_rejected_last_method", user_id=user.pk, provider=social.provider, identity_id=social.pk)
            raise LastSignInMethod(reason="last_sign_in_method")
        provider, social_id = social.provider, social.pk
        ciphertext = social.apple_refresh_token_encrypted
        social.delete()
        if ciphertext:
            transaction.on_commit(lambda: enqueue_apple_revocation([ciphertext]))
    audit("identity_unlinked", user_id=user.pk, provider=provider, identity_id=social_id)


def enqueue_apple_revocation(ciphertexts) -> None:
    """Queue revocation of stored Apple refresh tokens (still encrypted)."""
    ciphertexts = [c for c in ciphertexts if c]
    if not ciphertexts:
        return
    from hub.tasks.social_auth_tasks import revoke_apple_tokens_task

    try:
        revoke_apple_tokens_task.delay(ciphertexts)
    except Exception:
        # Broker down: revoke inline rather than lose the obligation.
        logger.exception("social_auth.apple_revocation_enqueue_failed")
        revoke_apple_tokens_task.apply(args=[ciphertexts])


def apple_ciphertexts_for(user) -> list[str]:
    return [
        value
        for value in SocialIdentity.objects.filter(user=user, provider=APPLE).values_list(
            "apple_refresh_token_encrypted", flat=True
        )
        if value
    ]
