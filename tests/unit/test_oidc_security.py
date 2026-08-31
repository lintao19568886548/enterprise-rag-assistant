from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.oidc import OIDCTokenError, decode_oidc_token
from app.core.security import Principal, hash_service_account_secret, verify_service_account_secret
from app.core.settings import Settings


ISSUER = "https://id.example.com/realms/enterprise"
AUDIENCE = "enterprise-rag-api"
CLIENT_ID = "enterprise-rag-assistant"


@pytest.fixture(scope="module")
def keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _config(**overrides):
    values = {
        "app_env": "test",
        "oidc_enabled": True,
        "oidc_issuer_url": ISSUER,
        "oidc_client_id": CLIENT_ID,
        "oidc_audience": AUDIENCE,
        "oidc_allowed_algorithms": "RS256",
        "oidc_clock_skew_seconds": 0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _token(private_key, **claim_overrides):
    now = datetime.now(UTC)
    claims = {
        "iss": ISSUER,
        "sub": "employee-123",
        "aud": AUDIENCE,
        "azp": CLIENT_ID,
        "iat": now,
        "nbf": now - timedelta(seconds=1),
        "exp": now + timedelta(minutes=5),
        "token_use": "access",
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"typ": "at+jwt"})


def test_valid_oidc_access_token_is_accepted(keys):
    private_key, public_key = keys
    claims = decode_oidc_token(
        _token(private_key),
        config=_config(),
        signing_key=public_key,
    )
    assert claims.subject == "employee-123"
    assert claims.issuer == ISSUER


@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},
        {"aud": "wrong-audience"},
        {"iss": "https://attacker.invalid"},
        {"token_use": "id"},
        {"azp": "another-client"},
    ],
)
def test_invalid_oidc_claims_are_rejected(keys, claim_overrides):
    private_key, public_key = keys
    with pytest.raises(OIDCTokenError):
        decode_oidc_token(
            _token(private_key, **claim_overrides),
            config=_config(),
            signing_key=public_key,
        )


def test_wrong_oidc_signature_is_rejected(keys):
    private_key, _public_key = keys
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(OIDCTokenError):
        decode_oidc_token(
            _token(private_key),
            config=_config(),
            signing_key=other_key.public_key(),
        )


def test_service_account_secret_is_salted_and_verifiable():
    secret = "secret-value-with-at-least-thirty-two-characters"
    first = hash_service_account_secret(secret, iterations=100_000)
    second = hash_service_account_secret(secret, iterations=100_000)
    assert first != second
    assert verify_service_account_secret(secret, first)
    assert not verify_service_account_secret(f"{secret}-wrong", first)
    assert secret not in first


def test_enterprise_role_permissions_are_not_simple_numeric_escalation():
    viewer = Principal("viewer", "viewer", "user", "tenant")
    editor = Principal("editor", "editor", "user", "tenant")
    auditor = Principal("auditor", "auditor", "user", "tenant")
    assert viewer.has_permission("read")
    assert not viewer.has_permission("write_document")
    assert editor.has_permission("write_document")
    assert not editor.has_permission("manage_tenant")
    assert auditor.has_permission("read_audit")
    assert not auditor.has_permission("read")
