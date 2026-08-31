"""Browser-facing OIDC Authorization Code + PKCE endpoints."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.errors import AppError, ErrorCode
from app.core.oidc import OIDCTokenError, decode_oidc_id_token, decode_oidc_token
from app.core.oidc_flow import (
    browser_session_from_tokens,
    build_authorization_request,
    build_end_session_url,
    exchange_authorization_code,
    resolve_browser_session,
)
from app.core.oidc_session import OIDCSessionError, oidc_session_manager
from app.core.security import RequireAuthenticated
from app.core.settings import settings
from app.db.identity_repositories import resolve_oidc_membership

router = APIRouter(prefix="/auth", tags=["enterprise-authentication"])


def _authentication_error(message: str = "企业身份认证失败") -> AppError:
    return AppError(ErrorCode.PERMISSION_DENIED, message, status_code=401)


def _set_session_cookie(response: RedirectResponse, session_id: str) -> None:
    response.set_cookie(
        settings.oidc_session_cookie_name,
        session_id,
        max_age=settings.oidc_session_ttl_seconds,
        secure=settings.is_deployed,
        httponly=True,
        samesite="strict",
        path="/",
    )


def _delete_auth_cookies(response: JSONResponse | RedirectResponse) -> None:
    response.delete_cookie(settings.oidc_session_cookie_name, path="/")
    response.delete_cookie(settings.oidc_state_cookie_name, path="/auth/callback")


@router.get("/config")
async def oidc_public_config() -> dict[str, object]:
    return {
        "enabled": settings.oidc_enabled,
        "login_url": "/auth/login" if settings.oidc_enabled else None,
        "logout_url": "/auth/logout" if settings.oidc_enabled else None,
    }


@router.get("/me")
async def oidc_current_identity(principal: RequireAuthenticated) -> dict[str, object]:
    return {
        "authenticated": True,
        "user_id": principal.user_id,
        "tenant_id": principal.tenant_id,
        "role": principal.role,
        "authentication_method": principal.authentication_method,
    }


@router.get("/login")
async def oidc_login(
    return_to: str | None = Query(default="/chat.html", max_length=512),
) -> RedirectResponse:
    try:
        authorization = await run_in_threadpool(
            build_authorization_request,
            return_to,
            manager=oidc_session_manager,
            config=settings,
        )
    except (OIDCTokenError, OIDCSessionError) as exc:
        raise _authentication_error("企业登录暂不可用") from exc
    response = RedirectResponse(authorization.url, status_code=302)
    response.set_cookie(
        settings.oidc_state_cookie_name,
        authorization.transaction.state,
        max_age=settings.oidc_transaction_ttl_seconds,
        secure=settings.is_deployed,
        httponly=True,
        samesite="lax",
        path="/auth/callback",
    )
    return response


@router.get("/callback")
async def oidc_callback(
    request: Request,
    code: str | None = Query(default=None, max_length=8192),
    state: str | None = Query(default=None, max_length=512),
    error: str | None = Query(default=None, max_length=128),
) -> RedirectResponse:
    cookie_state = request.cookies.get(settings.oidc_state_cookie_name)
    if error or not code or not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
        raise _authentication_error()
    try:
        transaction = await run_in_threadpool(
            oidc_session_manager.consume_login_transaction,
            state,
        )
        tokens = await run_in_threadpool(
            exchange_authorization_code,
            code,
            transaction.code_verifier,
            config=settings,
        )
        if not tokens.id_token:
            raise OIDCTokenError("OIDC provider did not return an ID token")
        id_claims = await run_in_threadpool(
            decode_oidc_id_token,
            tokens.id_token,
            nonce=transaction.nonce,
            config=settings,
        )
        access_claims = await run_in_threadpool(
            decode_oidc_token,
            tokens.access_token,
            config=settings,
        )
        if (
            id_claims.subject != access_claims.subject
            or id_claims.issuer != access_claims.issuer
        ):
            raise OIDCTokenError("OIDC token subjects do not match")
        identity = await run_in_threadpool(
            resolve_oidc_membership,
            access_claims.issuer,
            access_claims.subject,
        )
        if identity is None:
            raise OIDCTokenError("OIDC identity is not provisioned")
        browser_session = browser_session_from_tokens(tokens)
        session_id = await run_in_threadpool(
            oidc_session_manager.create_session,
            browser_session,
        )
    except (OIDCTokenError, OIDCSessionError) as exc:
        raise _authentication_error() from exc
    response = RedirectResponse(transaction.return_to, status_code=303)
    _set_session_cookie(response, session_id)
    response.delete_cookie(settings.oidc_state_cookie_name, path="/auth/callback")
    return response


@router.post("/refresh")
async def oidc_refresh(request: Request) -> dict[str, bool]:
    session_id = request.cookies.get(settings.oidc_session_cookie_name)
    if not session_id:
        raise _authentication_error("当前登录会话不存在")
    try:
        session = await run_in_threadpool(
            resolve_browser_session,
            session_id,
            manager=oidc_session_manager,
            config=settings,
            force_refresh=True,
        )
        await run_in_threadpool(decode_oidc_token, session.access_token, config=settings)
    except (OIDCTokenError, OIDCSessionError) as exc:
        raise _authentication_error("当前登录会话需要重新认证") from exc
    return {"refreshed": True}


@router.post("/logout")
async def oidc_logout(request: Request):
    session_id = request.cookies.get(settings.oidc_session_cookie_name)
    id_token: str | None = None
    if session_id:
        try:
            session = await run_in_threadpool(oidc_session_manager.get_session, session_id)
            id_token = session.id_token
        except OIDCSessionError:
            pass
        await run_in_threadpool(oidc_session_manager.delete_session, session_id)
    logout_url: str | None = None
    if settings.oidc_enabled:
        try:
            logout_url = await run_in_threadpool(build_end_session_url, id_token, config=settings)
        except OIDCTokenError:
            logout_url = settings.oidc_post_logout_redirect_uri
    if logout_url:
        response: JSONResponse | RedirectResponse = RedirectResponse(logout_url, status_code=303)
    else:
        response = JSONResponse({"logged_out": True})
    _delete_auth_cookies(response)
    return response
