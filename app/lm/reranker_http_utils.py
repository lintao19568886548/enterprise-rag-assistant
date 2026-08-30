import os

import dashscope
from dotenv import load_dotenv

from app.conf.reranker_config import reranker_config

load_dotenv()


def rerank_documents(query: str, documents: list[str]) -> list[float]:
    if not query or not documents:
        return []

 
    dashscope.api_key = reranker_config.text_rerank_api_key
    model_name = reranker_config.text_rerank_model
    instruct = reranker_config.text_rerank_instruct

    response = dashscope.TextReRank.call(
        model=model_name,
        query=query,
        documents=documents,
        top_n=len(documents),
        return_documents=False,
        instruct=instruct,
    )
    status_code = getattr(response, "status_code", None)
    if status_code not in (None, 200):
        message = getattr(response, "message", "") or str(response)
        raise RuntimeError(f"DashScope rerank 调用失败: {message}")

    results = response.output.get("results") or []
    scores = [0.0] * len(documents)
    for item in results:
        index = item.get("index")
        score = item.get("relevance_score")
        scores[int(index)] = float(score)
    return scores
