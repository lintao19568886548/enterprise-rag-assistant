# Disaster recovery runbook

## Objectives and ownership

- Recovery point objective (RPO): 15 minutes for PostgreSQL and object data.
- Recovery time objective (RTO): 2 hours at the current data volume.
- The on-call operator owns recovery; the tenant administrator validates application-level counts and permissions.
- Backups are immutable, encrypted outside this repository, retained according to company policy, and restored into an isolated project first.

## Backup contents

Run `scripts/backup_staging.ps1` from a trusted operations host with ignored `.env.staging` available. The script creates a new timestamped directory and refuses to overwrite an existing backup. It contains:

1. A logical PostgreSQL custom-format dump without owners or privileges.
2. Point-in-time filesystem snapshots of MinIO, Milvus, etcd and compatible `output` data volumes.
3. Deployment and migration structure, using `.env.staging.example`; real `.env`, certificates, private keys and tokens are excluded.
4. SHA-256 hashes plus a manifest.

Milvus has two recovery paths. The preferred path restores the coordinated Milvus/etcd/MinIO snapshots. If that snapshot is inconsistent, rebuild a new collection from PostgreSQL chunk metadata and retained source objects, run tenant-isolation validation, and switch aliases atomically. Never delete an old collection during recovery.

## Recovery order

1. Declare the incident and freeze writes at Nginx or the worker queues.
2. Select the newest backup whose hashes and manifest validate.
3. Run `scripts/restore_staging.ps1 -BackupPath <path> -RecoveryProjectName enterprise-rag-recovery-<incident> -ConfirmRestore`. The script rejects the live staging project name and verifies every recorded SHA-256 before creating recovery volumes.
4. Restore PostgreSQL first, run Alembic to the recorded head, then validate RLS owner/runtime roles.
5. Restore MinIO, etcd and Milvus snapshots. If Milvus health or counts fail, use the non-destructive rebuild path above.
6. Restore `output` only for parser compatibility and unfinished jobs. PostgreSQL remains the metadata authority.
7. Start Redis empty, then application APIs, then import/cleanup/evaluation workers, then Nginx.
8. Keep the recovered environment isolated until all checks pass. Promote traffic only through the infrastructure change process.

## Consistency checks

- `alembic current` equals the recorded head.
- `PRAGMA foreign_key_check` for SQLite drills, or PostgreSQL orphan/FK checks, returns zero rows.
- Tenant, knowledge-base, document, version, chunk, task, session and message counts match the manifest or incident expectations.
- Each active document version has the expected SQL chunk count and exactly one active vector version.
- MinIO source objects referenced by active versions exist; no internal object path is exposed through public APIs.
- Milvus active aliases resolve to the expected collections; 100 cross-tenant probes return zero leaks.
- A readonly principal cannot write; an editor cannot manage a tenant; a tenant cannot read another tenant's knowledge base, task, session, image or vectors.
- Both `/health/ready` endpoints report `ready`, and a synthetic import plus grounded query succeeds.

## Rollback

Do not modify the failed production volumes. If recovered checks fail, keep traffic on the last known-good environment, preserve logs and backup artifacts, and start another isolated recovery project from the previous immutable backup. Database migration rollback must be rehearsed on a copy first. Milvus rollback changes aliases back to a preserved collection; it never deletes the failed or previous collection.

## Recorded local drills

`docs/reports/phase2_recovery_drill.json` records an actually executed backup/restore of generated, sanitized SQLite data. It validates row counts, a deterministic digest, foreign keys and SQLite integrity. It passed with one tenant, two documents, zero foreign-key errors and `integrity_check=ok`.

The expanded Phase 3 drill is recorded in `docs/reports/phase3_recovery_drill.json`. It actually
restored generated tenant, user/membership, knowledge-base, two documents/versions, session, two
messages, citation, image and a two-record vector manifest representing seven chunks. It passed
with zero foreign-key errors, `integrity_check=ok`, a deterministic digest and 0.2168 seconds local
elapsed time. This is a sanitized SQLite control-path RTO measurement, not a staging RTO/RPO.

The full PostgreSQL/MinIO/Milvus staging restore cannot be honestly executed on this computer because Docker, PostgreSQL server/client and a real staging identity configuration are unavailable. The scripts and compose topology are present and statically validated, but a real external-service drill remains an operations acceptance item once that environment exists.

## Prometheus operations

Prometheus scrapes only the internal backend network; Nginx returns 404 for public `/metrics`. Dashboards should calculate P50/P95/P99 from `kb_http_request_duration_seconds_bucket`, plus error rate, model success/error/timeout, token/cost counters, Milvus latency/result count, workflow node duration, queue backlog, cleanup failures and RAG confidence/citation/evidence metrics. Alert rules are in `deploy/prometheus/alerts.yml`.
