# Rollback checklist

Use this checklist after the incident commander decides to roll back. Preserve evidence and data;
do not delete named volumes, collections, backups, uploads, logs, or audit events.

1. Stop traffic expansion and pause import workers if consistency may be affected.
2. Record deployment SHA/digest, UTC time, alert, correlation/trace IDs, affected tenants, and the
   current PostgreSQL/Milvus/object-store state.
3. Route traffic to the last known-good immutable image and its reviewed configuration.
4. Prefer forward-compatible application rollback without schema downgrade. If a downgrade is
   unavoidable, restore a copy and rehearse the exact Alembic path before touching the target.
5. Switch the Milvus alias only to the preserved prior collection. Never drop either collection
   during incident response.
6. Restore PostgreSQL, uploads/MinIO, Milvus/etcd, and checkpoint data only from hash-validated
   backups under an approved recovery plan.
7. Run liveness/readiness, two-tenant RLS, OIDC login/logout, cross-tenant session/image denial,
   document version, grounded query, and citation smoke checks.
8. Confirm pool, queue, outbox, error, latency, authorization-denial, model-cost, and Milvus signals
   return to the known-good range.
9. Keep the failed release artifacts and telemetry for investigation; document RTO/RPO and data
   loss, if any.
10. Require reviewed corrective actions and all release gates before another rollout.

For executable backup/restore procedures and limitations, see `docs/DISASTER_RECOVERY.md`.

