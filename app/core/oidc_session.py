"""Encrypted one-time OIDC transactions and browser sessions."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from Crypto.Cipher import AES

from app.clients.redis_utils import get_redis_client
from app.core.settings import Settings, settings


class OIDCSessionError(ValueError):
    """A non-sensitive OIDC state/session validation failure."""


@dataclass(frozen=True)
class OIDCLoginTransaction:
    state: str
    nonce: str
    code_verifier: str
    return_to: str
    created_at: float


@dataclass(frozen=True)
class OIDCBrowserSession:
    access_token: str
    refresh_token: str | None
    id_token: str | None
    expires_at: float
    created_at: float


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _pkce_challenge(verifier: str) -> str:
    return _base64url(hashlib.sha256(verifier.encode("ascii")).digest())


class _SessionCipher:
    def __init__(self, config: Settings) -> None:
        configured = config.reveal(config.oidc_session_encryption_key)
        self._key = configured.encode("utf-8") if configured else secrets.token_bytes(32)
        if len(self._key) != 32:
            raise OIDCSessionError("OIDC session encryption key is invalid")

    def encrypt(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=secrets.token_bytes(12))
        ciphertext, tag = cipher.encrypt_and_digest(raw)
        return f"v1.{_base64url(bytes(cipher.nonce) + tag + ciphertext)}"

    def decrypt(self, value: str) -> dict[str, Any]:
        try:
            version, encoded = value.split(".", 1)
            if version != "v1":
                raise ValueError
            padded = encoded + "=" * (-len(encoded) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            nonce, tag, ciphertext = raw[:12], raw[12:28], raw[28:]
            cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce)
            payload = json.loads(cipher.decrypt_and_verify(ciphertext, tag))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise OIDCSessionError("OIDC session payload is invalid") from exc
        if not isinstance(payload, dict):
            raise OIDCSessionError("OIDC session payload is invalid")
        return payload


class OIDCSessionManager:
    """Use Redis when enabled and a bounded process-local store otherwise."""

    def __init__(
        self,
        *,
        config: Settings = settings,
        redis_client: Any | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._redis_client = redis_client
        self._clock = clock
        self._cipher = _SessionCipher(config)
        self._memory: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def _redis(self):
        if self._redis_client is not None:
            return self._redis_client
        self._redis_client = get_redis_client()
        return self._redis_client

    def _key(self, kind: str, identifier: str) -> str:
        if not identifier or len(identifier) > 256:
            raise OIDCSessionError("OIDC session identifier is invalid")
        return f"kb:oidc:{kind}:{identifier}"

    def _put(self, key: str, value: str, ttl: int) -> None:
        if self.config.redis_enabled:
            self._redis().setex(key, ttl, value)
            return
        now = self._clock()
        with self._lock:
            self._purge_memory(now)
            if len(self._memory) >= 10_000:
                raise OIDCSessionError("OIDC session store capacity exceeded")
            self._memory[key] = (now + ttl, value)

    def _get(self, key: str, *, consume: bool = False) -> str | None:
        if self.config.redis_enabled:
            client = self._redis()
            raw = client.getdel(key) if consume else client.get(key)
            if isinstance(raw, bytes):
                return raw.decode("utf-8")
            return str(raw) if raw is not None else None
        now = self._clock()
        with self._lock:
            self._purge_memory(now)
            record = self._memory.pop(key, None) if consume else self._memory.get(key)
        if record is None or record[0] <= now:
            return None
        return record[1]

    def _delete(self, key: str) -> None:
        if self.config.redis_enabled:
            self._redis().delete(key)
            return
        with self._lock:
            self._memory.pop(key, None)

    def _purge_memory(self, now: float) -> None:
        for key, (expires_at, _value) in list(self._memory.items()):
            if expires_at <= now:
                self._memory.pop(key, None)

    def create_login_transaction(self, return_to: str) -> tuple[OIDCLoginTransaction, str]:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        transaction = OIDCLoginTransaction(
            state=state,
            nonce=secrets.token_urlsafe(32),
            code_verifier=verifier,
            return_to=return_to,
            created_at=self._clock(),
        )
        payload = {"kind": "transaction", **asdict(transaction)}
        self._put(
            self._key("transaction", state),
            self._cipher.encrypt(payload),
            self.config.oidc_transaction_ttl_seconds,
        )
        return transaction, _pkce_challenge(verifier)

    def consume_login_transaction(self, state: str) -> OIDCLoginTransaction:
        raw = self._get(self._key("transaction", state), consume=True)
        if raw is None:
            raise OIDCSessionError("OIDC login transaction is missing or expired")
        payload = self._cipher.decrypt(raw)
        if payload.pop("kind", None) != "transaction" or payload.get("state") != state:
            raise OIDCSessionError("OIDC login transaction is invalid")
        try:
            return OIDCLoginTransaction(**payload)
        except TypeError as exc:
            raise OIDCSessionError("OIDC login transaction is invalid") from exc

    def create_session(self, session: OIDCBrowserSession) -> str:
        session_id = secrets.token_urlsafe(32)
        payload = {"kind": "session", **asdict(session)}
        self._put(
            self._key("session", session_id),
            self._cipher.encrypt(payload),
            self.config.oidc_session_ttl_seconds,
        )
        return session_id

    def get_session(self, session_id: str) -> OIDCBrowserSession:
        raw = self._get(self._key("session", session_id))
        if raw is None:
            raise OIDCSessionError("OIDC browser session is missing or expired")
        payload = self._cipher.decrypt(raw)
        if payload.pop("kind", None) != "session":
            raise OIDCSessionError("OIDC browser session is invalid")
        try:
            return OIDCBrowserSession(**payload)
        except TypeError as exc:
            raise OIDCSessionError("OIDC browser session is invalid") from exc

    def replace_session(self, session_id: str, session: OIDCBrowserSession) -> None:
        payload = {"kind": "session", **asdict(session)}
        self._put(
            self._key("session", session_id),
            self._cipher.encrypt(payload),
            self.config.oidc_session_ttl_seconds,
        )

    def delete_session(self, session_id: str) -> None:
        self._delete(self._key("session", session_id))


oidc_session_manager = OIDCSessionManager()
