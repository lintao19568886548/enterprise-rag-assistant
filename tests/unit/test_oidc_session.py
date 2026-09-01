import base64
import hashlib

import fakeredis
import pytest

from app.core.oidc_session import (
    OIDCBrowserSession,
    OIDCSessionError,
    OIDCSessionManager,
)
from app.core.settings import Settings


def _config(**overrides):
    values = {
        "app_env": "test",
        "oidc_session_encryption_key": "O8!sP3@vK6#rT2$x" * 2,
        "oidc_transaction_ttl_seconds": 60,
        "oidc_session_ttl_seconds": 300,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_pkce_transaction_is_one_time_and_encrypted():
    manager = OIDCSessionManager(config=_config())
    transaction, challenge = manager.create_login_transaction("/chat.html")
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(transaction.code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert challenge == expected
    stored = next(iter(manager._memory.values()))[1]
    assert transaction.code_verifier not in stored
    assert transaction.nonce not in stored
    assert manager.consume_login_transaction(transaction.state) == transaction
    with pytest.raises(OIDCSessionError):
        manager.consume_login_transaction(transaction.state)


def test_browser_session_is_encrypted_and_can_be_revoked():
    manager = OIDCSessionManager(config=_config())
    access_token = "header." + "access-payload" + ".signature"
    session = OIDCBrowserSession(
        access_token=access_token,
        refresh_token="refresh-value",
        id_token="id-token-value",
        expires_at=500,
        created_at=100,
    )
    session_id = manager.create_session(session)
    stored = next(iter(manager._memory.values()))[1]
    assert access_token not in stored
    assert manager.get_session(session_id) == session
    manager.delete_session(session_id)
    with pytest.raises(OIDCSessionError):
        manager.get_session(session_id)


def test_redis_transactions_are_consumed_atomically():
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    manager = OIDCSessionManager(
        config=_config(redis_enabled=True),
        redis_client=redis_client,
    )
    transaction, _challenge = manager.create_login_transaction("/admin.html")
    assert manager.consume_login_transaction(transaction.state) == transaction
    with pytest.raises(OIDCSessionError):
        manager.consume_login_transaction(transaction.state)


def test_tampered_session_ciphertext_is_rejected():
    manager = OIDCSessionManager(config=_config())
    session_id = manager.create_session(
        OIDCBrowserSession("access", None, None, expires_at=500, created_at=100)
    )
    key = manager._key("session", session_id)
    expires_at, value = manager._memory[key]
    manager._memory[key] = (expires_at, value[:-1] + ("A" if value[-1] != "A" else "B"))
    with pytest.raises(OIDCSessionError):
        manager.get_session(session_id)
