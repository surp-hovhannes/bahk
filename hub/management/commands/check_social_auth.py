"""Report whether Sign in with Apple / Google is configured for this environment.

Read-only and offline by default: prints which providers the app would show,
which audiences are accepted, whether the Apple revocation key is usable, and
which settings are missing. It never prints secret values (the private key and
encryption keys are reported only as present/absent/valid).

``--fetch-jwks`` additionally downloads each enabled provider's public key set
(two unauthenticated GETs to Apple/Google) to prove outbound access.

Exit status is non-zero when ``SOCIAL_AUTH_ENABLED`` is on but a live provider
is misconfigured, so it can gate a deploy or a staging smoke test.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from hub.services.social_auth import config
from hub.services.social_auth.apple import AppleTokenClient, AppleTokenError, apple_key_configured
from hub.services.social_auth.crypto import decrypt_payload, encrypt_payload
from hub.services.social_auth.errors import SocialAuthError
from hub.services.social_auth.providers import PROVIDER_SPECS, fetch_jwks


class Command(BaseCommand):
    help = "Check Sign in with Apple/Google configuration without exposing secrets."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fetch-jwks",
            action="store_true",
            help="Also download each enabled provider's JWKS to verify network access.",
        )

    def handle(self, *args, **options):
        problems, warnings = [], []
        public = config.public_config()
        self.stdout.write(f"SOCIAL_AUTH_ENABLED: {public['enabled']}")
        for provider in config.PROVIDERS:
            audiences = config.allowed_audiences(provider)
            self.stdout.write(
                f"  {provider}: live={public['providers'][provider]} audiences={audiences or '[]'}"
            )
            if public["enabled"] and not audiences:
                warnings.append(f"{provider}: no allowed audiences, so its button stays hidden")

        if config.google_requires_nonce():
            self.stdout.write("  google: nonce required")
        else:
            self.stdout.write("  google: nonce checked when present (SOCIAL_AUTH_GOOGLE_REQUIRE_NONCE=False)")
        self.stdout.write(f"  reauth window for link/unlink: {config.reauth_max_age_seconds()}s")

        # Apple revocation key (App Store guideline 5.1.1(v)).
        team = bool(getattr(settings, "APPLE_TEAM_ID", ""))
        key_id = bool(getattr(settings, "APPLE_KEY_ID", ""))
        key = bool(getattr(settings, "APPLE_PRIVATE_KEY", ""))
        self.stdout.write(
            f"Apple revocation key: team_id={'set' if team else 'missing'} "
            f"key_id={'set' if key_id else 'missing'} private_key={'set' if key else 'missing'}"
        )
        if apple_key_configured():
            audience = (config.allowed_audiences(config.APPLE) or ["com.example.app"])[0]
            try:
                AppleTokenClient().client_secret(audience)
                self.stdout.write("  private key signs an ES256 client secret: ok")
            except AppleTokenError as exc:
                problems.append(f"apple: private key unusable ({exc.reason})")
        elif public["providers"][config.APPLE]:
            problems.append(
                "apple: live without APPLE_TEAM_ID/APPLE_KEY_ID/APPLE_PRIVATE_KEY, so tokens "
                "cannot be revoked at account deletion"
            )

        explicit_keys = bool(getattr(settings, "SOCIAL_AUTH_TOKEN_ENCRYPTION_KEYS", []))
        try:
            decrypt_payload(encrypt_payload({"check": True}))
            self.stdout.write(
                "Token encryption: ok ("
                + ("explicit SOCIAL_AUTH_TOKEN_ENCRYPTION_KEYS" if explicit_keys else "derived from SECRET_KEY")
                + ")"
            )
        except Exception as exc:  # noqa: BLE001 - report, never echo key material
            problems.append(f"token encryption unusable ({type(exc).__name__})")
        if not explicit_keys:
            warnings.append("SOCIAL_AUTH_TOKEN_ENCRYPTION_KEYS unset: rotating SECRET_KEY would orphan stored Apple tokens")

        if options["fetch_jwks"]:
            for provider in config.PROVIDERS:
                if not public["providers"][provider]:
                    continue
                try:
                    keys = fetch_jwks(PROVIDER_SPECS[provider], force=True).get("keys", [])
                    self.stdout.write(f"  {provider} JWKS: {len(keys)} keys")
                except SocialAuthError as exc:
                    problems.append(f"{provider}: JWKS fetch failed ({exc.reason})")

        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"warning: {warning}"))
        for problem in problems:
            self.stdout.write(self.style.ERROR(f"problem: {problem}"))
        if problems and public["enabled"]:
            raise CommandError(f"{len(problems)} social auth configuration problem(s)")
        if not problems:
            self.stdout.write(self.style.SUCCESS("Social auth configuration looks consistent."))
