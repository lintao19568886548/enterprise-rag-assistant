import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.import_process.agent.nodes.node_import_milvus import NodeImportMilvus
from app.utils.milvus_utils import build_chunk_filter, build_scope_filter


def _filter_records(records, expression):
    equality = dict(re.findall(r'(tenant_id|knowledge_base_id|document_id) == "([^"]*)"', expression))
    active_only = "is_active == true" in expression
    return [
        record
        for record in records
        if all(str(record.get(field)) == expected for field, expected in equality.items())
        and (not active_only or record.get("is_active") is True)
    ]


def test_scope_filter_requires_both_server_owned_boundaries():
    with pytest.raises(ValueError, match="tenant_id"):
        build_scope_filter(None, "kb")
    with pytest.raises(ValueError, match="knowledge_base_id"):
        build_scope_filter("tenant", None)


def test_one_hundred_cross_tenant_vector_queries_have_zero_leaks():
    records = []
    for round_number in range(100):
        for tenant in ("tenant-a", "tenant-b"):
            records.append(
                {
                    "tenant_id": tenant,
                    "knowledge_base_id": "shared-kb-name",
                    "document_id": f"same-manual-{round_number}",
                    "is_active": True,
                }
            )
    leak_count = 0
    for round_number in range(100):
        expression = build_chunk_filter([], "tenant-a", "shared-kb-name")
        results = _filter_records(records, expression)
        leak_count += sum(record["tenant_id"] != "tenant-a" for record in results)
        assert any(record["document_id"] == f"same-manual-{round_number}" for record in results)
    assert leak_count == 0


def test_concurrent_filter_building_never_reuses_another_tenant():
    scopes = [(f"tenant-{index}", f"kb-{index}") for index in range(100)]
    with ThreadPoolExecutor(max_workers=20) as executor:
        expressions = list(executor.map(lambda scope: build_chunk_filter([], *scope), scopes))
    for (tenant_id, knowledge_base_id), expression in zip(scopes, expressions, strict=True):
        assert f'tenant_id == "{tenant_id}"' in expression
        assert f'knowledge_base_id == "{knowledge_base_id}"' in expression
        assert expression.count("tenant_id ==") == 1


def test_import_rejects_mixed_tenant_vector_batch():
    chunks = [
        {
            "dense_vector": [0.1, 0.2],
            "sparse_vector": {1: 0.5},
            "tenant_id": "tenant-a",
            "knowledge_base_id": "kb-a",
            "document_id": "doc-a",
            "document_version": 1,
            "task_id": "task-a",
            "is_active": True,
        },
        {
            "dense_vector": [0.2, 0.3],
            "sparse_vector": {2: 0.5},
            "tenant_id": "tenant-b",
            "knowledge_base_id": "kb-a",
            "document_id": "doc-a",
            "document_version": 1,
            "task_id": "task-a",
            "is_active": True,
        },
    ]
    with pytest.raises(ValueError, match="不一致的租户范围"):
        NodeImportMilvus()._step_1_check_input({"chunks": chunks})


class _Schema:
    def __init__(self):
        self.fields = []

    def add_field(self, *args, **kwargs):
        if args:
            kwargs = {"field_name": args[0], "datatype": args[1], **kwargs}
        self.fields.append(kwargs)


class _Indexes:
    def add_index(self, **_kwargs):
        return None


class _CollectionClient:
    def __init__(self):
        self.schema = _Schema()

    def create_schema(self, **_kwargs):
        return self.schema

    def prepare_index_params(self):
        return _Indexes()

    def create_collection(self, **_kwargs):
        return None


def test_new_chunk_collection_uses_tenant_partition_key():
    client = _CollectionClient()
    NodeImportMilvus()._create_collection(client, "chunks_v2", 2)
    tenant_field = next(field for field in client.schema.fields if field["field_name"] == "tenant_id")
    assert tenant_field["is_partition_key"] is True
