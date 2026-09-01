"""Run the production OIDC router against the isolated local Keycloak realm.

This harness is intentionally restricted to loopback development/test use. It
avoids starting a second RAG model while exercising the real authorization-code,
PKCE, token-validation, membership-resolution, session, refresh and logout code.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.api.auth_router import router as auth_router
from app.core.middleware import install_common_api_features
from app.core.server import run_api
from app.core.settings import settings
from app.db.identity_repositories import create_user_membership, resolve_oidc_membership
from app.db.repositories import DEFAULT_TENANT_ID, ensure_defaults
from app.db.session import init_database

LOCAL_SUBJECT = "11111111-1111-4111-8111-111111111111"


def _assert_local_only() -> None:
    issuer = (settings.oidc_issuer_url or "").rstrip("/")
    if settings.app_env not in {"development", "test"}:
        raise RuntimeError("OIDC acceptance harness is restricted to development/test")
    if not issuer.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise RuntimeError("OIDC acceptance harness requires a loopback issuer")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _assert_local_only()
    init_database()
    ensure_defaults()
    issuer = (settings.oidc_issuer_url or "").rstrip("/")
    if resolve_oidc_membership(issuer, LOCAL_SUBJECT) is None:
        create_user_membership(
            tenant_id=DEFAULT_TENANT_ID,
            username="oidc-local-admin",
            display_name="OIDC Local Admin",
            role="tenant_admin",
            external_identity_id=LOCAL_SUBJECT,
            oidc_issuer=issuer,
        )
    yield


app = FastAPI(title="Enterprise RAG OIDC Acceptance Harness", lifespan=lifespan)
install_common_api_features(app, "oidc-acceptance")
app.include_router(auth_router)


@app.get("/", response_class=HTMLResponse)
@app.get("/chat.html", response_class=HTMLResponse)
async def acceptance_page() -> str:
    return """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
    <title>OIDC 验收</title><body><h1>OIDC 验收页面</h1>
    <p id="identity">正在读取身份…</p>
    <button id="logout" type="button">退出登录</button>
    <script>
    fetch('/auth/me').then(async response => {
      const payload = await response.json();
      document.getElementById('identity').textContent = response.ok
        ? `${payload.authentication_method}: ${payload.role}`
        : `未认证: ${response.status}`;
    });
    document.getElementById('logout').onclick = () => fetch('/auth/logout', {
      method: 'POST', redirect: 'follow'
    }).then(() => location.href = '/');
    </script></body></html>"""


if __name__ == "__main__":
    run_api(app, host="127.0.0.1", port=settings.query_service_port)
