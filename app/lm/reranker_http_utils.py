import random
import time

import dashscope
from dotenv import load_dotenv

from app.conf.reranker_config import reranker_config
from app.core.logger import logger
from app.core.settings import settings

load_dotenv()


def rerank_documents(query: str, documents: list[str]) -> list[float]:
    if not query or not documents:
        return []

 
    if not reranker_config.text_rerank_api_key:
        raise ValueError("OPENAI_API_KEY is required for DashScope reranking")
    dashscope.api_key = reranker_config.text_rerank_api_key
    model_name = reranker_config.text_rerank_model
    instruct = reranker_config.text_rerank_instruct

    response = None
    last_error: Exception | None = None
    for attempt in range(settings.model_max_retries + 1):
        try:
            response = dashscope.TextReRank.call(
                model=model_name,
                query=query,
                documents=documents,
                top_n=len(documents),
                return_documents=False,
                instruct=instruct,
                timeout=settings.model_request_timeout_seconds,
            )
            status_code = getattr(response, "status_code", None)
            if status_code not in (None, 200):
                message = getattr(response, "message", "") or "provider error"
                raise RuntimeError(f"DashScope rerank returned {status_code}: {message}")
            break
        except Exception as exc:
            last_error = exc
            if attempt >= settings.model_max_retries:
                raise RuntimeError("DashScope rerank request failed") from exc
            delay = min(4.0, 0.5 * (2**attempt))
            delay += random.uniform(0.0, min(0.25, delay * 0.2))
            logger.warning(
                "Rerank 调用失败，准备重试，attempt={}/{}，error={}",
                attempt + 1,
                settings.model_max_retries,
                exc.__class__.__name__,
            )
            time.sleep(delay)

    if response is None:
        raise RuntimeError("DashScope rerank request failed") from last_error

    results = response.output.get("results") or []
    scores = [0.0] * len(documents)
    for item in results:
        index = item.get("index")
        score = item.get("relevance_score")
        scores[int(index)] = float(score)
    return scores
