# Production readiness

## Current decision

Phase 3 local engineering acceptance is **complete**, but the system is not approved for
production. Local code, live PyCharm services, security, deterministic evaluation, performance,
browser, Milvus isolation, and sanitized recovery evidence are available in
`docs/PHASE3_ACCEPTANCE.md` and `docs/reports/phase3_final_acceptance.json`.
The following external gates remain blocking:

1. Revoke the DashScope key previously visible in a screenshot and configure a new key locally.
2. Obtain 70 real business-expert labels; the release evaluation is blocked at 30/100.
3. Replace the container base when fixes exist for the 5 remaining critical Debian findings, or
   obtain an explicit, time-bounded security-owner risk decision; do not hide them in scan rules.
4. Supply real enterprise issuer/client ID/redirect/scopes and place the client secret locally.
5. Run online approved RAG evaluation, full application staging load tests, and isolated
   PostgreSQL/MinIO/Milvus/etcd restore. Local PostgreSQL RLS, Keycloak and infrastructure Compose
   acceptance are complete, but they are not target-environment evidence.
6. Create and review the Phase 2 and Phase 3 pull requests; do not merge or release automatically.

## Deployment checklist

The authoritative executable checklist is `docs/RELEASE_CHECKLIST.md`; rollback steps are in
`docs/ROLLBACK_CHECKLIST.md`. The compact list below is retained as an operator summary.

- [ ] Reviewed branch and CI commit SHA are identical.
- [ ] Secret scan and dependency/image scans pass; SBOM is retained.
- [ ] All tests, Ruff, Mypy, compileall and Alembic rehearsal pass.
- [ ] PostgreSQL owner/runtime roles and RLS suite pass on the target engine.
- [ ] Enterprise OIDC login, refresh, logout, disablement and role/tenant mapping pass.
- [ ] 100/100 expert-approved release dataset and online release gate pass.
- [ ] 1/10/30/50 staging load profile meets agreed SLOs with no pool/queue leak.
- [ ] Backup is recent, immutable, hash-valid and restored successfully in isolation.
- [ ] Grafana dashboard, alerts, on-call routing and runbooks are active.
- [ ] Change owner approves rollout, rollback point and observation window.

## Start and verify staging

Create ignored `.env.staging` from the example, inject real secrets, then:

```powershell
scripts\start_staging.ps1
```

The script validates Compose, builds, waits for health and prints service state. Verify HTTPS
entry points, both readiness endpoints, Prometheus targets, Grafana panels, OIDC flow, a synthetic
import/query and tenant isolation. Stop while preserving all volumes:

```powershell
scripts\stop_staging.ps1
```

`-RemoveContainers` removes containers/networks but still never removes named volumes.

## Rollout

Deploy the immutable image by digest to a canary. Keep database migrations backward compatible
during the observation window. Compare error, p95/p99, pool, queues, Milvus/model latency, cost,
abstention and authorization-denial signals against the baseline. Expand traffic only through a
reviewed change.

## Rollback checklist

- [ ] Stop rollout and freeze document writes/workers if data consistency may be affected.
- [ ] Route traffic to the last known-good image/config; do not reuse changed mutable tags.
- [ ] If schema rollback is required, rehearse it on a restored copy and use the reviewed Alembic
      downgrade path. Prefer forward repair when old code remains schema compatible.
- [ ] Switch Milvus aliases to the preserved prior collection; never delete either collection.
- [ ] Preserve PostgreSQL/MinIO/Milvus/etcd volumes, logs, audit events and trace IDs.
- [ ] Re-run health, RLS, OIDC, tenant/image access and grounded query smoke checks.
- [ ] Record incident and corrective actions before another rollout.
