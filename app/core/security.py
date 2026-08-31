"""Small API-key/RBAC foundation that can later be replaced by SSO/JWT."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, Header

from app.core.errors import AppError, ErrorCode
from app.core.settings import settings
from app.db.repositories import DEFAULT_TENANT_ID, DEFAULT_USER_ID

Role = Literal["readonly", "user", "admin"]
ROLE_LEVEL: dict[Role, int] = {"readonly": 10, "user": 20, "admin": 30}


@dataclass(frozen=True)
class Principal:
    subject: str
    role: Role
    user_id: str
    tenant_id: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _resolve_api_key(api_key: str) -> Principal | None:
    for role in ("admin", "user", "readonly"):
        for configured in settings.api_keys_for_role(role):
            if secrets.compare_digest(api_key, configured):
                return Principal(
                    subject=f"api-key:{role}",
                    role=role,
                    user_id=DEFAULT_USER_ID,
                    tenant_id=DEFAULT_TENANT_ID,
                )
    return None


def require_role(minimum_role: Role):
    async def dependency(
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> Principal:
        if not settings.auth_enabled:
            return Principal(
                subject="development",
                role="admin",
                user_id=DEFAULT_USER_ID,
                tenant_id=DEFAULT_TENANT_ID,
            )
        if not x_api_key:
            raise AppError(
                ErrorCode.PERMISSION_DENIED,
                "缺少 X-API-Key",
                status_code=401,
            )
        principal = _resolve_api_key(x_api_key)
        if principal is None:
            raise AppError(
                ErrorCode.PERMISSION_DENIED,
                "API Key 无效",
                status_code=401,
            )
        if settings.database_enabled:
            from app.db.repositories import get_user

            user = get_user(principal.user_id, principal.tenant_id)
            if user is None or not user.enabled:
                raise AppError(
                    ErrorCode.PERMISSION_DENIED,
                    "当前身份已被禁用",
                    status_code=401,
                )
        if ROLE_LEVEL[principal.role] < ROLE_LEVEL[minimum_role]:
            raise AppError(
                ErrorCode.PERMISSION_DENIED,
                "当前身份没有执行该操作的权限",
                status_code=403,
            )
        return principal

    return dependency


RequireReadonly = Annotated[Principal, Depends(require_role("readonly"))]
RequireUser = Annotated[Principal, Depends(require_role("user"))]
RequireAdmin = Annotated[Principal, Depends(require_role("admin"))]
