"""Tag / Summary / Implications 스테이지 — 기획서 §10.6.

- 태그 (호출 1): 문서 앞 3,000자 → JSON `{topic_tags, entity_tags, document_type, time_reference}`
- 요약 (호출 2): map-reduce (긴 문서는 청크별 mini-summary 합성) → `{summary_3line, implications}`
- 변경점 diff (호출 3): `Day 5` 이후. doc_embedding 필요하므로 임베딩 스테이지 이후에 삽입

실패 정책 (§10.10)
- LLMProvider 가 내부적으로 3회 retry 후 최종 실패 → 예외를 swallow 하고 `documents` 필드를
  NULL 로 유지. 파이프라인은 계속 진행. `ingest_logs` 에는 `failed` 로 기록.
- 부분 성공 허용: 태그 성공 / 요약 실패 같은 경우 성공한 쪽만 저장.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.adapters.impl.gemini_llm import GeminiLLMProvider
from app.adapters.llm import ChatMessage
from app.adapters.parser import ExtractionResult
from app.db import get_supabase_client
from app.ingest.jobs import begin_stage, end_stage, update_stage
from app.services.quota import is_quota_exhausted

logger = logging.getLogger(__name__)

_STAGE = "tag_summarize"
_TAG_INPUT_CHARS = 3000
_SUMMARY_INPUT_CHARS = 12000

_llm = GeminiLLMProvider()


def run_tag_summarize_stage(
    job_id: str, *, doc_id: str, extraction: ExtractionResult
) -> None:
    """태그·요약을 생성해 documents 에 저장. 실패 시 graceful 이어가기."""
    update_stage(job_id, stage=_STAGE)
    log_id = begin_stage(job_id, stage=_STAGE)
    started = time.monotonic()

    tags: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    errors: list[str] = []

    quota_exhausted = False  # W9 Day 6 한계 #53 — 첫 호출 quota 시 두 번째 skip

    try:
        tags = _call_tags(extraction.raw_text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("태그 호출 실패 (doc=%s): %s", doc_id, exc)
        errors.append(f"tags: {exc}")
        # W9 Day 7 — exc 객체 자체 전달 → class name + status code 정확 검사 (한계 #50).
        if is_quota_exhausted(exc):
            quota_exhausted = True

    if quota_exhausted:
        logger.info(
            "tag_summarize: doc=%s quota 감지 → summary 호출 skip (LLM 비용 절약)",
            doc_id,
        )
        errors.append("summary: skipped due to quota")
    else:
        try:
            summary = _call_summary(extraction.raw_text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("요약 호출 실패 (doc=%s): %s", doc_id, exc)
            errors.append(f"summary: {exc}")

    try:
        _persist(doc_id, tags=tags, summary=summary)
    except Exception as exc:  # noqa: BLE001 — DB 쓰기 실패는 상위로 올려 fail_job
        duration_ms = int((time.monotonic() - started) * 1000)
        end_stage(
            log_id,
            status="failed",
            error_msg=f"persist: {exc}",
            duration_ms=duration_ms,
        )
        raise

    duration_ms = int((time.monotonic() - started) * 1000)
    if errors:
        end_stage(
            log_id,
            status="failed" if not (tags or summary) else "succeeded",
            error_msg=" | ".join(errors) if errors else None,
            duration_ms=duration_ms,
        )
    else:
        end_stage(log_id, status="succeeded", duration_ms=duration_ms)


# ---------------------- LLM 호출 ----------------------


def _call_tags(raw_text: str) -> dict[str, Any]:
    head = (raw_text or "")[:_TAG_INPUT_CHARS]
    if not head.strip():
        return {
            "topic_tags": [],
            "entity_tags": [],
            "document_type": None,
            "time_reference": None,
        }

    system = (
        "당신은 한국어 문서의 태그 추출 도우미입니다. 주어진 텍스트에서 다음 JSON 을 생성하세요.\n"
        "- topic_tags: 주제 키워드 3~7 개 (한국어, 명사형)\n"
        "- entity_tags: 인명·조직·제품·지명 등 고유명사 0~10 개\n"
        "- document_type: 보고서|논문|기사|블로그|메모|회의록|이메일|메신저|공지|기타 중 하나\n"
        "- time_reference: 문서가 다루는 시점 (YYYY 또는 YYYY-MM, 없으면 null)\n"
        "응답은 반드시 위 4개 키를 가진 단일 JSON 객체만 포함. 설명·Markdown·코드블록 금지."
    )
    user = f"다음 텍스트에서 태그를 추출하세요:\n\n{head}"
    response = _llm.complete(
        [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
        temperature=0.1,
        json_mode=True,
    )
    return _parse_json(response)


def _call_summary(raw_text: str) -> dict[str, Any]:
    body = (raw_text or "")[:_SUMMARY_INPUT_CHARS]
    if not body.strip():
        return {"summary_3line": "", "implications": ""}

    system = (
        "당신은 한국어 문서 요약 도우미입니다. 주어진 텍스트에서 다음 JSON 을 생성하세요.\n"
        "- summary_3line: 3줄 요약 (각 줄 60자 이내, '\\n' 구분)\n"
        "- implications: 이 문서가 개인 지식 관점에서 의미하는 바 (1~2문장)\n"
        "응답은 반드시 위 2개 키를 가진 단일 JSON 객체만 포함. 설명·Markdown·코드블록 금지."
    )
    user = f"다음 텍스트를 요약하세요:\n\n{body}"
    response = _llm.complete(
        [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
        temperature=0.2,
        json_mode=True,
    )
    return _parse_json(response)


def _parse_json(text: str) -> dict[str, Any]:
    # response_mime_type='application/json' 이라도 혹시 모를 코드블록·공백 대비
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip("`\n ")
    return json.loads(cleaned)


# ---------------------- DB 반영 ----------------------


def _persist(
    doc_id: str,
    *,
    tags: dict[str, Any] | None,
    summary: dict[str, Any] | None,
) -> None:
    patch: dict[str, Any] = {}
    if tags is not None:
        topic = list(tags.get("topic_tags") or [])
        entity = list(tags.get("entity_tags") or [])
        patch["tags"] = list(dict.fromkeys([*topic, *entity]))  # 순서 유지 + 중복 제거
        flags_patch: dict[str, Any] = {}
        if tags.get("document_type"):
            flags_patch["document_type"] = tags["document_type"]
        if tags.get("time_reference"):
            flags_patch["time_reference"] = tags["time_reference"]
        if flags_patch:
            patch["flags"] = _merge_flags(doc_id, flags_patch)
    if summary is not None:
        if summary.get("summary_3line") is not None:
            patch["summary"] = summary["summary_3line"]
        if summary.get("implications") is not None:
            patch["implications"] = summary["implications"]

    if not patch:
        return

    get_supabase_client().table("documents").update(patch).eq("id", doc_id).execute()


def _merge_flags(doc_id: str, new_flags: dict[str, Any]) -> dict[str, Any]:
    resp = (
        get_supabase_client()
        .table("documents")
        .select("flags")
        .eq("id", doc_id)
        .limit(1)
        .execute()
    )
    current: dict[str, Any] = dict((resp.data[0].get("flags") if resp.data else None) or {})
    current.update(new_flags)
    return current
