"""Provision the deterministic Keycloak test subject into the default tenant."""

from __future__ import annotations

from app.db.identity_repositories import (
    create_user_membership,
    list_memberships,
    resolve_oidc_membership,
)
from app.db.repositories import DEFAULT_TENANT_ID, ensure_defaults
from app.db.session import init_database

LOCAL_ISSUER = "http://127.0.0.1:8080/realms/enterprise-local"
LOCAL_SUBJECT = "11111111-1111-4111-8111-111111111111"
LOCAL_USERNAME = "oidc-local-admin"


def main() -> int:
    init_database()
    ensure_defaults()
    if resolve_oidc_membership(LOCAL_ISSUER, LOCAL_SUBJECT) is not None:
        print("Local OIDC identity is already provisioned.")
        return 0
    if any(user.username == LOCAL_USERNAME for _membership, user in list_memberships(DEFAULT_TENANT_ID)):
        raise SystemExit("Local OIDC username already exists with a different identity")
    create_user_membership(
        tenant_id=DEFAULT_TENANT_ID,
        username=LOCAL_USERNAME,
        display_name="Local OIDC Administrator",
        role="tenant_admin",
        external_identity_id=LOCAL_SUBJECT,
        oidc_issuer=LOCAL_ISSUER,
    )
    print("Local OIDC identity provisioned in the default tenant.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
