from dataclasses import dataclass

from app.core.settings import settings


# 定义mcp的服务配置
@dataclass
class McpConfig:
    mcp_base_url: str | None
    api_key: str | None

mcp_config = McpConfig(
    mcp_base_url=settings.mcp_dashscope_base_url,
    api_key=settings.reveal(settings.openai_api_key),
)
