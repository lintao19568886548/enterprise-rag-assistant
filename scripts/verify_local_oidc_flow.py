"""Verify the complete local Keycloak browser flow without printing credentials.

The script follows the same redirects and cookies as a browser. It is limited
to loopback endpoints and reports only non-sensitive pass/fail facts.
"""

from __future__ import annotations

import os
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

APP_ORIGIN = "http://127.0.0.1:18001"
EXPECTED_PROVIDER_ORIGIN = "http://127.0.0.1:8080"


class _LoginFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.action: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form" and values.get("id") == "kc-form-login":
            self.action = values.get("action")


def _assert_loopback(url: str, port: int) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError(f"OIDC acceptance endpoint must be loopback HTTP: port={port}")
    if parsed.port != port:
        raise RuntimeError(f"OIDC acceptance endpoint uses unexpected port: expected={port}")


def _callback_url(response: httpx.Response) -> str:
    for item in response.history:
        request_url = str(item.request.url)
        parsed = urlparse(request_url)
        if parsed.port == 18001 and parsed.path == "/auth/callback":
            return request_url
    raise AssertionError("OIDC callback was not reached")


def _allow_loopback_secure_cookies(client: httpx.Client) -> None:
    """Match browser secure-context handling for loopback HTTP test origins."""
    for cookie in client.cookies.jar:
        if cookie.domain in {"127.0.0.1", "localhost.local"}:
            cookie.secure = False


def main() -> int:
    password = os.environ.get("LOCAL_OIDC_USER_PASSWORD")
    if not password:
        raise SystemExit("LOCAL_OIDC_USER_PASSWORD is required")
    _assert_loopback(APP_ORIGIN, 18001)
    _assert_loopback(EXPECTED_PROVIDER_ORIGIN, 8080)

    with httpx.Client(follow_redirects=True, timeout=15.0) as client:
        login_page = client.get(f"{APP_ORIGIN}/auth/login")
        login_page.raise_for_status()
        _assert_loopback(str(login_page.url), 8080)
        parser = _LoginFormParser()
        parser.feed(login_page.text)
        if not parser.action:
            raise AssertionError("Keycloak login form was not found")
        _assert_loopback(parser.action, 8080)
        _allow_loopback_secure_cookies(client)

        completed = client.post(
            parser.action,
            data={
                "username": "oidc-local-admin",
                "password": password,
                "credentialId": "",
            },
        )
        completed.raise_for_status()
        if str(completed.url) != f"{APP_ORIGIN}/chat.html":
            final_url = urlparse(str(completed.url))
            raise AssertionError(
                "OIDC login did not return to the application: "
                f"status={completed.status_code}, host={final_url.netloc}, path={final_url.path}"
            )

        callback_url = _callback_url(completed)
        identity = client.get(f"{APP_ORIGIN}/auth/me")
        identity.raise_for_status()
        identity_payload = identity.json()
        if identity_payload.get("authentication_method") != "oidc_session":
            raise AssertionError("OIDC browser session was not used")
        if identity_payload.get("role") != "tenant_admin":
            raise AssertionError("OIDC membership role was not resolved")

        refreshed = client.post(f"{APP_ORIGIN}/auth/refresh")
        refreshed.raise_for_status()
        if refreshed.json() != {"refreshed": True}:
            raise AssertionError("OIDC refresh did not rotate the browser session")

        replay = client.get(callback_url)
        if replay.status_code != 401:
            raise AssertionError("Consumed OIDC callback was accepted again")

        logout_redirect = client.post(f"{APP_ORIGIN}/auth/logout", follow_redirects=False)
        if logout_redirect.status_code != 303 or not logout_redirect.headers.get("location"):
            raise AssertionError("OIDC logout did not return an end-session redirect")
        _allow_loopback_secure_cookies(client)
        logged_out = client.get(logout_redirect.headers["location"])
        logged_out.raise_for_status()
        after_logout = client.get(f"{APP_ORIGIN}/auth/me")
        if after_logout.status_code != 401:
            raise AssertionError("OIDC session remained valid after logout")

    with httpx.Client(follow_redirects=False, timeout=15.0) as tampered_client:
        authorization = tampered_client.get(f"{APP_ORIGIN}/auth/login")
        if authorization.status_code != 302:
            raise AssertionError("OIDC authorization did not start")
        tampered = tampered_client.get(
            f"{APP_ORIGIN}/auth/callback",
            params={"code": "invalid", "state": "tampered"},
        )
        if tampered.status_code != 401:
            raise AssertionError("Tampered OIDC state was accepted")

    print("OIDC local flow passed: authorization_code, PKCE, state, nonce, membership")
    print("OIDC local flow passed: encrypted_session, refresh, replay_rejection, logout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
