# Security hardening

## Secrets and configuration

- `.env`, backups, databases, uploads, logs, model caches, keys and local volumes are ignored.
- Provider keys, DSNs, OIDC secrets, Redis/Celery URLs and encryption keys use `SecretStr` and
  safe accessor properties.
- Recursive logging redacts credential fields, bearer/JWT patterns, URL passwords and configured
  secret values. Deployed environments suppress exception objects and document/question content.
- Staging/production fail closed on disabled authentication/isolation, wildcard CORS, HTTP OIDC
  or provider endpoints, weak/default secrets, SQLite, in-memory tasks, unauthenticated Redis,
  public/insecure MinIO, missing encrypted PostgreSQL checkpointing and unknown model allowlists.

`scripts/scan_secrets.py` reports only rule, path and line. It does not print matched values. It
runs in pre-commit and CI. Phase 3 local scans found no committed real credential. Any DashScope
key previously visible in a screenshot remains considered compromised and must be revoked by the
account owner; the replacement belongs only in local `.env` or a secret manager.

## Application controls

- OIDC uses PKCE/state/nonce and encrypted one-time transaction/session state.
- RBAC, knowledge-base grants, PostgreSQL RLS and Milvus tenant/knowledge-base filters form
  independent authorization layers.
- Uploads validate type, signature/UTF-8, name, count and size and use server-generated paths.
- Retrieved content/history is delimited as untrusted data; output leakage patterns trigger a
  security refusal. Answers without sufficient retrieved evidence abstain.
- Rate limiting, model timeout/retry/fallback/circuit controls and Milvus keepalive limits reduce
  resource and retry storms.

## Supply chain and container

- Runtime and development versions are locked by `uv.lock`.
- GitHub Actions are pinned to commit SHAs.
- CI exports the locked runtime set, runs `pip-audit`, builds the image, verifies UID/GID
  `10001:10001`, generates a CycloneDX SBOM with Syft and fails on fixed high/critical Trivy
  findings.
- The image includes OCI version/Git SHA/build-time labels and runs as a non-root user.
- Staging application containers drop Linux capabilities, enable no-new-privileges, use a
  read-only root filesystem and expose only explicit writable volumes/tmpfs.
- Dependabot proposes monthly uv, Actions and Docker updates; changes still pass all gates.

The first local dependency audit found five advisories in `transformers 4.57.6` and
`setuptools 81.0.0`. The lock was upgraded to `transformers 5.16.1` and `setuptools 83.0.0`;
`tiktoken 0.12.0` remains explicitly pinned for Windows Enterprise Code Integrity compatibility.
A final real `pip-audit` scan evaluated 176 locked runtime requirements (including conditional
platform entries) and found zero known vulnerabilities. Because Linux uses
`torch==2.13.0+cpu`, both local acceptance and CI provide the official PyTorch CPU index to
`pip-audit`; otherwise pip cannot resolve the locked local-version wheel.

The final local acceptance image was built from `python:3.11-slim-bookworm` with security updates,
CPU-only PyTorch and UID/GID `10001:10001`. It is approximately 2.39 GB unpacked (506,763,228
bytes of Docker content), and its writable data/log/output/model directories were verified under
the non-root user. Syft generated a 4,028-component CycloneDX SBOM. Trivy found 5 critical,
21 high, 97 medium, 103 low and 10 unknown findings. None of the high/critical findings currently
has an available fixed version. The five critical Debian findings are retained in the machine-
readable report rather than ignored: one `zlib1g`, one `libsqlite3-0`, and three `perl-base`
advisories. Production release remains blocked until a patched base is available or the security
owner completes a documented risk decision. CI still repeats build, SBOM and Trivy gates.

## Rotation and response

Rotate provider keys, OIDC client secret, session/checkpoint encryption keys, database/Redis and
MinIO credentials through the secret manager. Use overlapping validation only when the provider
supports it, revoke the old value promptly, restart affected services and verify authentication,
health and audit records. Never commit rotation evidence containing the value.
