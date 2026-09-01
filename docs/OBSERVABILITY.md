# Observability

## Signals

Both APIs expose internal Prometheus metrics at `/metrics`; Nginx does not publish that endpoint
externally. `deploy/prometheus/prometheus.yml` scrapes the import and query services.

Implemented signals include:

- HTTP request count, route/status, latency and in-flight requests;
- LangGraph node executions and latency by workflow/node;
- import outcomes, cleanup stages and Redis queue length;
- embedding batches/items/latency and Milvus result count/latency;
- model calls, timeouts/errors, latency, token direction and estimated USD cost;
- query end-to-end latency, evidence sufficiency, confidence and citation count;
- PostgreSQL pool size, checked-out/overflow connections and checkout timeouts;
- authentication outcome/reason and persisted audit-event outcome.

Every HTTP response carries `X-Request-ID` and `X-Trace-ID`. Structured completion logs add the
service, route, status, latency, tenant and user where available. Workflow logs add the session or
task and knowledge-base context. Secrets are recursively redacted. User questions, answers,
history and retrieved document contents are represented only by type/length unless
`LOG_SENSITIVE_CONTENT=true`; deployed validation rejects that unsafe setting.

## Dashboard and alerts

Import `deploy/grafana/enterprise-rag-dashboard.json` into Grafana and bind it to the Prometheus
data source. It includes API rate/errors/in-flight, HTTP percentiles, LangGraph stages, model,
embedding/Milvus, queues, PostgreSQL pool, RAG evidence/citations, security and cleanup panels.

`deploy/prometheus/alerts.yml` covers:

- service target down;
- API 5xx and p95 latency;
- worker and cleanup/outbox queue backlog;
- model timeouts and estimated-cost spike;
- cleanup failures, import failures and Milvus failures;
- PostgreSQL pool exhaustion/timeouts;
- authorization-denial spike and high abstention ratio.

The initial thresholds are operational starting points. Tune them only from measured staging or
production history and preserve a change record; never relax them solely to make a release pass.

## Operator queries

Useful PromQL:

```promql
histogram_quantile(0.95, sum by (le, service) (rate(kb_http_request_duration_seconds_bucket[5m])))
histogram_quantile(0.95, sum by (le, workflow, node) (rate(kb_workflow_node_duration_seconds_bucket[5m])))
sum by (model, status) (rate(kb_model_calls_total[5m]))
kb_database_pool_checked_out / clamp_min(kb_database_pool_size + kb_database_pool_overflow, 1)
sum(increase(kb_model_estimated_cost_usd_total[1h]))
sum by (queue) (kb_worker_queue_length)
```

Never add tenant IDs, user IDs, questions, document titles or session IDs as Prometheus labels;
they are high-cardinality or sensitive and belong in access-controlled logs/audit storage.
