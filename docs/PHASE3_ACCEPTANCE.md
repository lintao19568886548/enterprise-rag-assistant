# Phase 3 acceptance

## Decision

Phase 3 local engineering acceptance is **passed**, but production release is **blocked**. The
branch is suitable for review after it is pushed. Real local PostgreSQL RLS, local Keycloak and
container supply-chain evidence now exist; this is still not evidence that enterprise OIDC,
target-environment load, full multi-service recovery, business-label or production approval gates
have passed.

Evidence in this document comes from commands and live services executed on 2026-09-01
(Asia/Shanghai). No pending business label was approved by AI, and no production release or PR
merge was performed.

## Live local acceptance

PyCharm's `全部服务 (19530 + 8000 + 8001)` compound configuration is running:

| Component | Address | Result |
|---|---|---|
| Import service | `http://127.0.0.1:8000/import.html` | ready |
| Query service | `http://127.0.0.1:8001/chat.html` | ready |
| Milvus Lite | `127.0.0.1:19530` | listening |

Both `/health/ready` endpoints returned `status=ready` with Milvus, database, and model
configuration healthy. Redis and MinIO are deliberately disabled in the local development
profile and are not counted as staging evidence.

A live non-streaming query against the existing default knowledge base completed successfully:

- answer length: 262 characters;
- citations: 5;
- image URLs: 7;
- confidence: 0.9115;
- sufficient evidence: true;
- local/model/total workflow latency: 22527 / 2128 / 24655 ms;
- input/output/total tokens: 3166 / 173 / 3339.

The browser acceptance used a real 390 x 844 device-metrics viewport, not a CSS-width guess.
Desktop and 390px checks for `chat.html` and `import.html` found no horizontal overflow and no
console warning/error. Temporary browser device emulation was cleared after the checks.

## Quality and security gates

| Gate | Actual result | Status |
|---|---|---|
| Full pytest | 135 passed, 4 skipped, 1 provider deprecation warning | passed with declared skips |
| Ruff | all checks passed | passed |
| Mypy | 147 source files, no issues | passed |
| compileall | `app`, `tests`, `scripts`, `alembic`, and `evaluation` | passed |
| Secret scan | 306 repository files inspected | passed |
| Dependency audit | 176 locked runtime requirements, 0 known vulnerabilities | passed |
| SQLite Alembic rehearsal | upgrade -> downgrade base -> upgrade, `d4e5f6a7b8c9 (head)` | passed |
| Real PostgreSQL Alembic/RLS | PostgreSQL 17 blank DB to head; 4/4 RLS tests | passed locally |
| Local Keycloak OIDC | code + PKCE/state/nonce/session/refresh/replay/logout | passed locally |
| Container runtime | CPU-only, UID/GID 10001:10001, OCI labels, writable mounts | passed locally |
| Container SBOM/Trivy | 4,028 components; 5 critical, 21 high; 0 fixable high/critical | blocked for production |
| Milvus tenant isolation | 100 rounds, 200 disposable vectors, 0 leaks, p95 5.722 ms | passed |
| OIDC/security automated tests | forged/expired/wrong issuer/audience/nonce/state/replay cases | passed |
| RAG PR gate | 30/30 approved offline scorer-contract cases | passed |
| RAG release gate | 30 approved, 70 pending; required categories missing | blocked as designed |
| Recovery drill | sanitized SQLite restore, FK errors 0, integrity `ok`, 0.2168 s | passed locally |
| Local concurrency baseline | 1/10/30/50, five local scenarios, 0 errors/timeouts | passed locally |

The four skips in the ordinary host-side pytest invocation are the real PostgreSQL integration
cases because owner/runtime URLs are intentionally not exported into the developer shell. They
were executed separately in an isolated Docker network against the real PostgreSQL service and
completed `4 passed`; a skip is still not counted as a pass in the 135-test host run.

The acceptance infrastructure is healthy in Docker: PostgreSQL 17, Redis 7.4, MinIO, etcd and
Milvus 2.6.22, plus a separate loopback-only Keycloak 26.7.2 and Redis. The PostgreSQL image-level
migration check returned `d4e5f6a7b8c9 (head)`. Full application staging was not claimed because
the staging file intentionally contains disabled placeholder provider/OIDC credentials.

## Implemented deliverables

- fail-closed secret/configuration validation, recursive log redaction, and repository scanning;
- real-PostgreSQL RLS CI gate and runtime-role preparation;
- OIDC Authorization Code + PKCE/state/nonce/session/refresh/logout implementation and tests;
- expert annotation XLSX/CSV package, schema-validating importer, PR and release evaluation gates;
- RAG provenance, de-duplication, evidence and latency/token/cost telemetry improvements;
- bounded reranker retry with exponential backoff and jitter;
- request, embedding, query, database-pool, authorization, task, model and cost metrics;
- Prometheus alerts, Grafana dashboard, incident runbook, performance baseline and DR drill;
- non-root/read-only container hardening, pinned Actions, dependency audit, SBOM and Trivy CI;
- staging start/stop, backup/restore, release, and rollback operating procedures.

## Evidence artifacts

- `docs/reports/phase3_final_acceptance.json`
- `docs/reports/phase3_performance.json`
- `docs/reports/phase3_recovery_drill.json`
- `docs/reports/phase3_dependency_audit_summary.json`
- `docs/reports/phase3_pip_audit.json`
- `evaluation/reports/phase3-pr-gate.json` and `.md`
- `evaluation/reports/phase3-release-gate.json` and `.md`
- `outputs/phase3_evaluation/business_expert_annotation.xlsx`
- `outputs/phase3_evaluation/business_expert_annotation.csv`

## Blocking items before production

1. The previously exposed DashScope key must be revoked and replaced only in local `.env` or a
   secret manager.
2. A business expert must complete and approve the 70 pending cases. Release gate remains blocked
   until 100 approved cases and required security/image categories exist.
3. The final Trivy report contains 5 critical Debian base-package vulnerabilities with no current
   fixed version. Use a patched base when available or obtain a time-bounded security-owner risk
   decision; do not suppress them merely to pass the gate.
4. Enterprise OIDC requires real issuer URL, client ID, redirect URI, and scopes. The client secret
   must be placed locally and never sent in chat.
5. Staging still needs online provider/streaming/OIDC/worker load evidence, full application
   Compose readiness, full PostgreSQL/MinIO/Milvus/etcd restore evidence and agreed SLOs.
6. Phase 2 and Phase 3 pull requests require explicit confirmation immediately before creation;
   neither may be auto-merged or released.
