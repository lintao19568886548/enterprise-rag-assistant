import threading
import hashlib
from datetime import UTC, datetime
from typing import Any

from pymilvus import AnnSearchRequest, DataType, MilvusClient, WeightedRanker
from app.conf.milvus_config import milvus_config
from app.core.logger import logger

# 全局Milvus客户端实例，实现单例复用
_milvus_client = None
_milvus_client_lock = threading.Lock()
_schema_migration_lock = threading.Lock()


CHUNK_METADATA_FIELDS: dict[str, tuple[DataType, dict[str, Any]]] = {
    "tenant_id": (DataType.VARCHAR, {"max_length": 64}),
    "knowledge_base_id": (DataType.VARCHAR, {"max_length": 64}),
    "document_id": (DataType.VARCHAR, {"max_length": 64}),
    "document_version": (DataType.INT64, {}),
    "task_id": (DataType.VARCHAR, {"max_length": 64}),
    "file_name": (DataType.VARCHAR, {"max_length": 1024}),
    "page_number": (DataType.INT64, {}),
    "section_title": (DataType.VARCHAR, {"max_length": 65535}),
    "parent_chunk_id": (DataType.VARCHAR, {"max_length": 128}),
    "chunk_index": (DataType.INT64, {}),
    "item_aliases": (DataType.JSON, {}),
    "content_hash": (DataType.VARCHAR, {"max_length": 64}),
    "parser_version": (DataType.VARCHAR, {"max_length": 64}),
    "permission_scope": (DataType.VARCHAR, {"max_length": 64}),
    "created_at": (DataType.VARCHAR, {"max_length": 64}),
    "is_active": (DataType.BOOL, {}),
}

ITEM_METADATA_FIELDS: dict[str, tuple[DataType, dict[str, Any]]] = {
    "tenant_id": (DataType.VARCHAR, {"max_length": 64}),
    "knowledge_base_id": (DataType.VARCHAR, {"max_length": 64}),
    "document_id": (DataType.VARCHAR, {"max_length": 64}),
    "document_version": (DataType.INT64, {}),
    "task_id": (DataType.VARCHAR, {"max_length": 64}),
    "is_active": (DataType.BOOL, {}),
}

LEGACY_DEFAULT_KNOWLEDGE_BASE_ID = "00000000-0000-0000-0000-000000000010"
LEGACY_DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000100"


def _read_all_entities(
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


def _legacy_metadata(row: dict[str, Any], index: int) -> dict[str, Any]:
    content = str(row.get("content") or "")
    return {
        "tenant_id": LEGACY_DEFAULT_TENANT_ID,
        "knowledge_base_id": LEGACY_DEFAULT_KNOWLEDGE_BASE_ID,
        "document_id": "",
        "document_version": 1,
        "file_name": str(row.get("file_title") or ""),
        "page_number": None,
        "section_title": str(row.get("title") or row.get("parent_title") or ""),
        "parent_chunk_id": None,
        "chunk_index": index,
        "item_aliases": [],
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "parser_version": "legacy",
        "permission_scope": "private",
        "created_at": datetime.now(UTC).isoformat(),
        "is_active": True,
    }


def _rebuild_legacy_collection(
    client: MilvusClient,
    collection_name: str,
) -> str:
    """Rebuild a fixed-schema collection and retain the renamed original.

    Milvus Lite currently exposes ``add_collection_field`` in the SDK but its
    server returns UNIMPLEMENTED.  Rename + copy is therefore the only
    non-lossy migration available locally.  The backup is never deleted here.
    """
    description = client.describe_collection(collection_name=collection_name)
    field_descriptions = list(description.get("fields", []))
    field_names = [str(field["name"]) for field in field_descriptions]
    rows = _read_all_entities(client, collection_name, field_names)
    primary = next((field for field in field_descriptions if field.get("is_primary")), None)
    primary_name = str(primary["name"]) if primary else ""

    schema = client.create_schema(
        auto_id=bool(description.get("auto_id")),
        enable_dynamic_field=True,
    )
    for field in field_descriptions:
        kwargs = dict(field.get("params") or {})
        if field.get("is_primary"):
            kwargs.update(
                is_primary=True,
                auto_id=bool(field.get("auto_id") or description.get("auto_id")),
            )
        schema.add_field(
            field_name=str(field["name"]),
            datatype=field["type"],
            description=str(field.get("description") or ""),
            **kwargs,
        )

    index_params = client.prepare_index_params()
    for index_name in client.list_indexes(collection_name=collection_name):
        info = client.describe_index(collection_name=collection_name, index_name=index_name)
        extra_params: dict[str, Any] = {}
        if str(info.get("index_type")) == "IVF_FLAT":
            extra_params["params"] = {"nlist": 128}
        elif str(info.get("index_type")) == "SPARSE_INVERTED_INDEX":
            extra_params["params"] = {"inverted_index_algo": "DAAT_MAXSCORE"}
        index_params.add_index(
            field_name=str(info["field_name"]),
            index_name=str(info.get("index_name") or index_name),
            index_type=str(info["index_type"]),
            metric_type=str(info["metric_type"]),
            **extra_params,
        )

    backup_name = f"{collection_name}_legacy_backup_{datetime.now(UTC):%Y%m%d%H%M%S}"
    client.release_collection(collection_name=collection_name)
    client.rename_collection(collection_name, backup_name)
    try:
        client.create_collection(
            collection_name=collection_name,
            schema=schema,
            index_params=index_params,
        )
        for start in range(0, len(rows), 500):
            migrated: list[dict[str, Any]] = []
            for offset, original in enumerate(rows[start : start + 500], start=start):
                row = dict(original)
                if description.get("auto_id") and primary_name:
                    row.pop(primary_name, None)
                for key, value in _legacy_metadata(row, offset).items():
                    row.setdefault(key, value)
                migrated.append(row)
            if migrated:
                client.insert(collection_name=collection_name, data=migrated)
        client.load_collection(collection_name=collection_name)
    except Exception:
        if client.has_collection(collection_name=collection_name):
            client.drop_collection(collection_name=collection_name)
        client.rename_collection(backup_name, collection_name)
        client.load_collection(collection_name=collection_name)
        raise

    logger.warning(
        "Milvus旧集合[{}]已无损迁移，原集合保留为备份[{}]，迁移记录数={}",
        collection_name,
        backup_name,
        len(rows),
    )
    return backup_name


def ensure_collection_fields(
    client: MilvusClient,
    collection_name: str,
    field_specs: dict[str, tuple[DataType, dict[str, Any]]],
) -> list[str]:
    """Add nullable metadata fields to a legacy fixed-schema collection.

    Milvus cannot turn dynamic fields on after collection creation.  Adding
    nullable scalar fields in place preserves every legacy vector while making
    knowledge-base/document filters available to newly imported records.
    """
    with _schema_migration_lock:
        description = client.describe_collection(collection_name=collection_name)
        if description.get("enable_dynamic_field"):
            return []
        existing = {str(field.get("name")) for field in description.get("fields", [])}
        missing = [name for name in field_specs if name not in existing]
        if not missing:
            return []

        logger.warning(
            "Milvus集合[{}]为旧版固定Schema，开始原地补充{}个元数据字段",
            collection_name,
            len(missing),
        )
        client.release_collection(collection_name=collection_name)
        added: list[str] = []
        unsupported_error: Exception | None = None
        try:
            for field_name in missing:
                data_type, kwargs = field_specs[field_name]
                try:
                    client.add_collection_field(
                        collection_name=collection_name,
                        field_name=field_name,
                        data_type=data_type,
                        nullable=True,
                        **kwargs,
                    )
                    added.append(field_name)
                except Exception as exc:
                    if "UNIMPLEMENTED" in str(exc).upper() or "METHOD NOT IMPLEMENTED" in str(exc).upper():
                        unsupported_error = exc
                        break
                    # A second worker may have completed the same migration.
                    refreshed = client.describe_collection(collection_name=collection_name)
                    refreshed_names = {
                        str(field.get("name")) for field in refreshed.get("fields", [])
                    }
                    if field_name not in refreshed_names:
                        raise
            if unsupported_error is None:
                logger.info("Milvus集合[{}]元数据字段迁移完成：{}", collection_name, added)
        finally:
            client.load_collection(collection_name=collection_name)
        if unsupported_error is not None:
            backup_name = _rebuild_legacy_collection(client, collection_name)
            logger.info("Milvus旧Schema兼容迁移已生成备份集合：{}", backup_name)
            return missing
        return added


def _load_configured_collections(client):
    """加载已存在的业务集合。

    独立运行的 Milvus Lite 服务重启后会把集合恢复为 released 状态；
    搜索或查询前必须重新 load。这里在客户端首次连接时统一处理，确保
    导入服务和问答服务连接同一个本地 Milvus 进程时都能直接使用。
    """
    collection_names = {
        milvus_config.chunks_collection,
        milvus_config.entity_name_collection,
        milvus_config.item_name_collection,
    }
    for collection_name in sorted(name for name in collection_names if name):
        try:
            if client.has_collection(collection_name=collection_name):
                client.load_collection(collection_name=collection_name)
                logger.info("Milvus集合[{}]已加载", collection_name)
        except Exception as e:
            logger.opt(exception=True).warning(
                "Milvus集合[{}]自动加载失败，将由业务节点继续处理：{}",
                collection_name,
                e,
            )


def get_milvus_client():
    """
    Milvus客户端单例获取方法
    实现客户端连接复用，避免重复创建连接消耗资源
    :return: MilvusClient实例，连接失败返回None
    """
    try:
        global _milvus_client
        # 单例判断：未初始化则创建新连接
        if _milvus_client is None:
            with _milvus_client_lock:
                if _milvus_client is None:
                    milvus_uri = milvus_config.milvus_url
                    if not milvus_uri:
                        logger.error("Milvus客户端连接失败：缺少MILVUS_URL环境变量配置")
                        return None
                    grpc_options = {
                        "grpc.keepalive_time_ms": milvus_config.keepalive_time_ms,
                        "grpc.keepalive_timeout_ms": milvus_config.keepalive_timeout_ms,
                        "grpc.keepalive_permit_without_calls": (
                            milvus_config.keepalive_permit_without_calls
                        ),
                    }
                    _milvus_client = MilvusClient(uri=milvus_uri, grpc_options=grpc_options)
                    _load_configured_collections(_milvus_client)
                    logger.info("Milvus客户端连接成功")
        return _milvus_client
    except Exception as e:
        logger.opt(exception=True).error("Milvus客户端连接异常：{}", e)
        return None


def _coerce_int64_ids(ids):
    """
    转换chunk_id为Milvus要求的INT64类型（主键字段schema为INT64）
    过滤无效ID，分离可转换/不可转换的ID
    :param ids: 待转换的chunk_id列表
    :return: 元组(ok_ids, bad_ids)，ok_ids为可转换的int64类型ID列表，bad_ids为无效ID列表
    """
    ok, bad = [], []
    for x in (ids or []):
        if x is None:
            continue
        try:
            ok.append(int(x))
        except Exception:
            bad.append(x)
    return ok, bad


def fetch_chunks_by_chunk_ids(
        client,
        collection_name: str,
        chunk_ids,
        *,
        tenant_id: str,
        knowledge_base_id: str,
        output_fields=None,
        batch_size: int = 100,
):
    """
    通过chunk_id主键批量查询Milvus中的切片数据
    用于补全「仅拥有chunk_id无文本内容」场景的切片信息
    优先使用get方法（主键直查，性能最优），失败则回退query过滤查询
    :param client: MilvusClient实例
    :param collection_name: 集合名称
    :param chunk_ids: 待查询的chunk_id列表
    :param output_fields: 需要返回的字段列表，默认返回核心切片字段
    :param batch_size: 分批查询大小，避免单次查询数据量过大，默认100
    :return: List[dict]，Milvus实体字典列表，查询失败返回空列表
    """
    # 前置校验：客户端/集合名无效直接返回空
    if client is None:
        return []
    if not collection_name:
        return []
    # 默认返回字段：核心切片标识与内容字段
    if output_fields is None:
        output_fields = ["chunk_id", "content", "title", "parent_title", "item_name"]

    # 转换ID为INT64类型，分离有效/无效ID
    ok_ids, bad_ids = _coerce_int64_ids(chunk_ids)
    if bad_ids:
        # 记录无效ID，跳过查询
        logger.warning(f"存在无法转换为INT64的chunk_id，将跳过查询：{bad_ids}")

    # 无有效ID直接返回空
    if not ok_ids:
        return []

    results = []
    # 分批查询：按batch_size切分有效ID，循环查询
    for i in range(0, len(ok_ids), batch_size):
        batch = ok_ids[i: i + batch_size]

        # 主键 get 无法附加租户过滤，因此统一使用服务端构造的 query 表达式。
        try:
            from app.utils.milvus_utils import build_scope_filter

            expr = (
                f"{build_scope_filter(tenant_id, knowledge_base_id)} and "
                f"chunk_id in [{', '.join(str(x) for x in batch)}]"
            )
            q = client.query(collection_name=collection_name, filter=expr, output_fields=output_fields)
            if q:
                results.extend(q)
        except Exception as e:
            logger.opt(exception=True).error("Milvus query方法批量查询chunk_id失败：{}", e)

    return results


def create_hybrid_search_requests(dense_vector, sparse_vector, dense_params=None, sparse_params=None, expr=None,
                                  limit=5):
    """
    构建Milvus混合搜索请求对象
    分别创建稠密/稀疏向量的搜索请求，用于后续混合搜索融合
    :param dense_vector: 文本生成的稠密向量
    :param sparse_vector: 文本生成的稀疏向量
    :param dense_params: 稠密向量搜索参数，默认使用余弦相似度
    :param sparse_params: 稀疏向量搜索参数，默认使用内积相似度
    :param expr: 搜索过滤表达式，用于精准筛选数据
    :param limit: 单向量搜索返回结果数量，默认5
    :return: 搜索请求列表，包含[dense_req, sparse_req]
    """
    # 稠密向量默认搜索参数：余弦相似度（COSINE），适配BGE-M3稠密向量
    if dense_params is None:
        dense_params = {"metric_type": "COSINE"}
    # 稀疏向量默认搜索参数：内积（IP），适配BGE-M3稀疏向量
    if sparse_params is None:
        sparse_params = {"metric_type": "IP"}

    # 构建稠密向量搜索请求，关联Milvus的dense_vector字段 近似最近邻（ANN）检索请求的核心类
    dense_req = AnnSearchRequest(
        data=[dense_vector],
        anns_field="dense_vector",
        param=dense_params,
        expr=expr,
        limit=limit
    )

    # 构建稀疏向量搜索请求，关联Milvus的sparse_vector字段
    sparse_req = AnnSearchRequest(
        data=[sparse_vector],
        anns_field="sparse_vector",
        param=sparse_params,
        expr=expr,
        limit=limit
    )

    return [dense_req, sparse_req]


def hybrid_search(client, collection_name, reqs, ranker_weights=(0.5, 0.5), norm_score=False, limit=5,
                  output_fields=None, search_params=None):
    """
    执行Milvus稠密+稀疏向量混合搜索
    基于WeightedRanker实现双向量搜索结果加权融合，提升检索准确性
    :param client: MilvusClient实例
    :param collection_name: 集合名称
    :param reqs: 搜索请求列表，固定为[dense_req, sparse_req]
    :param ranker_weights: 加权融合权重，默认(0.5,0.5)，依次对应稠密/稀疏向量
    :param norm_score: 是否归一化评分后再融合，避免评分量级差异导致权重失效
    :param limit: 混合搜索最终返回结果数量，默认5
    :param output_fields: 需要返回的字段列表，默认返回item_name
    :param search_params: 搜索参数，如ef/topk等，默认None
    :return: 混合搜索结果列表，搜索失败返回None
    """
    try:
        # 初始化加权排名器：按权重融合稠密/稀疏向量的搜索结果
        # norm_score=True：先将两个向量评分归一化到0~1区间，再加权计算，避免一个得分特别大、另一个特别小导致权重失效。
        # 旧版本写法（2.4）
        rerank = WeightedRanker(ranker_weights[0], ranker_weights[1], norm_score=norm_score)

        # 新版本写法（2.6）
        # rerank = Function(
        #     name="weight",
        #     input_field_names=[], # Must be an empty list
        #     function_type=FunctionType.RERANK,
        #     params={
        #         "reranker": "weighted",
        #         "weights":  list(ranker_weights),
        #         "norm_score": norm_score
        #     }
        # )

        # 默认返回字段：文档标识字段
        if output_fields is None:
            output_fields = ["item_name"]

        # 执行混合搜索：融合稠密+稀疏向量结果，按权重重新排序
        res = client.hybrid_search(
            collection_name=collection_name,
            reqs=reqs,
            ranker=rerank,
            limit=limit,
            output_fields=output_fields,
            search_params=search_params
        )

        logger.info(f"Milvus混合搜索完成，集合[{collection_name}]共检索到{len(res[0])}条结果")
        return res
    except Exception as e:
        logger.opt(exception=True).error("Milvus混合搜索执行失败，集合[{}]：{}", collection_name, e)
        return None
