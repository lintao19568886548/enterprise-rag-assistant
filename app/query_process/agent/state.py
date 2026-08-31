
import copy
from typing import List, TypedDict

from app.core.logger import logger

class QueryGraphState(TypedDict, total=False):
    """
    QueryGraphState 定义了整个查询流程中流转的数据结构。
    TypedDict 让我们在代码中能有自动补全和类型检查。
    使用字典式访问（如 state["session_id"]、state.get("original_query")）
    """

    task_id: str          # 任务唯一ID，用于追踪日志

    session_id: str  # 会话唯一标识
    tenant_id: str  # 租户隔离边界
    user_id: str  # 会话所有者
    knowledge_base_id: str  # 知识库隔离边界
    original_query: str  # 用户原始问题

    # 检索过程中的中间数据
    embedding_chunks: list  # 普通向量检索回来的切片
    hyde_embedding_chunks:list # 已向量化的假设性问题切片
    hyde_doc: str
    web_search_docs: list  # 网络搜索回来的文档

    # 排序过程中的数据
    rrf_chunks: list  # RRF 融合排序后的切片
    reranked_docs: list  # 重排序后的最终 Top-K 文档

    # 生成过程中的数据
    prompt: str  # 组装好的 Prompt
    answer: str  # 最终生成的答案
    citations: list  # 可核验引用
    image_urls: list  # 答案关联图片
    confidence: float  # 基于检索证据的置信度
    has_sufficient_evidence: bool  # 是否达到生成阈值
    model: str  # 实际使用的生成模型
    latency_ms: int  # 生成耗时

    # 辅助信息
    item_names: List[str]  # 提取出的商品名称
    rewritten_query: str  # 改写后的问题
    history: list  # 历史对话记录
    is_stream: bool  # 是否流式输出标记


# 定义图状态的默认初始值
graph_default_state: QueryGraphState = {
    "task_id":"",
    "session_id": "",
    "tenant_id": "",
    "user_id": "",
    "knowledge_base_id": "",
    "original_query": "",
    "embedding_chunks": [],
    "hyde_embedding_chunks": [],
    "hyde_doc": "",
    "web_search_docs": [],
    "rrf_chunks": [],
    "reranked_docs": [],
    "prompt": "",
    "answer": "",
    "citations": [],
    "image_urls": [],
    "confidence": 0.0,
    "has_sufficient_evidence": False,
    "model": "",
    "latency_ms": 0,
    "item_names": [],
    "rewritten_query": "",
    "history": [],
    "is_stream": False
}


def create_default_state(**overrides) -> QueryGraphState:
    """
    创建默认状态，支持覆盖
    优势：
    ✅ 自动填充所有字段的默认值
    ✅ 只需传需要覆盖的字段
    ✅ 避免遗漏字段
    ✅ 深拷贝隔离，避免污染全局状态
    ✅ 代码更简洁、可读性更好

    用法：state = create_default_state(session_id="sess_001", original_query="如何使用万用表？")

    :param overrides: 要覆盖的字段（关键字参数解包）
    :return: 新的状态实例
    """
    # 默认状态
    state = copy.deepcopy(graph_default_state)
    # 用 overrides 覆盖默认值
    state.update(overrides)
    # 返回创建好的状态字典实例
    return state


def get_default_state() -> QueryGraphState:
    """
    返回一个新的状态实例，避免全局变量污染
    """
    return copy.deepcopy(graph_default_state)


if __name__ == "__main__":
    """
    测试
    """
    # ✅ 创建默认状态
    state = create_default_state(
        task_id="task_001",
        session_id="query_20260331_001",
        original_query="如何使用万用表测量电压？"
    )

    logger.info(state)
