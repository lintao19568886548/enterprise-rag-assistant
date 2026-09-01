# Incident runbook

## First response

1. Acknowledge the alert, record incident time/owner and preserve request/trace IDs.
2. Check `/health/live`, `/health/ready`, Prometheus targets and the Grafana operations dashboard.
3. Decide whether impact is one tenant, one dependency, one worker queue or both APIs.
4. Freeze document writes/import workers before any recovery that could create split state.
5. Do not print secrets, document content or user questions into the incident channel.
6. Prefer rollback or traffic isolation over deleting data. Never run `docker compose down -v`.

## Common failures

### API unavailable or high 5xx

- Correlate `kb_http_requests_total`, p95 and in-flight metrics with recent deploy SHA.
- Inspect structured logs by trace ID; confirm PostgreSQL/Milvus/Redis/MinIO readiness components.
- If correlated with a release, stop rollout and use the release rollback checklist.

### PostgreSQL pool exhaustion

- Check checked-out, configured size, overflow and timeout counters.
- Find long transactions and connection leaks before increasing pool size.
- Confirm application runtime is not a superuser and does not have `BYPASSRLS`.

### Milvus errors or `too_many_pings`

- Verify keepalive is 300000ms and permit-without-calls is false.
- Check Milvus/etcd/MinIO health and retrieval error metrics.
- Do not delete or recreate the active collection. Rebuild a new collection and switch aliases
  only after count and tenant-isolation checks pass.

### Import or cleanup backlog

- Check Redis queue lengths and worker health.
- Inspect persisted task/outbox status and retryability. Retry through the idempotent API rather
  than manually deleting rows or objects.
- Preserve dead-letter records and audit actions.

### Model failures or cost spike

- Check provider status, timeout/error series, fallback/circuit state, token and cost counters.
- Reduce admission rate or disable expensive optional paths; do not remove evidence or security
  gates. Rotate any suspected credential locally in the secret manager.

### Authorization denial spike

- Separate expected permission enforcement from a client/config regression.
- Verify issuer/audience, memberships and knowledge-base grants using audit events.
- Treat cross-tenant success as critical: isolate traffic, preserve evidence and begin the security
  response process.

## Recovery and closure

Recover into an isolated Compose project using `docs/DISASTER_RECOVERY.md`. Validate hashes,
migrations, RLS, entity/vector/object counts and tenant isolation before promotion. After service
restoration, record timeline, root cause, affected scope, RTO/RPO, evidence links and corrective
actions. Update alerts/tests for any detection gap.
