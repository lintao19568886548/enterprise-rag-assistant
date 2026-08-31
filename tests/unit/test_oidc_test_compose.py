import json
from pathlib import Path

import yaml


def test_local_oidc_stack_is_loopback_only_and_uses_secret_placeholders():
    compose = yaml.safe_load(Path("compose.oidc-test.yaml").read_text(encoding="utf-8"))
    keycloak = compose["services"]["keycloak"]
    redis = compose["services"]["oidc-redis"]
    assert keycloak["image"] == "quay.io/keycloak/keycloak:26.7.2"
    assert "start-dev" in keycloak["command"]
    assert keycloak["ports"] == ["127.0.0.1:8080:8080"]
    assert redis["ports"] == ["127.0.0.1:6380:6379"]
    assert keycloak["environment"]["LOCAL_OIDC_CLIENT_SECRET"].startswith("${LOCAL_OIDC_CLIENT_SECRET:?")
    assert redis["environment"]["LOCAL_OIDC_REDIS_PASSWORD"].startswith("${LOCAL_OIDC_REDIS_PASSWORD:?")


def test_local_realm_enforces_pkce_and_resolves_credentials_from_environment():
    realm = json.loads(
        Path("deploy/keycloak/enterprise-local-realm.json").read_text(encoding="utf-8")
    )
    client = realm["clients"][0]
    user = realm["users"][0]
    assert client["publicClient"] is False
    assert client["directAccessGrantsEnabled"] is False
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert client["secret"] == "${LOCAL_OIDC_CLIENT_SECRET}"
    assert user["credentials"][0]["value"] == "${LOCAL_OIDC_USER_PASSWORD}"
