# Release checklist

This checklist is fail-closed. An unchecked blocking item prevents production release.

Latest engineering evidence is in `docs/PRE_MERGE_ACCEPTANCE.md`. Current decision:
**conditionally ready for pull-request review; blocked for production release**.

## Identity, data, and security

- [ ] Previously exposed DashScope key revoked; replacement exists only in the target secret store.
- [ ] No default/weak API, JWT, OIDC, database, Redis, MinIO, or encryption secret is configured.
- [ ] Secret scan and locked dependency audit pass; CycloneDX SBOM is retained; every unresolved
      high/critical container finding has either a patched image or an approved, expiring risk
      decision. The current 5 critical Debian findings have no such approval.
- [ ] Enterprise OIDC login, state/nonce/PKCE, refresh, logout, disablement, role mapping, and tenant
      selection pass against the real IdP.
- [ ] PostgreSQL migration owner and application runtime roles are separate; runtime is
      `NOSUPERUSER` and `NOBYPASSRLS`.
- [ ] Real PostgreSQL two-tenant RLS, missing-context, pool-reuse, write/delete, and audit tests pass.
- [ ] Milvus tenant/knowledge-base isolation and image authorization tests pass.

## Quality, performance, and recovery

- [ ] CI commit SHA equals the reviewed immutable image label and deployment manifest.
- [ ] Pytest, Ruff, Mypy, compileall, Alembic rehearsal, and all required integration jobs pass.
- [ ] All 100 evaluation rows have real expert approval and the online release gate passes without
      relaxed thresholds.
- [ ] Staging 1/10/30/50 ordinary and streaming query, import, OIDC, PostgreSQL, Milvus, and worker
      profiles meet approved SLOs with no pool/queue leak.
- [ ] Recent PostgreSQL, MinIO/upload, Milvus/etcd, and configuration backups have validated hashes.
- [ ] An isolated full-stack restore proves tenant/user/membership/KB/version/session/message/
      citation/image/vector/RLS integrity and records RTO/RPO.
- [ ] Grafana dashboard, Prometheus targets, alerts, routing, retention, and incident runbook are
      active and tested.

## Change approval

- [ ] Pull requests are reviewed and approved; no automation has bypassed protected branches.
- [ ] Change owner approves canary scope, observation window, rollback point, and on-call owner.
- [ ] Last known-good image digest, database backup, Milvus alias/collection, and rollback commands
      are recorded before rollout.
- [ ] Production deployment and release publication have explicit human approval.

## Current blocker owners

| Blocker | Required owner/action |
|---|---|
| Exposed DashScope key | Account owner completes `docs/KEY_ROTATION_CHECKLIST.md` |
| 70 pending evaluation rows | Business and security experts complete `docs/HUMAN_EVALUATION_GUIDE.md` |
| 5 critical / 21 high unfixed image findings | Security owner records a time-bounded decision or waits for patched packages |
| Real enterprise OIDC | Identity owner provides target IdP parameters and participates in acceptance |
| Target online/load evidence | Release owner approves SLOs and staging window after the preceding blockers close |
| Merge/deploy/release | Explicit human approval for each separate state change |
