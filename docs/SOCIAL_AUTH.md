# Sign in with Apple and Google

Server half of [bahk #598](https://github.com/surp-hovhannes/bahk/issues/598); the app half is
[bahk_react_native #474](https://github.com/surp-hovhannes/bahk_react_native/issues/474), provider
setup is [bahk_react_native #475](https://github.com/surp-hovhannes/bahk_react_native/issues/475).

Social sign-in is an extra way to get the **existing** SimpleJWT pair. Users stay Django users with
the same IDs, profiles and history. JWT lifetimes, `/api/token/refresh/`, logout and account
deletion are unchanged. Email/password keeps working indefinitely.

## Code map

| Piece | Where |
|---|---|
| `SocialIdentity` model (`provider`, `provider_subject`, unique per provider and per user+provider) | `hub/models.py`, migration `0073` |
| Shared user/profile creation and token issuance (also used by `/api/register/`) | `hub/services/accounts.py` |
| ID-token verification (JWKS, iss/aud/exp, nonce, replay) | `hub/services/social_auth/providers.py` |
| Sign-in, link and unlink rules | `hub/services/social_auth/accounts.py` |
| Apple code exchange + revocation client | `hub/services/social_auth/apple.py` |
| Encryption of the stored Apple refresh token | `hub/services/social_auth/crypto.py` |
| Revocation task | `hub/tasks/social_auth_tasks.py` |
| Endpoints | `hub/views/social_auth.py` |
| Config check | `python manage.py check_social_auth [--fetch-jwks]` |
| Tests | `hub/tests/test_social_auth.py`, `hub/tests/test_social_auth_postgres.py` |

## API contract

All errors are `{"code": "...", "detail": "..."}` with a fixed, generic `detail`. Clients switch on
`code` (and HTTP status), never on `detail`.

### `GET /api/auth/social/config/` (public)

```json
{"enabled": true, "providers": {"apple": true, "google": false}}
```

The app shows a provider button only when `providers.<name>` is `true`, and shows none when this
call fails. This is the remote kill switch: turning `SOCIAL_AUTH_ENABLED` (or one provider's
switch) off hides the buttons without an app release.

### `POST /api/auth/social/` (unauthenticated, throttled)

```json
{
  "provider": "apple",
  "id_token": "<ID token issued by Apple or Google>",
  "nonce": "<raw per-attempt nonce>",
  "authorization_code": "<Apple only: single-use code from the same sign-in>",
  "name": "<Apple only, optional: full name Apple gave the app on first sign-in>"
}
```

Never send the provider's user ID or email as proof of identity; they are read from the verified
token. Never send an access token as `id_token`. `authorization_code` and `name` are optional.

Success, `200`:

```json
{
  "tokens": {"access": "<SimpleJWT>", "refresh": "<SimpleJWT>"},
  "user": {"id": 123},
  "is_new_user": false,
  "profile_incomplete": false,
  "provider": "apple"
}
```

Activate the pair exactly like a password login (`tokenManager.setTokens`). `profile_incomplete`
is `true` while the profile lacks a name or a church (the two fields email registration requires);
ask for them **after** sign-in. It is never an auth failure.

### `POST /api/auth/social/link/` (authenticated, recent sign-in, throttled)

Same body as above minus `name`. The credential must be **fresh** (a new provider sign-in with a new
nonce): tokens are single-use. The app session must be recent (see Re-authentication).
`201` when linked, `200` when that identity was already linked to the caller:

```json
{
  "identity": {"id": 7, "provider": "google", "email": "a@example.com", "is_private_email": false,
               "created_at": "...", "last_login_at": null},
  "created": true,
  "password": true,
  "identities": [ ...same shape as identity... ]
}
```

### `GET /api/auth/social/identities/` (authenticated)

```json
{"password": true, "identities": [{"id": 7, "provider": "google", "email": "a@example.com", ...}]}
```

`password` is `true` only when the account has a usable password **and** an email to log in with.
`email` is what the provider asserted when the identity was linked (diagnostic, shown to the owner
only; never used to find accounts). Subjects and credentials are never returned.

### `DELETE /api/auth/social/identities/{id}/` (authenticated, recent sign-in, throttled)

`200` with the same body as the list. Another user's identity or an unknown ID is `404`
`not_found`. Removing the last usable sign-in method is refused. Unlinking Apple also revokes its
stored Apple token.

### Error codes

| Case | Status | `code` |
|---|---|---|
| Malformed body, unknown provider | 400 | `invalid_request` (plus DRF `errors`) |
| Token has no usable subject | 400 | `invalid_provider_token` |
| Token malformed, expired, bad signature, wrong issuer/audience, replayed | 401 | `invalid_provider_token` |
| Nonce missing, malformed or mismatched | 401 | `invalid_nonce` |
| New identity whose verified, non-relay email belongs to an existing user | 409 | `account_link_required` |
| Linked user is inactive | 403 | `account_disabled` |
| Link/unlink with a stale app sign-in | 403 | `reauthentication_required` |
| Link: identity belongs to another user | 409 | `identity_in_use` |
| Link: caller already has a different identity for that provider | 409 | `provider_already_linked` |
| Unlink would remove the last way to sign in | 409 | `last_sign_in_method` |
| Feature flag or provider switched off | 503 | `social_auth_unavailable` |
| Provider key set unreachable (retry) | 503 | `provider_unavailable` |
| Throttled | 429 | DRF default body |

`account_link_required` reveals no more than `/api/register/` already does ("user with this email
already exists").

## Nonce

Per attempt the app generates a random raw nonce (16–256 printable ASCII characters), keeps it in
memory for that attempt only, and sends the raw value to the backend.

- **Apple:** the app gives Apple `SHA-256(raw)` as lowercase hex; the token's `nonce` claim is that
  hash. The backend hashes the raw nonce and compares. Required.
- **Google:** when an SDK binds one, the token's `nonce` claim is the raw value and must match. The
  app's Google package (`@react-native-google-signin/google-signin`, free "Original" API) cannot
  bind a nonce on iOS or Android, so a Google token without a nonce claim is accepted unless
  `SOCIAL_AUTH_GOOGLE_REQUIRE_NONCE=true`. Signature, issuer, the single web-client audience, short
  expiry and the replay guard still apply, and the app signs out of the Google SDK before each
  sign-in so a cached, already-used token is never resent. Keep the setting `false` until the app
  adopts an SDK that binds nonces, or every Google sign-in will fail with `invalid_nonce`.

Every verified token is accepted **once** (keyed on its hash in the shared cache until it expires).
A retry needs a new provider sign-in.

## Account rules

1. `(provider, sub)` is the key. A known identity signs in its user whatever email the provider
   reports now.
2. Email never links or signs in an account. A new identity whose verified, non-relay email matches
   an existing user (case-insensitive) gets `account_link_required`: sign in to that account, then
   link explicitly.
3. Apple private-relay addresses and unverified emails are never matched. A new subject creates a
   new account; a relay address that collides is not stored, so two accounts never share a login
   email.
4. New accounts get an unusable password, an opaque username (`google-<hash>`), the verified email
   when one is available, and the name from Google's token or Apple's first-sign-in `name`.
5. A user has at most one identity per provider. An identity can belong to one user only.
6. Concurrent first sign-ins / links are settled by the unique constraints; the loser re-reads the
   winner's row, so tokens are only minted for the account that owns the identity.
7. Unlinking never removes the last usable sign-in method (password counts only with an email).
8. Account deletion (`DELETE /api/account/delete/`) works for social-only accounts, cascades the
   identities, revokes Apple tokens, and the account's refresh tokens stay rejected.

### Re-authentication

Tokens from `/api/token/`, `/api/register/` and `/api/auth/social/` carry an `auth_time` claim,
which SimpleJWT copies into refreshed access tokens. Link and unlink require
`now - auth_time <= SOCIAL_AUTH_REAUTH_MAX_AGE_SECONDS` (default 900; `0` disables), otherwise
`403 reauthentication_required`. Sessions issued before this release have no `auth_time` and must
sign in again before changing sign-in methods. The usual link flow (409 → password login → link)
is always fresh.

## Apple token revocation

Required by App Store Review Guideline 5.1.1(v) (decision 8 on #598).

- The app sends Apple's `authorization_code` with Apple sign-in and link requests.
- When `APPLE_TEAM_ID`, `APPLE_KEY_ID` and `APPLE_PRIVATE_KEY` are set, the backend exchanges it
  (ES256 client secret, 5-minute lifetime) and stores the refresh token Fernet-encrypted with its
  client ID. Without the key the exchange is skipped and logged; sign-in still succeeds.
- Account deletion and Apple unlink enqueue `revoke_apple_tokens_task` with the **ciphertext**;
  plaintext exists only in the worker during the call. Transient failures retry with backoff;
  `invalid_grant` counts as already revoked.
- Exchange failures never fail sign-in; revocation failures never block deletion. Both log Apple's
  short error code only.

## Settings

| Variable | Default | Notes |
|---|---|---|
| `SOCIAL_AUTH_ENABLED` | `false` | Master switch. Off: 503 everywhere, buttons hidden. |
| `SOCIAL_AUTH_APPLE_ENABLED` / `SOCIAL_AUTH_GOOGLE_ENABLED` | `true` | Per-provider kill switches. |
| `APPLE_ALLOWED_AUDIENCES` | empty | iOS bundle ID: `com.stjohn.bahk`. Add `com.stjohn.bahk.e2e` on staging only if used. |
| `GOOGLE_ALLOWED_AUDIENCES` | empty | The **web** OAuth client ID only (decision 1 on #598). |
| `APPLE_TEAM_ID`, `APPLE_KEY_ID` | empty | Public IDs from #475 (`FHF872BAQR`, `UUF2LN7TD9`). |
| `APPLE_PRIVATE_KEY` | empty | The `.p8` PEM; secret manager only; `\n` escapes accepted. Never logged. |
| `SOCIAL_AUTH_TOKEN_ENCRYPTION_KEYS` | derived | Comma-separated Fernet keys, newest first. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Set before ever rotating `SECRET_KEY`. |
| `SOCIAL_AUTH_GOOGLE_REQUIRE_NONCE` | `false` | See Nonce. |
| `SOCIAL_AUTH_REAUTH_MAX_AGE_SECONDS` | `900` | Link/unlink freshness window. |
| `SOCIAL_AUTH_THROTTLE_RATE` | `20/min` | Per IP (sign-in) / per user (link, unlink). |
| `SOCIAL_AUTH_JWT_LEEWAY_SECONDS` | `60` | Clock skew. |
| `SOCIAL_AUTH_HTTP_TIMEOUT_SECONDS` | `5` | JWKS and Apple calls. |

Outbound hosts: `appleid.apple.com` (JWKS, token, revoke) and `www.googleapis.com` (JWKS).
JWKS are cached in the shared cache (Redis) for the provider's `Cache-Control` lifetime, clamped to
5 minutes–24 hours; an unknown `kid` triggers at most one forced refetch per provider per minute.

## Logging and audit

Logs carry user/identity IDs, provider, error `code` and a fixed internal `reason` label. They never
contain ID tokens, nonces, authorization codes, Apple tokens, client secrets, keys or emails.
Security-sensitive changes go to the `hub.social_auth.audit` logger:
`account_created`, `identity_linked`, `identity_unlinked`, `link_required`,
`link_rejected_identity_in_use`, `unlink_rejected_last_method`, `sign_in_rejected_inactive`.

Analytics: `USER_LOGGED_IN` has `data.method` = `apple`/`google` (password logins stay `jwt`) and
`data.is_new_user`; `USER_ACCOUNT_CREATED` now has `data.method` = `password`/`apple`/`google`.

## Rollout runbook

There is no staging service yet (#475, C1). Until one exists, validate on production with the flag
off except for the validation window, or stand up a staging service first; record which in #598.

1. Deploy with `SOCIAL_AUTH_ENABLED=false`. The migration only adds a table.
2. Owner pastes `APPLE_PRIVATE_KEY` into the secret manager (agents never handle it). Optionally set
   `SOCIAL_AUTH_TOKEN_ENCRYPTION_KEYS`.
3. `python manage.py check_social_auth --fetch-jwks` must exit 0 and show both JWKS fetched and the
   private key able to sign.
4. Enable on the validation environment (`SOCIAL_AUTH_ENABLED=true`) and run the device checklist
   in the app repo (`docs/qa/social-sign-in.md`): Apple new/returning/hide-email, Google new/returning
   on iOS and Android, conflict → login → link, unlink rules, account deletion (confirm the Apple ID
   settings page no longer lists the app after deletion).
5. Watch logs for `social_auth.request_rejected` by `code`, `apple_code_exchange_failed` and
   `apple_revocation_*`.
6. Production: enable only after step 4 passes and the app build that contains the buttons is
   approved. Buttons follow `GET /api/auth/social/config/`, so the flag can be enabled before the app
   reaches users.

## Rollback

- **Immediate:** set `SOCIAL_AUTH_ENABLED=false` (or one provider's switch). Buttons disappear on the
  next config fetch; existing sessions keep working because they are ordinary SimpleJWT tokens.
  Social-only users cannot start a new session while the switch is off; they can still set a
  password through "Forgot password" when their account has an email.
- **Code:** reverting the release is safe; the `SocialIdentity` table is additive and no existing
  credential or row was modified. Leave the table in place (it holds the Apple tokens needed for
  revocation) rather than reversing migration `0073`.
