from dataclasses import dataclass

from app.core.settings import settings


# 定义minerU服务配置
@dataclass
class LLMConfig:
    base_url: str | None
    api_key: str | None
    lv_model: str
    llm_model: str
    llm_temperature: float

lm_config = LLMConfig(
    base_url=settings.openai_base_url,
    api_key=settings.reveal(settings.openai_api_key),
    lv_model=settings.vl_model,
    llm_model=settings.llm_model,
    llm_temperature=settings.llm_temperature,
)
