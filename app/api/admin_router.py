"""Enterprise administration APIs. Tenant scope always comes from Principal."""

from __future__ import annotations

import secrets
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError, ErrorCode
from app.core.security import (
    RequireAuditor,
    RequireTenantAdmin,
    hash_service_account_secret,
)
from app.core.settings import settings
from app.db.identity_repositories import (
    add_audit_log,
    create_department,
    create_knowledge_base_grant,
    create_service_account,
    create_tenant,
    create_user_membership,
    list_audit_logs,
    list_departments,
    list_memberships,
    list_service_accounts,
    list_tenants,
    revoke_service_account,
    update_membership,
)


router = APIRouter(prefix="/admin", tags=["enterprise-administration"])


class TenantCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
    name: str = Field(min_length=1, max_length=255)


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    parent_id: str | None = Field(default=None, max_length=36)


EnterpriseRole = Literal[
    "platform_admin",
    "tenant_admin",
    "kb_manager",
    "editor",
    "viewer",
    "auditor",
]


class MembershipCreate(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    oidc_subject: str | None = Field(default=None, min_length=1, max_length=255)
    oidc_issuer: str | None = Field(default=None, max_length=1024)
    department_id: str | None = Field(default=None, max_length=36)
    role: EnterpriseRole = "viewer"


class MembershipUpdate(BaseModel):
    role: EnterpriseRole | None = None
    enabled: bool | None = None
    department_id: str | None = Field(default=None, max_length=36)


class KnowledgeBaseGrantCreate(BaseModel):
    knowledge_base_id: str = Field(min_length=1, max_length=36)
    subject_type: Literal["user", "department", "role"]
    subject_id: str = Field(min_length=1, max_length=128)
    permission: Literal["read", "write", "manage"]


class ServiceAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    role: Literal["tenant_admin", "kb_manager", "editor", "viewer", "auditor"] = "viewer"


def _audit(
    request: Request,
    principal,
    event_type: str,
    outcome: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> None:
    add_audit_log(
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_type="service_account" if principal.service_account_id else "user",
        event_type=event_type,
        outcome=outcome,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=getattr(request.state, "request_id", None),
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/me")
async def current_principal(principal: RequireTenantAdmin):
    return {
        "subject": principal.subject,
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        "department_id": principal.department_id,
        "role": principal.role,
        "authentication_method": principal.authentication_method,
    }


@router.get("/tenants")
async def get_tenants(principal: RequireTenantAdmin):
    records = list_tenants()
    if principal.role != "platform_admin":
        records = [record for record in records if record.id == principal.tenant_id]
    return {
        "items": [
            {
                "id": record.id,
                "slug": record.slug,
                "name": record.name,
                "enabled": record.enabled,
                "created_at": record.created_at,
            }
            for record in records
        ]
    }


@router.post("/tenants", status_code=201)
async def post_tenant(payload: TenantCreate, request: Request, principal: RequireTenantAdmin):
    if principal.role != "platform_admin":
        raise AppError(ErrorCode.PERMISSION_DENIED, "只有平台管理员可以创建租户", status_code=403)
    try:
        record = create_tenant(payload.slug, payload.name.strip())
    except IntegrityError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, "租户标识已经存在", status_code=409) from exc
    _audit(request, principal, "tenant.created", "success", "tenant", record.id)
    return {"id": record.id, "slug": record.slug, "name": record.name, "enabled": record.enabled}


@router.get("/departments")
async def get_departments(principal: RequireTenantAdmin):
    return {
        "items": [
            {
                "id": record.id,
                "tenant_id": record.tenant_id,
                "parent_id": record.parent_id,
                "name": record.name,
                "description": record.description,
                "enabled": record.enabled,
            }
            for record in list_departments(principal.tenant_id)
        ]
    }


@router.post("/departments", status_code=201)
async def post_department(
    payload: DepartmentCreate,
    request: Request,
    principal: RequireTenantAdmin,
):
    try:
        record = create_department(
            principal.tenant_id,
            payload.name.strip(),
            payload.description.strip(),
            payload.parent_id,
        )
    except LookupError as exc:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "上级部门不存在", status_code=404) from exc
    except IntegrityError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, "部门名称已经存在", status_code=409) from exc
    _audit(request, principal, "department.created", "success", "department", record.id)
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "parent_id": record.parent_id,
        "name": record.name,
        "description": record.description,
        "enabled": record.enabled,
    }


@router.get("/memberships")
async def get_members(principal: RequireTenantAdmin):
    return {
        "items": [
            {
                "id": membership.id,
                "tenant_id": membership.tenant_id,
                "user_id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "email": user.email,
                "department_id": membership.department_id,
                "role": membership.role,
                "enabled": membership.enabled and user.enabled,
            }
            for membership, user in list_memberships(principal.tenant_id)
        ]
    }


@router.post("/memberships", status_code=201)
async def post_member(payload: MembershipCreate, request: Request, principal: RequireTenantAdmin):
    if payload.role == "platform_admin" and principal.role != "platform_admin":
        raise AppError(ErrorCode.PERMISSION_DENIED, "只有平台管理员可以授予平台角色", status_code=403)
    issuer = payload.oidc_issuer or ((settings.oidc_issuer_url or "").rstrip("/") or None)
    try:
        user, membership = create_user_membership(
            tenant_id=principal.tenant_id,
            username=payload.username.strip(),
            role=payload.role,
            external_identity_id=payload.oidc_subject,
            oidc_issuer=issuer if payload.oidc_subject else None,
            email=payload.email,
            display_name=payload.display_name,
            department_id=payload.department_id,
        )
    except LookupError as exc:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "租户或部门不存在", status_code=404) from exc
    except (IntegrityError, ValueError) as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, "成员信息无效或已经存在", status_code=409) from exc
    _audit(request, principal, "membership.created", "success", "membership", membership.id)
    return {
        "id": membership.id,
        "user_id": user.id,
        "tenant_id": membership.tenant_id,
        "department_id": membership.department_id,
        "role": membership.role,
        "enabled": membership.enabled,
    }


@router.patch("/memberships/{membership_id}")
async def patch_member(
    membership_id: str,
    payload: MembershipUpdate,
    request: Request,
    principal: RequireTenantAdmin,
):
    if payload.role == "platform_admin" and principal.role != "platform_admin":
        raise AppError(ErrorCode.PERMISSION_DENIED, "只有平台管理员可以授予平台角色", status_code=403)
    try:
        record = update_membership(
            membership_id,
            principal.tenant_id,
            role=payload.role,
            enabled=payload.enabled,
            department_id=payload.department_id,
        )
    except LookupError as exc:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "成员关系不存在", status_code=404) from exc
    _audit(request, principal, "membership.updated", "success", "membership", record.id)
    if payload.enabled is False:
        _audit(request, principal, "user.disabled", "success", "membership", record.id)
    if payload.role is not None:
        _audit(request, principal, "permission.changed", "success", "membership", record.id)
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "department_id": record.department_id,
        "role": record.role,
        "enabled": record.enabled,
    }


@router.post("/knowledge-base-grants", status_code=201)
async def post_grant(
    payload: KnowledgeBaseGrantCreate,
    request: Request,
    principal: RequireTenantAdmin,
):
    try:
        record = create_knowledge_base_grant(
            tenant_id=principal.tenant_id,
            knowledge_base_id=payload.knowledge_base_id,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            permission=payload.permission,
            granted_by=principal.user_id,
        )
    except LookupError as exc:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识库不存在", status_code=404) from exc
    except IntegrityError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, "该授权已经存在", status_code=409) from exc
    _audit(request, principal, "knowledge_base.grant_created", "success", "knowledge_base", record.knowledge_base_id)
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "knowledge_base_id": record.knowledge_base_id,
        "subject_type": record.subject_type,
        "subject_id": record.subject_id,
        "permission": record.permission,
    }


@router.post("/service-accounts", status_code=201)
async def post_service_account(
    payload: ServiceAccountCreate,
    request: Request,
    principal: RequireTenantAdmin,
):
    raw_secret = secrets.token_urlsafe(36)
    try:
        record = create_service_account(
            principal.tenant_id,
            payload.name.strip(),
            hash_service_account_secret(raw_secret),
            payload.role,
        )
    except IntegrityError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, "服务账户名称已经存在", status_code=409) from exc
    _audit(request, principal, "service_account.created", "success", "service_account", record.id)
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "name": record.name,
        "role": record.role,
        "api_key": f"sa_{record.id}_{raw_secret}",
        "warning": "此密钥只显示一次，请立即存入安全的密钥管理系统",
    }


@router.get("/service-accounts")
async def get_service_accounts(principal: RequireTenantAdmin):
    return {
        "items": [
            {
                "id": record.id,
                "tenant_id": record.tenant_id,
                "name": record.name,
                "role": record.role,
                "enabled": record.enabled,
                "expires_at": record.expires_at,
                "last_used_at": record.last_used_at,
                "revoked_at": record.revoked_at,
                "created_at": record.created_at,
            }
            for record in list_service_accounts(principal.tenant_id)
        ]
    }


@router.delete("/service-accounts/{account_id}")
async def delete_service_account(
    account_id: str,
    request: Request,
    principal: RequireTenantAdmin,
    confirm: bool = Query(default=False),
):
    if not confirm:
        raise AppError(ErrorCode.VALIDATION_ERROR, "撤销服务账户必须传入 confirm=true", status_code=409)
    try:
        record = revoke_service_account(account_id, principal.tenant_id)
    except LookupError as exc:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "服务账户不存在", status_code=404) from exc
    _audit(request, principal, "service_account.revoked", "success", "service_account", record.id)
    return {"id": record.id, "revoked": True, "revoked_at": record.revoked_at}


@router.get("/audit-logs")
async def get_audit_logs(
    principal: RequireAuditor,
    limit: int = Query(default=100, ge=1, le=500),
):
    return {
        "items": [
            {
                "id": record.id,
                "tenant_id": record.tenant_id,
                "actor_id": record.actor_id,
                "actor_type": record.actor_type,
                "event_type": record.event_type,
                "outcome": record.outcome,
                "resource_type": record.resource_type,
                "resource_id": record.resource_id,
                "metadata": record.metadata_json,
                "request_id": record.request_id,
                "trace_id": record.trace_id,
                "created_at": record.created_at,
            }
            for record in list_audit_logs(principal.tenant_id, limit)
        ]
    }
