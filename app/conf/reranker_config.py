from dataclasses import dataclass

from app.core.settings import settings


@dataclass
class RerankerConfig:
    text_rerank_api_key: str | None # DashScope API Key
    text_rerank_model: str # 模型名称
    text_rerank_instruct: str # 重排指令

# 实例化配置对象，和原代码lm_config风格保持一致
reranker_config = RerankerConfig(
    text_rerank_api_key=settings.reveal(settings.openai_api_key),
    text_rerank_model=settings.rerank_model,
    text_rerank_instruct=settings.rerank_instruct,
)
