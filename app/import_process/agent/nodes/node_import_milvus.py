import json
from typing import Dict, Any, List

from pymilvus import DataType

from app.clients.milvus_utils import (
    CHUNK_METADATA_FIELDS,
    ensure_collection_fields,
    get_milvus_client,
)
from app.conf.milvus_config import milvus_config
from app.core.logger import logger
from app.import_process.agent.node_base import NodeBase
from app.import_process.agent.state import ImportGraphState
from app.utils.milvus_utils import build_scope_filter


class NodeImportMilvus(NodeBase):
    """
    节点: 导入向量库 (node_import_milvus)
    为什么叫这个名字: 将处理好的向量数据写入 Milvus 数据库。
    未来要实现:
    1. 连接 Milvus。
    2. 根据 item_name 删除旧数据 (幂等性)。
    3. 批量插入新的向量数据。
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_import_milvus"

    def process(self, state: ImportGraphState) -> ImportGraphState:
        """
        LangGraph核心节点：Milvus切片数据入库主流程
        执行流程（串行执行，一步一校验，保证数据一致性）：
            1. 输入校验：验证切片有效性、向量字段完整性，提取向量维度
            2. 环境准备：连接Milvus，集合不存在则自动创建Schema+索引
            3. 幂等清理：删除同item_name旧数据，避免重复存储
            4. 批量插入：预处理数据后批量入库，回填Milvus自增chunk_id
            5. 状态更新：将回填了chunk_id的切片更新回全局状态，供下游使用

        异常处理：
            任一步骤失败抛出ValueError，终止节点执行，保证数据不脏写

        必要参数：task_id、chunks
        更新参数：chunks字段回填chunk_id

        :param state: 工作流状态对象
        :return: 更新后的状态对象
        """

        # 步骤1：输入数据有效性校验
        chunks_json_data, vector_dimension = self._step_1_check_input(state)

        # 步骤2：Milvus客户端连接+集合准备（自动建表）
        client = self._step_2_prepare_collection(vector_dimension)

        # 步骤3：幂等性处理 - 清理同item_name旧数据
        self._step_3_clean_old_data(client, chunks_json_data)

        # 步骤4：批量插入数据+主键chunk_id回填
        updated_chunks = self._step_4_insert_data(client, chunks_json_data)

        # 步骤5：更新全局状态，将回填后的切片回传下游
        state["chunks"] = updated_chunks


        return state

    def _step_4_insert_data(self, client, chunks_json_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        步骤4：批量插入切片数据到Milvus+主键回填
        核心逻辑：
            1. 批量插入数据：提升入库效率，减少Milvus连接次数
            2. 回填chunk_id：将Milvus生成的自增主键回填到切片，供下游业务使用
        参数：
            client - MilvusClient实例
            chunks_json_data: List[Dict[str, Any]] - 待入库的切片列表
        返回：
            List[Dict[str, Any]] - 回填了chunk_id的切片列表
        """

        # 1、定义数据集合
        data_to_insert = []
        for item in chunks_json_data:
            item_copy = item.copy()

            # 补充part字段
            if "part" not in item_copy:
                item_copy["part"] = 0

            data_to_insert.append(item_copy)

        logger.info(f"开始批量插入{len(data_to_insert)}个切片")

        # 2、批量插入数据
        insert_result = client.insert(collection_name=milvus_config.chunks_collection, data=data_to_insert)

        insert_count = insert_result.get("insert_count", 0)
        logger.info(f"已批量插入{insert_count}个切片")

        # 3、获取返回的ids
        inserted_ids = insert_result.get("ids", [])
        if inserted_ids:
            for idx, item in enumerate(chunks_json_data):
                item["chunk_id"] = inserted_ids[idx]
                logger.info(f"已回填第{idx+1}个切片的chunk_id为{inserted_ids[idx]}")

        return chunks_json_data


    def _step_3_clean_old_data(self, client, chunks_json_data: List[Dict[str, Any]]):
        """
        步骤3：幂等性处理 - 基于item_name清理旧数据
        核心设计：
            插入新数据前删除同item_name的所有旧切片，确保多次执行仅保留最新数据
            支持多item_name批量清理，自动去重避免重复操作
        参数：
            client - MilvusClient实例
            chunks_json_data: List[Dict[str, Any]] - 待入库的切片列表
        """
        first_chunk = chunks_json_data[0]
        tenant_id = str(first_chunk.get("tenant_id") or "")
        knowledge_base_id = str(first_chunk.get("knowledge_base_id") or "")
        document_ids = sorted(
            {
                str(chunk.get("document_id", "")).strip()
                for chunk in chunks_json_data or []
                if str(chunk.get("document_id", "")).strip()
            }
        )
        if document_ids:
            for document_id in document_ids:
                client.delete(
                    collection_name=milvus_config.chunks_collection,
                    filter=build_scope_filter(
                        tenant_id,
                        knowledge_base_id,
                        document_id=document_id,
                        active_only=False,
                    ),
                )
                logger.info("已按 document_id={} 清理旧版本切片", document_id)
            return

        # Legacy records have no document_id; retain the old item-name fallback.
        # 提取并去重item_name，避免重复清理同一商品数据
        item_names = sorted({
            str(x.get("item_name", "")).strip()
            for x in chunks_json_data or []
            if str(x.get("item_name", "")).strip()
        })

        if not item_names:
            logger.warning("未找到item_name，跳过清理")
            return

        if len(item_names) > 1:
            logger.warning(f"找到多个item_name，将清理所有数据：{item_names}")

        # 清理同item_name旧数据
        for item_name in item_names:
            self._clear_chunks_by_item_name(
                client,
                item_name,
                tenant_id,
                knowledge_base_id,
            )

    def _clear_chunks_by_item_name(
        self,
        client,
        item_name: str,
        tenant_id: str,
        knowledge_base_id: str,
    ):
        """
        内部核心函数：根据item_name删除Milvus中的旧切片数据
        参数：
            client - MilvusClient实例
            item_name: str - 要清理的商品名称
        异常：
            清理失败抛出ValueError，终止整个入库流程（保证幂等性）
        """

        try:
            filter_expr = build_scope_filter(
                tenant_id,
                knowledge_base_id,
                item_name=item_name,
                active_only=False,
            )
            client.delete(collection_name=milvus_config.chunks_collection, filter=filter_expr)

            logger.info(f"已清理设备名称为{item_name}的旧切片数据")
        except Exception as e:
            err_msg = f"幂等清理失败，设备名称为{item_name}，错误{str(e)}"
            logger.exception(err_msg)
            raise RuntimeError(err_msg)

    def _step_2_prepare_collection(self, vector_dimension: int):
        """
        步骤2：Milvus客户端连接+集合准备
        核心逻辑：
            1. 获取Milvus单例客户端，验证连接有效性
            2. 集合不存在则自动创建（Schema+索引），存在则直接复用
        参数：
            vector_dimension: int - 稠密向量维度（步骤1提取）
        返回：
            MilvusClient - 已连接、集合准备完成的客户端实例
        异常：
            客户端获取失败/集合名称未配置，抛出ValueError终止流程
        """

        # 1、从环境变量读取 Milvus 核心配置，与 MilvusConfig 配置类保持一致
        collection_name = milvus_config.chunks_collection
        # 从配置文件读取切片集合名称，与配置解耦，便于环境切换

        # 2、配置缺失校验：配置为空则跳过 Milvus 存储，记录警告
        if not collection_name:
            logger.error("Milvus集合名称未配置：CHUNKS_COLLECTION_NAME为空")
            raise ValueError("未配置CHUNKS_COLLECTION集合名称")

        # 3、获取 Milvus 单例客户端，连接失败则直接返回
        client = get_milvus_client()
        if not client:
            logger.error("Milvus客户端获取失败：get_milvus_client()返回空，连接可能异常")
            raise ValueError("Milvus 连接失败：get_milvus_client() 返回空")

        # 4. 集合不存在则自动创建
        if not client.has_collection(collection_name=collection_name):

            logger.info(f"Milvus集合{collection_name}不存在，开始自动创建Schema和索引")
            self._create_collection(client, collection_name, vector_dimension)
        else:
            logger.info(f"Milvus集合{collection_name}已存在，直接复用")

        ensure_collection_fields(client, collection_name, CHUNK_METADATA_FIELDS)

        return client

    def _create_collection(self, client, collection_name: str, vector_dimension: int):
        """
        辅助函数：Milvus集合+索引自动创建
        核心逻辑：
            1. 定义集合Schema：包含业务字段+双向量字段，自增主键chunk_id
            2. 构建向量索引：稠密向量用AUTOINDEX（Milvus自动选最优索引），稀疏向量用专用索引
        参数：
            client - MilvusClient实例（已连接）
            collection_name: str - 要创建的集合名称
            vector_dimension: int - 稠密向量维度（与向量化模型保持一致）
        """
        # 1. 创建Schema：自增主键+支持动态字段，适配灵活的业务扩展
        schema = client.create_schema(auto_id=True, enable_dynamic_field=True)

        # 2. 新增字段：业务字段+主键+双向量字段，字段类型/长度适配业务场景
        schema.add_field(field_name="chunk_id", datatype=DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field(
            field_name="tenant_id",
            datatype=DataType.VARCHAR,
            max_length=64,
            is_partition_key=True,
        )
        schema.add_field(field_name="knowledge_base_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="document_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="document_version", datatype=DataType.INT64)
        schema.add_field(field_name="task_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="is_active", datatype=DataType.BOOL)
        schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=65535)  # 切片内容
        schema.add_field(field_name="title", datatype=DataType.VARCHAR, max_length=65535)  # 切片标题
        schema.add_field(field_name="parent_title", datatype=DataType.VARCHAR, max_length=65535)  # 父标题
        schema.add_field(field_name="part", datatype=DataType.INT8)  # 分片编号
        schema.add_field(field_name="file_title", datatype=DataType.VARCHAR, max_length=65535)  # 源文件标题
        schema.add_field(field_name="item_name", datatype=DataType.VARCHAR, max_length=65535)  # 商品名称（幂等性依据）
        schema.add_field(field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR)  # 稀疏向量
        schema.add_field(field_name="dense_vector", datatype=DataType.FLOAT_VECTOR, dim=vector_dimension)  # 稠密向量

        # 3. 构建索引参数：为向量字段创建索引，提升检索性能
        index_params = client.prepare_index_params()
        # 稠密向量索引：AUTOINDEX自动选最优索引类型+余弦相似度（语义检索常用）
        index_params.add_index(
            field_name="dense_vector",
            index_name="dense_vector_index",
            index_type="AUTOINDEX",
            metric_type="COSINE"
        )
        # 稀疏向量索引：专用SPARSE_INVERTED_INDEX+内积（IP），适配稀疏向量检索
        index_params.add_index(
            field_name="sparse_vector",
            index_name="sparse_inverted_index",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="IP",
            params={"inverted_index_algo": "DAAT_MAXSCORE", "normalize": True, "quantization": "none"}
        )

        # 4. 创建集合：Schema+索引参数结合，一次性完成初始化
        client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params)
        logger.info(f"Milvus集合创建成功：{collection_name}，向量维度：{vector_dimension}")


    def _step_1_check_input(self, state: Dict[str, Any]) -> tuple[List[Dict[str, Any]], int]:
        """
        步骤1：输入数据有效性校验
        核心校验项：
            1. chunks非空且为列表类型
            2. 切片包含dense_vector核心字段
            3. 提取向量维度，为集合创建/索引构建提供依据
        参数：
            state: Dict[str, Any] - 流程状态对象，包含上游传入的chunks数据
        返回：
            tuple - (校验通过的切片列表, 稠密向量维度)
        异常：
            任一校验项不通过，抛出ValueError终止入库流程，避免脏数据处理

        """

        # 校验1：chunks非空
        chunks = state.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            raise ValueError("核心参数chunks为空或非列表类型")

        # 校验2：切片包含dense_vector字段
        first_chunk = chunks[0]
        if 'dense_vector' not in first_chunk:
            raise ValueError("错误: 数据中缺失dense_vector字段")

        # 校验3：切片包含 sparse_vector 字段
        if 'sparse_vector' not in first_chunk:
            raise ValueError("错误: 数据中缺失sparse_vector字段")

        required_fields = (
            "tenant_id",
            "knowledge_base_id",
            "document_id",
            "document_version",
            "task_id",
            "is_active",
        )
        expected_scope = (
            str(first_chunk.get("tenant_id") or ""),
            str(first_chunk.get("knowledge_base_id") or ""),
            str(first_chunk.get("document_id") or ""),
            int(first_chunk.get("document_version") or 0),
            str(first_chunk.get("task_id") or ""),
        )
        if not all(expected_scope) or first_chunk.get("is_active") is not True:
            raise ValueError("Milvus切片缺少强制租户、知识库、文档、版本、任务或活跃状态")
        for chunk in chunks:
            if any(field not in chunk for field in required_fields):
                raise ValueError("Milvus切片缺少强制隔离元数据")
            scope = (
                str(chunk.get("tenant_id") or ""),
                str(chunk.get("knowledge_base_id") or ""),
                str(chunk.get("document_id") or ""),
                int(chunk.get("document_version") or 0),
                str(chunk.get("task_id") or ""),
            )
            if scope != expected_scope or chunk.get("is_active") is not True:
                raise ValueError("单次Milvus写入包含不一致的租户范围")

        # 提取向量维度和商品名称，用于后续集合创建/日志展示
        vector_dimension = len(first_chunk['dense_vector'])
        item_name = first_chunk.get('item_name', '未知商品名')
        logger.info(f"Milvus入库校验通过，待入库切片数：{len(chunks)} | 向量维度：{vector_dimension} | 商品名称：{item_name}")

        return chunks, vector_dimension

if __name__ == "__main__":

    # 获取项目所在路径
    from app.import_process.agent.state import create_default_state
    from app.utils.path_util import PROJECT_ROOT


    # 组装文件的绝对路径
    chunks_path = PROJECT_ROOT / "output/hak180产品安全手册/chunks_with_vector.json"
    # 读取切片
    chunks_json = chunks_path.read_text(encoding="utf-8")
    # 将json字符串chunks转成列表
    chunks = json.loads(chunks_json)
    # 当前节点图状态初始值
    init_state = create_default_state(
        task_id="task_001",
        chunks=chunks
    )

    # 执行节点的业务调用
    node_import_milvus = NodeImportMilvus()
    final_state = node_import_milvus(init_state)
