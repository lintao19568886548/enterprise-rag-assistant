
import hashlib
from typing import List, Dict, Any, Tuple

from app.query_process.agent.node_base import NodeBase
from app.core.logger import logger
from app.query_process.agent.state import QueryGraphState, create_default_state
from app.utils.task_utils import add_done_task


class NodeRrf(NodeBase):
    """
    节点功能：Reciprocal Rank Fusion
    流程：将多路召回的结果（向量、HyDE）进行加权融合排序，提高相关性
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_rrf"

    def process(self, state: QueryGraphState) -> QueryGraphState:

        # 1. 各路搜索的结果（排除网络搜索: reranK节点做）
        # RRF(多路结果的融合：基于文档的排名，把多路都命中的文档，未来计算出来的得分更高，对应顺序靠前)
        # 1.1 获取向量检索路的结果
        vector_search_chunks = state.get('embedding_chunks') or []
        # 1.2 获取hyde向量检索路的结果
        hyde_search_chunks = state.get('hyde_embedding_chunks') or []

        # 2. 为不同路的搜索结果设置不同的权重
        search_source = {
            "vector_search_result": (self._normalize_input(vector_search_chunks), 1.0),
            "hyde_search_result": (self._normalize_input(hyde_search_chunks), 1.0)
        }

        # 3. 构建rrf_inputs
        rrf_inputs = list(search_source.values())

        # 4. 利用RRF的计算公式去获取到所有路查询到的所有chunk对应的score
        rrf_merge_results = self._rrf_merge(rrf_inputs, k=60, max_results=10)

        # 5. 获取rrf_chunks（只取文档，不要分数）
        rrf_chunks = [doc for doc, _ in rrf_merge_results]
        logger.info(f"RRF 融合完成，返回 {len(rrf_chunks)} 条结果")

        # 6. 记录分数范围（便于调试）
        scores = [s for _, s in rrf_merge_results]
        if scores:
            logger.info(f"分数范围: [{min(scores):.6f}, {max(scores):.6f}]")
        else:
            logger.info("RRF 没有可融合的有效结果")

        # 7. 更新state
        state['rrf_chunks'] = rrf_chunks

        add_done_task(state["session_id"], self.name)
        # 8. 返回state
        return state

    def _normalize_input(self, rrf_input: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        统一处理各路检索到的结果
        :param rrf_input:   各路不同数据结构的检索结果
        :return:            统一处理后的标准数据结构的检索结果
        """

        diff_path_result = []
        # 1. 遍历各路搜索结果
        if not rrf_input:
            return []

        # 2. 遍历该路的所有结果
        for doc in rrf_input:

            # 2.1 判断是否有效
            if not isinstance(doc, dict):
                continue

            # 2.2  获取entity
            entity = doc.get('entity')
            if not entity:
                continue
            normalized = dict(entity)
            raw_score = doc.get("distance", doc.get("score"))
            if raw_score is not None:
                normalized.setdefault("retrieval_score", float(raw_score))
            diff_path_result.append(normalized)

        return diff_path_result

    def _rrf_merge(self, rrf_inputs, k: int = 60, max_results: int = None) -> List[Tuple[Dict[str, Any], float]]:
        """
        利用 RRF 公式计算每一个文档的总得分
        :param rrf_inputs:  列表，每个元素是(各路的搜索结果列表, 权重)的元组
        :param k:           平滑参数(RFF常数)，通常取 60
        :param max_results: 合并完之后返回的文档数，None 表示全部
        :return:            合并以及排序后的文档列表，[(元素, RRF 得分), ...] 按得分降序
        """
        chunk_scores = {}  # 存放所有 chunk 的 RRF 计算后的分数值
        chunk_data = {}    # 存放所有 chunk 的文档数据

        for rrf_input, weight in rrf_inputs:
            for rank, doc in enumerate(rrf_input, start=1):
                chunk_id = self._document_key(doc)
                # RRF 公式: score += weight / (k + rank)
                chunk_scores[chunk_id] = chunk_scores.get(chunk_id, 0.0) + weight / (k + rank)

                # 使用 setdefault 保留首次遇到的文档版本(只记录第一次)
                chunk_data.setdefault(chunk_id, doc)

        # 按得分降序排序
        sorted_results = sorted(
            [({**chunk_data[cid], "rrf_score": score}, score) for cid, score in chunk_scores.items()],
            # 排序时看每个元素的第 2 个值（也就是分数）
            key=lambda x: x[1],
            reverse=True
        )
        # 等价写法
        # def get_score(item):
        #     return item[1]  # 返回分数
        # sorted_results = sorted(..., key=get_score, reverse=True)

        # 动态截取前 max_results 条
        return sorted_results[:max_results] if max_results else sorted_results

    @staticmethod
    def _document_key(doc: Dict[str, Any]) -> str:
        """Deduplicate by stable provenance, falling back to normalized content."""
        chunk_id = str(doc.get("chunk_id") or "").strip()
        if chunk_id:
            return f"chunk:{chunk_id}"
        content_hash = str(doc.get("content_hash") or "").strip()
        if content_hash:
            return f"hash:{content_hash}"
        provenance = ":".join(
            str(doc.get(field) or "")
            for field in ("document_id", "document_version", "chunk_index")
        )
        if provenance.replace(":", ""):
            return f"provenance:{provenance}"
        content = " ".join(str(doc.get("content") or "").split()).casefold()
        return f"content:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"

if __name__ == '__main__':

    logger.info("开始测试: RRF 融合节点")

    # 模拟两路检索结果
    mock_state = {
        "embedding_chunks": [
            {"entity": {"chunk_id": "chunk_1", "content": "向量搜索结果#1"}},
            {"entity": {"chunk_id": "chunk_2", "content": "向量搜索结果#2"}},
            {"entity": {"chunk_id": "chunk_3", "content": "向量搜索结果#3"}},
        ],
        "hyde_embedding_chunks": [
            {"entity": {"chunk_id": "chunk_1", "content": "HyDE搜索结果#1"}},
            {"entity": {"chunk_id": "chunk_4", "content": "HyDE搜索结果#2"}},
            {"entity": {"chunk_id": "chunk_2", "content": "HyDE搜索结果#3"}},
        ]
    }

    logger.info("【输入状态】:")
    logger.info(f"  embedding_chunks: {len(mock_state['embedding_chunks'])} 条")
    logger.info(f"  hyde_embedding_chunks: {len(mock_state['hyde_embedding_chunks'])} 条")
    logger.info("-" * 60)

    node_rrf = NodeRrf()
    result = node_rrf.process(mock_state)

    logger.info("【融合结果】:")
    for i, chunk in enumerate(result["rrf_chunks"], 1):
        logger.info(f"[{i}] {chunk.get('chunk_id')} - {chunk.get('content')}")

    logger.info("测试完成")
