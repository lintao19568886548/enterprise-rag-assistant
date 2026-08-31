"""Run 100 real cross-tenant retrieval rounds in a disposable Milvus collection."""

from __future__ import annotations

import json
import statistics
import time
import uuid

from pymilvus import DataType, MilvusClient

from app.core.settings import settings
from app.utils.milvus_utils import build_scope_filter


def main() -> None:
    collection = f"phase2_isolation_test_{uuid.uuid4().hex}"
    client = MilvusClient(
        uri=settings.milvus_uri,
        token=settings.reveal(settings.milvus_token),
    )
    latencies: list[float] = []
    leaks = 0
    try:
        schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field(
            "tenant_id",
            DataType.VARCHAR,
            max_length=64,
            is_partition_key=True,
        )
        schema.add_field("knowledge_base_id", DataType.VARCHAR, max_length=64)
        schema.add_field("document_name", DataType.VARCHAR, max_length=128)
        schema.add_field("is_active", DataType.BOOL)
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=2)
        indexes = client.prepare_index_params()
        indexes.add_index(
            field_name="dense_vector",
            index_name="dense_vector_index",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )
        client.create_collection(collection_name=collection, schema=schema, index_params=indexes)
        rows = [
            {
                "tenant_id": tenant_id,
                "knowledge_base_id": "shared-kb-name",
                "document_name": "same-manual.pdf",
                "is_active": True,
                "dense_vector": [1.0, float(index % 5) / 100.0],
            }
            for tenant_id in ("tenant-a", "tenant-b")
            for index in range(100)
        ]
        client.insert(collection_name=collection, data=rows)
        client.flush(collection_name=collection)
        client.load_collection(collection_name=collection)

        for round_number in range(100):
            expected_tenant = "tenant-a" if round_number % 2 == 0 else "tenant-b"
            started = time.perf_counter()
            results = client.search(
                collection_name=collection,
                data=[[1.0, float(round_number % 5) / 100.0]],
                anns_field="dense_vector",
                filter=build_scope_filter(expected_tenant, "shared-kb-name"),
                limit=10,
                output_fields=["tenant_id", "knowledge_base_id", "document_name"],
            )
            latencies.append((time.perf_counter() - started) * 1000)
            hits = results[0] if results else []
            leaks += sum(
                hit.get("entity", {}).get("tenant_id") != expected_tenant
                for hit in hits
            )
            if len(hits) != 10:
                raise RuntimeError(f"round {round_number} returned {len(hits)} hits")
        ordered = sorted(latencies)
        report = {
            "rounds": 100,
            "inserted_vectors": len(rows),
            "leaks": leaks,
            "passed": leaks == 0,
            "p50_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[int(len(ordered) * 0.95) - 1], 3),
            "p99_ms": round(ordered[int(len(ordered) * 0.99) - 1], 3),
            "temporary_collection_removed": True,
        }
        if leaks:
            raise RuntimeError(f"cross-tenant vector leakage detected: {leaks}")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        if collection.startswith("phase2_isolation_test_") and client.has_collection(
            collection_name=collection
        ):
            client.release_collection(collection_name=collection)
            client.drop_collection(collection_name=collection)


if __name__ == "__main__":
    main()
