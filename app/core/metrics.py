"""Prometheus metrics shared by workflows and workers."""

from prometheus_client import Counter, Histogram


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
