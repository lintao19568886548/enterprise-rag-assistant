"""Grounded answer generation with evidence gating and structured citations."""

from __future__ import annotations

import re
import time
from typing import Any

from app.clients.mongo_history_utils import save_chat_message
from app.clients.minio_utils import presign_minio_uri
from app.core.load_prompt import load_prompt
from app.core.logger import logger
from app.core.metrics import RAG_CITATIONS, RAG_CONFIDENCE, RAG_EVIDENCE
from app.core.settings import settings
from app.lm.lm_utils import get_llm_client
from app.query_process.agent.node_base import NodeBase
from app.query_process.agent.state import QueryGraphState
from app.utils.sse_utils import SSEEvent, push_to_session
from app.utils.task_utils import add_done_task, set_task_result


NO_EVIDENCE_ANSWER = "抱歉，当前知识库中没有足够的可核验资料来回答这个问题。请补充相关文档或换一种更具体的问法。"
NO_EVIDENCE_SIGNALS = (
    "参考资料未明确",
    "参考资料中未明确",
    "知识库中未找到",
    "资料中未找到",
    "没有足够的可核验资料",
    "无法从参考资料",
    "未提供相关信息",
)


class NodeAnswerOutput(NodeBase):
    """Generate only evidence-backed answers and expose their provenance."""

    name = "node_answer_output"

    def process(self, state: QueryGraphState) -> QueryGraphState:
        answer_was_preexisting = bool(state.get("answer"))
        if answer_was_preexisting:
            state["citations"] = []
            state["confidence"] = 0.0
            state["has_sufficient_evidence"] = False
            self._publish_existing_answer(state)
        else:
            self._prepare_evidence(state)
            if not state["has_sufficient_evidence"]:
                state["answer"] = NO_EVIDENCE_ANSWER
                self._publish_existing_answer(state)
            else:
                prompt = self._construct_prompt(state)
                state["prompt"] = prompt
                self._generate_response(state, prompt)
                self._reconcile_generated_evidence(state)

        image_urls = (
            self._extract_images_from_docs(state.get("reranked_docs") or [])
            if state.get("has_sufficient_evidence")
            else []
        )
        state["image_urls"] = image_urls
        self._persist_task_result(state)
        # Item-name confirmation already persisted its own assistant response.
        if state.get("answer") and not answer_was_preexisting:
            self._write_history(state)

        add_done_task(state["session_id"], self.name, state.get("is_stream"))
        if state.get("is_stream"):
            push_to_session(state["session_id"], SSEEvent.FINAL, self._response_payload(state))

        logger.info(
            "答案节点完成，引用数={}，置信度={:.3f}，证据充分={}",
            len(state.get("citations") or []),
            state.get("confidence", 0.0),
            state.get("has_sufficient_evidence", False),
        )
        return state

    def _prepare_evidence(self, state: QueryGraphState) -> None:
        docs = [
            doc
            for doc in (state.get("reranked_docs") or [])
            if isinstance(doc, dict) and str(doc.get("content") or "").strip()
        ][: settings.citation_max_count]
        numeric_scores = [
            float(doc["score"])
            for doc in docs
            if isinstance(doc.get("score"), (int, float))
        ]
        if numeric_scores:
            top_scores = numeric_scores[:3]
            confidence = 0.7 * max(top_scores) + 0.3 * (sum(top_scores) / len(top_scores))
            confidence = max(0.0, min(1.0, confidence))
            score_is_sufficient = max(numeric_scores) >= settings.answer_min_relevance_score
        else:
            confidence = 0.5 if docs else 0.0
            score_is_sufficient = bool(docs)

        sufficient = len(docs) >= settings.answer_min_evidence_chunks and score_is_sufficient
        state["confidence"] = round(confidence, 4)
        state["has_sufficient_evidence"] = sufficient
        state["citations"] = self._build_citations(docs) if sufficient else []

    @staticmethod
    def _build_citations(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        citations: list[dict[str, Any]] = []
        for index, doc in enumerate(docs, start=1):
            score = doc.get("score")
            citations.append(
                {
                    "id": index,
                    "label": f"[{index}]",
                    "source": doc.get("source") or "local",
                    "title": doc.get("file_name") or doc.get("title") or doc.get("file_title") or "未命名资料",
                    "chunk_id": str(doc.get("chunk_id") or ""),
                    "document_id": str(doc.get("document_id") or ""),
                    "document_version": doc.get("document_version"),
                    "page_number": doc.get("page_number"),
                    "url": doc.get("url") or "",
                    "score": round(float(score), 4) if isinstance(score, (int, float)) else None,
                }
            )
        return citations

    def _construct_prompt(self, state: QueryGraphState) -> str:
        docs = (state.get("reranked_docs") or [])[: settings.citation_max_count]
        context, remaining = self._format_docs(docs, settings.answer_context_max_chars)
        history, _ = self._format_history(state.get("history") or [], remaining)
        prompt = load_prompt(
            "answer_out",
            context=context or "无可用参考内容",
            history=history or "暂无历史对话",
            item_names=", ".join(state.get("item_names") or []) or "无指定商品",
            question=state.get("rewritten_query") or state.get("original_query", ""),
        )
        logger.info("回答提示词组装完成，长度={}，上下文条数={}", len(prompt), len(docs))
        return prompt

    @staticmethod
    def _format_docs(docs: list[dict[str, Any]], budget: int) -> tuple[str, int]:
        entries: list[str] = []
        used = 0
        for index, doc in enumerate(docs, start=1):
            content = str(doc.get("content") or "").strip()
            if not content:
                continue
            metadata = [f"[{index}]", f"[source={doc.get('source') or 'local'}]"]
            for field in (
                "title", "file_name", "file_title", "chunk_id", "document_id",
                "document_version", "page_number", "url",
            ):
                value = doc.get(field)
                if value not in (None, ""):
                    metadata.append(f"[{field}={value}]")
            score = doc.get("score")
            if isinstance(score, (int, float)):
                metadata.append(f"[score={float(score):.4f}]")
            entry = " ".join(metadata) + "\n" + content
            if used + len(entry) > budget:
                break
            entries.append(entry)
            used += len(entry) + 2
        return "\n\n".join(entries), max(0, budget - used)

    @staticmethod
    def _format_history(history: list[dict[str, Any]], budget: int) -> tuple[str, int]:
        labels = {"user": "用户", "assistant": "助手"}
        lines: list[str] = []
        used = 0
        for message in history:
            role = str(message.get("role") or "")
            content = str(message.get("text") or "").strip()
            if role not in labels or not content:
                continue
            line = f"{labels[role]}: {content}"
            if used + len(line) > budget:
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines), max(0, budget - used)

    def _generate_response(self, state: QueryGraphState, prompt: str) -> None:
        llm = get_llm_client()
        state["model"] = settings.llm_model
        started = time.perf_counter()
        try:
            if state.get("is_stream"):
                parts: list[str] = []
                for chunk in llm.stream(prompt):
                    delta = str(getattr(chunk, "content", "") or "")
                    if delta:
                        parts.append(delta)
                        push_to_session(state["session_id"], SSEEvent.DELTA, {"delta": delta})
                state["answer"] = "".join(parts)
            else:
                response = llm.invoke(prompt)
                state["answer"] = str(response.content or "")
        except Exception as exc:
            logger.opt(exception=True).error("模型生成失败：{}", exc.__class__.__name__)
            raise RuntimeError("model generation failed") from exc
        finally:
            state["latency_ms"] = int((time.perf_counter() - started) * 1000)

        if not state.get("answer"):
            raise RuntimeError("model returned an empty answer")
        logger.info("模型生成完成，长度={}，耗时={}ms", len(state["answer"]), state["latency_ms"])

    @staticmethod
    def _reconcile_generated_evidence(state: QueryGraphState) -> None:
        """Keep the structured evidence flag aligned with a model refusal."""
        answer = str(state.get("answer") or "").strip()
        matched_positions = [
            answer.find(signal) for signal in NO_EVIDENCE_SIGNALS if signal in answer
        ]
        if not matched_positions:
            return
        has_inline_citation = re.search(r"\[\d+\]", answer) is not None
        # A grounded answer may honestly end with “other details are not stated”.
        # Treat the whole answer as a refusal only when that signal leads the
        # response or when the response contains no citation at all.
        if min(matched_positions) > 40 and has_inline_citation:
            return
        state["has_sufficient_evidence"] = False
        state["confidence"] = min(float(state.get("confidence") or 0.0), 0.2)
        state["citations"] = []

    @staticmethod
    def _publish_existing_answer(state: QueryGraphState) -> None:
        if state.get("is_stream") and state.get("answer"):
            push_to_session(state["session_id"], SSEEvent.DELTA, {"delta": state["answer"]})

    @staticmethod
    def _extract_images_from_docs(docs: list[dict[str, Any]]) -> list[str]:
        pattern = re.compile(r"!\[.*?\]\((.*?)\)")
        suffixes = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg")
        images: list[str] = []
        seen: set[str] = set()
        for doc in docs:
            direct_url = str(doc.get("url") or "").strip()
            candidates = [direct_url] if direct_url.lower().endswith(suffixes) else []
            candidates.extend(pattern.findall(str(doc.get("content") or "")))
            for candidate in candidates:
                value = candidate.strip()
                if value.startswith("minio://"):
                    try:
                        value = presign_minio_uri(value)
                    except Exception as exc:
                        logger.warning("MinIO 图片签名失败：{}", exc.__class__.__name__)
                        continue
                if value and value not in seen:
                    seen.add(value)
                    images.append(value)
        return images

    def _persist_task_result(self, state: QueryGraphState) -> None:
        confidence = float(state.get("confidence") or 0.0)
        citations = len(state.get("citations") or [])
        sufficient = bool(state.get("has_sufficient_evidence"))
        RAG_CONFIDENCE.observe(confidence)
        RAG_CITATIONS.observe(citations)
        RAG_EVIDENCE.labels(str(sufficient).lower()).inc()
        for key, value in self._response_payload(state).items():
            set_task_result(state["session_id"], key, value)

    @staticmethod
    def _response_payload(state: QueryGraphState) -> dict[str, Any]:
        return {
            "answer": state.get("answer") or "",
            "status": "completed",
            "citations": state.get("citations") or [],
            "image_urls": state.get("image_urls") or [],
            "confidence": state.get("confidence", 0.0),
            "has_sufficient_evidence": state.get("has_sufficient_evidence", False),
            "model": state.get("model") or "",
            "latency_ms": state.get("latency_ms", 0),
        }

    @staticmethod
    def _write_history(state: QueryGraphState) -> None:
        try:
            save_chat_message(
                session_id=state["session_id"],
                role="assistant",
                text=state.get("answer") or "",
                item_names=state.get("item_names") or [],
                image_urls=state.get("image_urls") or [],
                knowledge_base_id=state.get("knowledge_base_id"),
                citations=state.get("citations") or [],
                model=state.get("model") or None,
                latency_ms=state.get("latency_ms") or None,
                user_id=state.get("user_id") or "",
                tenant_id=state.get("tenant_id") or "",
            )
        except Exception as exc:
            logger.warning("对话历史写入失败，不影响本次回答：{}", exc.__class__.__name__)
