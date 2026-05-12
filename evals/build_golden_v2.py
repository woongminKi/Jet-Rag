"""S4-A D3 Phase 1 — `golden_v1.csv` (157 row, 12 컬럼) → `golden_v2.csv` (14 컬럼) 자동 보강.

목적
----
S4-A D3 의 답변 품질 측정 가드. v1 row 를 보존하면서 다음 두 가지를 자동 보강한다.

1. **doc 매칭 자동 fix** — `expected_doc_title` 만 있고 `doc_id` 비어 있는 row 를
   NFC 정규화 + ILIKE prefix 매칭으로 채운다. fail 11건 (G-A-104~113, G-N-004) 우선 처리.
2. **relevant/acceptable chunks 자동 추출** — 빈 칸 row 한정 BGE-M3 query embedding +
   같은 doc 내 적재된 dense_vec cosine 으로 top-k 후보 채택 (must_include hit 우선).

추가로 v1 12 컬럼에 v2 신규 2 컬럼:
- `doc_type` (pdf/hwp/hwpx/docx/pptx/image/url/txt/md) — docs.doc_type 자동 채움
- `caption_dependent` (bool) — vision_diagram/table_lookup query 또는 caption 패턴 chunk

flow:
1. v1 CSV 로드 → 14 컬럼 schema 로 padding
2. docs 테이블 1회 fetch — title/doc_type 캐시 (NFC normalize)
3. doc_id 매칭 자동 fix (negative row 제외)
4. cross_doc — expected_doc_title 의 `|` 분리 다중 doc 처리
5. relevant_chunks 빈 row → BGE-M3 query embed + 같은 doc cosine top-3 +
   must_include hit 우선 채택 (없으면 top-1)
6. acceptable_chunks 빈 row → 같은 doc 내 cosine ≥ threshold chunk_idx 합산
7. caption_dependent 마킹 — query_type 또는 chunk 패턴
8. v2 CSV 출력 (utf-8-sig, 14 컬럼)

산출
----
- `evals/golden_v2.csv` (14 컬럼)
- stdout 요약 — query_type 분포 / doc_type 분포 / caption_dependent 비율 /
  doc 매칭 fail 잔존 / acceptable sample 5건

사용
----
    cd evals && uv run python build_golden_v2.py
    # 또는
    uv run python build_golden_v2.py \\
        --input golden_v1.csv \\
        --output golden_v2.csv \\
        --cosine-threshold 0.7

⚠ 주의 — acceptable_chunks 는 LLM-judge 로 별도 보완됨 (2026-05-11~)
--------------------------------------------------------------
`evals/run_acceptable_chunks_judge.py` 가 `golden_v2.csv` 의 empty
`acceptable_chunks` row 를 Gemini judge 로 채운다 (BGE-M3 cosine 휴리스틱보다
정확). **본 스크립트를 `golden_v2.csv` 에 재실행하면 LLM 보완분이 소실된다** —
재실행 필요 시 보완분 백업 (`.bak.YYYYMMDD`) 또는 judge 재실행 필요.
(향후 `--preserve-acceptable` 옵션 추가 검토 중 — 미구현.)

CLAUDE.md 정합
--------------
- 의존성 추가 0 — auto_goldenset 의 `_parse_pgvector` / `_cosine` 재사용
- BGE-M3 무료 호출만 사용 (Gemini 호출 0)
- 골든셋 v1 보존 (input 파일 미수정)
- 본 모듈은 `app.adapters.impl.bgem3_hf_embedding` 의 `BGEM3HFEmbeddingProvider`
  를 직접 import 하여 query embedding LRU cache 활용. 같은 query 재호출 시 0 비용.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# api/ 를 import path 에 추가
_API_PATH = Path(__file__).resolve().parents[1] / "api"
if (_API_PATH / "app").exists():
    sys.path.insert(0, str(_API_PATH))

# auto_goldenset 의 schema·헬퍼 재사용 — 단일 출처
sys.path.insert(0, str(Path(__file__).parent))
from auto_goldenset import (  # noqa: E402
    _V07_FIELDNAMES,
    _cosine,
    _parse_pgvector,
)

logger = logging.getLogger(__name__)

# v2 schema — v1 12 컬럼 + 2 컬럼 추가
_V2_FIELDNAMES: tuple[str, ...] = _V07_FIELDNAMES + ("doc_type", "caption_dependent")

_DEFAULT_INPUT = Path(__file__).parent / "golden_v1.csv"
_DEFAULT_OUTPUT = Path(__file__).parent / "golden_v2.csv"

# 매칭 알고리즘 상수
_TITLE_PREFIX_LEN = 25  # 25자 prefix 매칭 (auto_goldenset 과 동일)
_TOP_K_RELEVANT = 3
_DEFAULT_COSINE_THRESHOLD = 0.7
_DEFAULT_USER_DOC_ID = ""  # cross_doc separator
_CROSS_DOC_SEPARATOR = "|"

# caption 패턴 — query_type 외에 chunk text 자체에 caption 마커가 있으면 의존성 인정
_CAPTION_PATTERNS: tuple[str, ...] = (
    "[표", "<표", "[그림", "<그림", "[도식", "<도식",
    "Figure", "Table", "도표", "다이어그램",
)
# query_type 으로도 caption_dependent 인정
_CAPTION_QUERY_TYPES: frozenset[str] = frozenset({"vision_diagram", "table_lookup"})

# acceptable sample 출력 개수 (stdout)
_ACCEPTABLE_SAMPLE_N = 5


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocRecord:
    """docs 테이블 row — title 정규화·doc_type 캐시."""

    doc_id: str
    title: str
    title_norm: str  # NFC normalize + 공백/밑줄 통일 + 25자 prefix
    doc_type: str


@dataclass(frozen=True)
class ChunkRecord:
    """chunks 테이블 row — dense_vec 파싱 + caption 후보 검출 캐시."""

    chunk_idx: int
    text: str
    page: int | None
    dense_vec: list[float] | None
    has_caption_marker: bool


# ---------------------------------------------------------------------------
# 정규화 헬퍼
# ---------------------------------------------------------------------------


def normalize_title(title: str) -> str:
    """title NFC + 공백↔밑줄 통일 + 25자 prefix.

    auto_goldenset 의 `_normalize_title` 과 동일 알고리즘 — 인제스트 시
    공백/밑줄 변환·60자 truncate 차이 흡수.
    """
    if not title:
        return ""
    norm = unicodedata.normalize("NFC", title.strip())
    norm = norm.replace(" ", "_")
    return norm[:_TITLE_PREFIX_LEN]


def split_cross_doc_titles(raw: str) -> list[str]:
    """`expected_doc_title` 의 `|` 분리 → 다중 doc title list. 단일 title 도 list 1.

    cross_doc query 는 사용자가 `title_a|title_b` 로 표기하는 관행 (G-U-015 등).
    """
    parts = [p.strip() for p in raw.split(_CROSS_DOC_SEPARATOR) if p.strip()]
    return parts


def detect_caption_marker(text: str) -> bool:
    """chunk text 에 caption 패턴 (표/그림/도식/Figure/Table) 포함 여부."""
    if not text:
        return False
    return any(p in text for p in _CAPTION_PATTERNS)


# ---------------------------------------------------------------------------
# DB fetch
# ---------------------------------------------------------------------------


def fetch_docs_index(client) -> dict[str, DocRecord]:
    """docs 테이블 → title_norm → DocRecord 인덱스.

    user_id 필터 + deleted_at IS NULL. user_id 는 settings.default_user_id.
    """
    from app.config import get_settings

    settings = get_settings()
    rows = (
        client.table("documents")
        .select("id, title, doc_type")
        .eq("user_id", settings.default_user_id)
        .is_("deleted_at", "null")
        .execute()
        .data
        or []
    )
    index: dict[str, DocRecord] = {}
    for row in rows:
        title = row.get("title") or ""
        record = DocRecord(
            doc_id=row["id"],
            title=title,
            title_norm=normalize_title(title),
            doc_type=row.get("doc_type") or "",
        )
        index[record.title_norm] = record
    logger.info("docs 인덱스 %d건 로드", len(index))
    return index


def fetch_chunks(client, doc_id: str) -> list[ChunkRecord]:
    """doc_id 의 chunks 전체 — chunk_idx ASC."""
    rows = (
        client.table("chunks")
        .select("chunk_idx, text, page, dense_vec, metadata")
        .eq("doc_id", doc_id)
        .order("chunk_idx")
        .execute()
        .data
        or []
    )
    out: list[ChunkRecord] = []
    for r in rows:
        text = r.get("text") or ""
        out.append(
            ChunkRecord(
                chunk_idx=int(r["chunk_idx"]),
                text=text,
                page=r.get("page"),
                dense_vec=_parse_pgvector(r.get("dense_vec")),
                has_caption_marker=detect_caption_marker(text),
            )
        )
    return out


# ---------------------------------------------------------------------------
# doc_id 매칭
# ---------------------------------------------------------------------------


def _strip_decoration(text: str) -> str:
    """title 데코레이션 제거 — `(붙임1)`, `(1)`, 앞뒤 공백/특수문자.

    docs DB title 이 `(붙임2) 2025년 ... (1)` 처럼 prefix/suffix 데코를 가질 수 있어
    v1 의 사용자 표기 (`2025년 ...`) 와 매칭되도록 정규화 단계에서 한 번 더 흡수.

    macOS HFS+ 인제스트 path 에서 한글이 NFD (조합형) 로 저장될 수 있어
    regex 매칭 전 NFC 정규화 선행. NFD 자모 분리 상태에서는 `[가-힣]*` 가 실패한다.
    """
    import re as _re
    text_nfc = unicodedata.normalize("NFC", text)
    cleaned = _re.sub(r"\(\s*[가-힣]*\d*\s*\)", "", text_nfc)  # `(붙임2)`, `(1)`
    cleaned = _re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def match_doc_id(
    raw_title: str, docs_index: dict[str, DocRecord]
) -> DocRecord | None:
    """expected_doc_title → DocRecord. NFC normalize + prefix/substring 매칭.

    매칭 순서 (early return):
    1. exact title_norm 일치 (가장 정확)
    2. raw → 데코 제거 후 다시 normalize → 일치
    3. ILIKE prefix substring — v1 norm 이 doc norm 의 substring 이거나 반대.
       자료 13건 규모에서 prefix 충돌 위험 무시.

    매칭 실패 시 None — 호출 측에서 빈 doc_id 유지.
    """
    norm = normalize_title(raw_title)
    if not norm:
        return None

    # 1) exact 일치
    rec = docs_index.get(norm)
    if rec is not None:
        return rec

    # 2) 데코 제거 후 재시도
    stripped = _strip_decoration(raw_title)
    if stripped and stripped != raw_title:
        norm_stripped = normalize_title(stripped)
        rec = docs_index.get(norm_stripped)
        if rec is not None:
            return rec

    # 3) substring 양방향 — v1 norm 이 docs norm 안에 있거나 반대.
    #    일정 길이 (prefix 8자 이상) 보장으로 우연 매칭 방어.
    norm_a = norm if len(norm) >= 8 else None
    if norm_a is None:
        return None
    for doc_norm, rec in docs_index.items():
        if not doc_norm:
            continue
        # v1 norm 이 docs norm 의 substring  (예: `2025년_데이터센터_...` ⊂ `(붙임2)_2025년_...`)
        if norm_a in doc_norm or doc_norm in norm_a:
            return rec
        # 데코 제거 docs norm 과 v1 norm 의 substring 매칭
        doc_stripped = normalize_title(_strip_decoration(rec.title))
        if doc_stripped and (norm_a in doc_stripped or doc_stripped in norm_a):
            return rec
    return None


def match_cross_doc_ids(
    raw_title: str, docs_index: dict[str, DocRecord]
) -> list[DocRecord]:
    """cross_doc — `|` 분리된 각 sub-title 별 DocRecord. 매칭 실패 sub 는 제외."""
    out: list[DocRecord] = []
    for sub in split_cross_doc_titles(raw_title):
        rec = match_doc_id(sub, docs_index)
        if rec is not None:
            out.append(rec)
    return out


# ---------------------------------------------------------------------------
# relevant / acceptable 추출
# ---------------------------------------------------------------------------


def parse_must_include(raw: str) -> list[str]:
    """`must_include` 컬럼의 `;` 분리 토큰 list."""
    if not raw:
        return []
    return [t.strip() for t in raw.split(";") if t.strip()]


def chunk_hits_must_include(text: str, must_include: list[str]) -> int:
    """chunk text 가 must_include 토큰 중 몇 개 포함하는지 (단순 substring)."""
    if not must_include or not text:
        return 0
    return sum(1 for tok in must_include if tok and tok in text)


def select_relevant_chunks(
    query_vec: list[float],
    chunks: list[ChunkRecord],
    must_include: list[str],
    *,
    top_k: int = _TOP_K_RELEVANT,
) -> tuple[list[int], dict[int, float]]:
    """BGE-M3 cosine top-k 후보 → must_include hit 우선 채택.

    return: (relevant_chunk_idxs, idx→cosine 매핑)

    룰:
    - cosine 내림차순 top-k 후보
    - 후보 중 must_include 토큰 ≥ 1 hit chunk 모두 채택
    - 0 hit 이면 top-1 만 채택 (하나는 무조건 보장)
    """
    if not query_vec or not chunks:
        return [], {}

    scored: list[tuple[int, float]] = []
    for c in chunks:
        if not c.dense_vec or len(c.dense_vec) != len(query_vec):
            continue
        sim = _cosine(query_vec, c.dense_vec)
        scored.append((c.chunk_idx, sim))
    if not scored:
        return [], {}
    scored.sort(key=lambda x: x[1], reverse=True)
    candidates = scored[:top_k]
    cosine_map = {idx: sim for idx, sim in candidates}

    text_by_idx = {c.chunk_idx: c.text for c in chunks}
    hits: list[int] = []
    for idx, _sim in candidates:
        if chunk_hits_must_include(text_by_idx.get(idx, ""), must_include) >= 1:
            hits.append(idx)

    if hits:
        return hits, cosine_map
    # fallback — top-1
    return [candidates[0][0]], cosine_map


def select_acceptable_chunks(
    query_vec: list[float],
    chunks: list[ChunkRecord],
    *,
    threshold: float,
    exclude: Iterable[int] = (),
) -> list[int]:
    """같은 doc 내 cosine ≥ threshold chunk (relevant 제외).

    cosine 내림차순.
    """
    if not query_vec or not chunks:
        return []
    excl = set(exclude)
    scored: list[tuple[int, float]] = []
    for c in chunks:
        if c.chunk_idx in excl:
            continue
        if not c.dense_vec or len(c.dense_vec) != len(query_vec):
            continue
        sim = _cosine(query_vec, c.dense_vec)
        if sim >= threshold:
            scored.append((c.chunk_idx, sim))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [idx for idx, _ in scored]


# ---------------------------------------------------------------------------
# caption_dependent 판단
# ---------------------------------------------------------------------------


def is_caption_dependent(
    query_type: str, relevant_chunks: list[ChunkRecord]
) -> bool:
    """query_type 또는 정답 chunk 의 caption 마커로 caption 의존성 판단.

    룰 (OR):
    1. query_type ∈ {vision_diagram, table_lookup}
    2. 정답 chunk 의 text 에 caption 패턴 포함

    관찰 — 본 PC chunks.metadata 에 table_caption / figure_caption 키가 없어
    chunk text 패턴으로 대체 (S4-A 측정 의도 부합).
    """
    if query_type in _CAPTION_QUERY_TYPES:
        return True
    return any(c.has_caption_marker for c in relevant_chunks)


# ---------------------------------------------------------------------------
# v1 → v2 변환 메인
# ---------------------------------------------------------------------------


@dataclass
class BuildStats:
    """build 결과 요약 — stdout 출력용."""

    total_rows: int = 0
    doc_match_fixed: int = 0
    doc_match_failed: list[str] = None  # row id list
    relevant_filled: int = 0
    acceptable_filled: int = 0
    cross_doc_processed: int = 0
    caption_dependent_count: int = 0
    # Phase 2 — stale doc_id 추적 (CSV 에 이미 doc_id 가 있는데 docs_index 에 미존재).
    stale_doc_id_count: int = 0
    stale_doc_id_fixed: int = 0  # title fallback 으로 auto-fix 된 건수
    stale_doc_id_kept: list[str] = None  # 복구 실패해 보존된 row id 리스트
    query_type_dist: Counter = None
    doc_type_dist: Counter = None
    acceptable_samples: list[str] = None  # row id, query, acceptable_chunks

    def __post_init__(self) -> None:
        if self.doc_match_failed is None:
            self.doc_match_failed = []
        if self.stale_doc_id_kept is None:
            self.stale_doc_id_kept = []
        if self.query_type_dist is None:
            self.query_type_dist = Counter()
        if self.doc_type_dist is None:
            self.doc_type_dist = Counter()
        if self.acceptable_samples is None:
            self.acceptable_samples = []


def _pad_v2_row(v1_row: dict) -> dict:
    """v1 12 컬럼 → v2 14 컬럼 padding. 누락 키는 빈 값."""
    return {
        field: (v1_row.get(field, "") or "").strip()
        for field in _V2_FIELDNAMES
    }


def build_v2_row(
    v1_row: dict,
    *,
    docs_index: dict[str, DocRecord],
    fetch_chunks_fn,
    embed_query_fn,
    threshold: float,
    stats: BuildStats,
    chunks_cache: dict[str, list[ChunkRecord]] | None = None,
) -> dict:
    """v1 row 1건 → v2 row 1건 자동 보강.

    fetch_chunks_fn: doc_id → list[ChunkRecord] (테스트용 의존성 주입)
    embed_query_fn: query → list[float] (테스트용 의존성 주입)
    chunks_cache: doc_id → chunks 캐시. 없으면 fetch_chunks_fn 마다 호출.
    """
    if chunks_cache is None:
        chunks_cache = {}

    row = _pad_v2_row(v1_row)
    qid = row.get("id", "")
    is_negative = (row.get("negative") or "").lower() == "true"
    query = row.get("query") or ""
    title_raw = row.get("expected_doc_title") or ""
    must_include = parse_must_include(row.get("must_include") or "")
    qtype_raw = (row.get("query_type") or "").strip()

    # negative row — doc_id/relevant/acceptable 모두 빈 값 유지, doc_type/caption 만 처리
    if is_negative:
        row["caption_dependent"] = (
            "true" if qtype_raw in _CAPTION_QUERY_TYPES else "false"
        )
        row["doc_type"] = ""
        return row

    # D 결정 — cross_doc row 는 인간 라벨 보존. 자동 cosine 휴리스틱(query embed +
    # sub-doc top-1)을 폐기한다. cross_doc 정답 라벨은 `alias:chunk_idx` 형식이고
    # 정답 chunk 가 doc 간 비대칭 분포 → cosine top-1 추정이 실제와 어긋났음
    # (G-U-031/032 등). embed 호출도 하지 않는다. stats 집계만 갱신.
    if qtype_raw == "cross_doc":
        stats.cross_doc_processed += 1
        return row

    # `|` 분리 sub-title — cross_doc 아닌 multi-title U-row (예: G-U-018 fuzzy_memory)
    sub_titles = split_cross_doc_titles(title_raw)
    is_cross_doc = len(sub_titles) >= 2

    matched_docs: list[DocRecord] = []
    if is_cross_doc:
        matched_docs = match_cross_doc_ids(title_raw, docs_index)
    else:
        # doc_id 가 이미 있으면 docs_index 로 doc_type 만 보충
        existing_doc_id = row.get("doc_id") or ""
        if existing_doc_id:
            for rec in docs_index.values():
                if rec.doc_id == existing_doc_id:
                    matched_docs = [rec]
                    break
            if not matched_docs:
                # Phase 2 — stale doc_id (docs_index 미존재) 차단.
                # 이전엔 pass 로 통과시켜 stale id 가 그대로 propagate 됐음.
                # 이제: 경고 + 카운트 + title fallback 으로 auto-fix 시도.
                logger.warning(
                    "[%s] stale doc_id 검출: %s (title=%r) — title fallback 시도",
                    qid, existing_doc_id, title_raw,
                )
                stats.stale_doc_id_count += 1
                recovered = match_doc_id(title_raw, docs_index) if title_raw else None
                if recovered is not None:
                    logger.info(
                        "[%s]  → title 매칭 auto-fix: %s → %s",
                        qid, existing_doc_id, recovered.doc_id,
                    )
                    matched_docs = [recovered]
                    stats.stale_doc_id_fixed += 1
                    # row 의 stale doc_id 도 정정 — 후속 chunk fetch 가 새 id 로
                    # 향해야 acceptable/relevant 추출이 정상 진행.
                    row["doc_id"] = recovered.doc_id
                else:
                    logger.warning(
                        "[%s]  → title fallback 실패 — stale id 보존 (수동 정정 필요)",
                        qid,
                    )
                    stats.stale_doc_id_kept.append(qid)
        if not matched_docs:
            rec = match_doc_id(title_raw, docs_index)
            if rec is not None:
                matched_docs = [rec]
                if not row.get("doc_id"):
                    stats.doc_match_fixed += 1

    if not matched_docs and title_raw:
        stats.doc_match_failed.append(qid)

    # doc_type 채움 — cross_doc 은 첫 sub doc_type, 동일 type 일관 (혼합 시 첫 sub)
    if matched_docs:
        row["doc_type"] = matched_docs[0].doc_type
        # doc_id 채움 — 단일 doc 일 때만 (cross_doc 은 비워둠 — sub doc_id 다수)
        if not is_cross_doc and not row.get("doc_id"):
            row["doc_id"] = matched_docs[0].doc_id

    if is_cross_doc:
        stats.cross_doc_processed += 1

    # relevant_chunks / acceptable_chunks 채움 — 빈 row 한정
    needs_relevant = not row.get("relevant_chunks")
    needs_acceptable = not row.get("acceptable_chunks")

    relevant_chunk_records: list[ChunkRecord] = []

    if matched_docs and (needs_relevant or needs_acceptable):
        try:
            query_vec = embed_query_fn(query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] query embed 실패: %s", qid, exc)
            query_vec = []

        if query_vec:
            # cross_doc — 각 sub_doc 별 top-1 합산
            if is_cross_doc:
                sub_relevants: list[int] = []
                sub_acceptables: list[int] = []
                sub_relevants_per_doc: dict[str, list[int]] = {}
                for rec in matched_docs:
                    chunks = chunks_cache.get(rec.doc_id)
                    if chunks is None:
                        chunks = fetch_chunks_fn(rec.doc_id)
                        chunks_cache[rec.doc_id] = chunks
                    if not chunks:
                        continue
                    rel_idxs, _cmap = select_relevant_chunks(
                        query_vec, chunks, must_include, top_k=1
                    )
                    sub_relevants.extend(rel_idxs)
                    sub_relevants_per_doc[rec.doc_id] = rel_idxs
                    relevant_chunk_records.extend(
                        c for c in chunks if c.chunk_idx in rel_idxs
                    )
                    if needs_acceptable:
                        acc = select_acceptable_chunks(
                            query_vec, chunks, threshold=threshold,
                            exclude=rel_idxs,
                        )
                        sub_acceptables.extend(acc)
                if needs_relevant and sub_relevants:
                    row["relevant_chunks"] = ",".join(str(i) for i in sub_relevants)
                    stats.relevant_filled += 1
                if needs_acceptable and sub_acceptables:
                    row["acceptable_chunks"] = ",".join(
                        str(i) for i in sub_acceptables
                    )
                    stats.acceptable_filled += 1
            else:
                rec = matched_docs[0]
                chunks = chunks_cache.get(rec.doc_id)
                if chunks is None:
                    chunks = fetch_chunks_fn(rec.doc_id)
                    chunks_cache[rec.doc_id] = chunks
                if chunks:
                    rel_idxs, _cmap = select_relevant_chunks(
                        query_vec, chunks, must_include, top_k=_TOP_K_RELEVANT
                    )
                    relevant_chunk_records.extend(
                        c for c in chunks if c.chunk_idx in rel_idxs
                    )
                    if needs_relevant and rel_idxs:
                        row["relevant_chunks"] = ",".join(
                            str(i) for i in rel_idxs
                        )
                        stats.relevant_filled += 1
                    if needs_acceptable:
                        acc = select_acceptable_chunks(
                            query_vec, chunks, threshold=threshold,
                            exclude=rel_idxs,
                        )
                        if acc:
                            row["acceptable_chunks"] = ",".join(
                                str(i) for i in acc
                            )
                            stats.acceptable_filled += 1

    # 기존 row 의 relevant_chunks 만 있고 chunk record 가 비었으면 — caption 판단을 위해
    # chunks fetch 시도 (chunks_cache 에 doc_id 있으면 재사용)
    if (
        not relevant_chunk_records
        and matched_docs
        and not is_cross_doc
        and row.get("relevant_chunks")
    ):
        rec = matched_docs[0]
        chunks = chunks_cache.get(rec.doc_id)
        if chunks is None:
            try:
                chunks = fetch_chunks_fn(rec.doc_id)
                chunks_cache[rec.doc_id] = chunks
            except Exception:  # noqa: BLE001
                chunks = []
        rel_set = {
            int(x) for x in row["relevant_chunks"].split(",")
            if x.strip().lstrip("-").isdigit()
        }
        relevant_chunk_records.extend(c for c in chunks if c.chunk_idx in rel_set)

    # caption_dependent 판단
    qtype = row.get("query_type") or ""
    caption = is_caption_dependent(qtype, relevant_chunk_records)
    row["caption_dependent"] = "true" if caption else "false"
    if caption:
        stats.caption_dependent_count += 1

    return row


def build_golden_v2(
    v1_rows: list[dict],
    *,
    docs_index: dict[str, DocRecord],
    fetch_chunks_fn,
    embed_query_fn,
    threshold: float = _DEFAULT_COSINE_THRESHOLD,
) -> tuple[list[dict], BuildStats]:
    """v1 rows → v2 rows + stats.

    fetch_chunks_fn / embed_query_fn 의존성 주입으로 단위 테스트에서 mock 가능.
    """
    stats = BuildStats()
    chunks_cache: dict[str, list[ChunkRecord]] = {}
    out_rows: list[dict] = []

    for v1_row in v1_rows:
        v2_row = build_v2_row(
            v1_row,
            docs_index=docs_index,
            fetch_chunks_fn=fetch_chunks_fn,
            embed_query_fn=embed_query_fn,
            threshold=threshold,
            stats=stats,
            chunks_cache=chunks_cache,
        )
        out_rows.append(v2_row)
        stats.total_rows += 1
        stats.query_type_dist[v2_row.get("query_type", "")] += 1
        if v2_row.get("doc_type"):
            stats.doc_type_dist[v2_row["doc_type"]] += 1
        # acceptable sample 5건 — relevant + acceptable 둘 다 있는 row 우선
        if (
            len(stats.acceptable_samples) < _ACCEPTABLE_SAMPLE_N
            and v2_row.get("acceptable_chunks")
            and v2_row.get("relevant_chunks")
        ):
            stats.acceptable_samples.append(
                f"  {v2_row.get('id', '')} | "
                f"q={(v2_row.get('query') or '')[:30]!r} | "
                f"relevant={v2_row.get('relevant_chunks')} | "
                f"acceptable={v2_row.get('acceptable_chunks')[:60]}"
            )

    return out_rows, stats


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------


def load_v1_csv(path: Path) -> list[dict]:
    """v1 CSV → list[dict]. utf-8-sig 자동 처리."""
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def write_v2_csv(rows: list[dict], path: Path) -> None:
    """v2 CSV 쓰기 — 14 컬럼, utf-8-sig (Excel 한글 호환)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(_V2_FIELDNAMES))
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# stdout 요약
# ---------------------------------------------------------------------------


def print_summary(stats: BuildStats) -> None:
    """build 결과 stderr 요약 — query_type/doc_type 분포 + caption + fail."""
    print(f"\n[OK] 총 {stats.total_rows} row 변환 완료", file=sys.stderr)
    print(
        f"  doc 매칭 fix: {stats.doc_match_fixed}건 / "
        f"fail 잔존: {len(stats.doc_match_failed)}건",
        file=sys.stderr,
    )
    if stats.doc_match_failed:
        print(
            f"  [WARN] doc 매칭 fail row id: {stats.doc_match_failed[:20]}"
            f"{'...' if len(stats.doc_match_failed) > 20 else ''}",
            file=sys.stderr,
        )
    print(
        f"  relevant 자동 채움: {stats.relevant_filled}건 / "
        f"acceptable 자동 채움: {stats.acceptable_filled}건",
        file=sys.stderr,
    )
    print(f"  cross_doc 처리: {stats.cross_doc_processed}건", file=sys.stderr)
    # Phase 2 — stale doc_id 통계 (hook 발동 시에만 노출).
    if stats.stale_doc_id_count > 0 or stats.stale_doc_id_kept:
        print(
            f"  [stale doc_id] 검출 {stats.stale_doc_id_count}건 / "
            f"title fallback auto-fix {stats.stale_doc_id_fixed}건 / "
            f"보존 {len(stats.stale_doc_id_kept)}건",
            file=sys.stderr,
        )
        if stats.stale_doc_id_kept:
            print(
                f"  [WARN] 복구 실패 row id (수동 정정 필요): "
                f"{stats.stale_doc_id_kept[:20]}"
                f"{'...' if len(stats.stale_doc_id_kept) > 20 else ''}",
                file=sys.stderr,
            )

    print("\n[query_type 분포]", file=sys.stderr)
    for label, n in stats.query_type_dist.most_common():
        print(f"  {label:20s} {n:4d}건", file=sys.stderr)

    print("\n[doc_type 분포]", file=sys.stderr)
    for label, n in stats.doc_type_dist.most_common():
        print(f"  {label:8s} {n:4d}건", file=sys.stderr)

    cap_pct = (
        stats.caption_dependent_count / stats.total_rows * 100
        if stats.total_rows
        else 0.0
    )
    print(
        f"\n[caption_dependent] {stats.caption_dependent_count}/"
        f"{stats.total_rows} ({cap_pct:.1f}%)",
        file=sys.stderr,
    )

    print("\n[acceptable sample 5건]", file=sys.stderr)
    for line in stats.acceptable_samples:
        print(line, file=sys.stderr)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="S4-A D3 Phase 1 — golden_v1.csv → golden_v2.csv 자동 보강"
    )
    parser.add_argument(
        "--input", type=Path, default=_DEFAULT_INPUT,
        help=f"입력 v1 CSV (default {_DEFAULT_INPUT.name})",
    )
    parser.add_argument(
        "--output", type=Path, default=_DEFAULT_OUTPUT,
        help=f"출력 v2 CSV (default {_DEFAULT_OUTPUT.name})",
    )
    parser.add_argument(
        "--cosine-threshold", type=float, default=_DEFAULT_COSINE_THRESHOLD,
        help=f"acceptable cosine 임계 (default {_DEFAULT_COSINE_THRESHOLD})",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    v1_rows = load_v1_csv(args.input)
    print(f"[OK] v1 CSV 로드: {len(v1_rows)} row", file=sys.stderr)

    # 실 DB 클라이언트 + BGE-M3 embedder 주입
    from app.adapters.impl.bgem3_hf_embedding import BGEM3HFEmbeddingProvider
    from app.db import get_supabase_client

    client = get_supabase_client()
    docs_index = fetch_docs_index(client)
    print(f"[OK] docs 인덱스 {len(docs_index)}건 로드", file=sys.stderr)

    embedder = BGEM3HFEmbeddingProvider()

    def fetch_chunks_fn(doc_id: str) -> list[ChunkRecord]:
        return fetch_chunks(client, doc_id)

    def embed_query_fn(query: str) -> list[float]:
        return embedder.embed_query(query)

    v2_rows, stats = build_golden_v2(
        v1_rows,
        docs_index=docs_index,
        fetch_chunks_fn=fetch_chunks_fn,
        embed_query_fn=embed_query_fn,
        threshold=args.cosine_threshold,
    )

    write_v2_csv(v2_rows, args.output)
    print(f"[OK] v2 CSV 저장: {args.output} ({len(v2_rows)} row)", file=sys.stderr)

    print_summary(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
