"""Sign in with Apple / Google (issue #598): verification, accounts and endpoints.

Every token here is a real RS256 JWT signed by a local key and served from a
patched JWKS endpoint, so the production verification path is exercised end to
end. See ``hub/tests/social_auth_helpers.py``.
"""
import logging
import time
from unittest import mock

import jwt
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from events.models import Event, EventType
from hub.models import Profile, SocialIdentity
from hub.services.accounts import AUTH_TIME_CLAIM, issue_tokens_for_user
from hub.services.social_auth import accounts as social_accounts
from hub.services.social_auth import providers
from hub.services.social_auth.apple import (
    APPLE_AUDIENCE as APPLE_SERVER_AUDIENCE,
    AppleTokenClient,
    AppleTokenError,
)
from hub.services.social_auth.crypto import decrypt_payload, encrypt_payload, generate_key
from hub.services.social_auth.errors import (
    InvalidNonce,
    InvalidProviderToken,
    ProviderUnavailable,
    SocialAuthUnavailable,
)
from hub.tasks.social_auth_tasks import revoke_apple_tokens_task
from hub.tests.social_auth_helpers import (
    APPLE_AUDIENCE,
    GOOGLE_AUDIENCE,
    GOOGLE_IOS_CLIENT,
    SocialAuthTestMixin,
    ec_private_key_pem,
    make_id_token,
    new_nonce,
    public_jwk,
    rsa_key,
)
from tests.fixtures.test_data import TestDataFactory

User = get_user_model()

SOCIAL_URL = "/api/auth/social/"
LINK_URL = "/api/auth/social/link/"
IDENTITIES_URL = "/api/auth/social/identities/"
CONFIG_URL = "/api/auth/social/config/"


def identity_url(pk):
    return f"/api/auth/social/identities/{pk}/"


class ProviderVerificationTests(SocialAuthTestMixin, TestCase):
    """``verify_id_token``: every rejection path the issue's test matrix lists."""

    def verify(self, provider, token, nonce=None):
        return providers.verify_id_token(provider, token, raw_nonce=nonce)

    def test_valid_apple_token_verifies(self):
        nonce = new_nonce()
        token = make_id_token(
            "apple", "apple-sub-1", nonce=nonce, email="person@example.com", is_private_email=False
        )
        identity = self.verify("apple", token, nonce)
        self.assertEqual(identity.subject, "apple-sub-1")
        self.assertEqual(identity.audience, APPLE_AUDIENCE)
        self.assertEqual(identity.email, "person@example.com")
        self.assertTrue(identity.email_verified)
        self.assertFalse(identity.is_private_relay)

    def test_valid_google_token_verifies_with_raw_nonce(self):
        nonce = new_nonce()
        token = make_id_token("google", "google-sub-1", nonce=nonce, email="g@example.com", name="G User")
        identity = self.verify("google", token, nonce)
        self.assertEqual(identity.subject, "google-sub-1")
        self.assertEqual(identity.audience, GOOGLE_AUDIENCE)
        self.assertEqual(identity.name, "G User")

    def test_google_token_without_nonce_claim_accepted_unless_required(self):
        token = make_id_token("google", "google-sub-2")
        self.assertEqual(self.verify("google", token).subject, "google-sub-2")
        with override_settings(SOCIAL_AUTH_GOOGLE_REQUIRE_NONCE=True):
            with self.assertRaises(InvalidNonce):
                self.verify("google", make_id_token("google", "google-sub-3"))

    def test_google_nonce_claim_must_match_when_present(self):
        token = make_id_token("google", "google-sub-4", nonce=new_nonce())
        with self.assertRaises(InvalidNonce):
            self.verify("google", token, new_nonce())
        with self.assertRaises(InvalidNonce):
            self.verify("google", make_id_token("google", "google-sub-5", nonce=new_nonce()))

    def test_apple_nonce_is_required_and_hashed(self):
        nonce = new_nonce()
        with self.assertRaises(InvalidNonce):
            self.verify("apple", make_id_token("apple", "a", nonce=nonce))  # raw nonce not sent
        with self.assertRaises(InvalidNonce):
            self.verify("apple", make_id_token("apple", "a"), nonce)  # token has no nonce
        with self.assertRaises(InvalidNonce):
            self.verify("apple", make_id_token("apple", "a", nonce=nonce), new_nonce())
        # The token must carry SHA-256(raw), not the raw value itself.
        raw_in_claim = make_id_token("apple", "a", extra={"nonce": nonce})
        with self.assertRaises(InvalidNonce):
            self.verify("apple", raw_in_claim, nonce)

    def test_malformed_nonce_rejected(self):
        with self.assertRaises(InvalidNonce):
            self.verify("google", make_id_token("google", "g"), "short")

    def test_forged_signature_rejected(self):
        forged = make_id_token("apple", "victim", nonce=(n := new_nonce()), key=rsa_key("attacker"))
        with self.assertRaises(InvalidProviderToken) as ctx:
            self.verify("apple", forged, n)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_alg_none_and_hs256_key_confusion_rejected(self):
        unsigned = jwt.encode(
            {"iss": "https://appleid.apple.com", "aud": APPLE_AUDIENCE, "sub": "x",
             "iat": int(time.time()), "exp": int(time.time()) + 600},
            None, algorithm="none", headers={"kid": "kid-1"},
        )
        with self.assertRaises(InvalidProviderToken):
            self.verify("apple", unsigned)
        hs = jwt.encode({"sub": "x"}, "not-a-real-secret-but-long-enough-for-hs256", algorithm="HS256", headers={"kid": "kid-1"})
        with self.assertRaises(InvalidProviderToken):
            self.verify("apple", hs)

    def test_wrong_issuer_audience_and_expiry_rejected(self):
        cases = {
            "wrong_issuer": make_id_token("google", "g", iss="https://evil.example.com"),
            "apple_issuer_for_google": make_id_token("google", "g", iss="https://appleid.apple.com"),
            "ios_client_is_not_an_audience": make_id_token("google", "g", aud=GOOGLE_IOS_CLIENT),
            "other_app": make_id_token("google", "g", aud="someone-else.apps.googleusercontent.com"),
            "expired": make_id_token("google", "g", iat=int(time.time()) - 7200, exp=int(time.time()) - 3600),
            "issued_in_future": make_id_token("google", "g", iat=int(time.time()) + 3600, exp=int(time.time()) + 7200),
            "missing_exp": make_id_token("google", "g", drop=("exp",)),
        }
        for label, token in cases.items():
            with self.subTest(label), self.assertRaises(InvalidProviderToken):
                self.verify("google", token)

    def test_clock_skew_within_leeway_accepted(self):
        token = make_id_token("google", "g-skew", iat=int(time.time()) + 30, exp=int(time.time()) - 30)
        self.assertEqual(self.verify("google", token).subject, "g-skew")

    def test_missing_or_unusable_subject_is_a_400(self):
        for token in (
            make_id_token("google", "", drop=()),
            make_id_token("google", "x", drop=("sub",)),
            make_id_token("google", "x" * 300),
        ):
            with self.subTest(token=token[:20]), self.assertRaises(InvalidProviderToken) as ctx:
                self.verify("google", token)
            self.assertEqual(ctx.exception.status_code, 400)

    def test_replayed_token_rejected(self):
        token = make_id_token("google", "g-replay")
        self.verify("google", token)
        with self.assertRaises(InvalidProviderToken) as ctx:
            self.verify("google", token)
        self.assertEqual(ctx.exception.reason, "token_replayed")

    def test_embedded_jwk_or_jku_header_is_ignored(self):
        attacker = rsa_key("attacker")
        token = make_id_token(
            "google", "victim", key=attacker,
            headers={"jwk": public_jwk(attacker, "kid-1"), "jku": "https://evil.example.com/keys"},
        )
        with self.assertRaises(InvalidProviderToken):
            self.verify("google", token)
        self.assertEqual(self.jwks.fetches, ["google"])  # only the provider's own JWKS URL

    def test_key_rotation_refetches_once_and_is_rate_limited(self):
        self.verify("google", make_id_token("google", "warm"))  # populate cache
        self.jwks.keys["google"].append(public_jwk(rsa_key("rotated"), "kid-2"))
        identity = self.verify("google", make_id_token("google", "after-rotation", kid="kid-2", key=rsa_key("rotated")))
        self.assertEqual(identity.subject, "after-rotation")
        self.assertEqual(self.jwks.fetches, ["google", "google"])
        # A burst of unknown kids does not hammer the provider.
        for i in range(3):
            with self.assertRaises(InvalidProviderToken):
                self.verify("google", make_id_token("google", f"x{i}", kid=f"forged-{i}"))
        self.assertEqual(self.jwks.fetches, ["google", "google"])

    def test_jwks_is_cached_between_verifications(self):
        for i in range(3):
            self.verify("apple", make_id_token("apple", f"a{i}", nonce=(n := new_nonce())), n)
        self.assertEqual(self.jwks.fetches, ["apple"])

    def test_non_rsa_key_for_kid_rejected(self):
        self.jwks.keys["google"] = [{"kty": "EC", "kid": "kid-1", "crv": "P-256", "x": "AA", "y": "AA"}]
        with self.assertRaises(InvalidProviderToken):
            self.verify("google", make_id_token("google", "g"))

    def test_jwks_outage_is_retryable_503(self):
        self.jwks.fail = True
        with self.assertRaises(ProviderUnavailable) as ctx:
            self.verify("google", make_id_token("google", "g"))
        self.assertEqual(ctx.exception.status_code, 503)

    def test_disabled_provider_or_missing_audience_unavailable(self):
        with override_settings(SOCIAL_AUTH_GOOGLE_ENABLED=False), self.assertRaises(SocialAuthUnavailable):
            self.verify("google", make_id_token("google", "g"))
        with override_settings(APPLE_ALLOWED_AUDIENCES=[]), self.assertRaises(SocialAuthUnavailable):
            self.verify("apple", make_id_token("apple", "a"))

    def test_apple_relay_and_string_booleans(self):
        nonce = new_nonce()
        token = make_id_token(
            "apple", "relay", nonce=nonce, email="abc123@privaterelay.appleid.com",
            email_verified=True, is_private_email=True,
        )
        identity = self.verify("apple", token, nonce)
        self.assertTrue(identity.is_private_relay)
        self.assertTrue(identity.email_verified)
        unverified = make_id_token("apple", "unv", nonce=(n := new_nonce()), email="u@example.com", email_verified=False)
        self.assertEqual(self.verify("apple", unverified, n).trusted_email, "")


class SocialSignInEndpointTests(SocialAuthTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.church = TestDataFactory.create_church(name="Social Church")

    def sign_in(self, provider, sub, **kwargs):
        name = kwargs.pop("display_name", None)
        code = kwargs.pop("authorization_code", None)
        payload = self.social_payload(provider, sub, **kwargs)
        if name is not None:
            payload["name"] = name
        if code is not None:
            payload["authorization_code"] = code
        return self.client.post(SOCIAL_URL, payload, format="json")

    def test_feature_flag_off_returns_503_and_hides_buttons(self):
        with override_settings(SOCIAL_AUTH_ENABLED=False):
            response = self.sign_in("google", "g")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["code"], "social_auth_unavailable")
            config = self.client.get(CONFIG_URL).json()
            self.assertEqual(config, {"enabled": False, "providers": {"apple": False, "google": False}})
        self.assertEqual(User.objects.count(), 0)

    def test_config_endpoint_reports_live_providers_only(self):
        with override_settings(SOCIAL_AUTH_GOOGLE_ENABLED=False):
            self.assertEqual(
                self.client.get(CONFIG_URL).json(),
                {"enabled": True, "providers": {"apple": True, "google": False}},
            )

    def test_new_apple_identity_creates_one_complete_account(self):
        response = self.sign_in("apple", "apple-new", email="new@example.com", display_name="Anna Apple")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["is_new_user"])
        self.assertTrue(body["profile_incomplete"])  # no church yet
        self.assertEqual(set(body["tokens"]), {"access", "refresh"})
        user = User.objects.get()
        self.assertEqual(body["user"], {"id": user.pk})
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.email, "new@example.com")
        self.assertNotIn("@", user.username)
        profile = Profile.objects.get(user=user)
        self.assertEqual(profile.name, "Anna Apple")
        identity = SocialIdentity.objects.get(user=user)
        self.assertEqual((identity.provider, identity.provider_subject), ("apple", "apple-new"))

        # The app tokens are ordinary SimpleJWT tokens for this user.
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['tokens']['access']}")
        self.assertEqual(self.client.get("/api/profile/").json()["user_id"], user.pk)
        self.client.credentials()
        refreshed = self.client.post("/api/token/refresh/", {"refresh": body["tokens"]["refresh"]}, format="json")
        self.assertEqual(refreshed.status_code, 200)

    def test_repeat_sign_in_returns_same_user_and_profile(self):
        first = self.sign_in("google", "google-repeat", email="r@example.com").json()
        profile_id = Profile.objects.get(user_id=first["user"]["id"]).pk
        second = self.sign_in("google", "google-repeat", email="changed@example.com")
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["is_new_user"])
        self.assertEqual(second.json()["user"]["id"], first["user"]["id"])
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Profile.objects.get(user_id=first["user"]["id"]).pk, profile_id)
        self.assertEqual(SocialIdentity.objects.count(), 1)

    def test_apple_repeat_sign_in_without_email_or_name_uses_subject(self):
        self.sign_in("apple", "apple-once", email="once@example.com", display_name="First Time")
        again = self.sign_in("apple", "apple-once")
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()["is_new_user"])
        self.assertEqual(Profile.objects.get().name, "First Time")

    def test_new_apple_subject_without_email_creates_incomplete_account(self):
        response = self.sign_in("apple", "apple-no-email")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_new_user"])
        self.assertTrue(response.json()["profile_incomplete"])
        self.assertEqual(User.objects.get().email, "")

    def test_profile_complete_once_name_and_church_set(self):
        body = self.sign_in("google", "g-complete", email="c@example.com", name="Complete Person").json()
        Profile.objects.filter(user_id=body["user"]["id"]).update(church=self.church)
        again = self.sign_in("google", "g-complete").json()
        self.assertFalse(again["profile_incomplete"])

    def test_profane_name_is_dropped_not_fatal(self):
        response = self.sign_in("apple", "apple-rude", display_name="shit head")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(Profile.objects.get().name)

    def test_matching_verified_email_requires_explicit_link(self):
        existing = TestDataFactory.create_user(username="Owner@Example.com", email="Owner@Example.com")
        TestDataFactory.create_profile(user=existing, church=self.church)
        for provider in ("google", "apple"):
            with self.subTest(provider):
                response = self.sign_in(provider, f"{provider}-takeover", email="owner@example.COM")
                self.assertEqual(response.status_code, 409)
                self.assertEqual(
                    response.json(),
                    {
                        "code": "account_link_required",
                        "detail": "Sign in to your existing account to connect this sign-in method.",
                    },
                )
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(SocialIdentity.objects.exists())

    def test_unverified_matching_email_never_reaches_existing_account(self):
        existing = TestDataFactory.create_user(username="victim@example.com", email="victim@example.com")
        response = self.sign_in("google", "g-unverified", email="victim@example.com", email_verified=False)
        self.assertEqual(response.status_code, 200)
        new_user_id = response.json()["user"]["id"]
        self.assertNotEqual(new_user_id, existing.pk)
        self.assertEqual(User.objects.get(pk=new_user_id).email, "")
        self.assertFalse(SocialIdentity.objects.filter(user=existing).exists())

    def test_apple_relay_email_is_never_matched(self):
        relay = "xyz@privaterelay.appleid.com"
        existing = TestDataFactory.create_user(username=relay, email=relay)
        response = self.sign_in("apple", "apple-relay", email=relay, is_private_email=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_new_user"])
        new_user = User.objects.get(pk=response.json()["user"]["id"])
        self.assertNotEqual(new_user.pk, existing.pk)
        self.assertEqual(new_user.email, "")  # never a second account sharing a login email

    def test_inactive_linked_user_rejected(self):
        body = self.sign_in("google", "g-inactive").json()
        User.objects.filter(pk=body["user"]["id"]).update(is_active=False)
        response = self.sign_in("google", "g-inactive")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "account_disabled")
        self.assertNotIn("tokens", response.json())

    def test_invalid_token_and_nonce_errors_are_structured(self):
        forged = {"provider": "google", "id_token": make_id_token("google", "g", key=rsa_key("attacker"))}
        response = self.client.post(SOCIAL_URL, forged, format="json")
        self.assertEqual((response.status_code, response.json()["code"]), (401, "invalid_provider_token"))
        bad_nonce = self.social_payload("apple", "a")
        bad_nonce["nonce"] = new_nonce()
        response = self.client.post(SOCIAL_URL, bad_nonce, format="json")
        self.assertEqual((response.status_code, response.json()["code"]), (401, "invalid_nonce"))
        no_sub = {"provider": "google", "id_token": make_id_token("google", "x", drop=("sub",))}
        response = self.client.post(SOCIAL_URL, no_sub, format="json")
        self.assertEqual((response.status_code, response.json()["code"]), (400, "invalid_provider_token"))
        self.assertEqual(User.objects.count(), 0)

    def test_malformed_requests_rejected(self):
        for payload in ({}, {"provider": "facebook", "id_token": "x.y.z"}, {"provider": "google"}):
            with self.subTest(payload=payload):
                response = self.client.post(SOCIAL_URL, payload, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "invalid_request")

    def test_access_token_is_not_accepted_as_id_token(self):
        user = TestDataFactory.create_user(username="u@example.com", email="u@example.com")
        app_access = issue_tokens_for_user(user)["access"]
        response = self.client.post(SOCIAL_URL, {"provider": "google", "id_token": app_access}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_stale_bearer_header_does_not_block_sign_in(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer expired.or.garbage")
        self.assertEqual(self.sign_in("google", "g-bearer").status_code, 200)

    def test_replayed_request_rejected(self):
        payload = self.social_payload("google", "g-replay-endpoint")
        self.assertEqual(self.client.post(SOCIAL_URL, payload, format="json").status_code, 200)
        replay = self.client.post(SOCIAL_URL, payload, format="json")
        self.assertEqual((replay.status_code, replay.json()["code"]), (401, "invalid_provider_token"))

    def test_concurrent_first_sign_in_resolves_to_the_winner(self):
        """The check-then-create race: the loser's IntegrityError re-reads the winner."""
        winner = self.sign_in("google", "g-race").json()["user"]["id"]
        real_find = social_accounts._find_identity
        calls = {"n": 0}

        def stale_first_lookup(identity):
            calls["n"] += 1
            return None if calls["n"] == 1 else real_find(identity)

        with mock.patch.object(social_accounts, "_find_identity", side_effect=stale_first_lookup):
            response = self.sign_in("google", "g-race")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["id"], winner)
        self.assertFalse(response.json()["is_new_user"])
        self.assertEqual(User.objects.count(), 1)

    def test_username_left_by_an_unlinked_identity_does_not_block_sign_up(self):
        first = self.sign_in("google", "g-recycled").json()["user"]["id"]
        SocialIdentity.objects.filter(user_id=first).delete()
        response = self.sign_in("google", "g-recycled")
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()["user"]["id"], first)
        self.assertEqual(User.objects.count(), 2)

    def test_login_and_account_created_events_record_method_without_tokens(self):
        EventType.get_or_create_default_types()
        with override_settings(TRACK_USER_ACCOUNT_CREATED=True):
            payload = self.social_payload("apple", "apple-events", email="e@example.com")
            response = self.client.post(SOCIAL_URL, payload, format="json")
        self.assertEqual(response.status_code, 200)
        login = Event.objects.get(event_type__code=EventType.USER_LOGGED_IN)
        self.assertEqual(login.data["method"], "apple")
        self.assertTrue(login.data["is_new_user"])
        created = Event.objects.get(event_type__code=EventType.USER_ACCOUNT_CREATED)
        self.assertEqual(created.data["method"], "apple")
        serialized = str([e.data for e in Event.objects.all()]) + str([e.title for e in Event.objects.all()])
        self.assertNotIn(payload["id_token"], serialized)
        self.assertNotIn(response.json()["tokens"]["access"], serialized)

    def test_provider_tokens_never_logged(self):
        payload = self.social_payload("google", "g-logs", email="log@example.com")
        forged = {"provider": "google", "id_token": make_id_token("google", "x", key=rsa_key("attacker"))}
        with self.assertLogs("hub", level="DEBUG") as logs:
            self.client.post(SOCIAL_URL, payload, format="json")
            self.client.post(SOCIAL_URL, forged, format="json")
            logging.getLogger("hub").debug("sentinel")
        dump = "\n".join(
            f"{record.getMessage()} {record.__dict__}" for record in logs.records
        )
        for secret in (payload["id_token"], payload["nonce"], forged["id_token"], "log@example.com"):
            self.assertNotIn(secret, dump)

    def test_throttled(self):
        with override_settings(SOCIAL_AUTH_THROTTLE_RATE="2/min"):
            statuses = [self.sign_in("google", f"g-throttle-{i}").status_code for i in range(3)]
        self.assertEqual(statuses, [200, 200, 429])


class AppleAuthorizationCodeTests(SocialAuthTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def apple_sign_in(self, sub, code="code-1"):
        payload = self.social_payload("apple", sub)
        payload["authorization_code"] = code
        return self.client.post(SOCIAL_URL, payload, format="json")

    @override_settings(APPLE_TEAM_ID="TEAM123456", APPLE_KEY_ID="KEY1234567", APPLE_PRIVATE_KEY="configured")
    def test_code_exchanged_and_refresh_token_stored_encrypted(self):
        response = self.apple_sign_in("apple-code")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.apple_client.exchanged, [("code-1", APPLE_AUDIENCE)])
        stored = SocialIdentity.objects.get().apple_refresh_token_encrypted
        self.assertTrue(stored)
        self.assertNotIn("apple-refresh-for-code-1", stored)
        self.assertEqual(
            decrypt_payload(stored),
            {"client_id": APPLE_AUDIENCE, "refresh_token": "apple-refresh-for-code-1"},
        )
        self.assertNotIn("apple-refresh", response.content.decode())

    @override_settings(APPLE_TEAM_ID="TEAM123456", APPLE_KEY_ID="KEY1234567", APPLE_PRIVATE_KEY="configured")
    def test_exchange_failure_does_not_fail_sign_in(self):
        self.apple_client.fail_exchange = AppleTokenError("invalid_grant")
        response = self.apple_sign_in("apple-code-fail")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SocialIdentity.objects.get().apple_refresh_token_encrypted, "")

    @override_settings(APPLE_TEAM_ID="TEAM123456", APPLE_KEY_ID="KEY1234567", APPLE_PRIVATE_KEY="configured")
    def test_exchange_for_a_different_subject_is_not_stored(self):
        self.apple_client.subject_override = "someone-else"
        self.assertEqual(self.apple_sign_in("apple-mismatch").status_code, 200)
        self.assertEqual(SocialIdentity.objects.get().apple_refresh_token_encrypted, "")

    def test_exchange_skipped_until_key_configured(self):
        self.assertEqual(self.apple_sign_in("apple-no-key").status_code, 200)
        self.assertEqual(self.apple_client.exchanged, [])


class AppleTokenClientTests(TestCase):
    """The real client against a fake HTTP session and a locally generated P-256 key."""

    def setUp(self):
        self.ec_key, pem = ec_private_key_pem()
        # Render-style storage: literal "\n" escapes.
        override = override_settings(
            APPLE_TEAM_ID="TEAM123456", APPLE_KEY_ID="KEY1234567", APPLE_PRIVATE_KEY=pem.replace("\n", "\\n")
        )
        override.enable()
        self.addCleanup(override.disable)
        self.session = mock.Mock()
        self.client = AppleTokenClient(session=self.session)

    def response(self, status_code, payload):
        resp = mock.Mock(status_code=status_code)
        resp.json.return_value = payload
        return resp

    def test_client_secret_is_es256_signed_for_apple(self):
        secret = self.client.client_secret(APPLE_AUDIENCE, now=int(time.time()))
        self.assertEqual(jwt.get_unverified_header(secret), {"alg": "ES256", "kid": "KEY1234567", "typ": "JWT"})
        claims = jwt.decode(secret, self.ec_key.public_key(), algorithms=["ES256"], audience=APPLE_SERVER_AUDIENCE)
        self.assertEqual((claims["iss"], claims["sub"]), ("TEAM123456", APPLE_AUDIENCE))
        self.assertLessEqual(claims["exp"] - claims["iat"], 300)

    def test_exchange_posts_code_and_checks_subject(self):
        id_token = jwt.encode({"sub": "apple-sub"}, "x" * 32, algorithm="HS256")
        self.session.post.return_value = self.response(200, {"refresh_token": "r-1", "id_token": id_token})
        tokens = self.client.exchange_authorization_code("the-code", client_id=APPLE_AUDIENCE, expected_subject="apple-sub")
        self.assertEqual(tokens.refresh_token, "r-1")
        url, = self.session.post.call_args.args
        data = self.session.post.call_args.kwargs["data"]
        self.assertEqual(url, "https://appleid.apple.com/auth/token")
        self.assertEqual((data["code"], data["grant_type"], data["client_id"]), ("the-code", "authorization_code", APPLE_AUDIENCE))
        with self.assertRaises(AppleTokenError):
            self.client.exchange_authorization_code("c", client_id=APPLE_AUDIENCE, expected_subject="other")

    def test_exchange_error_carries_only_apples_code(self):
        self.session.post.return_value = self.response(400, {"error": "invalid_grant", "error_description": "x"})
        with self.assertRaises(AppleTokenError) as ctx:
            self.client.exchange_authorization_code("c", client_id=APPLE_AUDIENCE, expected_subject="s")
        self.assertEqual((ctx.exception.reason, ctx.exception.retryable), ("invalid_grant", False))

    def test_revoke_treats_invalid_grant_as_done_and_5xx_as_retryable(self):
        self.session.post.return_value = self.response(200, {})
        self.client.revoke_refresh_token("r", client_id=APPLE_AUDIENCE)
        self.assertEqual(self.session.post.call_args.kwargs["data"]["token_type_hint"], "refresh_token")
        self.session.post.return_value = self.response(400, {"error": "invalid_grant"})
        self.client.revoke_refresh_token("r", client_id=APPLE_AUDIENCE)
        self.session.post.return_value = self.response(503, {})
        with self.assertRaises(AppleTokenError) as ctx:
            self.client.revoke_refresh_token("r", client_id=APPLE_AUDIENCE)
        self.assertTrue(ctx.exception.retryable)

    @override_settings(APPLE_PRIVATE_KEY="")
    def test_unconfigured_key_raises_without_network(self):
        with self.assertRaises(AppleTokenError):
            self.client.client_secret(APPLE_AUDIENCE)
        self.session.post.assert_not_called()


class TokenEncryptionTests(TestCase):
    def test_round_trip_and_rotation(self):
        old, new = generate_key(), generate_key()
        with override_settings(SOCIAL_AUTH_TOKEN_ENCRYPTION_KEYS=[old]):
            ciphertext = encrypt_payload({"refresh_token": "t"})
        with override_settings(SOCIAL_AUTH_TOKEN_ENCRYPTION_KEYS=[new, old]):
            self.assertEqual(decrypt_payload(ciphertext), {"refresh_token": "t"})
        self.assertEqual(decrypt_payload(encrypt_payload({"a": 1})), {"a": 1})  # SECRET_KEY-derived


class LinkAndIdentityManagementTests(SocialAuthTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.church = TestDataFactory.create_church(name="Link Church")
        self.password_user = TestDataFactory.create_user(
            username="member@example.com", email="member@example.com", password="Secret123!"
        )
        self.profile = TestDataFactory.create_profile(user=self.password_user, church=self.church, name="Member")
        self.fast = TestDataFactory.create_fast(church=self.church)
        self.profile.fasts.add(self.fast)

    def password_login(self, email="member@example.com", password="Secret123!"):
        response = self.client.post("/api/token/", {"username": email, "password": password}, format="json")
        self.assertEqual(response.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['access']}")
        return response.json()

    def bearer(self, user, *, auth_time=None):
        refresh = RefreshToken.for_user(user)
        if auth_time is not None:
            refresh[AUTH_TIME_CLAIM] = auth_time
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def link(self, provider, sub, **kwargs):
        return self.client.post(LINK_URL, self.social_payload(provider, sub, **kwargs), format="json")

    def test_password_login_tokens_carry_auth_time_through_refresh(self):
        tokens = self.password_login()
        self.assertIn(AUTH_TIME_CLAIM, AccessToken(tokens["access"]).payload)
        refreshed = self.client.post("/api/token/refresh/", {"refresh": tokens["refresh"]}, format="json").json()
        self.assertEqual(
            AccessToken(refreshed["access"])[AUTH_TIME_CLAIM], AccessToken(tokens["access"])[AUTH_TIME_CLAIM]
        )

    def test_conflict_then_password_login_then_link_preserves_account(self):
        conflict = self.client.post(
            SOCIAL_URL, self.social_payload("google", "g-member", email="MEMBER@example.com"), format="json"
        )
        self.assertEqual(conflict.json()["code"], "account_link_required")

        self.password_login()
        linked = self.link("google", "g-member", email="member@example.com")
        self.assertEqual(linked.status_code, 201, linked.content)
        self.assertTrue(linked.json()["created"])
        self.assertTrue(linked.json()["password"])
        self.assertEqual([i["provider"] for i in linked.json()["identities"]], ["google"])

        self.client.credentials()
        social = self.client.post(SOCIAL_URL, self.social_payload("google", "g-member"), format="json")
        self.assertEqual(social.status_code, 200)
        self.assertEqual(social.json()["user"]["id"], self.password_user.pk)
        self.assertFalse(social.json()["is_new_user"])
        self.assertFalse(social.json()["profile_incomplete"])
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Profile.objects.get(user=self.password_user).pk, self.profile.pk)
        self.assertEqual(list(self.profile.fasts.values_list("pk", flat=True)), [self.fast.pk])

    def test_link_requires_recent_authentication(self):
        for label, auth_time in (("missing", None), ("stale", int(time.time()) - 3600)):
            with self.subTest(label):
                self.bearer(self.password_user, auth_time=auth_time)
                response = self.link("google", f"g-stale-{label}")
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["code"], "reauthentication_required")
        self.assertFalse(SocialIdentity.objects.exists())

    def test_link_requires_authentication(self):
        self.assertEqual(self.link("google", "g-anon").status_code, 401)

    def test_identity_of_another_user_cannot_be_linked(self):
        other = self.client.post(SOCIAL_URL, self.social_payload("google", "g-taken"), format="json").json()
        self.password_login()
        response = self.link("google", "g-taken")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "identity_in_use")
        self.assertEqual(SocialIdentity.objects.get().user_id, other["user"]["id"])

    def test_second_identity_for_same_provider_rejected_and_relink_idempotent(self):
        self.password_login()
        self.assertEqual(self.link("apple", "apple-1").status_code, 201)
        again = self.link("apple", "apple-1")
        self.assertEqual((again.status_code, again.json()["created"]), (200, False))
        second = self.link("apple", "apple-2")
        self.assertEqual((second.status_code, second.json()["code"]), (409, "provider_already_linked"))
        self.assertEqual(SocialIdentity.objects.count(), 1)

    def test_concurrent_relink_of_same_identity_is_idempotent(self):
        self.password_login()
        self.link("google", "g-link-race")
        with mock.patch.object(social_accounts, "_find_identity", return_value=None):
            response = self.link("google", "g-link-race")
        self.assertEqual((response.status_code, response.json()["created"]), (200, False))
        self.assertEqual(SocialIdentity.objects.count(), 1)

    def test_link_race_against_another_user_reports_identity_in_use(self):
        other = self.client.post(SOCIAL_URL, self.social_payload("google", "g-link-race-2"), format="json").json()
        self.password_login()
        real_find = social_accounts._find_identity
        calls = {"n": 0}

        def stale_first_lookup(identity):
            calls["n"] += 1
            return None if calls["n"] == 1 else real_find(identity)

        with mock.patch.object(social_accounts, "_find_identity", side_effect=stale_first_lookup):
            response = self.link("google", "g-link-race-2")
        self.assertEqual((response.status_code, response.json()["code"]), (409, "identity_in_use"))
        self.assertEqual(SocialIdentity.objects.get().user_id, other["user"]["id"])

    def test_link_with_flag_off(self):
        self.password_login()
        with override_settings(SOCIAL_AUTH_ENABLED=False):
            self.assertEqual(self.link("google", "g-off").json()["code"], "social_auth_unavailable")

    def test_list_and_unlink_with_ownership(self):
        self.password_login()
        identity_id = self.link("google", "g-own").json()["identity"]["id"]
        listed = self.client.get(IDENTITIES_URL).json()
        self.assertEqual(listed["password"], True)
        self.assertEqual(listed["identities"][0]["id"], identity_id)
        self.assertNotIn("provider_subject", listed["identities"][0])
        self.assertNotIn("apple_refresh_token_encrypted", str(listed))

        intruder = TestDataFactory.create_user(username="x@example.com", email="x@example.com")
        self.bearer(intruder, auth_time=int(time.time()))
        self.assertEqual(self.client.delete(identity_url(identity_id)).status_code, 404)
        self.assertTrue(SocialIdentity.objects.filter(pk=identity_id).exists())

        self.password_login()
        response = self.client.delete(identity_url(identity_id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"password": True, "identities": []})

    def test_last_sign_in_method_cannot_be_unlinked(self):
        body = self.client.post(SOCIAL_URL, self.social_payload("apple", "apple-only"), format="json").json()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['tokens']['access']}")
        identity_id = self.client.get(IDENTITIES_URL).json()["identities"][0]["id"]
        response = self.client.delete(identity_url(identity_id))
        self.assertEqual((response.status_code, response.json()["code"]), (409, "last_sign_in_method"))

        # With a second provider linked, one of them can go.
        self.assertEqual(self.link("google", "g-second").status_code, 201)
        self.assertEqual(self.client.delete(identity_url(identity_id)).status_code, 200)
        self.assertEqual([i["provider"] for i in self.client.get(IDENTITIES_URL).json()["identities"]], ["google"])

    def test_password_without_email_is_not_a_usable_method(self):
        body = self.client.post(SOCIAL_URL, self.social_payload("google", "g-pw"), format="json").json()
        user = User.objects.get(pk=body["user"]["id"])
        user.set_password("Whatever123!")
        user.save()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['tokens']['access']}")
        listed = self.client.get(IDENTITIES_URL).json()
        self.assertFalse(listed["password"])
        response = self.client.delete(identity_url(listed["identities"][0]["id"]))
        self.assertEqual(response.json()["code"], "last_sign_in_method")

    def test_unlink_requires_recent_authentication(self):
        self.password_login()
        identity_id = self.link("google", "g-unlink-stale").json()["identity"]["id"]
        self.bearer(self.password_user, auth_time=int(time.time()) - 3600)
        response = self.client.delete(identity_url(identity_id))
        self.assertEqual(response.json()["code"], "reauthentication_required")

    @override_settings(APPLE_TEAM_ID="TEAM123456", APPLE_KEY_ID="KEY1234567", APPLE_PRIVATE_KEY="configured")
    def test_unlinking_apple_revokes_its_token(self):
        self.password_login()
        payload = self.social_payload("apple", "apple-unlink")
        payload["authorization_code"] = "unlink-code"
        identity_id = self.client.post(LINK_URL, payload, format="json").json()["identity"]["id"]
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(identity_url(identity_id)).status_code, 200)
        self.assertEqual(self.apple_client.revoked, [("apple-refresh-for-unlink-code", APPLE_AUDIENCE)])


@override_settings(APPLE_TEAM_ID="TEAM123456", APPLE_KEY_ID="KEY1234567", APPLE_PRIVATE_KEY="configured")
class AccountDeletionTests(SocialAuthTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_social_only_account_can_be_deleted_and_apple_is_revoked(self):
        payload = self.social_payload("apple", "apple-delete")
        payload["authorization_code"] = "delete-code"
        body = self.client.post(SOCIAL_URL, payload, format="json").json()
        user_id = body["user"]["id"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['tokens']['access']}")

        response = self.client.delete("/api/account/delete/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(User.objects.filter(pk=user_id).exists())
        self.assertFalse(SocialIdentity.objects.exists())
        self.assertEqual(self.apple_client.revoked, [("apple-refresh-for-delete-code", APPLE_AUDIENCE)])

        # The deleted account's refresh token stays dead.
        self.client.credentials()
        refreshed = self.client.post("/api/token/refresh/", {"refresh": body["tokens"]["refresh"]}, format="json")
        self.assertEqual(refreshed.status_code, 401)
        # And signing in with the same Apple identity again starts a new account.
        again = self.client.post(SOCIAL_URL, self.social_payload("apple", "apple-delete"), format="json")
        self.assertTrue(again.json()["is_new_user"])

    def test_deletion_succeeds_when_apple_revocation_fails(self):
        self.apple_client.fail_revoke = AppleTokenError("invalid_client")
        payload = self.social_payload("apple", "apple-delete-fail")
        payload["authorization_code"] = "c"
        body = self.client.post(SOCIAL_URL, payload, format="json").json()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['tokens']['access']}")
        self.assertEqual(self.client.delete("/api/account/delete/").status_code, 204)
        self.assertFalse(User.objects.filter(pk=body["user"]["id"]).exists())


@override_settings(APPLE_TEAM_ID="TEAM123456", APPLE_KEY_ID="KEY1234567", APPLE_PRIVATE_KEY="configured")
class RevocationTaskTests(SocialAuthTestMixin, TestCase):
    def ciphertext(self, token="r"):
        return encrypt_payload({"client_id": APPLE_AUDIENCE, "refresh_token": token})

    def test_revokes_each_token(self):
        result = revoke_apple_tokens_task.apply(args=[[self.ciphertext("a"), self.ciphertext("b")]]).get()
        self.assertEqual(result["revoked"], 2)
        self.assertEqual([t for t, _ in self.apple_client.revoked], ["a", "b"])

    def test_transient_failure_retries_only_the_failed_token(self):
        self.apple_client.fail_revoke = [None, AppleTokenError("http_503", retryable=True)]
        with mock.patch.object(revoke_apple_tokens_task, "retry", side_effect=RuntimeError("retry")) as retry:
            with self.assertRaises(RuntimeError):
                revoke_apple_tokens_task.apply(args=[[self.ciphertext("a"), self.ciphertext("b")]], throw=True)
        self.assertEqual(len(retry.call_args.kwargs["args"][0]), 1)
        self.assertEqual(decrypt_payload(retry.call_args.kwargs["args"][0][0])["refresh_token"], "b")

    def test_undecryptable_token_is_skipped(self):
        result = revoke_apple_tokens_task.apply(args=[["not-a-ciphertext"]]).get()
        self.assertEqual((result["revoked"], result["failed"]), (0, 1))

    @override_settings(APPLE_PRIVATE_KEY="")
    def test_unconfigured_key_logs_and_drops(self):
        result = revoke_apple_tokens_task.apply(args=[[self.ciphertext()]]).get()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(self.apple_client.revoked, [])


class ExistingAuthUnaffectedTests(TestCase):
    """Password registration and login keep working, now with auth_time."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.church = TestDataFactory.create_church(name="Register Church")

    def test_registration_still_creates_password_account(self):
        response = self.client.post(
            reverse("auth_register"),
            {
                "email": "fresh@example.com",
                "password": "StrongPass123!",
                "password2": "StrongPass123!",
                "profile": {"name": "Fresh", "church": self.church.pk},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        user = User.objects.get(email="fresh@example.com")
        self.assertEqual(user.username, "fresh@example.com")
        self.assertTrue(user.check_password("StrongPass123!"))
        self.assertEqual(Profile.objects.get(user=user).church, self.church)
        self.assertIn(AUTH_TIME_CLAIM, AccessToken(response.json()["tokens"]["access"]).payload)

    def test_registration_records_password_method(self):
        EventType.get_or_create_default_types()
        with override_settings(TRACK_USER_ACCOUNT_CREATED=True):
            self.client.post(
                reverse("auth_register"),
                {
                    "email": "method@example.com",
                    "password": "StrongPass123!",
                    "password2": "StrongPass123!",
                    "profile": {"name": "Method", "church": self.church.pk},
                },
                format="json",
            )
        created = Event.objects.get(event_type__code=EventType.USER_ACCOUNT_CREATED)
        self.assertEqual(created.data["method"], "password")

    def test_social_identity_unique_constraints(self):
        user = TestDataFactory.create_user(username="c@example.com", email="c@example.com")
        SocialIdentity.objects.create(user=user, provider="google", provider_subject="s")
        other = TestDataFactory.create_user(username="d@example.com", email="d@example.com")
        with self.assertRaises(IntegrityError), transaction.atomic():
            SocialIdentity.objects.create(user=other, provider="google", provider_subject="s")
        with self.assertRaises(IntegrityError), transaction.atomic():
            SocialIdentity.objects.create(user=user, provider="google", provider_subject="s2")


class CheckSocialAuthCommandTests(TestCase):
    def run_command(self, **settings_kwargs):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        with override_settings(**settings_kwargs):
            call_command("check_social_auth", stdout=out)
        return out.getvalue()

    def test_reports_disabled_without_failing(self):
        output = self.run_command(SOCIAL_AUTH_ENABLED=False)
        self.assertIn("SOCIAL_AUTH_ENABLED: False", output)

    def test_fails_when_apple_live_without_revocation_key(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self.run_command(
                SOCIAL_AUTH_ENABLED=True, APPLE_ALLOWED_AUDIENCES=[APPLE_AUDIENCE],
                APPLE_TEAM_ID="", APPLE_KEY_ID="", APPLE_PRIVATE_KEY="",
            )

    def test_never_prints_the_private_key(self):
        _, pem = ec_private_key_pem()
        output = self.run_command(
            SOCIAL_AUTH_ENABLED=True, APPLE_ALLOWED_AUDIENCES=[APPLE_AUDIENCE],
            GOOGLE_ALLOWED_AUDIENCES=[GOOGLE_AUDIENCE],
            APPLE_TEAM_ID="TEAM123456", APPLE_KEY_ID="KEY1234567", APPLE_PRIVATE_KEY=pem,
        )
        self.assertIn("private key signs an ES256 client secret: ok", output)
        self.assertNotIn(pem.splitlines()[1], output)
