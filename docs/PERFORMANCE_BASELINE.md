# Phase 3 performance baseline

## Executed local load profile

On 2026-09-01 (Asia/Shanghai), `scripts/run_performance_acceptance.py` executed generated,
non-business data through real FastAPI handlers and a temporary SQLite database. The provider,
parser and Milvus calls were disabled or deterministic stubs. Every scenario ran at 1, 10, 30,
and 50 concurrent workers. The full result is in
`docs/reports/phase3_performance.json`.

At 50 concurrent workers:

| Local scenario | Requests | Throughput | P50 | P95 | P99 | Error/timeout |
|---|---:|---:|---:|---:|---:|---:|
| Health check | 50 | 144.95 req/s | 59.08 ms | 93.95 ms | 99.09 ms | 0% / 0% |
| Knowledge-base list | 50 | 30.93 req/s | 1125.92 ms | 1365.96 ms | 1404.35 ms | 0% / 0% |
| Session history | 50 | 16.14 req/s | 2255.40 ms | 2639.78 ms | 2654.40 ms | 0% / 0% |
| Ordinary query, local path | 50 | 14.92 req/s | 2910.04 ms | 3067.59 ms | 3112.56 ms | 0% / 0% |
| Document-import API, local path | 50 | 10.05 req/s | 4786.10 ms | 4860.71 ms | 4886.25 ms | 0% / 0% |

The complete run took 29.1120 seconds, used 29.7656 process CPU seconds and reached 12.42 MiB
of Python-traced peak memory. The final SQLAlchemy pool status had five connections returned to
the pool and zero checked-out/overflow connections. The 1 MiB upload-boundary probe returned
HTTP 413 and `FILE_TOO_LARGE`.

These figures are a regression baseline for local code paths, not a production capacity claim.
FastAPI `TestClient` puts client and server in one process and adds test-transport overhead.

## Latency separation

The query response now exposes legacy `latency_ms` plus `model_latency_ms`,
`total_latency_ms`, and `local_latency_ms`. Local latency is the measured end-to-end workflow
time minus measured model time. Provider token and estimated cost fields are returned when the
provider supplies usage metadata and cost rates are configured.

The executed local load profile intentionally reports online model, Milvus, OIDC, streaming,
PostgreSQL, Redis, MinIO, and worker-queue latency as `not_exercised`. Those figures require the
staging stack and cannot be inferred from the local result.

## Stability controls

- Milvus keepalive defaults remain 300 seconds with permit-without-calls disabled, preventing the
  earlier `ENHANCE_YOUR_CALM / too_many_pings` condition.
- Model clients are cached and have bounded timeouts, bounded retries, fallback allowlists, and a
  circuit breaker.
- Reranker retries are bounded and now use exponential delay plus jitter.
- PostgreSQL pool size, overflow, timeout, recycle, and pre-ping are configured centrally.
- Redis-backed rate limiting is mandatory in deployed environments; the local load harness
  disables it so the test measures handlers rather than the configured admission threshold.

## Required staging profile

Before production, repeat 1/10/30/50 concurrency against staging for OIDC login/refresh/logout,
document list/import, ordinary and streaming queries, Milvus retrieval, and workers. Record host
CPU/memory, PostgreSQL pool, Redis queues, Milvus p95, provider p95, tokens/cost, errors and
timeouts from Prometheus. Do not reuse this local baseline as the staging result.
