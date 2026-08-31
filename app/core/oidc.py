"""Generic OIDC discovery, JWKS caching and access-token validation."""

from __future__ import annotations

import json
import secrets
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import jwt
from jwt import PyJWKClient

from app.core.settings import Settings, settings


class OIDCTokenError(ValueError):
    """A deliberately non-sensitive OIDC validation failure."""


@dataclass(frozen=True)
class OIDCClaims:
    issuer: str
    subject: str
    claims: dict[str, Any]


class OIDCJWKSCache:
    """Thread-safe OIDC discovery and JWKS client cache with a bounded TTL."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._expires_at = 0.0
        self._issuer = ""
        self._metadata: dict[str, Any] | None = None
        self._jwks_client: PyJWKClient | None = None

    def clear(self) -> None:
        with self._lock:
            self._expires_at = 0.0
            self._issuer = ""
            self._metadata = None
            self._jwks_client = None

    def get_metadata(self, config: Settings = settings) -> dict[str, Any]:
        issuer = (config.oidc_issuer_url or "").rstrip("/")
        now = time.monotonic()
        with self._lock:
            if self._metadata is not None and self._issuer == issuer and now < self._expires_at:
                return dict(self._metadata)
            metadata = self._load_metadata(issuer, config)
            if str(metadata.get("issuer", "")).rstrip("/") != issuer:
                raise OIDCTokenError("OIDC discovery issuer mismatch")
            self._metadata = metadata
            self._issuer = issuer
            self._expires_at = now + config.oidc_jwks_cache_seconds
            self._jwks_client = None
            return dict(metadata)

    def get_client(self, config: Settings = settings) -> PyJWKClient:
        issuer = (config.oidc_issuer_url or "").rstrip("/")
        metadata = self.get_metadata(config)
        now = time.monotonic()
        with self._lock:
            if self._jwks_client is not None and self._issuer == issuer and now < self._expires_at:
                return self._jwks_client
            jwks_uri = str(metadata.get("jwks_uri", ""))
            self._validate_remote_uri(jwks_uri, config)
            self._jwks_client = PyJWKClient(
                jwks_uri,
                cache_jwk_set=True,
                lifespan=config.oidc_jwks_cache_seconds,
                timeout=config.oidc_http_timeout_seconds,
            )
            self._issuer = issuer
            self._expires_at = now + config.oidc_jwks_cache_seconds
            return self._jwks_client

    @staticmethod
    def _validate_remote_uri(uri: str, config: Settings) -> None:
        parsed = urlparse(uri)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise OIDCTokenError("OIDC JWKS URI is invalid")
        if config.is_deployed and parsed.scheme != "https":
            raise OIDCTokenError("OIDC endpoints must use HTTPS when deployed")

    def _load_metadata(self, issuer: str, config: Settings) -> dict[str, Any]:
        if not issuer:
            raise OIDCTokenError("OIDC issuer is not configured")
        self._validate_remote_uri(issuer, config)
        request = urllib.request.Request(
            f"{issuer}/.well-known/openid-configuration",
            headers={"Accept": "application/json", "User-Agent": "enterprise-rag-assistant/0.2"},
        )
        try:
            with urllib.request.urlopen(request, timeout=config.oidc_http_timeout_seconds) as response:
                raw = response.read(1024 * 1024 + 1)
        except Exception as exc:
            raise OIDCTokenError("OIDC discovery is unavailable") from exc
        if len(raw) > 1024 * 1024:
            raise OIDCTokenError("OIDC discovery response is too large")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OIDCTokenError("OIDC discovery response is invalid") from exc
        if not isinstance(payload, dict):
            raise OIDCTokenError("OIDC discovery response is invalid")
        return payload


jwks_cache = OIDCJWKSCache()


def validate_oidc_endpoint(uri: str, config: Settings = settings) -> None:
    OIDCJWKSCache._validate_remote_uri(uri, config)


def decode_oidc_token(
    token: str,
    *,
    config: Settings = settings,
    signing_key: Any | None = None,
) -> OIDCClaims:
    """Validate signature and all security-relevant OIDC access-token claims."""
    if not token or len(token) > 16_384:
        raise OIDCTokenError("OIDC token is invalid")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise OIDCTokenError("OIDC token header is invalid") from exc
    algorithm = str(header.get("alg", "")).upper()
    if algorithm not in config.oidc_algorithms:
        raise OIDCTokenError("OIDC token algorithm is not allowed")
    token_type = str(header.get("typ", "")).lower()
    if token_type not in {"jwt", "at+jwt"}:
        raise OIDCTokenError("OIDC token type is not an access token")
    try:
        if signing_key is None:
            signing_key = jwks_cache.get_client(config).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=config.oidc_algorithms,
            audience=config.oidc_audience,
            issuer=(config.oidc_issuer_url or "").rstrip("/"),
            leeway=config.oidc_clock_skew_seconds,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise OIDCTokenError("OIDC token validation failed") from exc
    if not isinstance(claims, dict):
        raise OIDCTokenError("OIDC token claims are invalid")
    subject = claims.get("sub")
    issuer = claims.get("iss")
    if not isinstance(subject, str) or not subject.strip():
        raise OIDCTokenError("OIDC subject is missing")
    if not isinstance(issuer, str) or not issuer.strip():
        raise OIDCTokenError("OIDC issuer is missing")
    token_use = claims.get("token_use")
    if token_use is not None and str(token_use).lower() != "access":
        raise OIDCTokenError("OIDC token type is not an access token")
    authorized_party = claims.get("azp") or claims.get("client_id")
    if authorized_party is not None and authorized_party != config.oidc_client_id:
        raise OIDCTokenError("OIDC authorized party is invalid")
    return OIDCClaims(issuer=issuer.rstrip("/"), subject=subject, claims=claims)


def decode_oidc_id_token(
    token: str,
    *,
    nonce: str,
    config: Settings = settings,
    signing_key: Any | None = None,
) -> OIDCClaims:
    """Validate an OIDC ID token including the one-time login nonce."""
    if not token or len(token) > 16_384 or not nonce:
        raise OIDCTokenError("OIDC ID token is invalid")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise OIDCTokenError("OIDC ID token header is invalid") from exc
    algorithm = str(header.get("alg", "")).upper()
    if algorithm not in config.oidc_algorithms:
        raise OIDCTokenError("OIDC ID token algorithm is not allowed")
    token_type = str(header.get("typ", "jwt")).lower()
    if token_type != "jwt":
        raise OIDCTokenError("OIDC ID token type is invalid")
    try:
        if signing_key is None:
            signing_key = jwks_cache.get_client(config).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=config.oidc_algorithms,
            audience=config.oidc_client_id,
            issuer=(config.oidc_issuer_url or "").rstrip("/"),
            leeway=config.oidc_clock_skew_seconds,
            options={"require": ["exp", "iss", "aud", "sub", "nonce"]},
        )
    except jwt.PyJWTError as exc:
        raise OIDCTokenError("OIDC ID token validation failed") from exc
    if not isinstance(claims, dict) or not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OIDCTokenError("OIDC ID token nonce is invalid")
    subject = claims.get("sub")
    issuer = claims.get("iss")
    if not isinstance(subject, str) or not subject.strip():
        raise OIDCTokenError("OIDC subject is missing")
    if not isinstance(issuer, str) or not issuer.strip():
        raise OIDCTokenError("OIDC issuer is missing")
    return OIDCClaims(issuer=issuer.rstrip("/"), subject=subject, claims=claims)
