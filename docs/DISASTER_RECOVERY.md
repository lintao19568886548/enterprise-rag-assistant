# Disaster recovery runbook

## Objectives and ownership

- Recovery point objective (RPO): 15 minutes for PostgreSQL and object data.
- Recovery time objective (RTO): 2 hours at the current data volume.
- The on-call operator owns recovery; the tenant administrator validates application-level counts and permissions.
- Backups are immutable, encrypted outside this repository, retained according to company policy, and restored into an isolated project first.

## Backup contents

Freeze API and worker writes first, then run `scripts/backup_staging.ps1` from a trusted operations
host with ignored `.env.staging` available. The script creates a new timestamped directory and
refuses to overwrite an existing backup. PostgreSQL, MinIO and Milvus/etcd are separate stores, so
an unfrozen live copy must be treated only as crash-consistent evidence. The backup contains:

1. A logical PostgreSQL custom-format dump without owners or privileges.
2. Filesystem copies of MinIO, Milvus, etcd and compatible `output` volumes captured during the
   write freeze.
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

On 2026-09-01 a full local Docker storage-path drill was executed from
`backups/staging/20260901_155648` into isolated project
`enterprise-rag-premerge-recovery`. SHA-256 validation, PostgreSQL custom-dump restore, volume
restore, Alembic `d4e5f6a7b8c9`, 18-table exact row-count comparison, 16 RLS-enabled tables, zero
unvalidated foreign keys and healthy PostgreSQL/Redis/MinIO/etcd/Milvus all passed. The recovery
containers were stopped without deleting their volumes. This validates the mechanism, not the
production RTO/RPO or business-data scale: the local staging database had only bootstrap identity
records and no business documents. A real target-environment drill is still required.

The Windows drill also found and fixed a binary-safety defect: PowerShell text pipelines must not
carry `pg_dump` or tar bytes. The scripts now create binary files inside disposable containers and
transfer them with `docker cp`. Recovery refuses pre-existing project containers/volumes so that a
retry cannot silently mix old and restored state.

## Prometheus operations

Prometheus scrapes only the internal backend network; Nginx returns 404 for public `/metrics`. Dashboards should calculate P50/P95/P99 from `kb_http_request_duration_seconds_bucket`, plus error rate, model success/error/timeout, token/cost counters, Milvus latency/result count, workflow node duration, queue backlog, cleanup failures and RAG confidence/citation/evidence metrics. Alert rules are in `deploy/prometheus/alerts.yml`.
