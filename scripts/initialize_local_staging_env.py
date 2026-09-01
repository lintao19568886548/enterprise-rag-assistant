"""Create ignored local staging/OIDC env files without printing secret values.

The generated provider credentials are intentionally non-functional. An operator
must replace them locally after rotating any previously exposed provider key.
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _secret(length: int = 36) -> str:
    return secrets.token_urlsafe(length)


def _write_new(path: Path, lines: list[str]) -> None:
    if path.exists():
        raise SystemExit(f"Refusing to overwrite existing local configuration: {path.name}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _git_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging", default=".env.staging")
    parser.add_argument("--oidc", default=".env.oidc")
    args = parser.parse_args()

    staging_path = (REPOSITORY_ROOT / args.staging).resolve()
    oidc_path = (REPOSITORY_ROOT / args.oidc).resolve()
    if staging_path.parent != REPOSITORY_ROOT or oidc_path.parent != REPOSITORY_ROOT:
        raise SystemExit("Local env files must remain in the repository root")

    shared_oidc_secret = _secret()
    staging_lines = [
        "# Generated local infrastructure configuration. Never commit this file.",
        "# Provider credentials are intentionally non-functional until the owner rotates them.",
        "POSTGRES_USER=knowledge_owner",
        "POSTGRES_DB=knowledge_base",
        f"POSTGRES_PASSWORD={_secret()}",
        f"POSTGRES_RUNTIME_PASSWORD={_secret()}",
        f"REDIS_PASSWORD={_secret()}",
        f"MINIO_ACCESS_KEY=local-{secrets.token_hex(10)}",
        f"MINIO_SECRET_KEY={_secret()}",
        f"LANGGRAPH_AES_KEY={secrets.token_hex(16)}",
        "OIDC_ISSUER_URL=https://identity.invalid/realms/enterprise",
        "OIDC_CLIENT_ID=enterprise-rag",
        f"OIDC_CLIENT_SECRET={shared_oidc_secret}",
        "OIDC_AUDIENCE=enterprise-rag",
        "OIDC_REDIRECT_URI=https://127.0.0.1:8444/auth/callback",
        "OIDC_POST_LOGOUT_REDIRECT_URI=https://127.0.0.1:8444/chat.html",
        f"OIDC_SESSION_ENCRYPTION_KEY={secrets.token_hex(16)}",
        f"OPENAI_API_KEY=sk-local-disabled-{secrets.token_hex(16)}",
        "OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1",
        "MODEL_INPUT_COST_PER_1M_TOKENS=0",
        "MODEL_OUTPUT_COST_PER_1M_TOKENS=0",
        f"MINERU_API_TOKEN=local-disabled-{secrets.token_hex(16)}",
        "STAGING_PUBLIC_HOST=127.0.0.1",
        "APP_VERSION=0.1.0-phase3",
        f"GIT_SHA={_git_sha()}",
        f"BUILD_TIME={datetime.now(UTC).isoformat()}",
    ]
    oidc_lines = [
        "# Generated isolated Keycloak test configuration. Never commit this file.",
        "LOCAL_KEYCLOAK_ADMIN_USERNAME=local-admin",
        f"LOCAL_KEYCLOAK_ADMIN_PASSWORD={_secret()}",
        f"LOCAL_OIDC_CLIENT_SECRET={shared_oidc_secret}",
        f"LOCAL_OIDC_USER_PASSWORD={_secret()}",
        f"LOCAL_OIDC_REDIS_PASSWORD={_secret()}",
    ]

    _write_new(staging_path, staging_lines)
    _write_new(oidc_path, oidc_lines)
    print(
        "Created ignored local configuration files: "
        f"{staging_path.name} ({len(staging_lines) - 2} keys), "
        f"{oidc_path.name} ({len(oidc_lines) - 1} keys)."
    )
    print("Provider credentials remain intentionally disabled pending owner rotation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
