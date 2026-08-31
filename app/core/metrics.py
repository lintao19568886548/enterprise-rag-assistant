"""Prometheus metrics shared by workflows and workers."""

from prometheus_client import Counter, Gauge, Histogram


WORKFLOW_NODE_RUNS = Counter(
    "kb_workflow_node_runs_total",
    "Workflow node executions",
    ("workflow", "node", "status"),
)
WORKFLOW_NODE_LATENCY = Histogram(
    "kb_workflow_node_duration_seconds",
    "Workflow node latency",
    ("workflow", "node"),
)
IMPORT_TASKS = Counter(
    "kb_import_tasks_total",
    "Import task terminal states",
    ("status", "retryable"),
)
RETRIEVAL_RESULTS = Histogram(
    "kb_retrieval_result_count",
    "Number of documents returned by a retrieval path",
    ("path",),
    buckets=(0, 1, 2, 3, 5, 10, 20, 50),
)
MILVUS_RETRIEVAL_LATENCY = Histogram(
    "kb_milvus_retrieval_duration_seconds",
    "Milvus retrieval latency",
    ("path", "status"),
)
WORKER_QUEUE_LENGTH = Gauge(
    "kb_worker_queue_length",
    "Pending Celery messages by queue",
    ("queue",),
)
CLEANUP_EVENTS = Counter(
    "kb_cleanup_events_total",
    "Document cleanup stage outcomes",
    ("status", "stage"),
)
AUDIT_EVENTS = Counter(
    "kb_audit_events_total",
    "Persisted enterprise audit events",
    ("event_type", "outcome"),
)
AUTH_EVENTS = Counter(
    "kb_authentication_events_total",
    "Authentication outcomes without credential contents",
    ("method", "outcome", "reason"),
)
RAG_CONFIDENCE = Histogram(
    "kb_rag_answer_confidence",
    "Grounded answer confidence",
    buckets=(0, 0.1, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 1.0),
)
RAG_CITATIONS = Histogram(
    "kb_rag_citation_count",
    "Citations returned per answer",
    buckets=(0, 1, 2, 3, 5, 8, 10, 20),
)
RAG_EVIDENCE = Counter(
    "kb_rag_evidence_total",
    "Answers with or without sufficient evidence",
    ("sufficient",),
)
