import uuid

from app.db.identity_repositories import (
    create_knowledge_base_grant,
    create_tenant,
    create_user_membership,
    resolve_oidc_membership,
)
from app.db.repositories import create_knowledge_base, get_accessible_knowledge_base
from app.db.session import init_database


def test_oidc_subject_resolves_to_server_owned_membership():
    init_database()
    suffix = uuid.uuid4().hex
    tenant = create_tenant(f"oidc-{suffix}", "OIDC Tenant")
    user, membership = create_user_membership(
        tenant_id=tenant.id,
        username=f"employee-{suffix}",
        role="viewer",
        external_identity_id=f"subject-{suffix}",
        oidc_issuer="https://id.example.com/realms/enterprise",
    )

    resolved = resolve_oidc_membership(
        "https://id.example.com/realms/enterprise",
        f"subject-{suffix}",
    )
    assert resolved is not None
    resolved_user, resolved_membership, resolved_tenant = resolved
    assert resolved_user.id == user.id
    assert resolved_membership.id == membership.id
    assert resolved_tenant.id == tenant.id
    assert resolve_oidc_membership("https://attacker.invalid", f"subject-{suffix}") is None


def test_private_knowledge_base_grant_is_enforced_server_side():
    init_database()
    suffix = uuid.uuid4().hex
    tenant = create_tenant(f"grant-{suffix}", "Grant Tenant")
    owner, _ = create_user_membership(
        tenant_id=tenant.id,
        username=f"owner-{suffix}",
        role="kb_manager",
    )
    viewer, _ = create_user_membership(
        tenant_id=tenant.id,
        username=f"viewer-{suffix}",
        role="viewer",
    )
    knowledge_base = create_knowledge_base(
        f"private-{suffix}",
        "",
        "private",
        owner_id=owner.id,
        tenant_id=tenant.id,
    )
    assert (
        get_accessible_knowledge_base(
            knowledge_base.id,
            tenant.id,
            viewer.id,
            write=False,
        )
        is None
    )
    create_knowledge_base_grant(
        tenant_id=tenant.id,
        knowledge_base_id=knowledge_base.id,
        subject_type="user",
        subject_id=viewer.id,
        permission="read",
        granted_by=owner.id,
    )
    assert (
        get_accessible_knowledge_base(
            knowledge_base.id,
            tenant.id,
            viewer.id,
            write=False,
        )
        is not None
    )
    assert (
        get_accessible_knowledge_base(
            knowledge_base.id,
            tenant.id,
            viewer.id,
            write=True,
        )
        is None
    )
