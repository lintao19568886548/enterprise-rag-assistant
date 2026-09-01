# Key rotation checklist

Use this checklist for the DashScope key previously visible in a screenshot. Never paste the old
or replacement value into chat, a ticket, terminal output, Git history or an acceptance report.

## DashScope immediate response

- [ ] In the Alibaba Cloud Bailian/DashScope console, identify the exposed key by metadata only.
- [ ] Revoke or delete that old key. Disabling only the local `.env` value is not revocation.
- [ ] Create a replacement with the minimum service scope and a named owner/expiry policy.
- [ ] Update only the ignored local `.env` entry `OPENAI_API_KEY`. Keep the compatible DashScope
      base URL in `OPENAI_BASE_URL`; do not replace it with a ChatGPT key unless the application is
      intentionally migrated and retested against the OpenAI endpoint and model names.
- [ ] Restart the import and query processes so the old process environment is gone.
- [ ] Confirm only safe metadata: key is configured, is not a placeholder, and the provider base
      URL uses HTTPS. Do not print length, prefix, suffix or the value in shared logs.
- [ ] Run repository plus reachable-history scanning and local log/report scanning.
- [ ] Verify both readiness endpoints, then run one low-cost provider request and one grounded RAG
      query. Inspect status, citations and redacted logs, not request authorization headers.
- [ ] Record revocation time, replacement owner, verification time and operator in the secret
      manager or private change record. Do not put the credential itself in that record.

The account owner must explicitly confirm completion before provider-dependent acceptance resumes.
Local code cannot prove that a credential was revoked in the cloud console.

## Pre-production secret rotation

Before production, rotate and independently verify these secret classes through the target secret
manager:

- OIDC client secret and session-encryption key;
- LangGraph/checkpoint encryption key;
- PostgreSQL owner and runtime-role passwords;
- Redis/Celery authentication value;
- MinIO access and secret keys;
- MinerU/provider tokens and any API-key authentication values.

For each class, document owner, scope, creation time, expiry, last rotation, affected services and
rollback procedure. Prefer overlapping validation only when the provider supports it, revoke the
old value promptly, and restart every process that may retain the old secret in memory.

## Safe verification commands

From the repository root, the following commands are designed not to display matched values:

```powershell
uv run python scripts/scan_secrets.py --history
uv run python scripts/scan_secrets.py logs output evaluation/reports docs/reports
```

After the owner confirms rotation, use the application's health and RAG acceptance commands. Never
use `Get-Content .env`, `docker inspect` environment output, `set`, `env`, or a verbose HTTP client
in shared evidence because those can disclose the replacement.
