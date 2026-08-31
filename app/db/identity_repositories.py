"""Repositories for enterprise identity, grants, service accounts and audit logs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select

from app.db.models import (
    AuditLog,
    Department,
    KnowledgeBase,
    KnowledgeBaseGrant,
    Membership,
    ServiceAccount,
    Tenant,
    User,
)
from app.db.session import apply_postgres_rls_context, session_scope


ENTERPRISE_ROLES = {
    "platform_admin",
    "tenant_admin",
    "kb_manager",
    "editor",
    "viewer",
    "auditor",
}
GRANT_SUBJECT_TYPES = {"user", "department", "role"}
GRANT_PERMISSIONS = {"read", "write", "manage"}
PERMISSION_LEVEL = {"read": 10, "write": 20, "manage": 30}


def resolve_oidc_membership(
    issuer: str,
    subject: str,
) -> tuple[User, Membership, Tenant] | None:
    """Resolve an OIDC subject to its server-owned active tenant context."""
    with session_scope() as session:
        user = session.scalar(
            select(User).where(
                User.oidc_issuer == issuer,
                User.external_identity_id == subject,
                User.enabled.is_(True),
            )
        )
        if user is None:
            return None
        apply_postgres_rls_context(
            session.connection(),
            {
                "tenant_id": user.tenant_id,
                "user_id": user.id,
                "oidc_subject": subject,
                "oidc_issuer": issuer,
            },
        )
        membership = session.scalar(
            select(Membership)
            .where(
                Membership.tenant_id == user.tenant_id,
                Membership.user_id == user.id,
                Membership.enabled.is_(True),
            )
            .order_by(Membership.created_at.asc())
            .limit(1)
        )
        tenant = session.get(Tenant, user.tenant_id)
        if membership is None or tenant is None or not tenant.enabled:
            return None
        return user, membership, tenant


def get_active_membership(user_id: str, tenant_id: str) -> Membership | None:
    with session_scope() as session:
        return session.scalar(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.tenant_id == tenant_id,
                Membership.enabled.is_(True),
            )
        )


def list_tenants() -> list[Tenant]:
    with session_scope() as session:
        return list(session.scalars(select(Tenant).order_by(Tenant.created_at.desc())))


def create_tenant(slug: str, name: str) -> Tenant:
    with session_scope() as session:
        record = Tenant(slug=slug, name=name, enabled=True)
        session.add(record)
        session.flush()
        return record


def list_departments(tenant_id: str) -> list[Department]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(Department)
                .where(Department.tenant_id == tenant_id)
                .order_by(Department.name.asc())
            )
        )


def create_department(
    tenant_id: str,
    name: str,
    description: str = "",
    parent_id: str | None = None,
) -> Department:
    with session_scope() as session:
        if parent_id is not None:
            parent = session.get(Department, parent_id)
            if parent is None or parent.tenant_id != tenant_id:
                raise LookupError("parent department not found")
        record = Department(
            tenant_id=tenant_id,
            parent_id=parent_id,
            name=name,
            description=description,
            enabled=True,
        )
        session.add(record)
        session.flush()
        return record


def list_memberships(tenant_id: str) -> list[tuple[Membership, User]]:
    with session_scope() as session:
        rows = session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.tenant_id == tenant_id)
            .order_by(User.username.asc())
        ).all()
        return [(row[0], row[1]) for row in rows]


def create_user_membership(
    *,
    tenant_id: str,
    username: str,
    role: str,
    external_identity_id: str | None = None,
    oidc_issuer: str | None = None,
    email: str | None = None,
    display_name: str | None = None,
    department_id: str | None = None,
) -> tuple[User, Membership]:
    if role not in ENTERPRISE_ROLES:
        raise ValueError("invalid enterprise role")
    if bool(external_identity_id) != bool(oidc_issuer):
        raise ValueError("OIDC issuer and subject must be provided together")
    with session_scope() as session:
        tenant = session.get(Tenant, tenant_id)
        if tenant is None:
            raise LookupError("tenant not found")
        if department_id:
            department = session.get(Department, department_id)
            if department is None or department.tenant_id != tenant_id:
                raise LookupError("department not found")
        user = User(
            tenant_id=tenant_id,
            username=username,
            email=email,
            display_name=display_name,
            oidc_issuer=oidc_issuer,
            external_identity_id=external_identity_id,
            role="user",
            enabled=True,
        )
        session.add(user)
        session.flush()
        membership = Membership(
            tenant_id=tenant_id,
            user_id=user.id,
            department_id=department_id,
            role=role,
            enabled=True,
        )
        session.add(membership)
        session.flush()
        return user, membership


def update_membership(
    membership_id: str,
    tenant_id: str,
    *,
    role: str | None = None,
    enabled: bool | None = None,
    department_id: str | None = None,
) -> Membership:
    if role is not None and role not in ENTERPRISE_ROLES:
        raise ValueError("invalid enterprise role")
    with session_scope() as session:
        record = session.get(Membership, membership_id)
        if record is None or record.tenant_id != tenant_id:
            raise LookupError("membership not found")
        if department_id is not None:
            department = session.get(Department, department_id)
            if department is None or department.tenant_id != tenant_id:
                raise LookupError("department not found")
            record.department_id = department_id
        if role is not None:
            record.role = role
        if enabled is not None:
            record.enabled = enabled
        session.flush()
        return record


def create_knowledge_base_grant(
    *,
    tenant_id: str,
    knowledge_base_id: str,
    subject_type: str,
    subject_id: str,
    permission: str,
    granted_by: str,
) -> KnowledgeBaseGrant:
    if subject_type not in GRANT_SUBJECT_TYPES:
        raise ValueError("invalid grant subject type")
    if permission not in GRANT_PERMISSIONS:
        raise ValueError("invalid grant permission")
    with session_scope() as session:
        knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
        if knowledge_base is None or knowledge_base.tenant_id != tenant_id:
            raise LookupError("knowledge base not found")
        record = KnowledgeBaseGrant(
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            subject_type=subject_type,
            subject_id=subject_id,
            permission=permission,
            granted_by=granted_by,
        )
        session.add(record)
        session.flush()
        return record


def has_knowledge_base_grant(
    *,
    tenant_id: str,
    knowledge_base_id: str,
    user_id: str,
    role: str,
    department_id: str | None,
    required_permission: str,
) -> bool:
    required_level = PERMISSION_LEVEL[required_permission]
    subject_predicates = [
        (KnowledgeBaseGrant.subject_type == "user")
        & (KnowledgeBaseGrant.subject_id == user_id),
        (KnowledgeBaseGrant.subject_type == "role")
        & (KnowledgeBaseGrant.subject_id == role),
    ]
    if department_id:
        subject_predicates.append(
            (KnowledgeBaseGrant.subject_type == "department")
            & (KnowledgeBaseGrant.subject_id == department_id)
        )
    with session_scope() as session:
        permissions = session.scalars(
            select(KnowledgeBaseGrant.permission).where(
                KnowledgeBaseGrant.tenant_id == tenant_id,
                KnowledgeBaseGrant.knowledge_base_id == knowledge_base_id,
                or_(*subject_predicates),
            )
        )
        return any(PERMISSION_LEVEL.get(permission, 0) >= required_level for permission in permissions)


def get_service_account(account_id: str) -> ServiceAccount | None:
    with session_scope() as session:
        record = session.get(ServiceAccount, account_id)
        if (
            record is None
            or not record.enabled
            or record.revoked_at is not None
            or (record.expires_at is not None and record.expires_at <= datetime.now(UTC))
        ):
            return None
        return record


def create_service_account(
    tenant_id: str,
    name: str,
    secret_hash: str,
    role: str,
) -> ServiceAccount:
    if role not in ENTERPRISE_ROLES - {"platform_admin"}:
        raise ValueError("invalid service account role")
    with session_scope() as session:
        record = ServiceAccount(
            tenant_id=tenant_id,
            name=name,
            secret_hash=secret_hash,
            role=role,
            enabled=True,
        )
        session.add(record)
        session.flush()
        return record


def touch_service_account(account_id: str) -> None:
    with session_scope() as session:
        record = session.get(ServiceAccount, account_id)
        if record is not None:
            record.last_used_at = datetime.now(UTC)


def add_audit_log(
    *,
    tenant_id: str,
    actor_id: str | None,
    actor_type: str,
    event_type: str,
    outcome: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> None:
    with session_scope() as session:
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                actor_type=actor_type,
                event_type=event_type,
                outcome=outcome,
                resource_type=resource_type,
                resource_id=resource_id,
                metadata_json=metadata or {},
                request_id=request_id,
                trace_id=trace_id,
            )
        )


def list_audit_logs(tenant_id: str, limit: int = 100) -> list[AuditLog]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(AuditLog)
                .where(AuditLog.tenant_id == tenant_id)
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
            )
        )
