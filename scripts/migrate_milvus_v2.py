"""Create and verify a partition-key Milvus v2 collection without deleting source data.

The default mode is read-only. ``--execute`` creates and copies to a new target;
``--switch-alias`` is a separate opt-in and is allowed only after count and
scope verification succeed. The source collection is never renamed or dropped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from pymilvus import DataType, MilvusClient

from app.core.settings import settings
from app.db.repositories import DEFAULT_KNOWLEDGE_BASE_ID, DEFAULT_TENANT_ID
from app.utils.milvus_utils import build_scope_filter


SAFE_COLLECTION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,254}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=settings.milvus_collection)
    parser.add_argument("--target")
    parser.add_argument("--alias")
    parser.add_argument("--kind", choices=("chunks", "items"), default="chunks")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help="verify an existing target without overwriting it",
    )
    parser.add_argument("--switch-alias", action="store_true")
    parser.add_argument("--default-tenant", default=DEFAULT_TENANT_ID)
    parser.add_argument("--default-knowledge-base", default=DEFAULT_KNOWLEDGE_BASE_ID)
    return parser.parse_args()


def _validate_name(value: str, label: str) -> str:
    if not SAFE_COLLECTION.fullmatch(value):
        raise ValueError(f"invalid {label} name")
    return value


def _read_all(
    client: MilvusClient,
    collection_name: str,
    output_fields: list[str],
) -> list[dict[str, Any]]:
    iterator = client.query_iterator(
        collection_name=collection_name,
        batch_size=500,
        filter="",
        output_fields=output_fields,
    )
    rows: list[dict[str, Any]] = []
    try:
        while True:
            batch = iterator.next()
            if not batch:
                return rows
            rows.extend(dict(item) for item in batch)
    finally:
        iterator.close()


def _count_filter(client: MilvusClient, collection_name: str, primary_name: str, expression: str) -> int:
    iterator = client.query_iterator(
        collection_name=collection_name,
        batch_size=500,
        filter=expression,
        output_fields=[primary_name],
    )
    count = 0
    try:
        while True:
            batch = iterator.next()
            if not batch:
                return count
            count += len(batch)
    finally:
        iterator.close()


def _field_dimension(description: dict[str, Any], field_name: str) -> int:
    for field in description.get("fields", []):
        if field.get("name") == field_name:
            params = field.get("params") or {}
            return int(params.get("dim") or field.get("dim") or settings.embedding_dimension)
    return settings.embedding_dimension


def _create_schema(
    client: MilvusClient,
    kind: str,
    dimension: int,
) -> tuple[Any, Any, str]:
    schema = client.create_schema(auto_id=True, enable_dynamic_field=True)
    primary_name = "chunk_id" if kind == "chunks" else "pk"
    schema.add_field(primary_name, DataType.INT64, is_primary=True, auto_id=True)
    schema.add_field("tenant_id", DataType.VARCHAR, max_length=64, is_partition_key=True)
    schema.add_field("knowledge_base_id", DataType.VARCHAR, max_length=64)
    schema.add_field("document_id", DataType.VARCHAR, max_length=64)
    schema.add_field("document_version", DataType.INT64)
    schema.add_field("task_id", DataType.VARCHAR, max_length=64)
    schema.add_field("is_active", DataType.BOOL)
    schema.add_field("file_title", DataType.VARCHAR, max_length=65535)
    schema.add_field("item_name", DataType.VARCHAR, max_length=65535)
    if kind == "chunks":
        schema.add_field("content", DataType.VARCHAR, max_length=65535)
        schema.add_field("title", DataType.VARCHAR, max_length=65535)
        schema.add_field("parent_title", DataType.VARCHAR, max_length=65535)
        schema.add_field("part", DataType.INT8)
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=dimension)
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)

    indexes = client.prepare_index_params()
    indexes.add_index(
        field_name="dense_vector",
        index_name="dense_vector_index",
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )
    indexes.add_index(
        field_name="sparse_vector",
        index_name="sparse_vector_index",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="IP",
        params={"inverted_index_algo": "DAAT_MAXSCORE"},
    )
    return schema, indexes, primary_name


def _normalize_row(
    row: dict[str, Any],
    index: int,
    *,
    default_tenant: str,
    default_knowledge_base: str,
    primary_name: str,
) -> dict[str, Any]:
    migrated = dict(row)
    migrated.pop(primary_name, None)
    content = str(migrated.get("content") or "")
    migrated.update(
        {
            "tenant_id": str(migrated.get("tenant_id") or default_tenant),
            "knowledge_base_id": str(
                migrated.get("knowledge_base_id") or default_knowledge_base
            ),
            "document_id": str(migrated.get("document_id") or ""),
            "document_version": int(migrated.get("document_version") or 1),
            "task_id": str(migrated.get("task_id") or "legacy-migration"),
            "is_active": bool(migrated.get("is_active", True)),
            "file_title": str(migrated.get("file_title") or migrated.get("file_name") or ""),
            "item_name": str(migrated.get("item_name") or ""),
        }
    )
    migrated.setdefault("part", 0)
    migrated.setdefault("content_hash", hashlib.sha256(content.encode("utf-8")).hexdigest())
    migrated.setdefault("created_at", datetime.now(UTC).isoformat())
    migrated.setdefault("chunk_index", index)
    return migrated


def _switch_alias(client: MilvusClient, alias: str, target: str) -> None:
    if alias in client.list_aliases():
        client.alter_alias(collection_name=target, alias=alias)
    else:
        client.create_alias(collection_name=target, alias=alias)


def migrate(args: argparse.Namespace) -> dict[str, Any]:
    source = _validate_name(args.source, "source")
    target = _validate_name(args.target or f"{source}_v2", "target")
    alias = _validate_name(args.alias or f"{source}_active", "alias")
    if source == target:
        raise ValueError("source and target must be different")
    if args.switch_alias and not args.execute:
        raise ValueError("--switch-alias requires --execute")

    client = MilvusClient(
        uri=settings.milvus_uri,
        token=settings.reveal(settings.milvus_token),
    )
    if not client.has_collection(collection_name=source):
        raise LookupError(f"source collection does not exist: {source}")
    client.load_collection(collection_name=source)
    description = client.describe_collection(collection_name=source)
    static_fields = [str(field["name"]) for field in description.get("fields", [])]
    metadata_fields = [
        "tenant_id",
        "knowledge_base_id",
        "document_id",
        "document_version",
        "task_id",
        "is_active",
        "file_name",
        "page_number",
        "section_title",
        "chunk_index",
        "content_hash",
        "parser_version",
    ]
    output_fields = list(
        dict.fromkeys(
            [*static_fields, *metadata_fields]
            if description.get("enable_dynamic_field")
            else static_fields
        )
    )
    rows = _read_all(client, source, output_fields)
    plan = {
        "mode": "execute" if args.execute else "dry-run",
        "source": source,
        "target": target,
        "alias": alias,
        "kind": args.kind,
        "source_count": len(rows),
        "source_partition_key": description.get("partition_key_field"),
        "source_is_preserved": True,
    }
    if not args.execute:
        return plan
    target_exists = client.has_collection(collection_name=target)
    if target_exists and not args.verify_existing:
        raise RuntimeError(
            "target already exists; refusing to overwrite it. Choose a new versioned target name."
        )

    primary_name = "chunk_id" if args.kind == "chunks" else "pk"
    normalized = [
        _normalize_row(
            row,
            index,
            default_tenant=args.default_tenant,
            default_knowledge_base=args.default_knowledge_base,
            primary_name=primary_name,
        )
        for index, row in enumerate(rows)
    ]
    if not target_exists:
        dimension = _field_dimension(description, "dense_vector")
        schema, indexes, primary_name = _create_schema(client, args.kind, dimension)
        client.create_collection(collection_name=target, schema=schema, index_params=indexes)
        for start in range(0, len(normalized), 500):
            client.insert(collection_name=target, data=normalized[start : start + 500])
        client.flush(collection_name=target)
    client.load_collection(collection_name=target)

    target_rows = _read_all(client, target, [primary_name, "tenant_id", "knowledge_base_id"])
    if len(target_rows) != len(rows):
        raise RuntimeError(
            f"count verification failed: source={len(rows)}, target={len(target_rows)}; "
            "the source remains unchanged and the target was retained for inspection"
        )
    expected_scopes: dict[tuple[str, str], int] = {}
    for row in normalized:
        scope = (str(row["tenant_id"]), str(row["knowledge_base_id"]))
        expected_scopes[scope] = expected_scopes.get(scope, 0) + 1
    for (tenant_id, knowledge_base_id), expected in expected_scopes.items():
        actual = _count_filter(
            client,
            target,
            primary_name,
            build_scope_filter(tenant_id, knowledge_base_id, active_only=False),
        )
        if actual != expected:
            raise RuntimeError(
                f"scope verification failed for tenant/knowledge-base: expected={expected}, actual={actual}"
            )
    if args.switch_alias:
        _switch_alias(client, alias, target)
    plan.update(
        {
            "target_count": len(target_rows),
            "scope_count": len(expected_scopes),
            "verified": True,
            "alias_switched": bool(args.switch_alias),
        }
    )
    return plan


def main() -> None:
    print(json.dumps(migrate(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
