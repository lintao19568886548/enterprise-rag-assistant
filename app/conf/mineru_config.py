from dataclasses import dataclass

from app.core.settings import settings


# 定义minerU服务配置
@dataclass
class MineruConfig:
    base_url: str | None
    api_token: str | None

mineru_config = MineruConfig(
    base_url=settings.mineru_base_url,
    api_token=settings.reveal(settings.mineru_api_token),
)
