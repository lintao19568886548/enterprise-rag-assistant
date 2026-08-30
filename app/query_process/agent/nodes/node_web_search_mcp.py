import asyncio
import json

from agents.mcp import MCPServerStreamableHttp

from app.conf.bailian_mcp_config import mcp_config
from app.query_process.agent.node_base import NodeBase
from app.core.logger import logger
from app.query_process.agent.state import QueryGraphState, create_default_state
from app.utils.task_utils import add_done_task


class NodeWebSearchMcp(NodeBase):
    """
    节点功能，调用外部搜索引擎补充信息
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_web_search_mcp"

    def process(self, state: QueryGraphState) -> QueryGraphState:

        query = state.get("rewritten_query", "")
        docs = []
        # 如果没有查询内容，直接返回
        if query:
            try:
                result = asyncio.run(self._mcp_call(query))
            except Exception as exc:
                # 联网搜索是知识库检索的补充能力。外部 MCP 服务不可用时，
                # 应继续使用本地向量检索结果，不能中断整条问答流程。
                logger.warning("联网搜索 MCP 不可用，已降级为仅使用本地知识库：{}", exc)
                return {}
            if result:
                pages = json.loads(result.content[0].text).get("pages") or []
                # 统一输出结构化结果，供后续 rerank/引用使用
                # 每条：{title, url, snippet}

                for item in pages:
                    snippet = (item.get("snippet") or "").strip()
                    url = (item.get("url") or "").strip()
                    title = (item.get("title") or "").strip()
                    if not snippet:
                        continue
                    docs.append({"title": title, "url": url, "snippet": snippet})

                logger.info("MCP 搜索结果:", docs)

        if docs:
            add_done_task(state["session_id"], self.name)
            return {"web_search_docs": docs}
        return {}


    async def _mcp_call(self, query):

        search_mcp = MCPServerStreamableHttp(
            name="search_mcp",
            params={
                "url": mcp_config.mcp_base_url,
                "headers": {"Authorization": f"Bearer {mcp_config.api_key}"},
                "timeout": 10,
            },
            cache_tools_list=True,
            max_retry_attempts=3,
        )

        try:
            await search_mcp.connect()
            result = await search_mcp.call_tool(
                tool_name="bailian_web_search",
                arguments={"query": query, "count": 5},
            )
            return result
        finally:
            await search_mcp.cleanup()


if __name__ == "__main__":

    # 当前节点图状态初始值
    init_state = create_default_state(
        rewritten_query = "HAK 180 在出厂默认状态下，若想在纸张上只把烫金膜转印到顶部 50 mm–170 mm 的局部区域，应在操作面板上如何设置"
    )

    # 执行节点的业务调用
    node_web_search_mcp = NodeWebSearchMcp()
    final_state = node_web_search_mcp(init_state)

    # 输出搜索结果
    search_results = final_state.get('web_search_docs', [])
    print(f"搜索结果数量: {len(search_results)}")
    print("search_results", search_results)
