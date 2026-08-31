from app.clients.milvus_utils import create_hybrid_search_requests, get_milvus_client, hybrid_search
from app.conf.milvus_config import milvus_config
from app.core.load_prompt import load_prompt
from app.core.settings import settings
from app.core.metrics import RETRIEVAL_RESULTS
from app.lm.embedding_utils import generate_embeddings
from app.lm.lm_utils import get_llm_client
from app.query_process.agent.node_base import NodeBase
from app.core.logger import logger
from app.query_process.agent.state import QueryGraphState, create_default_state
from app.utils.task_utils import add_done_task
from app.utils.milvus_utils import build_chunk_filter
from app.query_process.agent.nodes.node_search_embedding import CHUNK_OUTPUT_FIELDS


class NodeSearchEmbeddingHyde(NodeBase):
    """
    节点功能：HyDE (Hypothetical Document Embedding)
    先让 LLM 生成假设性答案，再对答案进行向量检索，提高召回率。
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_search_embedding_hyde"

    def process(self, state: QueryGraphState) -> QueryGraphState:
        """
        HyDE (Hypothetical Document Embedding) 检索节点
        核心思想：通过LLM生成假设性答案（HyDE文档），将其向量化后用于检索，以解决短查询语义稀疏问题。

        执行步骤：
        1. 参数提取：从会话状态中获取改写后的查询（rewritten_query）和已确认的商品名（item_names）。
        2. 生成假设文档 (Step 1)：调用LLM，基于用户问题生成一段假设性的理想回答（即HyDE文档）。
        3. 混合检索 (Step 2)：
           - 将“用户问题 + 假设文档”合并，生成BGE-M3稠密+稀疏向量。
           - 在Milvus中执行混合检索（带商品名过滤），召回最相似的知识切片。
        4. 结果封装：返回检索到的切片列表和生成的假设文档，更新会话状态。

        :param state: 会话状态字典，包含 session_id, rewritten_query, item_names 等
        :return: 包含 hyde_embedding_chunks (检索结果) 和 hyde_doc (假设文档) 的字典
        """

        # 1、用户问题和已确认商品名
        rewritten_query = state.get("rewritten_query")
        item_names = state.get("item_names")

        try:

            if not settings.hyde_enabled:
                add_done_task(state["session_id"], self.name)
                return {"hyde_embedding_chunks": [], "hyde_doc": ""}

            # 2、生成假设性文档
            hyde_doc = self._step_1_create_hyde_doc(rewritten_query)

            # 3、用“重写问题 + 假设文档”检索切片
            res = self._step_2_search_embedding_hyde(
                rewritten_query=rewritten_query,
                hyde_doc=hyde_doc,
                item_names=item_names,
                knowledge_base_id=state.get("knowledge_base_id"),
                top_k=settings.retrieval_top_k,
            )

            # 4、结果封装
            add_done_task(state["session_id"], self.name)
            return {
                "hyde_embedding_chunks": res[0] if res else [],
                "hyde_doc": hyde_doc,
            }

        except Exception as e:
            logger.exception(f"假设性文档向量搜索失败: {e}")
            return {}

    def _step_1_create_hyde_doc(self, rewritten_query: str) -> str:
        """
        阶段1：利用大模型根据用户查询生成假设性文档（Hypothetical Document）。
        HyDE的核心在于：利用LLM生成一个“虚构但相关”的文档，用该文档的向量去检索真实的文档，
        从而缓解短查询（Query）与长文档（Document）在语义空间不匹配的问题。

        :param rewritten_query: 用户改写后的查询语句
        :return: LLM生成的假设性文档内容
        """

        logger.info("步骤1: 开始生成假设性文档")

        try:
            llm = get_llm_client()
            # 加载提示词模板，生成假设文档
            hyde_prompt = load_prompt("hyde_prompt", rewritten_query=rewritten_query)
            logger.debug(f"步骤1: Prompt加载成功")

            # 调用LLM生成
            response = llm.invoke(hyde_prompt)
            hyde_doc = response.content

            logger.info(f"步骤1: 假设文档生成完成, 长度: {len(hyde_doc)} 字符")
            return hyde_doc

        except Exception as e:
            logger.exception(f"步骤1: 生成假设文档失败: {e}")
            raise e

    def _step_2_search_embedding_hyde(
            self,
            rewritten_query: str,
            hyde_doc: str,
            item_names=None,
            knowledge_base_id: str | None = None,
            req_limit: int | None = None,
            top_k: int = 5,
            ranker_weights=(0.8, 0.2),  # 调整默认权重以偏向稠密向量 (0.8, 0.2)
            norm_score: bool = True,    # 默认开启归一化
            output_fields=None,
    ):
        """
        阶段2：利用“重写问题 + 假设性文档”生成 embedding，并到向量库检索切片。

        :param rewritten_query: 改写后的查询
        :param hyde_doc: Step 1 生成的假设性文档
        :param item_names: 商品名称列表，用于元数据过滤 (item_name in [...])
        :param req_limit: Milvus 搜索时的候选召回数量
        :param top_k: 最终返回的 Top K 结果数量
        :param ranker_weights: 混合检索权重 (Dense, Sparse)
        :param norm_score: 是否对分数进行归一化
        :param output_fields: 返回结果中包含的字段
        :return: 检索结果列表
        """

        try:
            # 1、拼接查询与假设文档，形成更丰富的语义上下文
            # 这里把用户问题 + 假设答案拼在一起生成向量，相当于：
            # 既保留了用户的原始意图（rewritten_query）
            # 又增强了语义丰富度（hyde_doc）
            combined_text = rewritten_query + " " + hyde_doc
            logger.info(f"步骤2: 拼接 Query + HyDE Doc, 总长度: {len(combined_text)}")

            # 2、生成向量 (Dense + Sparse)
            logger.info("步骤2: 正在生成混合向量 (Embedding)...")
            embeddings = generate_embeddings([combined_text])

            dense_vec = embeddings.get("dense")[0]
            sparse_vec = embeddings.get("sparse")[0]

            # 3. 获取Milvus的集合
            collection_name = milvus_config.chunks_collection
            logger.info(f"步骤2: 准备在集合 '{collection_name}' 中执行混合检索")

            # 4、构建安全的商品/知识库过滤表达式。
            expr = build_chunk_filter(
                item_names,
                knowledge_base_id,
                enforce_knowledge_base=True,
            )
            logger.info(
                "HyDE 检索过滤已构建，商品数量={}，知识库隔离={}",
                len(item_names or []),
                True,
            )

            # 5、构造Milvus混合搜索请求对象
            reqs = create_hybrid_search_requests(
                dense_vector = dense_vec,
                sparse_vector = sparse_vec,
                expr = expr,
                limit=req_limit or settings.retrieval_candidate_limit,
            )

            # 6、执行混合向量检索
            logger.info("步骤2: 开始执行 Milvus 混合检索...")
            client = get_milvus_client()
            res = hybrid_search(
                client=client,
                collection_name=collection_name,
                reqs=reqs,
                ranker_weights=ranker_weights,
                norm_score=norm_score,
                limit=top_k,
                output_fields=list(output_fields or CHUNK_OUTPUT_FIELDS),
            )
            RETRIEVAL_RESULTS.labels("hyde").observe(len(res[0]) if res else 0)

            return res

        except Exception as e:
            logger.error(f"步骤2: 检索过程发生异常: {e}")
            raise e

if __name__ == "__main__":

    # 当前节点图状态初始值
    init_state = create_default_state(
        session_id = "test_session_002",
        rewritten_query = "如何调整brother HAK180烫金机的转印温度？",
        item_names = ["BrotherHAK180烫金机"],
        is_stream = True
    )

    # 执行节点的业务调用
    node_search_embedding_hyde = NodeSearchEmbeddingHyde()
    final_state = node_search_embedding_hyde(init_state)
