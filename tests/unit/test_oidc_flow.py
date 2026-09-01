import json
from urllib.parse import parse_qs, urlsplit

import pytest

from app.core.oidc import OIDCTokenError, jwks_cache
from app.core.oidc_flow import (
    build_authorization_request,
    exchange_authorization_code,
    refresh_oidc_tokens,
    resolve_browser_session,
    sanitize_return_to,
)
from app.core.oidc_flow import OIDCTokenSet
from app.core.oidc_session import OIDCBrowserSession, OIDCSessionManager
from app.core.settings import Settings


def _config(**overrides):
    values = {
        "app_env": "test",
        "oidc_enabled": True,
        "oidc_issuer_url": "https://identity.example.com/realms/enterprise",
        "oidc_client_id": "enterprise-rag",
        "oidc_client_secret": ("Oc7Qm2Vz" + "9Lp4Rx6N") * 2,
        "oidc_audience": "enterprise-rag-api",
        "oidc_redirect_uri": "https://rag.example.com/auth/callback",
        "oidc_session_encryption_key": "O8!sP3@vK6#rT2$x" * 2,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _metadata():
    return {
        "issuer": "https://identity.example.com/realms/enterprise",
        "authorization_endpoint": "https://identity.example.com/authorize",
        "token_endpoint": "https://identity.example.com/token",
        "jwks_uri": "https://identity.example.com/jwks",
    }


def test_authorization_request_contains_pkce_state_and_nonce(monkeypatch):
    config = _config()
    manager = OIDCSessionManager(config=config)
    monkeypatch.setattr(jwks_cache, "get_metadata", lambda _config: _metadata())
    request = build_authorization_request("/chat.html", manager=manager, config=config)
    query = parse_qs(urlsplit(request.url).query)
    assert query["response_type"] == ["code"]
    assert query["state"] == [request.transaction.state]
    assert query["nonce"] == [request.transaction.nonce]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["openid profile email"]
    assert request.transaction.code_verifier not in request.url


@pytest.mark.parametrize("value", ["https://attacker.invalid", "//attacker.invalid", "/safe\nLocation: bad", "/safe\\bad"])
def test_return_path_rejects_open_redirects(value):
    with pytest.raises(OIDCTokenError):
        sanitize_return_to(value)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size):
        return json.dumps(self.payload).encode("utf-8")


def test_code_exchange_uses_verifier_and_validates_response(monkeypatch):
    config = _config()
    observed = {}
    monkeypatch.setattr(jwks_cache, "get_metadata", lambda _config: _metadata())

    def fake_urlopen(request, timeout):
        observed["body"] = parse_qs(request.data.decode("utf-8"))
        observed["timeout"] = timeout
        return _Response(
            {
                "access_token": "header.access.signature",
                "refresh_token": "refresh-token-value",
                "id_token": "header.id.signature",
                "token_type": "Bearer",
                "expires_in": 600,
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    tokens = exchange_authorization_code("authorization-code", "pkce-verifier", config=config)
    assert tokens.expires_in == 600
    assert observed["body"]["grant_type"] == ["authorization_code"]
    assert observed["body"]["code_verifier"] == ["pkce-verifier"]
    assert "client_secret" in observed["body"]


def test_refresh_rejects_non_bearer_response(monkeypatch):
    config = _config()
    monkeypatch.setattr(jwks_cache, "get_metadata", lambda _config: _metadata())
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(
            {"access_token": "opaque", "token_type": "MAC", "expires_in": 60}
        ),
    )
    with pytest.raises(OIDCTokenError):
        refresh_oidc_tokens("refresh-token", config=config)


def test_expired_browser_session_is_refreshed_and_rotated(monkeypatch):
    config = _config()
    manager = OIDCSessionManager(config=config)
    session_id = manager.create_session(
        OIDCBrowserSession(
            access_token="expired-access",
            refresh_token="old-refresh",
            id_token="original-id-token",
            expires_at=90,
            created_at=10,
        )
    )
    monkeypatch.setattr(
        "app.core.oidc_flow.refresh_oidc_tokens",
        lambda *_args, **_kwargs: OIDCTokenSet("new-access", "new-refresh", None, 600),
    )
    refreshed = resolve_browser_session(
        session_id,
        manager=manager,
        config=config,
        clock=lambda: 100,
    )
    assert refreshed.access_token == "new-access"
    assert refreshed.refresh_token == "new-refresh"
    assert refreshed.id_token == "original-id-token"
    assert manager.get_session(session_id) == refreshed
