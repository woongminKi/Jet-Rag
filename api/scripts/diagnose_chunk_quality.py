"""W3 Day 2 — DE-62 chunk 품질 진단 도구 (G(1) 단계, 보고서 기반).

배경
- 청킹 정책 검토 (work-log/2026-04-29 청킹 정책 검토.md) 결과 chunk.py 본격
  변경은 W4-Q-14 로 deferred. W3 안에서는 (1) 마이그레이션 004 의 flags 컬럼
  + 필터링 룰 (2) 본 진단 도구로 청크 품질 가시성 확보만 수행.
- 본 도구는 **진단 only** — DB 변경 0. 출력된 markdown 리포트를 사용자가
  검토 후 G(3) 자동 필터링 룰 (어떤 휴리스틱을 chunks.flags 에 기록할지) 결정.

휴리스틱 (heuristic)
- 표 노이즈 의심: 짧은 라인 ≥ 70% AND 숫자/특수문자 ≥ 50%
- 헤더/푸터 의심: 같은 doc_id 안에서 같은 텍스트 ≥ 3회 + len < 100
- section_title 채움 비율: KPI §13.1 (chunk metadata 충실도)

사용
    cd api && uv run python scripts/diagnose_chunk_quality.py
        # → stdout markdown
    cd api && uv run python scripts/diagnose_chunk_quality.py \\
        --output "../work-log/2026-04-30 chunk 품질 진단 리포트.md"
        # → 파일 저장 (공백 포함 경로는 따옴표로 감쌀 것)
    cd api && uv run python scripts/diagnose_chunk_quality.py --user-id <UUID>
        # → 특정 user_id 만 (default 는 settings.default_user_id)

의존성: stdlib (re, statistics, collections.Counter, argparse) + 기존 supabase client.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# api/ 루트를 sys.path 에 추가 — `uv run python scripts/...` 로 실행 시
# 자동 추가되지 않음 (backfill_extract_skipped.py 동일 패턴)
_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.config import get_settings  # noqa: E402
from app.db import get_supabase_client  # noqa: E402

# ---------------------- 휴리스틱 임계값 ----------------------

# 짧은 라인 기준 (chars). PDF 표 셀이 보통 30자 이내 — 그 이하는 표 셀 의심.
_SHORT_LINE_LEN = 30
# 표 노이즈 판정 — 짧은 라인 비율 + 숫자/특수문자 비율 동시 충족.
_TABLE_NOISE_SHORT_LINE_RATIO = 0.70
_TABLE_NOISE_DIGIT_PUNCT_RATIO = 0.50
# 헤더/푸터 판정 — 같은 doc 안에서 같은 텍스트 N회 이상 + 짧음.
_HEADER_FOOTER_REPEAT_THRESHOLD = 3
_HEADER_FOOTER_MAX_LEN = 100
# DB fetch 페이지 크기 — PostgREST 기본 1000건 limit 회피.
_FETCH_PAGE_SIZE = 1000

# 한국어/영문 글자 (Hangul + ASCII letters) — 그 외는 숫자/특수문자/기호로 분류.
_LETTER_PATTERN = re.compile(r"[A-Za-z가-힣]")
_DIGIT_PUNCT_PATTERN = re.compile(r"[\d\W_]", re.UNICODE)


# ---------------------- 메트릭 계산 ----------------------


def compute_chunk_metrics(text: str) -> dict[str, Any]:
    """단일 청크의 휴리스틱 메트릭 계산.

    반환:
        length: chars
        line_count: \n 으로 split 한 라인 수
        short_line_ratio: 짧은 라인 (len < _SHORT_LINE_LEN) 비율
        digit_punct_ratio: 숫자/특수문자 비율 (전체 chars 대비)
        is_potential_table_noise: bool
    """
    length = len(text)
    if length == 0:
        return {
            "length": 0,
            "line_count": 0,
            "short_line_ratio": 0.0,
            "digit_punct_ratio": 0.0,
            "is_potential_table_noise": False,
        }

    lines = text.split("\n")
    line_count = len(lines)
    short_lines = sum(1 for ln in lines if len(ln.strip()) < _SHORT_LINE_LEN)
    short_line_ratio = short_lines / line_count if line_count else 0.0

    digit_punct_count = sum(
        1 for ch in text if _DIGIT_PUNCT_PATTERN.match(ch)
    )
    # 공백/줄바꿈은 제외하고 비율 산정 (실제 의미있는 문자만)
    non_ws_total = sum(1 for ch in text if not ch.isspace())
    digit_punct_ratio = (
        digit_punct_count / non_ws_total if non_ws_total else 0.0
    )

    is_table_noise = (
        short_line_ratio >= _TABLE_NOISE_SHORT_LINE_RATIO
        and digit_punct_ratio >= _TABLE_NOISE_DIGIT_PUNCT_RATIO
    )

    return {
        "length": length,
        "line_count": line_count,
        "short_line_ratio": round(short_line_ratio, 3),
        "digit_punct_ratio": round(digit_punct_ratio, 3),
        "is_potential_table_noise": is_table_noise,
    }


def detect_header_footer_candidates(
    chunks_by_doc: dict[str, list[dict]],
) -> dict[str, set[str]]:
    """doc 별로 반복되는 짧은 텍스트 (헤더/푸터 의심) 의 chunk_id 집합 반환.

    같은 doc 안에서 동일 텍스트 (strip 후) 가 _HEADER_FOOTER_REPEAT_THRESHOLD
    회 이상 등장 + len < _HEADER_FOOTER_MAX_LEN 인 청크들.
    """
    out: dict[str, set[str]] = {}
    for doc_id, chunks in chunks_by_doc.items():
        text_counts: Counter[str] = Counter()
        for c in chunks:
            t = (c.get("text") or "").strip()
            if t and len(t) < _HEADER_FOOTER_MAX_LEN:
                text_counts[t] += 1
        repeated_texts = {
            t for t, n in text_counts.items()
            if n >= _HEADER_FOOTER_REPEAT_THRESHOLD
        }
        if not repeated_texts:
            continue
        flagged: set[str] = set()
        for c in chunks:
            t = (c.get("text") or "").strip()
            if t in repeated_texts:
                flagged.add(c["id"])
        out[doc_id] = flagged
    return out


# ---------------------- DB fetch ----------------------


def fetch_all_chunks(client: Any, user_id: str) -> list[dict]:
    """모든 chunks 를 페이지 단위로 fetch (PostgREST 1000건 limit 회피).

    user_id 격리 — documents 와 inner join 으로 다른 사용자 데이터 제외.
    soft-deleted 문서의 chunks 도 제외.
    """
    all_rows: list[dict] = []
    offset = 0
    while True:
        end = offset + _FETCH_PAGE_SIZE - 1
        resp = (
            client.table("chunks")
            .select(
                "id, doc_id, chunk_idx, text, section_title, page, "
                "documents!inner(user_id, deleted_at, title, doc_type)"
            )
            .eq("documents.user_id", user_id)
            .is_("documents.deleted_at", "null")
            .range(offset, end)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < _FETCH_PAGE_SIZE:
            break
        offset += _FETCH_PAGE_SIZE
    return all_rows


# ---------------------- 집계 ----------------------


def aggregate_doc_stats(
    chunks: list[dict],
    table_noise_chunk_ids: set[str],
    header_footer_chunk_ids: set[str],
) -> list[dict]:
    """doc 단위 집계 — 청크 수/평균 길이/노이즈 비율/section_title 채움 비율."""
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        by_doc[c["doc_id"]].append(c)

    out: list[dict] = []
    for doc_id, doc_chunks in by_doc.items():
        meta = doc_chunks[0].get("documents") or {}
        lengths = [len(c.get("text") or "") for c in doc_chunks]
        section_filled = sum(
            1 for c in doc_chunks if c.get("section_title")
        )
        table_noise = sum(
            1 for c in doc_chunks if c["id"] in table_noise_chunk_ids
        )
        header_footer = sum(
            1 for c in doc_chunks if c["id"] in header_footer_chunk_ids
        )
        n = len(doc_chunks)
        out.append(
            {
                "doc_id": doc_id,
                "title": meta.get("title") or "(제목 없음)",
                "doc_type": meta.get("doc_type") or "",
                "chunk_count": n,
                "avg_length": round(statistics.fmean(lengths), 1),
                "median_length": int(statistics.median(lengths)),
                "section_title_ratio": round(section_filled / n, 3),
                "table_noise_count": table_noise,
                "table_noise_ratio": round(table_noise / n, 3),
                "header_footer_count": header_footer,
                "header_footer_ratio": round(header_footer / n, 3),
            }
        )
    out.sort(key=lambda d: d["chunk_count"], reverse=True)
    return out


def aggregate_global_stats(
    chunks: list[dict],
    table_noise_chunk_ids: set[str],
    header_footer_chunk_ids: set[str],
) -> dict[str, Any]:
    """전체 집계 — KPI §13.1 의 chunk metadata 충실도 + 노이즈 의심 비율."""
    n = len(chunks)
    if n == 0:
        return {
            "chunk_total": 0,
            "doc_total": 0,
            "avg_length": None,
            "p50_length": None,
            "p95_length": None,
            "max_length": None,
            "table_noise_ratio": 0.0,
            "header_footer_ratio": 0.0,
            "section_title_filled_ratio": 0.0,
        }
    lengths = sorted(len(c.get("text") or "") for c in chunks)
    p50 = lengths[int(0.50 * (n - 1))]
    p95 = lengths[int(0.95 * (n - 1))]
    section_filled = sum(1 for c in chunks if c.get("section_title"))
    doc_total = len({c["doc_id"] for c in chunks})

    return {
        "chunk_total": n,
        "doc_total": doc_total,
        "avg_length": round(statistics.fmean(lengths), 1),
        "p50_length": int(p50),
        "p95_length": int(p95),
        "max_length": int(lengths[-1]),
        "table_noise_count": len(table_noise_chunk_ids),
        "table_noise_ratio": round(len(table_noise_chunk_ids) / n, 3),
        "header_footer_count": len(header_footer_chunk_ids),
        "header_footer_ratio": round(len(header_footer_chunk_ids) / n, 3),
        "section_title_filled_ratio": round(section_filled / n, 3),
    }


# ---------------------- 리포트 출력 ----------------------


def render_markdown(
    global_stats: dict[str, Any],
    doc_stats: list[dict],
    user_id: str,
) -> str:
    """markdown 리포트 — 사용자가 G(3) 자동 필터링 룰 결정에 사용."""
    lines: list[str] = []
    lines.append("# Chunk 품질 진단 리포트 (G(1))")
    lines.append("")
    lines.append(f"- 사용자: `{user_id}`")
    lines.append(
        f"- 청킹 정책 검토 보고서: `work-log/2026-04-29 청킹 정책 검토.md`"
    )
    lines.append(
        "- 본 리포트는 진단 only (DB 변경 0). G(3) 자동 필터링 룰 결정 입력."
    )
    lines.append("")

    lines.append("## 1. 전체 집계")
    lines.append("")
    lines.append(f"- chunk total: **{global_stats['chunk_total']}**")
    lines.append(f"- doc total: **{global_stats['doc_total']}**")
    lines.append(
        f"- 청크 길이 (chars): avg={global_stats['avg_length']}, "
        f"p50={global_stats['p50_length']}, p95={global_stats['p95_length']}, "
        f"max={global_stats['max_length']}"
    )
    lines.append(
        f"- **section_title 채움 비율** (KPI §13.1): "
        f"{global_stats['section_title_filled_ratio'] * 100:.1f}%"
    )
    lines.append(
        f"- 표 노이즈 의심: {global_stats.get('table_noise_count', 0)} "
        f"({global_stats['table_noise_ratio'] * 100:.1f}%)"
    )
    lines.append(
        f"- 헤더/푸터 의심: {global_stats.get('header_footer_count', 0)} "
        f"({global_stats['header_footer_ratio'] * 100:.1f}%)"
    )
    lines.append("")

    lines.append("## 2. 휴리스틱 정의")
    lines.append("")
    lines.append(
        f"- **표 노이즈 의심**: 짧은 라인 (len < {_SHORT_LINE_LEN}) "
        f"비율 ≥ {_TABLE_NOISE_SHORT_LINE_RATIO * 100:.0f}% "
        f"AND 숫자/특수문자 비율 ≥ {_TABLE_NOISE_DIGIT_PUNCT_RATIO * 100:.0f}%"
    )
    lines.append(
        f"- **헤더/푸터 의심**: 같은 doc 안에서 동일 텍스트가 "
        f"{_HEADER_FOOTER_REPEAT_THRESHOLD}회 이상 + len < {_HEADER_FOOTER_MAX_LEN}"
    )
    lines.append("")

    lines.append("## 3. doc 별 집계 (chunk_count 내림차순)")
    lines.append("")
    if not doc_stats:
        lines.append("- (데이터 없음)")
    else:
        lines.append(
            "| doc_id | type | title | chunks | avg_len | p50_len | section% | "
            "table_noise% | header_footer% |"
        )
        lines.append(
            "|---|---|---|---:|---:|---:|---:|---:|---:|"
        )
        for d in doc_stats:
            title_short = (d["title"] or "")[:40].replace("|", "\\|")
            lines.append(
                f"| `{d['doc_id'][:8]}…` "
                f"| {d['doc_type']} "
                f"| {title_short} "
                f"| {d['chunk_count']} "
                f"| {d['avg_length']} "
                f"| {d['median_length']} "
                f"| {d['section_title_ratio'] * 100:.0f}% "
                f"| {d['table_noise_ratio'] * 100:.0f}% "
                f"| {d['header_footer_ratio'] * 100:.0f}% |"
            )
    lines.append("")

    lines.append("## 4. 다음 단계 (G(3) 결정 입력)")
    lines.append("")
    lines.append(
        "- table_noise_ratio 가 5% 초과인 doc 가 다수면 → "
        "마이그레이션에 자동 filtered_reason='table_noise' 룰 추가 검토"
    )
    lines.append(
        "- header_footer_ratio 가 10% 초과인 doc 가 다수면 → "
        "추출 단계에서 header/footer 제거 로직 (W4-Q-14) 우선순위 상향"
    )
    lines.append(
        "- section_title 채움 비율이 50% 미만이면 → KPI §13.1 미달, "
        "chunk.py 의 section_title 추론 로직 점검 (W4-Q-14)"
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------- main ----------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--output",
        default=None,
        help="저장 경로 (default: stdout)",
    )
    ap.add_argument(
        "--user-id",
        default=None,
        help="대상 사용자 UUID (default: settings.default_user_id)",
    )
    args = ap.parse_args()

    client = get_supabase_client()
    user_id = args.user_id or get_settings().default_user_id

    print(f"# fetching chunks for user_id={user_id}", file=sys.stderr)
    chunks = fetch_all_chunks(client, user_id)
    print(f"# fetched {len(chunks)} chunks", file=sys.stderr)

    if not chunks:
        report = render_markdown(
            global_stats=aggregate_global_stats(chunks, set(), set()),
            doc_stats=[],
            user_id=user_id,
        )
        _emit(report, args.output)
        return 0

    # 표 노이즈 후보
    table_noise_ids: set[str] = set()
    for c in chunks:
        m = compute_chunk_metrics(c.get("text") or "")
        if m["is_potential_table_noise"]:
            table_noise_ids.add(c["id"])

    # 헤더/푸터 후보 (doc 별 집계)
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        by_doc[c["doc_id"]].append(c)
    header_footer_map = detect_header_footer_candidates(by_doc)
    header_footer_ids: set[str] = set()
    for ids in header_footer_map.values():
        header_footer_ids |= ids

    doc_stats = aggregate_doc_stats(chunks, table_noise_ids, header_footer_ids)
    global_stats = aggregate_global_stats(
        chunks, table_noise_ids, header_footer_ids
    )

    report = render_markdown(global_stats, doc_stats, user_id)
    _emit(report, args.output)
    return 0


def _emit(report: str, output_path: str | None) -> None:
    if output_path:
        Path(output_path).write_text(report, encoding="utf-8")
        print(f"# wrote {output_path}", file=sys.stderr)
    else:
        sys.stdout.write(report)


if __name__ == "__main__":
    sys.exit(main())
