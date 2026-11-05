"""Server-verified Sign in with Apple and Google (issue #598).

Modules:

- ``errors``     structured error codes shared with the mobile client
- ``config``     feature flags and per-provider configuration
- ``providers``  ID-token verification against each provider's JWKS
- ``apple``      Apple authorization-code exchange and token revocation
- ``crypto``     encryption for the one stored provider credential
- ``accounts``   identity resolution, sign-up, link and unlink rules

The API contract lives in ``docs/SOCIAL_AUTH.md``.
"""
