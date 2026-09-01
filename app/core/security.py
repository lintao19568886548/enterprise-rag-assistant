"""OIDC, service-account and development API-key authentication with RBAC."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Annotated, Literal, cast

from fastapi import Depends, Header, Request
from fastapi.concurrency import run_in_threadpool

from app.core.errors import AppError, ErrorCode
from app.core.logger import logger
from app.core.metrics import AUTH_EVENTS
from app.core.oidc import OIDCTokenError, decode_oidc_token
from app.core.oidc_flow import resolve_browser_session
from app.core.oidc_session import OIDCSessionError, oidc_session_manager
from app.core.settings import settings
from app.core.tenant_context import set_identity_context
from app.db.identity_repositories import (
    ENTERPRISE_ROLES,
    get_active_membership,
    get_service_account,
    resolve_oidc_membership,
    touch_service_account,
)
from app.db.repositories import DEFAULT_TENANT_ID, DEFAULT_USER_ID

EnterpriseRole = Literal[
    "platform_admin",
    "tenant_admin",
    "kb_manager",
    "editor",
    "viewer",
    "auditor",
]
Permission = Literal[
    "read",
    "query",
    "write_document",
    "manage_kb",
    "manage_tenant",
    "read_audit",
]

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "platform_admin": frozenset(
        {"read", "query", "write_document", "manage_kb", "manage_tenant", "read_audit"}
    ),
    "tenant_admin": frozenset(
        {"read", "query", "write_document", "manage_kb", "manage_tenant", "read_audit"}
    ),
    "kb_manager": frozenset({"read", "query", "write_document", "manage_kb"}),
    "editor": frozenset({"read", "query", "write_document"}),
    "viewer": frozenset({"read", "query"}),
    "auditor": frozenset({"read_audit"}),
}
LEGACY_ROLE_MAP: dict[str, EnterpriseRole] = {
    "admin": "tenant_admin",
    "user": "editor",
    "readonly": "viewer",
}


@dataclass(frozen=True)
class Principal:
    subject: str
    role: EnterpriseRole
    user_id: str
    tenant_id: str
    department_id: str | None = None
    authentication_method: str = "oidc"
    service_account_id: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role in {"platform_admin", "tenant_admin"}

    def has_permission(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS[self.role]


def hash_service_account_secret(secret: str, *, iterations: int = 310_000) -> str:
    if len(secret) < 32:
        raise ValueError("service account secret must be at least 32 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)
    return "$".join(
        (
            "pbkdf2-sha256",
            str(iterations),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_service_account_secret(secret: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if algorithm != "pbkdf2-sha256":
            return False
        iterations = int(raw_iterations)
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        salt = base64.urlsafe_b64decode(raw_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(raw_digest.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _resolve_legacy_api_key(api_key: str) -> Principal | None:
    for legacy_role in ("admin", "user", "readonly"):
        for configured in settings.api_keys_for_role(legacy_role):
            if secrets.compare_digest(api_key, configured):
                return Principal(
                    subject=f"api-key:{legacy_role}",
                    role=LEGACY_ROLE_MAP[legacy_role],
                    user_id=DEFAULT_USER_ID,
                    tenant_id=DEFAULT_TENANT_ID,
                    authentication_method="development_api_key",
                )
    return None


def _resolve_service_account_api_key(api_key: str) -> Principal | None:
    if not api_key.startswith("sa_"):
        return None
    try:
        account_id, secret = api_key[3:].split("_", 1)
    except ValueError:
        return None
    if len(account_id) != 36 or len(secret) < 32:
        return None
    account = get_service_account(account_id)
    if account is None or not verify_service_account_secret(secret, account.secret_hash):
        return None
    if account.role not in ENTERPRISE_ROLES:
        return None
    touch_service_account(account.id)
    return Principal(
        subject=f"service-account:{account.id}",
        role=cast(EnterpriseRole, account.role),
        user_id=account.id,
        tenant_id=account.tenant_id,
        authentication_method="service_account",
        service_account_id=account.id,
    )


def _unauthorized(message: str = "身份认证失败") -> AppError:
    return AppError(ErrorCode.PERMISSION_DENIED, message, status_code=401)


def _record_auth(method: str, outcome: str, reason: str, principal: Principal | None = None) -> None:
    safe_reason = reason if reason in {"ok", "invalid", "disabled", "missing", "forbidden"} else "invalid"
    AUTH_EVENTS.labels(method, outcome, safe_reason).inc()
    logger.bind(
        event_type=f"login.{outcome}",
        authentication_method=method,
        reason=safe_reason,
        tenant_id=getattr(principal, "tenant_id", None),
        user_id=getattr(principal, "user_id", None),
    ).info("authentication_event")


async def _resolve_oidc_principal(token: str, authentication_method: str) -> Principal:
    try:
        oidc_claims = await run_in_threadpool(decode_oidc_token, token)
    except OIDCTokenError as exc:
        _record_auth(authentication_method, "failure", "invalid")
        raise _unauthorized() from exc
    set_identity_context(
        tenant_id=None,
        user_id=None,
        oidc_subject=oidc_claims.subject,
        oidc_issuer=oidc_claims.issuer,
    )
    identity = await run_in_threadpool(
        resolve_oidc_membership,
        oidc_claims.issuer,
        oidc_claims.subject,
    )
    if identity is None:
        _record_auth(authentication_method, "failure", "disabled")
        raise _unauthorized("当前企业身份尚未开通或已被禁用")
    user, membership, tenant = identity
    if membership.role not in ENTERPRISE_ROLES:
        _record_auth(authentication_method, "failure", "forbidden")
        raise _unauthorized("当前企业角色无效")
    return Principal(
        subject=oidc_claims.subject,
        role=cast(EnterpriseRole, membership.role),
        user_id=user.id,
        tenant_id=tenant.id,
        department_id=membership.department_id,
        authentication_method=authentication_method,
    )


async def _authenticate(
    request: Request,
    authorization: str | None,
    x_api_key: str | None,
) -> Principal:
    if not settings.auth_enabled:
        principal = Principal(
            subject="development",
            role="platform_admin",
            user_id=DEFAULT_USER_ID,
            tenant_id=DEFAULT_TENANT_ID,
            authentication_method="development",
        )
    elif authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token or not settings.oidc_enabled:
            _record_auth("oidc", "failure", "invalid")
            raise _unauthorized()
        principal = await _resolve_oidc_principal(token, "oidc")
    elif x_api_key:
        candidate: Principal | None = await run_in_threadpool(
            _resolve_service_account_api_key,
            x_api_key,
        )
        if candidate is None and not settings.is_production:
            candidate = _resolve_legacy_api_key(x_api_key)
        if candidate is None:
            _record_auth("service_account", "failure", "invalid")
            raise _unauthorized()
        principal = candidate
    elif session_id := request.cookies.get(settings.oidc_session_cookie_name):
        if not settings.oidc_enabled:
            _record_auth("oidc_session", "failure", "disabled")
            raise _unauthorized()
        try:
            browser_session = await run_in_threadpool(
                resolve_browser_session,
                session_id,
                manager=oidc_session_manager,
                config=settings,
            )
        except (OIDCTokenError, OIDCSessionError) as exc:
            _record_auth("oidc_session", "failure", "invalid")
            raise _unauthorized("当前登录会话需要重新认证") from exc
        principal = await _resolve_oidc_principal(browser_session.access_token, "oidc_session")
    else:
        _record_auth("unknown", "failure", "missing")
        raise _unauthorized("缺少 Bearer Token 或服务账户密钥")

    set_identity_context(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        oidc_subject=principal.subject if principal.authentication_method.startswith("oidc") else None,
        oidc_issuer=(settings.oidc_issuer_url or "").rstrip("/")
        if principal.authentication_method.startswith("oidc")
        else None,
    )
    if settings.database_enabled and principal.authentication_method != "service_account":
        from app.db.repositories import get_user

        user_record = await run_in_threadpool(get_user, principal.user_id, principal.tenant_id)
        membership_record = await run_in_threadpool(
            get_active_membership,
            principal.user_id,
            principal.tenant_id,
        )
        if user_record is None or not user_record.enabled or membership_record is None:
            _record_auth(principal.authentication_method, "failure", "disabled", principal)
            raise _unauthorized("当前身份已被禁用")
    request.state.principal = principal
    _record_auth(principal.authentication_method, "success", "ok", principal)
    return principal


def require_permission(permission: Permission):
    async def dependency(
        request: Request,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> Principal:
        principal = await _authenticate(request, authorization, x_api_key)
        if not principal.has_permission(permission):
            _record_auth(principal.authentication_method, "failure", "forbidden", principal)
            raise AppError(
                ErrorCode.PERMISSION_DENIED,
                "当前身份没有执行该操作的权限",
                status_code=403,
            )
        return principal

    return dependency


async def require_authenticated(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Principal:
    return await _authenticate(request, authorization, x_api_key)


RequireReadonly = Annotated[Principal, Depends(require_permission("read"))]
RequireUser = Annotated[Principal, Depends(require_permission("query"))]
RequireEditor = Annotated[Principal, Depends(require_permission("write_document"))]
RequireAdmin = Annotated[Principal, Depends(require_permission("manage_kb"))]
RequireTenantAdmin = Annotated[Principal, Depends(require_permission("manage_tenant"))]
RequireAuditor = Annotated[Principal, Depends(require_permission("read_audit"))]
RequireAuthenticated = Annotated[Principal, Depends(require_authenticated)]
