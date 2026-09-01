"""Request-local identity context shared with repositories and PostgreSQL RLS."""

from __future__ import annotations

from contextvars import ContextVar, Token
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass


_tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)
_oidc_subject: ContextVar[str | None] = ContextVar("oidc_subject", default=None)
_oidc_issuer: ContextVar[str | None] = ContextVar("oidc_issuer", default=None)


@dataclass(frozen=True)
class ContextTokens:
    tenant_id: Token[str | None]
    user_id: Token[str | None]
    oidc_subject: Token[str | None]
    oidc_issuer: Token[str | None]


def set_identity_context(
    *,
    tenant_id: str | None,
    user_id: str | None,
    oidc_subject: str | None = None,
    oidc_issuer: str | None = None,
) -> ContextTokens:
    return ContextTokens(
        tenant_id=_tenant_id.set(tenant_id),
        user_id=_user_id.set(user_id),
        oidc_subject=_oidc_subject.set(oidc_subject),
        oidc_issuer=_oidc_issuer.set(oidc_issuer),
    )


def reset_identity_context(tokens: ContextTokens) -> None:
    _oidc_issuer.reset(tokens.oidc_issuer)
    _oidc_subject.reset(tokens.oidc_subject)
    _user_id.reset(tokens.user_id)
    _tenant_id.reset(tokens.tenant_id)


def clear_identity_context() -> None:
    _tenant_id.set(None)
    _user_id.set(None)
    _oidc_subject.set(None)
    _oidc_issuer.set(None)


def current_identity_context() -> dict[str, str | None]:
    return {
        "tenant_id": _tenant_id.get(),
        "user_id": _user_id.get(),
        "oidc_subject": _oidc_subject.get(),
        "oidc_issuer": _oidc_issuer.get(),
    }


@contextmanager
def identity_context(
    *,
    tenant_id: str | None,
    user_id: str | None,
    oidc_subject: str | None = None,
    oidc_issuer: str | None = None,
) -> Iterator[None]:
    tokens = set_identity_context(
        tenant_id=tenant_id,
        user_id=user_id,
        oidc_subject=oidc_subject,
        oidc_issuer=oidc_issuer,
    )
    try:
        yield
    finally:
        reset_identity_context(tokens)
