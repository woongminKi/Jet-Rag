"""W26 S1 D1 — self-supervised 골든셋 자동 생성 (v2 — v0.7 통합 schema).

flow:
1. 적재된 docs 에서 stratified sampling 으로 chunks 선택 (각 doc N건)
2. 각 chunk → Gemini 한국어 자연어 query 1개 생성 (한국어 강제 prompt)
3. 정답 chunk = 자기 chunk_idx (relevant, weight 1.0)
4. 같은 doc 내 chunks dense_vec 와 cosine ≥ 임계 → acceptable (weight 0.5)
5. **v2 신규** — query_type 9 라벨 룰 분류 + must_include 추출 + source_hint 추출
6. **v2 신규** — out_of_scope/negative query 사전 정의 5건 append

산출: `evals/golden_v0.7_auto.csv` (v0.6 user CSV 와 schema 호환)
schema (12 컬럼):
    id, query, query_type, doc_id, expected_doc_title, relevant_chunks,
    acceptable_chunks, source_chunk_text, expected_answer_summary,
    must_include, source_hint, negative

사용:
    cd api && uv run python ../evals/auto_goldenset.py --chunks-per-doc 5 --acceptable-cosine 0.7

비용: 11 docs × 5 chunks × 1 Gemini call ≈ 55 호출 (~$0.05). 시간 ~5~10분.

한국어 강제 prompt — RAGAS auto 의 영어 mix 한계 회피 (W25 D14 §6.3 학습).

v2 변경 (S1 D1):
- query_type: 룰 기반 9 라벨 분류 (LLM 호출 0)
- must_include: 숫자 + 한글 명사 키워드 추출 (re 룰)
- source_hint: chunks.page → "p.{N}"
- negative: 사전 정의 5건 (G-N-001 ~ G-N-005)
- expected_answer_summary: source_chunk_text 첫 60자 룰 요약

v2.1 변경 (S1 D2 — 사용자 자료 노출 방지):
- `--public-only-source-text` 옵션 (default true) — `assets/public/` 9건 외 doc 의
  source_chunk_text·expected_answer_summary 를 빈 값으로 비식별화. 정책 (b).
- `_PUBLIC_DOC_STEMS` set — assets/public/ stem 매칭. doc.title 이 set 에 있으면
  raw text 보존, 없으면 비식별화. doc_id·query·query_type·relevant_chunks 는 늘 채움.
"""

from __future__ import annotations

import argparse
import csv
import logging
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Literal

# api/ 를 import path 에 추가
_API_PATH = Path(__file__).resolve().parents[1] / "api"
sys.path.insert(0, str(_API_PATH))

from app.adapters.impl.gemini_llm import GeminiLLMProvider  # noqa: E402
from app.adapters.llm import ChatMessage  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import get_supabase_client  # noqa: E402
from app.services.query_classifier import (  # noqa: E402
    QUERY_TYPE_LABELS as _PROD_QUERY_TYPE_LABELS,
    QueryType as _ProdQueryType,
    classify_query_type as _prod_classify_query_type,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

_QUERY_PROMPT = """다음 chunk 의 핵심 정보를 묻는 한국어 자연어 query 1개를 생성해주세요.

[제약]
- 한국어로만 작성 (영어 단어 X)
- 사용자가 검색창에 자연스럽게 입력할 만한 형태 (10~25자, 의문문 또는 명사형 모두 OK)
- chunk 텍스트의 키워드를 그대로 복사하지 말고, 의미를 묻는 형태로 변형
- query 만 출력 (다른 설명·따옴표 X)

[chunk 텍스트]
{chunk_text}

[query]"""


# v2 — query_type 9 라벨 (master plan §8.2).
# 본 모듈의 단일 source 는 production 의 `app.services.query_classifier` 로 이전됨.
# backward-compat 위해 `from auto_goldenset import classify_query_type` 패턴은 유지.
QueryType = _ProdQueryType
_QUERY_TYPE_LABELS: tuple[QueryType, ...] = _PROD_QUERY_TYPE_LABELS

# 한글 명사 추출 시 제외할 stopword (조사·어미·일반 동사·접속사)
_KOREAN_STOPWORDS: frozenset[str] = frozenset({
    "있습니다", "합니다", "입니다", "됩니다", "있다", "하다", "되다", "이다",
    "그리고", "하지만", "따라서", "그러나", "그래서", "또는", "또한", "또",
    "이것", "그것", "저것", "여기", "거기", "저기", "이런", "그런", "저런",
    "있는", "없는", "하는", "되는", "이런", "다음", "관련", "통한", "위한",
    "있어", "있다", "없다", "보다", "같다", "이러", "그러", "저러", "어떤",
    "경우", "방법", "내용", "사항", "정보", "기준", "결과", "과정", "사용",
    "필요", "가능", "위해", "통해", "대한", "대해", "에서", "에는", "에게",
    "으로", "에서", "그리", "또한", "특히", "다만", "단지", "오직", "비록",
    "확인", "운영", "관리", "수행", "진행", "제공", "지원", "준수", "적용",
    "영향", "역할", "구분", "구성", "포함", "제외", "이용", "사용", "활용",
})

# v2.1 — `assets/public/` 자료 stem set (확장자 X). 인제스트 시 documents.title 이
# stem 으로 저장된다는 관찰에 따라 매칭. 새 public 자료 추가 시 `assets/public/README.md`
# 와 함께 본 set 도 갱신해야 한다 (senior-developer 의무).
_PUBLIC_DOC_STEMS: frozenset[str] = frozenset({
    "(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서",
    "보건의료_빅데이터_플랫폼_시범사업_추진계획(안)",
    "sample-report",
    "law sample3",
    "law_sample2",
    "직제_규정(2024.4.30.개정)",
    "한마음생활체육관_운영_내규(2024.4.30.개정)",
    "law_sample1",
})


_TITLE_NORMALIZE_PREFIX_LEN = 25


def _normalize_title(title: str) -> str:
    """title 정규화 — 인제스트 시 공백/밑줄 변환, 60자 truncate, NFC 차이 흡수.

    공백 ↔ 밑줄 통일 (밑줄로) + 한글 NFC + 첫 25자 prefix.

    25자 prefix 인 이유 — `(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서` 같은 긴
    한국어 제목이 60-byte truncate 시 codepoint 가 일정하지 않게 잘리므로, 두 title 의 공통
    prefix 만 비교. public set 8건 중 첫 25자 충돌은 없음 (수동 검증).
    """
    import unicodedata as _ud
    norm = _ud.normalize("NFC", title.strip())
    norm = norm.replace(" ", "_")
    return norm[:_TITLE_NORMALIZE_PREFIX_LEN]


# `_PUBLIC_DOC_STEMS` 의 정규화 캐시 (모듈 로드 시 1회 계산)
_PUBLIC_DOC_STEMS_NORMALIZED: frozenset[str] = frozenset(
    _normalize_title(s) for s in _PUBLIC_DOC_STEMS
)


def is_public_doc_title(title: str) -> bool:
    """doc.title 이 `assets/public/` 8건 중 하나인지 판단.

    인제스트 시 다음 변환이 적용될 수 있어 정규화로 흡수:
    - 확장자 strip
    - 공백 ↔ 밑줄 정규화 (예: `law_sample2.pdf` → `law sample2`)
    - 60자 truncate (예: 긴 한국어 제목)
    - 한글 NFC

    오탐 회피 — 정규화 후 50자 prefix 일치만 인정. 다른 사용자 자료가 같은 prefix 면
    같은 fixture 로 간주 (작은 위험 — public set 자체가 8건만이라 prefix 충돌 무시 가능).
    """
    if not title:
        return False
    return _normalize_title(title) in _PUBLIC_DOC_STEMS_NORMALIZED


# v2 — out_of_scope/negative query 사전 정의 (5건)
_NEGATIVE_QUERIES: tuple[tuple[str, str], ...] = (
    ("이 자료들에 환경 인증 절차 나와있어?", "환경;인증"),
    ("내가 받은 자료 중에 ESG 관련 있나", "ESG"),
    ("AI 윤리 가이드라인 같은 거 있어?", "AI;윤리"),
    ("자료에 GDPR 준수 방안 있어?", "GDPR;준수"),
    ("배송 정책 어디 있어?", "배송;정책"),
)

_MUST_INCLUDE_MAX_NUMERIC = 3
_MUST_INCLUDE_MAX_KOREAN = 3
_MUST_INCLUDE_MAX_TOTAL = 5
_KOREAN_TOKEN_MIN_LEN = 3
_KOREAN_TOKEN_MAX_LEN = 8
_SUMMARY_MAX_LEN = 60


# classify_query_type 의 단일 source 는 `app.services.query_classifier` 로 이전.
# 본 모듈 import 호환 alias 만 유지 (`from auto_goldenset import classify_query_type`).
classify_query_type = _prod_classify_query_type


def extract_must_include(source_chunk_text: str) -> list[str]:
    """source_chunk_text → 답변에 들어가야 할 키워드 (숫자 + 한글 명사).

    룰:
    - 숫자: re 로 단위 포함 토큰 추출, 상위 3개
    - 한글: 길이 3~8 한글 토큰 중 chunk 안 unique + stopword 제외, 상위 3개
    - 최종 ≤ 5개
    """
    if not source_chunk_text:
        return []

    text = source_chunk_text[:500]

    # 숫자 토큰 — 단위 포함 우선. 단위 alternation 은 긴 것 먼저 (regex 좌→우 우선).
    numeric_with_unit = re.findall(
        r"\d+(?:\.\d+)?\s*(?:개월|시간|kg|km|cm|%|원|년|월|일|회|건|개|점|명|분|초|m)",
        text,
    )
    # 중복 제거 (순서 보존)
    seen_num: set[str] = set()
    numeric_tokens: list[str] = []
    for tok in numeric_with_unit:
        norm = tok.replace(" ", "")
        if norm not in seen_num:
            seen_num.add(norm)
            numeric_tokens.append(norm)
        if len(numeric_tokens) >= _MUST_INCLUDE_MAX_NUMERIC:
            break

    # 한글 토큰 (3~8 글자) — Counter 로 빈도 측정 후 unique + stopword 제외
    korean_tokens_all = re.findall(
        rf"[가-힣]{{{_KOREAN_TOKEN_MIN_LEN},{_KOREAN_TOKEN_MAX_LEN}}}",
        text,
    )
    counter = Counter(korean_tokens_all)
    korean_tokens: list[str] = []
    for token, freq in counter.most_common():
        if len(korean_tokens) >= _MUST_INCLUDE_MAX_KOREAN:
            break
        if token in _KOREAN_STOPWORDS:
            continue
        # 빈도 1~2 우선 (전문용어 가능성), 동시에 빈도 ≥ 3 이면 핵심 반복어
        # 둘 다 채택하되 stopword 만 거름
        korean_tokens.append(token)

    combined = numeric_tokens + korean_tokens
    return combined[:_MUST_INCLUDE_MAX_TOTAL]


def extract_source_hint(chunk: dict) -> str:
    """chunk → source_hint 문자열. page 가 있으면 'p.{N}', 없으면 빈 문자열."""
    page = chunk.get("page")
    if page is None:
        return ""
    try:
        page_int = int(page)
    except (ValueError, TypeError):
        return ""
    if page_int <= 0:
        return ""
    return f"p.{page_int}"


def summarize_for_expected_answer(source_chunk_text: str) -> str:
    """source_chunk_text → expected_answer_summary (룰 요약, LLM 호출 0).

    첫 60자 (개행·중복 공백 정리). 더 정밀한 요약은 S1 D2 에서 LLM 결정.
    """
    if not source_chunk_text:
        return ""
    cleaned = re.sub(r"\s+", " ", source_chunk_text.strip())
    return cleaned[:_SUMMARY_MAX_LEN]


def build_negative_rows(start_qid: int = 1) -> list[dict]:
    """사전 정의 negative/out_of_scope query 5건 → v0.7 schema row.

    DB 호출 0, deterministic.
    """
    rows: list[dict] = []
    for i, (query, must_include) in enumerate(_NEGATIVE_QUERIES, start=start_qid):
        rows.append({
            "id": f"G-N-{i:03d}",
            "query": query,
            "query_type": "out_of_scope",
            "doc_id": "",
            "expected_doc_title": "",
            "relevant_chunks": "",
            "acceptable_chunks": "",
            "source_chunk_text": "",
            "expected_answer_summary": "자료에 없음",
            "must_include": must_include,
            "source_hint": "",
            "negative": "true",
        })
    return rows


def print_query_type_distribution(rows: list[dict]) -> None:
    """v0.7 rows → query_type 9 라벨 분포 stderr 출력 + DoD 충족 여부."""
    counter: Counter[str] = Counter(r.get("query_type", "") for r in rows)
    print("\n[query_type 분포]", file=sys.stderr)
    missing: list[str] = []
    for label in _QUERY_TYPE_LABELS:
        n = counter.get(label, 0)
        marker = "OK" if n > 0 else "MISSING"
        print(f"  {label:20s} {n:3d}건  [{marker}]", file=sys.stderr)
        if n == 0:
            missing.append(label)
    if missing:
        print(
            f"  [DoD] {len(missing)}/9 카테고리 미충족: {missing} "
            f"— S1 D2 의 prompt 다양화 또는 추가 negative 로 보완 필요",
            file=sys.stderr,
        )
    else:
        print("  [DoD] 9/9 카테고리 모두 cover", file=sys.stderr)


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def _parse_pgvector(emb) -> list[float] | None:
    """Supabase pgvector 응답 → list[float]. string 또는 list 둘 다."""
    if emb is None:
        return None
    if isinstance(emb, str):
        try:
            return [float(x) for x in emb.strip("[]").split(",")]
        except ValueError:
            return None
    if isinstance(emb, list):
        return [float(x) for x in emb]
    return None


def _sample_chunks(
    client, doc_id: str, n: int, seed: int = 42
) -> list[dict]:
    """doc 의 모든 chunks 중 stratified sampling — chunk_idx 균등 분포."""
    chunks = (
        client.table("chunks")
        .select("id, chunk_idx, text, dense_vec, page")
        .eq("doc_id", doc_id)
        .order("chunk_idx")
        .execute()
        .data
        or []
    )
    if not chunks:
        return []
    # 매우 짧은 chunk (50자 미만) 제외 — query 생성 의미 없음
    chunks = [c for c in chunks if len((c.get("text") or "").strip()) >= 50]
    if not chunks:
        return []
    if len(chunks) <= n:
        return chunks
    # stratified — 균등 간격
    step = len(chunks) / n
    indices = [int(step * i + step / 2) for i in range(n)]
    indices = list(dict.fromkeys(indices))[:n]
    return [chunks[i] for i in indices]


def _gemini_generate_query(
    llm: GeminiLLMProvider,
    chunk_text: str,
    *,
    inter_call_sleep: float = 1.0,
    extra_retry: int = 5,
) -> str:
    """chunk text → Gemini 한국어 query 1개.

    503 high demand 대응 — 외부 retry layer 추가 (gemini_llm 의 retry 3회 외).
    inter_call_sleep 초 sleep 으로 rate limit 완화.
    """
    import time

    prompt = _QUERY_PROMPT.format(chunk_text=chunk_text[:1500])
    last_exc: Exception | None = None
    for attempt in range(extra_retry):
        try:
            response = llm.complete(
                [ChatMessage(role="user", content=prompt)],
                temperature=0.4,
            )
            q = response.strip()
            for prefix in ("query:", "쿼리:", "질문:", "Q:"):
                if q.lower().startswith(prefix.lower()):
                    q = q[len(prefix):].strip()
            q = q.strip("'\"`「」『』")
            time.sleep(inter_call_sleep)  # 다음 호출 rate limit 완화
            return q
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            # 503 high demand 일 때 longer backoff
            err_str = str(exc)
            if "503" in err_str or "UNAVAILABLE" in err_str:
                wait = min(30.0, 5.0 * (2 ** attempt))
                logger.warning(
                    "extra retry %d/%d — 503 high demand, %.0fs 대기",
                    attempt + 1, extra_retry, wait,
                )
                time.sleep(wait)
            else:
                raise
    assert last_exc is not None
    raise last_exc


def _find_acceptable_chunks(
    target_chunk: dict, all_doc_chunks: list[dict], cosine_min: float
) -> list[int]:
    """같은 doc 내 chunks 와 cosine 계산 → 임계 이상 chunks (자기 제외)."""
    target_vec = _parse_pgvector(target_chunk.get("dense_vec"))
    if not target_vec or len(target_vec) != 1024:
        return []
    target_idx = target_chunk["chunk_idx"]
    acceptable: list[tuple[int, float]] = []
    for c in all_doc_chunks:
        if c["chunk_idx"] == target_idx:
            continue
        vec = _parse_pgvector(c.get("dense_vec"))
        if not vec or len(vec) != 1024:
            continue
        sim = _cosine(target_vec, vec)
        if sim >= cosine_min:
            acceptable.append((c["chunk_idx"], sim))
    # cosine 내림차순 — top-k 라기보다 임계 이상 모두
    acceptable.sort(key=lambda x: x[1], reverse=True)
    return [idx for idx, _ in acceptable]


# v0.7 통합 schema (12 컬럼) — v0.6 user CSV 와 호환
_V07_FIELDNAMES: tuple[str, ...] = (
    "id", "query", "query_type", "doc_id", "expected_doc_title",
    "relevant_chunks", "acceptable_chunks", "source_chunk_text",
    "expected_answer_summary", "must_include", "source_hint", "negative",
)


def redact_existing_csv(
    csv_path: Path, *, allow_private: bool = False
) -> tuple[int, int, int]:
    """기존 v0.7 CSV 에 노출 정책 (b) 재적용 — Gemini 재호출 없이 source_chunk_text·
    expected_answer_summary 만 public/private 분기로 다시 채움.

    `is_public_doc_title` 정규화 강화 후 over-redacted 됐던 row 를 raw 로 복원하거나
    반대로 raw 였던 private row 를 비식별화하는 idempotent 운영.

    DB 에서 doc_id 별 chunks 를 fetch (chunk_idx 일치) → raw text 복원.

    return: (public_rows, private_rows, restored_rows)
    """
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    rows = _load_csv_rows_for_redact(csv_path)
    client = get_supabase_client()

    public_rows = 0
    private_rows = 0
    restored_rows = 0

    # doc_id 별 (chunk_idx, text) 매핑 캐시
    doc_chunk_cache: dict[str, dict[int, str]] = {}

    for row in rows:
        doc_id = (row.get("doc_id") or "").strip()
        title = (row.get("expected_doc_title") or "").strip()
        # negative row (doc_id 빈 값) 는 skip — 이미 빈 값
        if not doc_id:
            continue

        is_public = is_public_doc_title(title)
        expose = allow_private or is_public

        if is_public:
            public_rows += 1
        else:
            private_rows += 1

        if not expose:
            # 비식별화 — 빈 값 강제
            if row.get("source_chunk_text") or row.get("expected_answer_summary"):
                row["source_chunk_text"] = ""
                row["expected_answer_summary"] = ""
                restored_rows += 1
            continue

        # public — raw 복원. 기존 row 가 비어있으면 DB 조회로 채움.
        if row.get("source_chunk_text") and row.get("expected_answer_summary"):
            continue  # 이미 채워짐

        if doc_id not in doc_chunk_cache:
            chunks = (
                client.table("chunks")
                .select("chunk_idx, text")
                .eq("doc_id", doc_id)
                .execute()
                .data
                or []
            )
            doc_chunk_cache[doc_id] = {c["chunk_idx"]: (c.get("text") or "") for c in chunks}

        chunk_idx_str = (row.get("relevant_chunks") or "").split(",")[0].strip()
        if not chunk_idx_str.isdigit():
            continue
        chunk_text = doc_chunk_cache[doc_id].get(int(chunk_idx_str), "")
        if not chunk_text:
            continue
        row["source_chunk_text"] = chunk_text[:200].replace("\n", " ")
        row["expected_answer_summary"] = summarize_for_expected_answer(chunk_text)
        restored_rows += 1

    # 다시 쓰기
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(_V07_FIELDNAMES))
        writer.writeheader()
        writer.writerows(rows)

    return public_rows, private_rows, restored_rows


def _load_csv_rows_for_redact(path: Path) -> list[dict]:
    """utf-8-sig 로드 → list[dict] (12 컬럼 보장)."""
    out: list[dict] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            normalized = {field: (row.get(field, "") or "") for field in _V07_FIELDNAMES}
            out.append(normalized)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="self-supervised 골든셋 자동 생성 (v2 — v0.7 통합 schema)")
    parser.add_argument(
        "--chunks-per-doc", type=int, default=5,
        help="각 doc 에서 sampling 할 chunks 수 (default 5)"
    )
    parser.add_argument(
        "--acceptable-cosine", type=float, default=0.7,
        help="acceptable chunks BGE-M3 cosine 임계 (default 0.7)"
    )
    parser.add_argument(
        "--output", type=str,
        default=str(Path(__file__).parent / "golden_v0.7_auto.csv"),
        help="출력 CSV 경로 (default golden_v0.7_auto.csv)",
    )
    parser.add_argument(
        "--limit-docs", type=int, default=None,
        help="처리할 docs 수 제한 (default 전체)"
    )
    parser.add_argument(
        "--model", type=str, default="gemini-2.5-flash",
        help="Gemini 모델 (default gemini-2.5-flash, 503 시 flash-lite 시도)"
    )
    parser.add_argument(
        "--inter-call-sleep", type=float, default=1.0,
        help="호출 간 sleep 초 (default 1.0)"
    )
    parser.add_argument(
        "--skip-negative", action="store_true",
        help="negative/out_of_scope query 5건 append 생략 (default false)"
    )
    parser.add_argument(
        "--allow-private-source-text", action="store_true",
        help=(
            "비공개 doc 의 source_chunk_text·expected_answer_summary 를 raw 그대로 저장. "
            "default 는 비식별화 (assets/public/ 8건만 raw, 나머지는 빈 값). "
            "사용자 PC 에서만 활성화 권장 — git 추적 시 사용자 자료 노출 위험."
        ),
    )
    parser.add_argument(
        "--redact-existing", action="store_true",
        help=(
            "Gemini 재호출 없이 기존 --output CSV 에 비식별화 정책만 재적용. "
            "is_public_doc_title 정규화 개선 후 over-redacted row 의 raw text 복원에 활용."
        ),
    )
    args = parser.parse_args()

    if args.redact_existing:
        public_n, private_n, restored = redact_existing_csv(
            Path(args.output),
            allow_private=args.allow_private_source_text,
        )
        print(
            f"[OK] 후처리 완료: public_rows={public_n} private_rows={private_n} "
            f"restored/zeroed={restored} → {args.output}",
            file=sys.stderr,
        )
        # 분포 재출력
        rows_after = _load_csv_rows_for_redact(Path(args.output))
        print_query_type_distribution(rows_after)
        return 0

    client = get_supabase_client()
    settings = get_settings()

    docs = (
        client.table("documents")
        .select("id, title, doc_type")
        .eq("user_id", settings.default_user_id)
        .is_("deleted_at", "null")
        .order("created_at")
        .execute()
        .data
        or []
    )
    if args.limit_docs:
        docs = docs[: args.limit_docs]
    if not docs:
        print("[ERROR] 적재된 docs 없음", file=sys.stderr)
        return 1
    print(f"[OK] 대상 docs: {len(docs)}건", file=sys.stderr)

    llm = GeminiLLMProvider(model=args.model)
    rows: list[dict] = []
    qid = 0
    random.seed(42)

    public_count = 0
    private_count = 0

    for d in docs:
        doc_id = d["id"]
        title_full = d["title"]
        title = title_full[:60]
        # v2.1 — public/private 판정. allow_private_source_text 가 True 면 무시.
        is_public = is_public_doc_title(title_full)
        expose_raw_text = args.allow_private_source_text or is_public
        if is_public:
            public_count += 1
        else:
            private_count += 1
        # 각 doc 의 모든 chunks 1회 fetch (acceptable 비교용 + sampling)
        all_chunks = (
            client.table("chunks")
            .select("id, chunk_idx, text, dense_vec, page")
            .eq("doc_id", doc_id)
            .order("chunk_idx")
            .execute()
            .data
            or []
        )
        sampled = _sample_chunks(client, doc_id, args.chunks_per_doc)
        if not sampled:
            print(f"  [{doc_id[:8]}] sampling 실패 (chunks 없음 또는 너무 짧음)", file=sys.stderr)
            continue

        for chunk in sampled:
            qid += 1
            try:
                query = _gemini_generate_query(
                    llm, chunk["text"],
                    inter_call_sleep=args.inter_call_sleep,
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"  [G-A-{qid:03d}] {doc_id[:8]} chunk={chunk['chunk_idx']} "
                    f"Gemini 실패: {exc}",
                    file=sys.stderr,
                )
                continue
            acceptable = _find_acceptable_chunks(
                chunk, all_chunks, args.acceptable_cosine
            )

            # v2 — 룰 기반 메타 추출. query_type / must_include 는 query 자체에서 도출되거나
            # source_chunk_text 의 룰 분석 결과라 raw text 노출 위험 낮음 (한정된 키워드).
            # 반면 source_chunk_text·expected_answer_summary 는 raw 텍스트 그대로 → 비식별화 대상.
            qtype = classify_query_type(
                query,
                source_chunk_text=chunk["text"],
                expected_doc_titles=[title],
                is_negative=False,
            )
            must_include = extract_must_include(chunk["text"])
            source_hint = extract_source_hint(chunk)

            if expose_raw_text:
                source_text_short = chunk["text"][:200].replace("\n", " ")
                expected_summary = summarize_for_expected_answer(chunk["text"])
            else:
                # 비식별화 — doc_id + chunk_idx 만 노출. 평가 시 DB 조회로 raw text 복원 가능.
                source_text_short = ""
                expected_summary = ""

            rows.append(
                {
                    "id": f"G-A-{qid:03d}",
                    "query": query,
                    "query_type": qtype,
                    "doc_id": doc_id,
                    "expected_doc_title": title,
                    "relevant_chunks": str(chunk["chunk_idx"]),
                    "acceptable_chunks": ",".join(map(str, acceptable)),
                    "source_chunk_text": source_text_short,
                    "expected_answer_summary": expected_summary,
                    "must_include": ";".join(must_include),
                    "source_hint": source_hint,
                    "negative": "false",
                }
            )
            print(
                f"  [G-A-{qid:03d}] {doc_id[:8]} chunk={chunk['chunk_idx']} "
                f"type={qtype} acceptable={len(acceptable)} q={query[:40]!r}",
                file=sys.stderr,
            )

    # v2 — negative/out_of_scope 5건 append
    if not args.skip_negative:
        neg_rows = build_negative_rows()
        rows.extend(neg_rows)
        print(f"\n[OK] negative/out_of_scope {len(neg_rows)}건 append", file=sys.stderr)

    # v0.7 통합 schema (12 컬럼) CSV 출력 — utf-8-sig 로 Excel 한글 호환
    output = Path(args.output)
    with output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(_V07_FIELDNAMES))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n[OK] {len(rows)} 건 → {output}", file=sys.stderr)

    # v2.1 — 노출 정책 요약
    expose_mode = "raw (private 포함)" if args.allow_private_source_text else "비식별화 (public 만 raw)"
    print(
        f"[OK] source_chunk_text 노출 정책: {expose_mode}  "
        f"public docs={public_count} / private docs={private_count}",
        file=sys.stderr,
    )

    # query_type 9 라벨 분포 + DoD 출력
    print_query_type_distribution(rows)

    return 0


if __name__ == "__main__":
    sys.exit(main())
