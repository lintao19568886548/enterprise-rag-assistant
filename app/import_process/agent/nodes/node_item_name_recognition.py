import json
import os.path
from sys import exc_info
from typing import Tuple, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage
from pymilvus import DataType

from app.clients.milvus_utils import ITEM_METADATA_FIELDS, ensure_collection_fields, get_milvus_client
from app.conf.milvus_config import milvus_config
from app.core.load_prompt import load_prompt
from app.core.logger import logger
from app.import_process.agent.node_base import NodeBase
from app.import_process.agent.state import ImportGraphState, create_default_state
from app.lm.embedding_utils import generate_embeddings
from app.lm.lm_utils import get_llm_client
from app.utils.milvus_utils import escape_milvus_string

# --- 配置参数 (Configuration) # --- 配置参数 (Configuration) ---
# 大模型识别商品名称的上下文切片数：取前5个切片，避免上下文过长导致大模型输入超限
DEFAULT_ITEM_NAME_CHUNK_K = 5
# 大模型上下文总字符数上限：适配主流大模型输入限制，默认2500
CONTEXT_TOTAL_MAX_CHARS = 2500

class NodeItemNameRecognition(NodeBase):
    """
    节点: 主体识别 (node_item_name_recognition)
    为什么叫这个名字: 识别文档核心描述的物品/商品名称 (Item Name)。
    未来要实现:
    1. 取文档前几段内容。
    2. 调用 LLM 识别这篇文档讲的是什么东西 (如: "Fluke 17B+ 万用表")。
    3. 存入 state["item_name"] 用于后续数据幂等性清理。
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_item_name_recognition"

    def process(self, state: ImportGraphState) -> ImportGraphState:
        """
        LangGraph 核心节点：商品主体名称识别
        流程总览：
            1. 提取输入（文件标题+文本切片）
            2. 构建大模型上下文
            3. 调用大模型识别商品名称
            4. 回填商品名称到状态和切片
            5. 生成商品名称的稠密/稀疏向量
            6. 将数据存入Milvus向量数据库

        必要参数：task_id、file_title、chunks
        更新参数：item_name

        :param state: 工作流状态对象
        :return: 更新后的状态对象
        """

        # 步骤1：提取并校验输入
        file_title, chunks = self._step_1_get_inputs(state)

        # 步骤2：构建大模型识别的上下文
        context = self._step_2_build_context(chunks)

        # 步骤3：调用大模型识别商品名称
        item_name = self._step_3_call_llm(file_title, context)

        # 步骤4：回填商品名称到状态和切片
        self._step_4_update_chunks(state, chunks, item_name)

        # 步骤5：为商品名称生成稠密/稀疏向量
        dense_vector, sparse_vector = self._step_5_generate_vectors(item_name)

        # 步骤6：将数据存入Milvus向量数据库
        self._step_6_save_to_milvus(state, file_title, item_name, dense_vector, sparse_vector)

        # 打印识别结果
        logger.info(f"--- 识别完成: {item_name} ---")

        return state


    def _step_6_save_to_milvus(self, state: ImportGraphState, file_title: str, item_name: str, dense_vector, sparse_vector):
        """
        步骤 6: 将商品名称、文件标题、双向量持久化到 Milvus 向量数据库
        核心逻辑：
            1. 配置校验：检查 Milvus 连接地址和集合名配置，缺失则跳过
            2. 客户端获取：获取单例 Milvus 客户端，连接失败则跳过
            3. 集合初始化：无集合则创建（定义 Schema+索引），有集合则直接使用
            4. 幂等性处理：删除同名商品数据，避免重复存储
            5. 数据插入：构造符合 Schema 的数据，非空向量才添加
            6. 集合加载：插入后强制加载集合，确保数据立即可查/Attu 可见
        索引设计：
            - 稠密向量：IVF_FLAT 索引 + 余弦相似度（COSINE），兼容性好，适合小数据量
            - 稀疏向量：SPARSE_INVERTED_INDEX 索引 + 内积（IP），稀疏向量专用，检索效率高
        参数：
            state: 流程状态对象，用于最终状态同步
            file_title: 处理后的文件标题
            item_name: 识别后的商品名称（主键去重依据）
            dense_vector: 步骤 5 生成的稠密向量（1024 维列表）
            sparse_vector: 步骤 5 生成的稀疏向量（字典格式）
        """

        # 1、对必要参数进行校验
        collection_name =  milvus_config.item_name_collection
        if not collection_name:
            logger.warning(f"【待处理数据】{file_title} | Milvus配置参数 ITEM_NAME_COLLECTION 缺失，跳过主体名称保存")
            return

        logger.info("步骤6：开始执行，将主体名称存入集合")

        # 2、获取Milvus客户端对象
        client = get_milvus_client()
        if not client:
            logger.warning(f"【待处理数据】{file_title} | 无法获取 Milvus 客户端，跳过主体名称保存")
            # 跳过这个步骤啥都不做
            return

        try:
            # 3、集合初始化：如果集合不存在则创建，存在则直接使用
            if not client.has_collection(collection_name=collection_name):

                logger.info(f"Milvus 集合 {collection_name} 不存在，开始创建")

                # 创建集合 Schema：自增主键 + 动态字段，适配灵活的数据存储
                schema = client.create_schema(auto_id=True, enable_dynamic_field=True)

                # 添加自增主键字段：INT64 类型，唯一标识每条数据
                schema.add_field(
                    field_name="pk",
                    datatype=DataType.INT64,
                    is_primary=True,
                    auto_id=True
                )

                # 添加文件标题字段：VARCHAR类型，最大长度65535，适配长标题
                schema.add_field(
                    field_name="file_title",
                    datatype=DataType.VARCHAR,
                    max_length=65535
                )

                # 添加设备名字段：VARCHAR类型，最大长度65535，去重依据
                schema.add_field(
                    field_name="item_name",
                    datatype=DataType.VARCHAR,
                    max_length=65535
                )

                # 添加稠密向量字段：FLOAT_VECTOR，1024维（BGE-M3固定维度）
                schema.add_field(
                    field_name="dense_vector",
                    datatype=DataType.FLOAT_VECTOR,
                    dim=1024

                )

                # 添加稀疏向量字段：SPARSE_FLOAT_VECTOR，变长
                schema.add_field(
                    field_name="sparse_vector",
                    datatype=DataType.SPARSE_FLOAT_VECTOR
                )

                # 构建索引参数：为向量字段创建索引，提升检索性能
                index_params = client.prepare_index_params()

                # 稠密向量索引：IVF_FLAT+COSINE，nlist=128（聚类数，平衡精度和速度）
                # 与其他索引对比（速览）
                """
                  索引	        精度          速度	        适用规模
                  FLAT	        100%          最慢	        ≤10 万
                  IVF_FLAT      高（≈98%）     中            10 万–1000 万
                  IVF_PQ	    中（≈90%）     快 	        千万–亿级
                  HNSW	        高	          最快	        全规模
                """
                index_params.add_index(
                    field_name="dense_vector",
                    index_name="dense_vector_index",
                    # IVF_FLAT（Inverted File with Flat） 是向量数据库中最常用的高精度、中等速度的近似最近邻（ANN）索引算法，核心是 “先聚类分桶、再桶内暴力精确检索”。
                    index_type="IVF_FLAT",
                    #  COSINE（余弦相似度） 文本语义检索中，不同长度的句子（如 “苹果手机” 和 “我想买苹果手机”）的向量长度不同，但方向一致，用余弦能精准匹配语义，忽略长度差异。
                    metric_type="COSINE",
                    # nlist：聚类数（桶数），通常设为 4×√N（N 为向量总数）
                    params={"nlist": 128}
                )

                # 稀疏向量索引
                index_params.add_index(
                    field_name="sparse_vector",
                    index_name="sparse_vector_index",
                    # 稀疏倒排索引 专门为稀疏向量（比如文本的 TF-IDF 向量、关键词权重向量，
                    # 特点是大部分元素为 0，只有少数维度有值，是稀疏向量检索的标配索引类型。
                    index_type="SPARSE_INVERTED_INDEX",
                    # IP（内积，Inner Product）如果向量是 “文本语义向量 + 关键词权重”，长度代表文本与主题的关联强度，
                    # 此时用 IP 能同时体现 “语义匹配度” 和 “关联强度”。
                    metric_type="IP",
                    params={
                        "inverted_index_algo": "DAAT_MAXSCORE",
                        # ↑ 使用 DAAT_MAXSCORE 算法（Dynamic And-And Threshold）
                        #   高效的稀疏检索算法，类似搜索引擎的倒排索引

                        "normalize": True,
                        # ↑ L2 归一化，让内积 (IP) 等价于余弦相似度
                        #   结果范围在 0-1 之间，便于理解

                        "quantization": "none"
                        # ↑ 关闭量化，保持原始精度：模型生成的向量已经压缩的一半的精度了（BGE_FP16=1），这里就不再压缩了

                        # "quantization": "none" → 存储原始向量，不压缩
                        # "quantization": "sq8" → 存储压缩后的向量（8-bit 量化
                    }
                )

                # 创建集合：Schema + 索引参数
                client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params)
                logger.info(f"Milvus集合[{collection_name}]创建成功，包含Schema和向量索引")

            ensure_collection_fields(client, collection_name, ITEM_METADATA_FIELDS)

            # 4、预加载集合到内存，提升删除和后续插入的性能
            # 把 Milvus 集合想象成一本书：
            #     未加载状态 = 书在书架上（节省内存，但无法快速查阅）
            #     已加载状态 = 书在桌面上（占用内存，可以随时翻阅）
            # 适合高频查询的生产环境
            client.load_collection(collection_name=collection_name)

            # 5、幂等性处理：删除同名商品数据，避免重复存储
            # 商品名称转义，防止特殊字符导致过滤表达式解析失败
            safe_item_name = escape_milvus_string(value=item_name)
            knowledge_base_id = str(state.get("knowledge_base_id") or "")
            filter_expr = f'item_name=="{safe_item_name}"'
            if knowledge_base_id:
                filter_expr += (
                    f' and knowledge_base_id=="{escape_milvus_string(knowledge_base_id)}"'
                )
            client.delete(collection_name=collection_name, filter=filter_expr)
            logger.info(f"Milvus幂等性处理完成，删除集合中的历史数据: {item_name}")

            # 6、组装要插入的数据
            data = {
                "file_title": file_title,
                "item_name": item_name,
                "tenant_id": str(state.get("tenant_id") or ""),
                "knowledge_base_id": knowledge_base_id,
                "document_id": str(state.get("document_id") or ""),
                "is_active": True,
                "dense_vector": dense_vector,
                "sparse_vector": sparse_vector
            }

            # 7、插入数据
            client.insert(collection_name=collection_name, data=[data])
            # 插入数据后强制加载集合，确保后驱的查询更高效，即数据立即可从缓冲区获取
            client.load_collection(collection_name=collection_name)

            # 8、同步数据到state
            state["item_name"] = item_name
            logger.info(f"步骤6：设备名称{item_name}成功存入Milvus数据库")
        except Exception as e:
            logger.opt(exception=True).warning(
                "步骤6：数据存入Milvus失败，请检查Milvus服务是否正常启动，原因为：{}", e
            )

    # 核心步骤: 有问题则抛出异常
    def _step_5_generate_vectors(self, item_name: str) -> Tuple[List, Dict] :
        """
        步骤 5: 为商品名称生成BGE-M3稠密+稀疏双向量（Milvus向量检索核心）
        核心说明：
            - 稠密向量（dense_vector）：BGE-M3固定1024维，记录文本深层语义信息
            - 稀疏向量（sparse_vector）：变长键值对，记录文本关键词/特征位置信息
        依赖工具：
            generate_embeddings：封装BGE-M3模型，批量生成双向量，兼容单条/批量输入
        参数：
            item_name: 步骤3识别的商品名称（非空，空值时直接返回空向量）
        返回值：
            Tuple[Any, Any]: (稠密向量列表, 稀疏向量字典)
        """
        logger.info(f"步骤5：为商品名称[{item_name}]生成BGE-M3双向量")

        # 1、商品名为空，则直接返回空向量
        if not item_name:
            error_msg = "商品名称为空，无法生成向量"
            logger.error(error_msg)
            raise ValueError(error_msg)

        try:

            # 2、调用向量模型生成向量
            vector_result = generate_embeddings([item_name])

            # 3、
            if vector_result and "dense" in vector_result and "sparse" in vector_result:

                # 3.1、获取稠稠密向量
                dense_vector = vector_result["dense"][0]

                # 3.2、获取稀疏向量
                sparse_vector = vector_result["sparse"][0]

                return dense_vector, sparse_vector

            else:
                error_msg = f"步骤5：BGE-M3双向量为空"
                logger.exception(error_msg)
                raise RuntimeError(error_msg)

        except Exception as e:
            error_msg = f"步骤5：BGE-M3双向量生成异常，原因：{str(e)}"
            logger.exception(error_msg)
            raise RuntimeError(error_msg)


    # 降级版本
    # def _step_5_generate_vectors(self, item_name: str) -> Tuple[List | None, Dict | None] :
    #     """
    #     步骤 5: 为商品名称生成BGE-M3稠密+稀疏双向量（Milvus向量检索核心）
    #     核心说明：
    #         - 稠密向量（dense_vector）：BGE-M3固定1024维，记录文本深层语义信息
    #         - 稀疏向量（sparse_vector）：变长键值对，记录文本关键词/特征位置信息
    #     依赖工具：
    #         generate_embeddings：封装BGE-M3模型，批量生成双向量，兼容单条/批量输入
    #     参数：
    #         item_name: 步骤3识别的商品名称（非空，空值时直接返回空向量）
    #     返回值：
    #         Tuple[Any, Any]: (稠密向量列表, 稀疏向量字典)，空值/异常时返回(None, None)
    #     """
    #     logger.info(f"步骤5：为商品名称[{item_name}]生成BGE-M3双向量")
    #
    #     # 1、商品名为空，则直接返回空向量
    #     if not item_name:
    #         logger.info(f"商品名称为空，直接返回空向量")
    #         return None, None
    #
    #     try:
    #
    #         # 2、调用向量模型生成向量
    #         vector_result = generate_embeddings([item_name])
    #
    #         # 3、
    #         if vector_result and "dense" in vector_result and "sparse" in vector_result:
    #
    #             # 3.1、获取稠稠密向量
    #             dense_vector = vector_result["dense"][0]
    #
    #             # 3.2、获取稀疏向量
    #             sparse_vector = vector_result["sparse"][0]
    #
    #             return dense_vector, sparse_vector
    #         else:
    #             return None, None
    #
    #     except Exception as e:
    #         logger.exception(f"步骤5：BGE-M3双向量生成异常，原因：{str(e)}")
    #         return None, None





    def _step_4_update_chunks(self, state: ImportGraphState, chunks: List[Dict], item_name: str):
        """
        步骤 4: 回填商品名称到流程状态和所有文本切片
        核心作用：
            1. 全局状态更新：将item_name存入state，供下游所有节点直接使用
            2. 切片数据补全：为每个切片添加item_name字段，保证数据一致性
            3. 状态同步：更新state中的chunks，确保切片修改全局生效
        设计思路：
            所有切片关联同一商品名称，保证后续向量入库、检索时的维度一致性
        参数：
            state: 流程状态对象（ImportGraphState），全局数据载体
            chunks: 校验后的文本切片列表（步骤1输出）
            item_name: 步骤3识别并清洗后的商品名称
        """
        # 1、将商品名称存入全局状态，供下游节点调用
        state["item_name"] = item_name

        # 2、遍历所有切片，为每个切片添加商品名字段，保证数据全链路一致
        for chunk in chunks:
            chunk["item_name"] = item_name

        # 3、同步更新state中的切片列表，确保修改全局生效
        state["chunks"] = chunks

        logger.info(f"步骤4：商品名称回填完成，共为{len(chunks)}个切片添加item_name字段，值为：{item_name}")

    def _step_3_call_llm(self, file_title: str, context: str) -> str:
        """
        步骤 3: 调用大模型实现商品名称/型号精准识别
        核心逻辑：
            1. 上下文为空 → 直接返回file_title（兜底，无需调用大模型）
            2. 上下文非空 → 加载标准化prompt模板，构建大模型对话消息
            3. 调用大模型后对返回结果做清洗，过滤无效字符
            4. 大模型返回空/调用异常 → 均返回file_title兜底，保证流程不中断
        核心特性：
            - 提示词解耦：通过load_prompt加载本地模板，无需硬编码
            - 格式兼容：兼容不同LLM客户端返回格式，防止属性报错
            - 异常兜底：全异常捕获，大模型服务不可用时不影响主流程
        参数：
            file_title: 处理后的文件标题（异常/空值时的兜底值）
            context: 步骤2构建的结构化切片上下文（大模型识别的核心依据）
        返回值：
            str: 清洗后的商品名称（异常/空值时返回原始file_title）
        """

        logger.info("开始执行步骤3：调用大模型识别商品名称")

        # 1、上下文为空时，直接返回文件标题，跳过大模型调用
        if not context:
            logger.warning("上下文为空，跳过大模型调用，直接使用文件标题作为商品名称")
            return file_title

        try:
            # 2、加载标准化prompt模板
            human_prompt = load_prompt("item_name_recognition", file_title=file_title, context=context)
            system_prompt = load_prompt("product_recognition_system")
            logger.debug(f"大模型调用提示词构建完成，系统提示词长度{len(system_prompt)}，人类提示词长度{len(human_prompt)}")

            # 3、创建大模型客户端
            llm = get_llm_client()
            if not llm:
                logger.error("大模型客户端创建失败，使用文件名称兜底")
                return file_title

            # 4、创建对话消息
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=human_prompt)
            ]

            # 5、调用大模型
            response = llm.invoke(messages)

            # 6、获取响应结果
            content_attributes = ["text", "content", "message", "response"]
            for attr in content_attributes:
                item_name = getattr(response, attr, None)
                if item_name:
                    break
                else:
                    item_name = ""

            item_name = item_name.strip()

            # 7、清洗返回结果：过滤空格、换行、回车、制表符等无效字符
            item_name = item_name.replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")

            if not item_name:
                logger.warning("大模型返回空结果，使用文件名称兜底")
                return file_title

            logger.info(f"模型识别商品名称成功，识别结果是：{item_name}")

            return item_name

        except Exception as e:
            logger.exception(f"大模型调用异常，使用文件名称兜底，异常信息是：{e}")
            #发生异常时返回文件标题兜底，保证流程继续执行
            return file_title

    def _step_2_build_context(self, chunks: List[Dict]) -> str:
        """
        步骤 2: 构造大模型商品名称识别的标准化上下文
        核心作用：
            1. 限制切片数量：仅取前k个切片，避免上下文过长
            2. 限制字符长度：总上下文字符限制，适配大模型输入上限
            3. 格式化内容：带序号的结构化格式，提升大模型识别精度
        参数说明：
            chunks: 文本切片列表
        返回值：
            str: 格式化后的上下文字符串
        """

        # 1、初始化变量
        # 存储格式化后的切片片段，保证上下文结构化
        parts: List[str] = []
        # 统计已拼接字符数，用于控制总长度不超限
        total_chars = 0

        # 2、遍历前k个切片，避免上下文过长
        for idx, chunk in enumerate(chunks[:DEFAULT_ITEM_NAME_CHUNK_K], start=1):

            # 2.1、提取切片标题和内容，去首尾空格，过滤无效字符
            chunk_title = chunk.get("title", "").strip()
            chunk_content = chunk.get("content", "").strip()

            # 2.2、结构化格式化切片：带序号+标题+内容，提升大模型识别效率
            piece = f"【切片{idx}】\n标题：{chunk_title} \n内容：{chunk_content}"
            parts.append(piece)

            # 2.3、累计字符数，包含分隔符
            total_chars += len(piece)

            # 2.3、总字符数超限时立即停止拼接，避免大模型输入超限
            if total_chars > CONTEXT_TOTAL_MAX_CHARS:
                logger.info(f"上下文总字数：{total_chars}，已超限（{CONTEXT_TOTAL_MAX_CHARS}），已停止拼接后续切片")
                break

        # 3、用空行分隔切片片段，拼接为最终上下文，去重空格
        context = "\n\n".join(parts).strip()

        # 4、二次截断，确保绝对不超限
        final_context = context[:CONTEXT_TOTAL_MAX_CHARS]
        logger.info(f"步骤2：上下文构建完成，最终长度{len(final_context)}字符")
        return final_context


    def _step_1_get_inputs(self, state: ImportGraphState) -> Tuple[str, List[Dict]]:
        """
        步骤 1: 接收并校验流程输入
        核心作用：
            1. 从流程状态中提取文件标题、文本切片核心数据
            2. 做多层空值兜底，避免后续流程因空值报错
            3. 基础数据类型校验，保证下游流程输入有效性
        依赖的状态数据（上游节点产出）：
            - state["file_title"]: 上游提取的文件标题（优先使用）
            - state["chunks"]: 文本切片列表（每个切片为字典，含title/content等字段）
        返回值：
            Tuple[str, List[Dict]]: (处理后的文件标题, 校验后的文本切片列表)
        """
        # 1、多层兜底获取文件标题：优先file_title → 其次file_name → 空字符串
        file_title = state.get("file_title", "")

        # 2、获取文本切片列表：空值时返回空列表，避免后续遍历报错
        chunks = state.get("chunks") or []


        # 3、二次兜底：
        if not file_title:
            if chunks and isinstance(chunks[0], dict):
                file_title = chunks[0].get("file_title", "")
                logger.warning(f"state中没有file_title，从第一个切片中获取了兜底标题: {file_title}")

        # 4、空值提示：
        if not file_title:
            logger.warning(f"state中缺少file_title 且 chunks缺少file_title，后续大模型识别主体的精度可能受到影响")

        # 5、chunks的数据类型校验：必须是列表行
        if not isinstance(chunks, list) or not chunks:
            logger.warning(f"state中chunks数据类型错误（可能为空）")
            return file_title, []

        return file_title, chunks


if __name__ == "__main__":

    from app.utils.path_util import PROJECT_ROOT

    chunk_path = PROJECT_ROOT / "output/hak180产品安全手册/chunks.json"
    chunk_json = chunk_path.read_text(encoding="utf-8")
    # print(type(chunk_json))
    chunk_list =  json.loads(chunk_json)
    # print(type(chunk_list))

    init_state = create_default_state(
        tast_id = "task_demo",
        file_title = "hak180产品安全手册",
        chunks=chunk_list
    )

    node_item_name_recognition = NodeItemNameRecognition()
    final_state = node_item_name_recognition(init_state)


    #将chunks中的内容进行备份
    backup_path = os.path.join(PROJECT_ROOT, "output", "hak180产品安全手册", "chunks_with_item_name.json")
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(final_state["chunks"],f,ensure_ascii=False, indent=2)
    logger.info(f"Chunk结果备份成功，备份文件路径：{backup_path}")



