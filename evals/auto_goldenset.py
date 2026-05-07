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


# v2 — query_type 9 라벨 (master plan §8.2)
QueryType = Literal[
    "exact_fact",
    "fuzzy_memory",
    "synonym_mismatch",
    "numeric_lookup",
    "table_lookup",
    "vision_diagram",
    "summary",
    "cross_doc",
    "out_of_scope",
]

_QUERY_TYPE_LABELS: tuple[QueryType, ...] = (
    "exact_fact", "fuzzy_memory", "synonym_mismatch", "numeric_lookup",
    "table_lookup", "vision_diagram", "summary", "cross_doc", "out_of_scope",
)

# 룰 키워드 (substring 매칭 — 한국어 토큰화 의존 0)
_VISION_KEYWORDS = ("다이어그램", "그림", "도식", "구조도", "이미지", "사진", "도표")
_TABLE_KEYWORDS = ("표", "리스트", "목록", "별표", "카테고리", "항목 목록")
_SUMMARY_KEYWORDS = ("요약", "핵심", "정리", "개요", "짧게", "한줄", "한 줄")
_CROSS_DOC_KEYWORDS = ("비교", "차이", "대비", "달라", "차이점")
_FUZZY_KEYWORDS = ("그때", "어디 있더라", "어디 있었", "뭐였지", "있었나", "있었지", "었더라", "았더라", "기억나")
_NUMERIC_PATTERNS = (
    # 단위 alternation 은 **긴 것 먼저** — "개월" 이 "개" 보다 먼저 매칭되도록.
    # regex alternation 은 좌→우 우선이라 짧은 것 먼저면 긴 단위가 잘림.
    re.compile(r"\d+(?:\.\d+)?\s*(?:개월|시간|kg|km|cm|%|원|년|월|일|회|건|개|점|명|분|초|m)"),
    re.compile(r"몇\s*[가-힣]"),
    re.compile(r"얼마"),
)
_NUMERIC_KEYWORDS = ("얼마", "금액", "가격", "비용", "수치", "수량", "개수", "지원금")

# 동의어 쌍 — query 가 한쪽 표현, source 가 반대편 표현이면 synonym_mismatch
# (a, b): query 안에 a 가 있고 source 안에 b 가 있거나 반대일 때
_SYNONYM_PAIRS: tuple[tuple[str, str], ...] = (
    ("개인정보", "비식별화"),
    ("환자 정보", "비식별화"),
    ("색상", "컬러"),
    ("시트", "가죽"),
    ("규정", "내규"),
    ("직원", "임직원"),
    ("회의", "협의"),
)

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


def classify_query_type(
    query: str,
    *,
    source_chunk_text: str = "",
    expected_doc_titles: list[str] | None = None,
    is_negative: bool = False,
) -> QueryType:
    """query → 9 라벨 중 1개 (룰 기반).

    우선순위:
    1. is_negative=True → out_of_scope
    2. vision_diagram (그림/다이어그램 키워드)
    3. table_lookup (표/목록 키워드)
    4. cross_doc (비교 키워드 또는 doc_title 2개 이상)
    5. numeric_lookup (숫자 패턴 또는 금액 키워드)
    6. summary (요약 키워드)
    7. synonym_mismatch (동의어 쌍 cross 매칭)
    8. fuzzy_memory (흐릿한 톤 키워드)
    9. exact_fact (default)
    """
    if is_negative:
        return "out_of_scope"

    q = query.strip()

    if any(kw in q for kw in _VISION_KEYWORDS):
        return "vision_diagram"

    if any(kw in q for kw in _TABLE_KEYWORDS):
        return "table_lookup"

    if expected_doc_titles and len(expected_doc_titles) >= 2:
        return "cross_doc"
    if any(kw in q for kw in _CROSS_DOC_KEYWORDS):
        return "cross_doc"

    if any(p.search(q) for p in _NUMERIC_PATTERNS):
        return "numeric_lookup"
    if any(kw in q for kw in _NUMERIC_KEYWORDS):
        return "numeric_lookup"

    if any(kw in q for kw in _SUMMARY_KEYWORDS):
        return "summary"

    if source_chunk_text:
        for term_a, term_b in _SYNONYM_PAIRS:
            in_query_a = term_a in q
            in_query_b = term_b in q
            in_source_a = term_a in source_chunk_text
            in_source_b = term_b in source_chunk_text
            # query 가 한쪽, source 가 반대편이면 synonym_mismatch
            if (in_query_a and in_source_b and not in_source_a) or (
                in_query_b and in_source_a and not in_source_b
            ):
                return "synonym_mismatch"

    if any(kw in q for kw in _FUZZY_KEYWORDS):
        return "fuzzy_memory"

    return "exact_fact"


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
    args = parser.parse_args()

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

    for d in docs:
        doc_id = d["id"]
        title = d["title"][:60]
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

            # v2 — 룰 기반 메타 추출
            source_text_short = chunk["text"][:200].replace("\n", " ")
            qtype = classify_query_type(
                query,
                source_chunk_text=chunk["text"],
                expected_doc_titles=[title],
                is_negative=False,
            )
            must_include = extract_must_include(chunk["text"])
            source_hint = extract_source_hint(chunk)
            expected_summary = summarize_for_expected_answer(chunk["text"])

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

    # query_type 9 라벨 분포 + DoD 출력
    print_query_type_distribution(rows)

    return 0


if __name__ == "__main__":
    sys.exit(main())
