# Pre-merge acceptance

## Decision

Acceptance was executed on 2026-09-01 (Asia/Shanghai) from
`phase3/release-readiness` at reviewed source SHA
`10b9beba6de000dd9308a93560945b182a3ee79b` before the acceptance-only changes in
this pull request.

- **Pull-request review:** conditionally ready. The implemented engineering gates pass and both
  pull requests must remain open for human review.
- **Production release:** **blocked**. Do not merge, deploy, publish a release, or describe the
  system as production-ready until every human blocker below is closed.
- **Data integrity:** the 100-row evaluation dataset was not rewritten by this acceptance run.
  Thirty rows remain approved and 70 remain `needs_human_label`.

PR #1 is `phase2/enterprise-production -> main` and PR #2 is
`phase3/release-readiness -> phase2/enterprise-production`. Both were open and unmerged when this
acceptance started.

## Executed gates

| Area | Executed evidence | Result |
|---|---|---|
| Local services | Import and query `/health/ready`; Milvus listener | passed |
| Python quality | compileall; Ruff; Mypy over 148 source files; full pytest | 138 passed, 4 declared skips, 1 provider deprecation warning |
| Secret handling | 310 working-tree files, 475 reachable historical blobs and 264 local artifact files; findings redact values | passed |
| Locked dependencies | `pip-audit==2.9.0`; Linux CPU Torch is a non-PyPI local-version wheel | 0 known vulnerabilities; Torch noted as skipped by the resolver |
| SQLite migration | Alembic current/upgrade | `d4e5f6a7b8c9 (head)` |
| Real PostgreSQL | four integration tests in the staging Docker network | 4 passed |
| PostgreSQL policy | 18 public tables, 16 with RLS, 0 unvalidated foreign keys; runtime role has no superuser/createdb/createrole/inherit/bypass-RLS flags | passed |
| Local OIDC | Keycloak code flow, PKCE, state, nonce, membership, encrypted session, refresh, replay rejection and logout | passed |
| Isolation/security | OIDC, tenant ACL, query ACL, document lifecycle, service account and role-boundary suite | 41 passed |
| Milvus isolation | 100 rounds, 200 disposable vectors, zero leaks; p95 5.397 ms | passed |
| Offline PR evaluation | approved scorer-contract cases | 30/30 passed |
| Release evaluation | 100 total, 30 approved, 70 pending; required categories incomplete | blocked as designed |
| Local performance control | concurrency 1/10/30/50 for five in-process scenarios; file-size boundary and pool release | 100% success, no timeouts; not target-capacity evidence |
| Sanitized logical recovery | tenant/document/session/citation/image/vector manifest; 7 chunks; FK 0; SQLite integrity `ok` | passed in 0.116 seconds |
| Full Docker backup/restore | PostgreSQL custom dump plus MinIO, Milvus, etcd and app-output snapshots, SHA-256 validation and isolated recovery | passed; details below |
| Current container image | `enterprise-rag-premerge:10b9beb`, read-only smoke import, OCI revision and UID/GID | passed; `10001:10001` |
| Current SBOM | Syft 1.33.0 CycloneDX | 4,026 components |
| Current image vulnerabilities | Trivy 0.67.2 | 5 critical, 21 high; 0 fixable critical/high |

The release-gate failures are expected and fail closed:
`minimum_approved_cases`, `permission_isolation`, `prompt_injection_containment`,
`image_citation_correctness`, and `required_category_coverage`.

## Full backup and isolated recovery

The validated local backup is under ignored path
`backups/staging/20260901_155648`. It contains a PostgreSQL custom-format dump, four volume
archives, configuration structure, a manifest and SHA-256 hashes. No `.env`, certificate, private
key or token is included.

It was restored into the isolated Compose project `enterprise-rag-premerge-recovery`. Source and
recovery databases had identical exact row counts across all 18 public tables. The recovery
database reached `d4e5f6a7b8c9`, retained 16 RLS-enabled tables and had zero unvalidated foreign
keys. PostgreSQL, Redis, MinIO, etcd and Milvus all became healthy. The recovery containers were
stopped without deleting their volumes.

The first Windows rehearsal found that PowerShell text pipelines could corrupt binary dump/tar
streams. Backup and restore now create binary artifacts inside disposable containers and transfer
them with `docker cp`. Restore also refuses a recovery name that already owns containers or
volumes and reuses the recorded staging migration image when it is locally available.

This proves the local recovery mechanism and control path. It is not a production-scale RTO/RPO
claim: the local staging database currently contains only the bootstrap identity records and no
business documents.

## RAG and provider status

Earlier phase-three live evidence records a grounded query with five citations, images and normal
history persistence. This pre-merge run deliberately did not make a new DashScope request because
the key shown in an earlier screenshot must be treated as compromised. A new online import,
ordinary/streaming query and provider-load run is permitted only after the owner confirms that the
old key was revoked and a replacement was installed locally.

## Human blockers

1. Revoke the previously exposed DashScope key, create a replacement, and store it only in the
   ignored local `.env` or the target secret manager. Follow `docs/KEY_ROTATION_CHECKLIST.md`.
2. A business expert must complete and approve the 70 pending evaluation rows. Follow
   `docs/HUMAN_EVALUATION_GUIDE.md`; AI must not invent those labels.
3. A security owner must either approve a time-bounded risk decision for the 5 critical and 21
   high unfixed base-image findings or wait for and adopt a patched base. The findings must not be
   suppressed to obtain a green release decision.
4. Supply the real enterprise OIDC issuer, client ID, client secret, audience, redirect URIs and
   scopes through the target secret/configuration system, then repeat the OIDC suite against that
   IdP.
5. After items 1, 2 and 4, execute target-environment online RAG, streaming, worker and 1/10/30/50
   load acceptance against approved SLOs.
6. Merging PR #1/#2, production deployment and Release publication each require separate explicit
   human approval.

## Local evidence locations

The following large or sensitive operational artifacts are intentionally ignored by Git:

- `output/pre_merge/security/sbom.cdx.json`
- `output/pre_merge/security/trivy-fixable.json`
- `output/pre_merge/security/trivy-all-high-critical.json`
- `output/pre_merge/sqlite-recovery.json`
- `output/pre_merge/performance.json`
- `backups/staging/20260901_155648`

GitHub Actions regenerates dependency, SBOM and Trivy evidence for the pushed commit and retains it
as a workflow artifact.
