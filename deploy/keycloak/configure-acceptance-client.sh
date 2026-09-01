#!/usr/bin/env bash
set -euo pipefail

: "${KC_BOOTSTRAP_ADMIN_USERNAME:?KC_BOOTSTRAP_ADMIN_USERNAME is required}"
: "${KC_BOOTSTRAP_ADMIN_PASSWORD:?KC_BOOTSTRAP_ADMIN_PASSWORD is required}"
: "${LOCAL_OIDC_CLIENT_SECRET:?LOCAL_OIDC_CLIENT_SECRET is required}"
: "${LOCAL_OIDC_USER_PASSWORD:?LOCAL_OIDC_USER_PASSWORD is required}"

KCADM=/opt/keycloak/bin/kcadm.sh
SERVER_URL=http://127.0.0.1:8080
REALM=enterprise-local

"$KCADM" config credentials \
  --server "$SERVER_URL" \
  --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" \
  --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null

client_uuid="$($KCADM get clients \
  -r "$REALM" \
  -q clientId=enterprise-rag \
  --fields id \
  --format csv \
  --noquotes | tail -n 1 | tr -d '\r')"

if [[ -z "$client_uuid" ]]; then
  echo "enterprise-rag client was not found" >&2
  exit 1
fi

user_uuid="$($KCADM get users \
  -r "$REALM" \
  -q username=oidc-local-admin \
  --fields id \
  --format csv \
  --noquotes | tail -n 1 | tr -d '\r')"

if [[ -z "$user_uuid" ]]; then
  echo "oidc-local-admin user was not found" >&2
  exit 1
fi

"$KCADM" update "clients/$client_uuid" \
  -r "$REALM" \
  -s "secret=$LOCAL_OIDC_CLIENT_SECRET" \
  -s 'redirectUris=["http://127.0.0.1:8001/auth/callback","http://127.0.0.1:18001/auth/callback"]' \
  -s 'webOrigins=["http://127.0.0.1:8001","http://127.0.0.1:18001"]' \
  -s 'attributes."pkce.code.challenge.method"="S256"' \
  -s 'attributes."post.logout.redirect.uris"="http://127.0.0.1:8001/*##http://127.0.0.1:18001/*"'

"$KCADM" update "users/$user_uuid" \
  -r "$REALM" \
  -s 'email=oidc-local-admin@example.test' \
  -s 'emailVerified=true' \
  -s 'requiredActions=[]'

"$KCADM" set-password \
  -r "$REALM" \
  --username oidc-local-admin \
  --new-password "$LOCAL_OIDC_USER_PASSWORD" \
  --temporary=false

echo "Local Keycloak acceptance client synchronized"
