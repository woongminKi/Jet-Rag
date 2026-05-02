"""W4-Q-14 dry-run — 새 chunk.py 정책으로 재청킹 시뮬레이션.

배경
- W4-Q-14 (4.1·4.2·4.4·4.5·4.3·4.6) 적용 후 실제 인제스트 (DE-65 = 재인제스트) 전에
  청크 수·평균 길이·section_title 채움 비율 변화를 측정해 사용자 confirm 게이트.
- 본 스크립트는 **DB 변경 0**.

모드 (W6 Day 4 추가)
- `simulated` (기본, 빠름) — 기존 chunks 를 ExtractedSection 으로 변환 후 chunk policy 통과.
  파서 raw 출력은 모름. 정상 시나리오 Δ ≈ 0% (chunks 가 이미 잘 분리된 상태).
- `realistic` — Storage 에서 원본 파일 다운로드 + 파서 실행 → 실 인제스트와 동일한 입력
  분포로 chunk policy 적용. DE-65 본 적용 결과를 사전 정확 예측. 비용 ↑ (Storage I/O + 파서).

사용
    cd api && uv run python scripts/dryrun_chunk_repolicy.py            # simulated
    cd api && uv run python scripts/dryrun_chunk_repolicy.py --mode realistic
    cd api && uv run python scripts/dryrun_chunk_repolicy.py --output ...
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.adapters.parser import ExtractedSection  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import get_supabase_client  # noqa: E402
from app.ingest.stages.chunk import (  # noqa: E402
    _merge_short_sections,
    _split_long_sections,
    _to_chunk_records,
)


def _fetch_documents(client: Any, user_id: str) -> list[dict]:
    """user_id 의 모든 documents 메타 fetch (storage_path 포함 — realistic 모드용)."""
    docs: list[dict] = []
    page_size = 100
    offset = 0
    while True:
        resp = (
            client.table("documents")
            .select("id, title, doc_type, storage_path")
            .eq("user_id", user_id)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = resp.data or []
        docs.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return docs


def _fetch_chunks_grouped(client: Any, user_id: str) -> dict[str, list[dict]]:
    """user_id 의 모든 chunks 를 doc_id 별로 그룹핑 (chunk_idx 순)."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    page_size = 1000
    offset = 0
    while True:
        resp = (
            client.table("chunks")
            .select(
                "id, doc_id, chunk_idx, text, section_title, page, "
                "documents!inner(user_id, deleted_at)"
            )
            .eq("documents.user_id", user_id)
            .is_("documents.deleted_at", "null")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = resp.data or []
        for r in rows:
            grouped[r["doc_id"]].append(r)
        if len(rows) < page_size:
            break
        offset += page_size
    # chunk_idx 정렬 (필수 — overlap 누적 시 순서 의존)
    for doc_id in grouped:
        grouped[doc_id].sort(key=lambda x: x.get("chunk_idx") or 0)
    return grouped


def _simulate_rechunk(chunks: list[dict]) -> list:
    """기존 chunks 를 ExtractedSection 으로 변환 후 새 chunk.py 통과.

    각 chunk 는 이미 적절한 page·section_title 메타를 가지고 있으므로 파서 출력에 가까운
    구조 — 이를 새 split/merge/overlap 로직에 통과시키면 실제 재인제스트 결과와 근사.
    """
    if not chunks:
        return []
    sections = [
        ExtractedSection(
            text=c.get("text") or "",
            page=c.get("page"),
            section_title=c.get("section_title"),
        )
        for c in chunks
        if (c.get("text") or "").strip()
    ]
    split = _split_long_sections(sections)
    merged = _merge_short_sections(split)
    return _to_chunk_records(doc_id="dryrun-doc", sections=merged)


def _realistic_rechunk(doc: dict) -> tuple[list, str | None]:
    """W6 Day 4 — Storage 에서 원본 파일 다운로드 + 파서 실행 → 실 인제스트와 동일한
    입력 분포로 chunk policy 통과 (realistic 모드).

    반환: (chunk records, error_msg or None). DocxParser·HwpxParser·PyMuPDFParser 등
    실 파서 출력 sections 을 chunk policy 에 통과시켜 정확 예측.
    """
    from app.adapters.impl.hwpml_parser import is_hwpml_bytes  # noqa: E402
    from app.adapters.impl.supabase_storage import SupabaseBlobStorage  # noqa: E402
    from app.ingest.stages.extract import (  # noqa: E402
        _PARSERS_BY_DOC_TYPE,
        _hwpml_parser,
    )

    parser = _PARSERS_BY_DOC_TYPE.get(doc["doc_type"])
    if parser is None:
        return [], f"unsupported doc_type={doc['doc_type']}"
    storage = SupabaseBlobStorage(bucket=get_settings().supabase_storage_bucket)
    try:
        data = storage.get(doc["storage_path"])
    except Exception as exc:  # noqa: BLE001
        return [], f"storage fetch fail: {exc}"
    if doc["doc_type"] == "hwp" and is_hwpml_bytes(data[:4096]):
        parser = _hwpml_parser
    try:
        extraction = parser.parse(data, file_name=doc.get("title") or doc["id"])
    except Exception as exc:  # noqa: BLE001
        return [], f"parse fail: {exc}"
    sections = list(extraction.sections)
    if not sections:
        return [], "empty sections"
    split = _split_long_sections(sections)
    merged = _merge_short_sections(split)
    return _to_chunk_records(doc_id="dryrun-doc", sections=merged), None


def main() -> int:
    parser = argparse.ArgumentParser(description="W4-Q-14 chunk.py dry-run")
    parser.add_argument("--output", "-o", help="출력 markdown 파일 경로 (기본: stdout)")
    parser.add_argument(
        "--user-id",
        help="대상 user_id (기본: settings.default_user_id)",
    )
    parser.add_argument(
        "--mode",
        choices=["simulated", "realistic"],
        default="simulated",
        help="simulated (기본, 빠름, chunks→resplit) / realistic (Storage+파서 실행, 정확)",
    )
    args = parser.parse_args()

    settings = get_settings()
    user_id = args.user_id or settings.default_user_id
    client = get_supabase_client()

    docs = _fetch_documents(client, user_id)
    chunks_by_doc = _fetch_chunks_grouped(client, user_id)

    lines: list[str] = []
    lines.append("# W4-Q-14 dry-run 리포트 — 새 chunk.py 정책 재청킹 시뮬레이션")
    lines.append("")
    if args.mode == "realistic":
        lines.append(
            "> **realistic 모드** (W6 Day 4) — Storage 에서 원본 다운로드 + 파서 실행 →"
            " 실 인제스트와 동일한 입력 분포로 chunk policy 통과. DE-65 본 적용 결과 정확 예측."
        )
    else:
        lines.append(
            "> **simulated 모드** (기본, 빠름) — 기존 chunks 를 ExtractedSection 으로 변환"
            " 후 chunk policy 통과. 정상 시나리오 Δ ≈ 0% (chunks 가 이미 잘 분리된 상태)."
        )
    lines.append("")
    lines.append(f"- user_id: `{user_id}`")
    lines.append(f"- 분석 doc 수: {len(docs)}")
    lines.append(
        f"- 현재 총 chunks: {sum(len(v) for v in chunks_by_doc.values())}"
    )
    lines.append(f"- 모드: `{args.mode}`")
    lines.append("")

    lines.append("## doc 별 비교")
    lines.append("")
    lines.append(
        "| doc | type | 현재 청크 | dry-run 청크 | Δ | 평균 len 현재 | 평균 len 신 |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|")

    total_current = 0
    total_new = 0
    delta_pcts: list[float] = []

    for doc in docs:
        doc_id = doc["id"]
        name = (doc.get("title") or "(no title)")[:40]
        doc_type = doc.get("doc_type") or "?"

        current_chunks = chunks_by_doc.get(doc_id, [])
        current_count = len(current_chunks)
        current_avg_len = (
            statistics.mean(len(c.get("text") or "") for c in current_chunks)
            if current_chunks
            else 0
        )

        if args.mode == "realistic":
            new_records, err = _realistic_rechunk(doc)
            if err:
                lines.append(
                    f"| {name} | {doc_type} | {current_count} | err | err | err | err |"
                )
                print(f"[realistic] {name}: {err}", file=sys.stderr)
                continue
        else:
            new_records = _simulate_rechunk(current_chunks)
        new_count = len(new_records)
        new_avg_len = (
            statistics.mean(len(r.text) for r in new_records)
            if new_records
            else 0
        )

        if current_count > 0:
            delta_pct = (new_count - current_count) / current_count * 100
            delta_pcts.append(delta_pct)
            delta_str = f"{delta_pct:+.1f}%"
        else:
            delta_str = "N/A"

        total_current += current_count
        total_new += new_count

        lines.append(
            f"| {name} | {doc_type} | {current_count} | {new_count} | "
            f"{delta_str} | {current_avg_len:.0f} | {new_avg_len:.0f} |"
        )

    lines.append("")
    lines.append("## 종합")
    lines.append("")
    lines.append(f"- **현재 총 청크**: {total_current}")
    lines.append(f"- **dry-run 총 청크**: {total_new}")
    if total_current:
        total_delta = (total_new - total_current) / total_current * 100
        lines.append(f"- **총 청크 수 Δ**: {total_delta:+.1f}%")
    if delta_pcts:
        lines.append(
            f"- **doc 평균 Δ**: {statistics.mean(delta_pcts):+.1f}% "
            f"(median {statistics.median(delta_pcts):+.1f}%, "
            f"min {min(delta_pcts):+.1f}%, max {max(delta_pcts):+.1f}%)"
        )
    lines.append("")
    lines.append("## 합성 시나리오 — doc 별 전체 텍스트를 단일 섹션으로 (worst case)")
    lines.append("")
    lines.append(
        "기존 chunks 를 doc 별로 모두 concat → 단일 ExtractedSection 으로 처리.\n"
        "파서가 헤딩 구분 못한 worst case (긴 단일 섹션) 시뮬레이션."
    )
    lines.append("")
    lines.append("| doc | type | 현재 청크 | concat-resplit 청크 | Δ | 평균 len 신 |")
    lines.append("|---|---|---:|---:|---:|---:|")
    worst_total_current = 0
    worst_total_new = 0
    for doc in docs:
        doc_id = doc["id"]
        name = (doc.get("title") or "(no title)")[:40]
        doc_type = doc.get("doc_type") or "?"
        current_chunks = chunks_by_doc.get(doc_id, [])
        if not current_chunks:
            continue
        concat_text = "\n\n".join(
            (c.get("text") or "").strip() for c in current_chunks
        )
        synthetic_section = [
            ExtractedSection(text=concat_text, page=1, section_title=None)
        ]
        sp = _split_long_sections(synthetic_section)
        mg = _merge_short_sections(sp)
        new_records = _to_chunk_records(doc_id="dryrun-doc", sections=mg)
        new_count = len(new_records)
        current_count = len(current_chunks)
        new_avg_len = (
            statistics.mean(len(r.text) for r in new_records)
            if new_records
            else 0
        )
        worst_total_current += current_count
        worst_total_new += new_count
        delta_pct = (
            (new_count - current_count) / current_count * 100
            if current_count
            else 0
        )
        lines.append(
            f"| {name} | {doc_type} | {current_count} | {new_count} | "
            f"{delta_pct:+.1f}% | {new_avg_len:.0f} |"
        )
    if worst_total_current:
        worst_delta = (
            (worst_total_new - worst_total_current) / worst_total_current * 100
        )
        lines.append("")
        lines.append(
            f"- worst-case 종합: {worst_total_current} → {worst_total_new} "
            f"({worst_delta:+.1f}%)"
        )
    lines.append("")
    lines.append("## DE-65 게이트")
    lines.append("")
    lines.append(
        "- 명세 §4 AC: 청크 수 변화 < 10% (방향성 단순 시뮬). "
        "실제 재인제스트 전에 사용자 confirm 필요."
    )
    lines.append(
        "- 본 dry-run 한계 — 입력으로 기존 chunks 사용 → overlap 효과가 청크 수 증가에 직접 반영"
        " (기존 chunks 가 이미 적절한 크기). 실제 재인제스트는 파서 출력 (긴 섹션 + 짧은 섹션 혼재)"
        " 에서 시작하므로 overlap 도입 효과는 더 작을 수 있음."
    )
    lines.append("")

    out = "\n".join(lines)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"[OK] 리포트 저장: {args.output}", file=sys.stderr)
    else:
        print(out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
