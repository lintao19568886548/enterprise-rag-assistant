from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.auth_router as auth_router
import app.core.security as security
from app.core.errors import install_exception_handlers
from app.core.oidc import OIDCClaims, jwks_cache
from app.core.oidc_flow import OIDCTokenSet
from app.core.oidc_session import OIDCSessionManager
from app.core.settings import Settings


def _config():
    return Settings(
        _env_file=None,
        app_env="test",
        auth_enabled=True,
        oidc_enabled=True,
        oidc_issuer_url="https://identity.example.com/realms/enterprise",
        oidc_client_id="enterprise-rag",
        oidc_client_secret=("Oc7Qm2Vz" + "9Lp4Rx6N") * 2,
        oidc_audience="enterprise-rag-api",
        oidc_redirect_uri="https://rag.example.com/auth/callback",
        oidc_session_encryption_key="O8!sP3@vK6#rT2$x" * 2,
        database_enabled=False,
    )


def _app() -> FastAPI:
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(auth_router.router)
    return app


def test_browser_login_callback_cookie_auth_and_replay_protection(monkeypatch):
    config = _config()
    manager = OIDCSessionManager(config=config)
    metadata = {
        "issuer": config.oidc_issuer_url,
        "authorization_endpoint": "https://identity.example.com/authorize",
        "token_endpoint": "https://identity.example.com/token",
        "jwks_uri": "https://identity.example.com/jwks",
    }
    claims = OIDCClaims(
        issuer=config.oidc_issuer_url or "",
        subject="employee-123",
        claims={"sub": "employee-123"},
    )
    identity = (
        SimpleNamespace(id="user-1"),
        SimpleNamespace(role="viewer", department_id=None),
        SimpleNamespace(id="tenant-1"),
    )
    monkeypatch.setattr(auth_router, "settings", config)
    monkeypatch.setattr(auth_router, "oidc_session_manager", manager)
    monkeypatch.setattr(security, "settings", config)
    monkeypatch.setattr(security, "oidc_session_manager", manager)
    monkeypatch.setattr(jwks_cache, "get_metadata", lambda _config: metadata)
    monkeypatch.setattr(
        auth_router,
        "exchange_authorization_code",
        lambda *_args, **_kwargs: OIDCTokenSet(
            "access-token",
            "refresh-token",
            "id-token",
            600,
        ),
    )
    monkeypatch.setattr(auth_router, "decode_oidc_id_token", lambda *_args, **_kwargs: claims)
    monkeypatch.setattr(auth_router, "decode_oidc_token", lambda *_args, **_kwargs: claims)
    monkeypatch.setattr(auth_router, "resolve_oidc_membership", lambda *_args: identity)
    monkeypatch.setattr(security, "decode_oidc_token", lambda *_args, **_kwargs: claims)
    monkeypatch.setattr(security, "resolve_oidc_membership", lambda *_args: identity)

    with TestClient(_app()) as client:
        login = client.get("/auth/login?return_to=/chat.html", follow_redirects=False)
        assert login.status_code == 302
        authorization_query = parse_qs(urlsplit(login.headers["location"]).query)
        state = authorization_query["state"][0]
        callback = client.get(
            f"/auth/callback?code=code-value&state={state}",
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == "/chat.html"
        assert "HttpOnly" in callback.headers["set-cookie"]
        me = client.get("/auth/me")
        assert me.status_code == 200
        assert me.json() == {
            "authenticated": True,
            "user_id": "user-1",
            "tenant_id": "tenant-1",
            "role": "viewer",
            "authentication_method": "oidc_session",
        }
        client.cookies.set(config.oidc_state_cookie_name, state, path="/auth/callback")
        replay = client.get(
            f"/auth/callback?code=code-value&state={state}",
            follow_redirects=False,
        )
        assert replay.status_code == 401


def test_callback_rejects_state_cookie_mismatch(monkeypatch):
    config = _config()
    manager = OIDCSessionManager(config=config)
    monkeypatch.setattr(auth_router, "settings", config)
    monkeypatch.setattr(auth_router, "oidc_session_manager", manager)
    with TestClient(_app()) as client:
        client.cookies.set(config.oidc_state_cookie_name, "cookie-state", path="/auth/callback")
        response = client.get(
            "/auth/callback?code=code-value&state=attacker-state",
            follow_redirects=False,
        )
    assert response.status_code == 401
