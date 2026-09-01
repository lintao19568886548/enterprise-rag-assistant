# OIDC operations

## Supported flow

The browser flow uses Authorization Code with PKCE S256, random state and nonce. Discovery and
JWKS are issuer-bound; access and ID tokens validate signature, allowed algorithm, issuer,
audience, expiry and clock skew. The ID token also validates nonce. Callback state is one-time,
the encrypted transaction expires, and access/ID subject and issuer must agree.

The server stores an AES-GCM encrypted session in an HttpOnly, Secure, SameSite=Strict cookie.
The short-lived state cookie is HttpOnly, Secure and SameSite=Lax for the redirect callback.
Refresh rotates the encrypted browser session. Logout revokes local state and uses the provider's
end-session endpoint when advertised. Return paths are restricted to same-origin relative paths.

## Required enterprise values

Configure these on the host or secret manager, never in Git:

- `OIDC_ISSUER_URL`
- `OIDC_CLIENT_ID`
- `OIDC_AUDIENCE`
- `OIDC_REDIRECT_URI`
- `OIDC_POST_LOGOUT_REDIRECT_URI`
- `OIDC_SCOPES` including `openid`
- `OIDC_CLIENT_SECRET` in local `.env`/secret manager only
- `OIDC_SESSION_ENCRYPTION_KEY`, exactly 32 bytes, in local `.env`/secret manager only

Staging/production rejects non-HTTPS issuer/redirect URLs, weak secrets and missing `openid`.
Do not paste client secrets into tickets, chat or command output.

## Local test identity provider

`compose.oidc-test.yaml` and `deploy/keycloak/enterprise-local-realm.json` define a loopback-only
Keycloak 26.7.2 test IdP plus password-protected Redis. Copy `.env.oidc.example` to an ignored
local file and replace every placeholder before start. This environment is for automated/local
flow testing and is not enterprise OIDC acceptance.

The local Compose environment was executed on 2026-09-01. Keycloak discovery returned HTTP 200
and the real Authorization Code flow passed PKCE S256, state, nonce, issuer/audience validation,
membership lookup, encrypted Redis session storage, refresh rotation, replay rejection and
logout. `deploy/keycloak/configure-acceptance-client.sh` synchronizes the loopback client without
printing its secret, and `scripts/verify_local_oidc_flow.py` performs the repeatable acceptance.
Uvicorn access logging is disabled for the API entry points so callback query strings containing
short-lived authorization codes/state are never written to the normal access log; the structured
request log records the path but not its query string.

This proves the implementation against the local test IdP only. Enterprise OIDC remains
**not verified** until the real issuer, client ID, redirect URI and scopes are supplied and the
client secret is placed directly in the target secret store.

## Provisioning and role behavior

Login requires an existing active tenant membership for the issuer/subject. Unknown or disabled
users do not receive implicit access. Membership role maps to the existing RBAC permissions;
knowledge-base grants remain an additional authorization boundary. A user with multiple tenant
memberships must select/use a provisioned tenant context; a browser session cannot invent one.

## Troubleshooting

- Discovery/JWKS failure: verify issuer exactness, TLS trust, DNS and outbound access.
- `invalid state` or replay: start a fresh login; state/transaction is one-time and short-lived.
- nonce/audience/issuer failure: correct IdP client mappings; never disable validation.
- callback loop: confirm the registered redirect exactly matches `OIDC_REDIRECT_URI`.
- refresh failure: reauthenticate; do not extend expired tokens locally.
- 403 after valid login: inspect active membership/role and knowledge-base grant audit records.

Automated tests cover forged signature, issuer/audience/expiry/nonce failures, one-time state,
session encryption/expiry/refresh/logout, open-redirect rejection and callback provisioning.
