"""M1 W-2 (S4-D) — 인제스트 단계 동의어 후보 마커 주입 / 제거.

설계 (senior-planner 명세 — 1안: 마이그레이션 0, text append, strippable 마커):
- chunk text 끝에 `\\n\\n[검색어: {공백구분 키워드들}]` 한 줄 주입.
  PGroonga (`idx_chunks_text_pgroonga`) 가 `chunks.text` 컬럼을 Mecab 으로 인덱싱하므로
  마이그레이션 없이 즉시 sparse 매칭 가능.
- dense 임베딩(BGE-M3 = `chunks.text`) 오염은 짧은 list (doc 8 / chunk 5 cap) 라 수용.
  ON/OFF ablation 은 M2 W-4 재인제스트 후 측정 (default OFF — 측정 전까지 영향 0).
- snippet 노출 방지 — `strip_synonym_marker` 를 검색 측 snippet 생성 직전에 호출.
- `metadata["synonym_candidates"]` 에 list 항상 보존 + `metadata["synonym_source"]`
  (= `"dict"` / `"dict+llm"`) 디버그 필드.

ENV (둘 다 default false — chunk.py 가 함수 호출 시점에 평가, 모듈 import 시점 X):
- `JETRAG_SYNONYM_INJECTION_ENABLED` — true 일 때만 후보 수집·마커 주입.
- `JETRAG_SYNONYM_INJECTION_LLM` — true 일 때만 doc당 1회 Flash-Lite 후보 생성 (b 단계).
  ENABLED=true 일 때만 의미. (b) 는 최소 구현 — 프롬프트 정교화는 M2.
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata

logger = logging.getLogger(__name__)

_ENV_ENABLED = "JETRAG_SYNONYM_INJECTION_ENABLED"
_ENV_LLM = "JETRAG_SYNONYM_INJECTION_LLM"

_CAP_PER_DOC_LLM_PAIRS = 8  # doc-level LLM 후보 쌍 cap (dense 오염 최소화)
_LLM_INPUT_CHARS = 3000  # tag_summarize 의 _TAG_INPUT_CHARS 와 동일

_MARKER_PREFIX = "[검색어: "
_MARKER_SUFFIX = "]"
# 마커 strip 정규식 — text 끝의 `\n*[검색어: ... ]` (+ 후행 공백). 없으면 no-op.
_SYNONYM_MARKER_RE = re.compile(r"\n*\[검색어:[^\]]*\]\s*$")


def _env_true(key: str) -> bool:
    """extract.py 패턴 — ENV 값을 strip().lower() 후 'true' 비교 (호출 시점 평가)."""
    return os.environ.get(key, "false").strip().lower() == "true"


# ---------------------- 후보 수집 ----------------------


def collect_synonym_candidates(
    text: str,
    *,
    doc_llm_pairs: list[tuple[str, list[str]]] | None = None,
    cap_per_chunk: int = 5,
) -> list[str]:
    """chunk text 에 주입할 동의어 후보 list 를 산출.

    ENV `JETRAG_SYNONYM_INJECTION_ENABLED` != true 면 항상 빈 list (호출 시점 평가).

    규칙:
    - 정적 사전 키가 text 에 등장 → 그 동의어 후보들
    - doc_llm_pairs 의 term 이 text 에 등장 → 그 synonyms 후보들
    - 이미 text 에 그 string 그대로 있으면 제외 (중복 인덱싱 회피)
    - dedupe + cap_per_chunk

    Args:
        text: NFC 정규화된 chunk 본문.
        doc_llm_pairs: `generate_doc_llm_pairs` 결과 (없으면 None).
        cap_per_chunk: 한 chunk 에 주입할 최대 후보 수.
    """
    if not _env_true(_ENV_ENABLED):
        return []
    if not text:
        return []

    # lazy import — 모듈 import 그래프 단순화 (chunk.py 가 이 모듈을 lazy import).
    from app.services.synonym_dict import iter_dict_keys, lookup_synonyms

    out: list[str] = []
    for key in iter_dict_keys():
        if key in text:
            out.extend(lookup_synonyms(key))
    if doc_llm_pairs:
        for term, synonyms in doc_llm_pairs:
            if term and term in text:
                out.extend(synonyms)

    # 이미 text 에 등장하는 후보 제외 + dedupe (보존 순서) + cap.
    # senior-qa P2 — 후보 string 에 `[` / `]` 가 섞이면 `[검색어: ...]` 마커 구조가 깨져
    # `strip_synonym_marker` 가 마커 일부만 남길 수 있음 → inject 진입 전 단일 chokepoint 에서
    # 양 대괄호 제거. 정적 사전 36 entry 는 무관(no-op), LLM 후보(`generate_doc_llm_pairs`)만 해당.
    seen: set[str] = set()
    result: list[str] = []
    for cand in out:
        c = (cand or "").strip().replace("[", "").replace("]", "").strip()
        if not c or c in seen or c in text:
            continue
        seen.add(c)
        result.append(c)
        if len(result) >= cap_per_chunk:
            break
    return result


# ---------------------- 마커 주입 / 제거 ----------------------


def inject_marker(text: str, candidates: list[str]) -> str:
    """text 끝에 `\\n\\n[검색어: ...]` 마커 주입 (결과 NFC). 빈 후보면 text 그대로."""
    if not candidates:
        return text
    marker = _MARKER_PREFIX + " ".join(candidates) + _MARKER_SUFFIX
    return unicodedata.normalize("NFC", text + "\n\n" + marker)


def strip_synonym_marker(text: str) -> str:
    """`inject_marker` 가 붙인 마커를 제거 (없으면 no-op).

    검색 측 snippet/하이라이트 생성 직전에 호출 — 사용자에게 마커 노출 방지.
    마커가 없는 (재인제스트 전) chunk 는 변화 없음.
    """
    if not text or _MARKER_PREFIX not in text:
        return text
    return _SYNONYM_MARKER_RE.sub("", text)


# ---------------------- (b) LLM doc-level 후보 (stub → 최소 구현) ----------------------


def generate_doc_llm_pairs(raw_text: str) -> list[tuple[str, list[str]]]:
    """doc당 1회 Flash-Lite 호출로 (term, synonyms) 쌍 후보 생성.

    ENV `JETRAG_SYNONYM_INJECTION_LLM` != true 면 빈 list (stub — 호출 시점 평가).
    true 면 `get_llm_provider("synonym")` 경유 1회 호출. 파싱 실패·dict 아님·quota·예외 →
    빈 list (graceful — chunk 저장 차단 회피). 최소 구현 — 프롬프트 정교화는 M2.
    """
    if not _env_true(_ENV_LLM):
        return []
    head = (raw_text or "")[:_LLM_INPUT_CHARS]
    if not head.strip():
        return []

    try:
        from app.adapters.factory import get_llm_provider
        from app.adapters.llm import ChatMessage

        system = (
            "당신은 한국어 문서 검색 보조 사전 생성기입니다. 주어진 텍스트의 핵심 용어와 "
            "그 동의어·약어·일상어 후보를 보수적으로 3~5쌍 추출하세요.\n"
            "- 너무 일반적인 명사(정보·관리·규정 등 단독) 는 제외\n"
            "- 각 쌍의 term 은 텍스트에 실제 등장하는 표현\n"
            "응답은 반드시 다음 형식의 단일 JSON 객체만 포함 (설명·Markdown·코드블록 금지):\n"
            '{"pairs":[{"term":"...","synonyms":["...","..."]}]}'
        )
        user = f"다음 텍스트에서 검색 보조 사전 쌍을 추출하세요:\n\n{head}"
        provider = get_llm_provider("synonym")
        response = provider.complete(
            [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
            temperature=0.1,
            json_mode=True,
        )
        return _parse_llm_pairs(response)
    except Exception as exc:  # noqa: BLE001 — graceful: chunk 저장 차단 회피
        logger.warning("synonym LLM 후보 생성 실패 (graceful, 빈 list 반환): %s", exc)
        return []


def _parse_llm_pairs(text: str) -> list[tuple[str, list[str]]]:
    """LLM 응답 JSON → [(term, synonyms), ...]. 형식 안 맞으면 빈 list."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip("`\n ")
    try:
        data = json.loads(cleaned)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict):
        return []
    pairs_raw = data.get("pairs")
    if not isinstance(pairs_raw, list):
        return []
    out: list[tuple[str, list[str]]] = []
    for item in pairs_raw:
        if not isinstance(item, dict):
            continue
        term = item.get("term")
        synonyms = item.get("synonyms")
        if not isinstance(term, str) or not term.strip():
            continue
        if not isinstance(synonyms, list):
            continue
        clean_syns = [s.strip() for s in synonyms if isinstance(s, str) and s.strip()]
        if not clean_syns:
            continue
        out.append((term.strip(), clean_syns))
        if len(out) >= _CAP_PER_DOC_LLM_PAIRS:
            break
    return out
