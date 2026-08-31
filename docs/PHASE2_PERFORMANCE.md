# Phase 2 performance and resilience acceptance

## Executed local results

The repeatable harness `scripts/run_performance_acceptance.py` ran against generated data only. It exercised real FastAPI request handling, validation and SQLite metadata persistence while replacing external model/parser calls with deterministic stubs.

| Scenario | Success | P50 | P95 | P99 |
|---|---:|---:|---:|---:|
| 20 concurrent non-stream query requests | 20/20 (100%) | 615.25 ms | 801.24 ms | 808.39 ms |
| 5 concurrent Markdown imports | 5/5 (100%) | 644.65 ms | 655.13 ms | 655.13 ms |

The 1 MiB configured upload boundary rejected a 1 MiB+ payload with HTTP 413 and stable code `FILE_TOO_LARGE`. The run used 1.9375 process CPU seconds, 2.1331 seconds wall time and 8.14 MiB peak traced Python memory. The machine-readable result is `docs/reports/phase2_performance.json`.

The live local Milvus phase migration separately executed 100 cross-tenant probes with zero leaks. Lifecycle tests demonstrate stage-ledger recovery after a worker crash, dead-letter/manual retry, MinIO failure compensation, Milvus-stage failure handling and repeated-delete idempotency. SSE cancellation testing confirms the in-memory session queue is removed when the client disconnects.

## What these numbers do not claim

The latency figures are not real DashScope model latency or MinerU parsing throughput. Docker, a PostgreSQL server, Redis server and staging MinIO are unavailable on this host, so the following cannot be honestly measured here:

- provider timeout rate and 20-way real model concurrency;
- Celery queue backlog and worker-process restart under Redis;
- Redis restart recovery;
- PostgreSQL connection interruption/recovery;
- a live MinIO interruption;
- staging CPU/container memory under the production-shaped compose stack.

These are mandatory staging acceptance cases. Use Prometheus to record success rate, P50/P95/P99, CPU, memory, queue length, model timeout rate and error-code distribution. The committed alert rules cover elevated 5xx rate, API P95 latency, worker backlog, model timeout rate and cleanup failures.
