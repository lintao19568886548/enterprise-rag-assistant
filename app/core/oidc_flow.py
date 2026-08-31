"""OIDC Authorization Code + PKCE request and token-exchange helpers."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from app.core.oidc import OIDCTokenError, jwks_cache, validate_oidc_endpoint
from app.core.oidc_session import OIDCBrowserSession, OIDCLoginTransaction, OIDCSessionManager
from app.core.settings import Settings, settings

MAX_TOKEN_RESPONSE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class OIDCAuthorizationRequest:
    url: str
    transaction: OIDCLoginTransaction


@dataclass(frozen=True)
class OIDCTokenSet:
    access_token: str
    refresh_token: str | None
    id_token: str | None
    expires_in: int


def sanitize_return_to(value: str | None) -> str:
    candidate = (value or "/chat.html").strip()
    parsed = urlsplit(candidate)
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or "\\" in candidate
        or "\r" in candidate
        or "\n" in candidate
        or len(candidate) > 512
    ):
        raise OIDCTokenError("OIDC return path is invalid")
    return candidate


def _metadata_endpoint(name: str, *, config: Settings) -> str:
    metadata = jwks_cache.get_metadata(config)
    endpoint = str(metadata.get(name, ""))
    validate_oidc_endpoint(endpoint, config)
    return endpoint


def build_authorization_request(
    return_to: str | None,
    *,
    manager: OIDCSessionManager,
    config: Settings = settings,
) -> OIDCAuthorizationRequest:
    if not config.oidc_enabled or not config.oidc_client_id or not config.oidc_redirect_uri:
        raise OIDCTokenError("OIDC browser login is not configured")
    safe_return_to = sanitize_return_to(return_to)
    transaction, challenge = manager.create_login_transaction(safe_return_to)
    authorization_endpoint = _metadata_endpoint("authorization_endpoint", config=config)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": config.oidc_client_id,
            "redirect_uri": config.oidc_redirect_uri,
            "scope": " ".join(config.oidc_scope_values),
            "state": transaction.state,
            "nonce": transaction.nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return OIDCAuthorizationRequest(
        url=f"{authorization_endpoint}?{query}",
        transaction=transaction,
    )


def _request_tokens(payload: dict[str, str], *, config: Settings) -> OIDCTokenSet:
    if not config.oidc_client_id:
        raise OIDCTokenError("OIDC client is not configured")
    payload["client_id"] = config.oidc_client_id
    client_secret = config.reveal(config.oidc_client_secret)
    if client_secret:
        payload["client_secret"] = client_secret
    endpoint = _metadata_endpoint("token_endpoint", config=config)
    request = urllib.request.Request(
        endpoint,
        data=urlencode(payload).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "enterprise-rag-assistant/0.3",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=config.oidc_http_timeout_seconds) as response:
            raw = response.read(MAX_TOKEN_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise OIDCTokenError("OIDC token endpoint is unavailable") from exc
    if len(raw) > MAX_TOKEN_RESPONSE_BYTES:
        raise OIDCTokenError("OIDC token response is too large")
    try:
        response_payload: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OIDCTokenError("OIDC token response is invalid") from exc
    if not isinstance(response_payload, dict):
        raise OIDCTokenError("OIDC token response is invalid")
    access_token = response_payload.get("access_token")
    token_type = str(response_payload.get("token_type", "Bearer"))
    refresh_token = response_payload.get("refresh_token")
    id_token = response_payload.get("id_token")
    try:
        expires_in = int(response_payload.get("expires_in", 300))
    except (TypeError, ValueError) as exc:
        raise OIDCTokenError("OIDC token expiry is invalid") from exc
    if (
        not isinstance(access_token, str)
        or not access_token
        or len(access_token) > 16_384
        or token_type.casefold() != "bearer"
        or expires_in < 1
        or expires_in > 86400
    ):
        raise OIDCTokenError("OIDC token response is invalid")
    if refresh_token is not None and (not isinstance(refresh_token, str) or len(refresh_token) > 16_384):
        raise OIDCTokenError("OIDC refresh token is invalid")
    if id_token is not None and (not isinstance(id_token, str) or len(id_token) > 16_384):
        raise OIDCTokenError("OIDC ID token is invalid")
    return OIDCTokenSet(access_token, refresh_token, id_token, expires_in)


def exchange_authorization_code(
    code: str,
    code_verifier: str,
    *,
    config: Settings = settings,
) -> OIDCTokenSet:
    if not code or len(code) > 8192 or not code_verifier or not config.oidc_redirect_uri:
        raise OIDCTokenError("OIDC authorization response is invalid")
    return _request_tokens(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.oidc_redirect_uri,
            "code_verifier": code_verifier,
        },
        config=config,
    )


def refresh_oidc_tokens(refresh_token: str, *, config: Settings = settings) -> OIDCTokenSet:
    if not refresh_token or len(refresh_token) > 16_384:
        raise OIDCTokenError("OIDC refresh token is invalid")
    return _request_tokens(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(config.oidc_scope_values),
        },
        config=config,
    )


def browser_session_from_tokens(
    tokens: OIDCTokenSet,
    *,
    previous_refresh_token: str | None = None,
    clock: Any = time.time,
) -> OIDCBrowserSession:
    now = float(clock())
    return OIDCBrowserSession(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token or previous_refresh_token,
        id_token=tokens.id_token,
        expires_at=now + tokens.expires_in,
        created_at=now,
    )


def resolve_browser_session(
    session_id: str,
    *,
    manager: OIDCSessionManager,
    config: Settings = settings,
    force_refresh: bool = False,
    clock: Any = time.time,
) -> OIDCBrowserSession:
    session = manager.get_session(session_id)
    now = float(clock())
    if not force_refresh and session.expires_at > now + config.oidc_clock_skew_seconds:
        return session
    if not session.refresh_token:
        manager.delete_session(session_id)
        raise OIDCTokenError("OIDC browser session requires reauthentication")
    tokens = refresh_oidc_tokens(session.refresh_token, config=config)
    refreshed = browser_session_from_tokens(
        tokens,
        previous_refresh_token=session.refresh_token,
        clock=clock,
    )
    if refreshed.id_token is None:
        refreshed = OIDCBrowserSession(
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token,
            id_token=session.id_token,
            expires_at=refreshed.expires_at,
            created_at=session.created_at,
        )
    manager.replace_session(session_id, refreshed)
    return refreshed


def build_end_session_url(id_token: str | None, *, config: Settings = settings) -> str | None:
    metadata = jwks_cache.get_metadata(config)
    endpoint = str(metadata.get("end_session_endpoint", ""))
    if not endpoint:
        return config.oidc_post_logout_redirect_uri
    validate_oidc_endpoint(endpoint, config)
    payload: dict[str, str] = {}
    if id_token:
        payload["id_token_hint"] = id_token
    if config.oidc_post_logout_redirect_uri:
        payload["post_logout_redirect_uri"] = config.oidc_post_logout_redirect_uri
    return f"{endpoint}?{urlencode(payload)}" if payload else endpoint
