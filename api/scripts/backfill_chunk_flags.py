"""W3 v0.5 §3.G(3) — 기존 chunks 에 chunk_filter 룰 백필 마킹.

배경
- chunk_filter 스테이지는 신규 인제스트부터 적용. 기존 chunks (W2~W3 Day 3 까지 적재)
  는 flags 가 비어 있어 검색에서 표 노이즈/헤더-푸터 의심 청크가 그대로 노출됨.
- 본 스크립트는 DB 의 모든 chunks 를 fetch → 룰 적용 → flags.filtered_reason 마킹된
  것만 UPDATE.

dry-run vs execute
- 기본 dry-run — DB 변경 0. 마킹 대상 비율 + sample 5건 출력.
- --execute 명시 시 실제 UPDATE.

사용
    cd api && uv run python scripts/backfill_chunk_flags.py
        # → dry-run, 마킹 비율 + sample 출력
    cd api && uv run python scripts/backfill_chunk_flags.py --execute
        # → 실제 UPDATE
    cd api && uv run python scripts/backfill_chunk_flags.py --user-id <UUID>
        # → 특정 사용자 (default: settings.default_user_id)

주의
- chunk_filter 의 휴리스틱과 임계값은 이 스크립트와 1:1 매칭 (모듈 import) → 정합성 보장.
- 기존 flags 값은 보존 — filtered_reason 만 추가/덮어쓰기. 다른 마커는 건드리지 않음.

마킹 되돌리기 (C-4 P2)
- 전체 마킹 삭제: Supabase Studio SQL Editor 에서
    UPDATE chunks
    SET flags = flags - 'filtered_reason'
    WHERE flags ? 'filtered_reason';
- 사용자 한정: WHERE 절에 doc_id IN (SELECT id FROM documents WHERE user_id = '...')
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# api/ 루트를 sys.path 에 추가 — `uv run python scripts/...` 로 실행 시 자동 추가 안 됨.
_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.adapters.vectorstore import ChunkRecord  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import get_supabase_client  # noqa: E402
from app.ingest.stages.chunk_filter import (  # noqa: E402
    _classify_chunk,
    _detect_header_footer_texts,
)

_FETCH_PAGE_SIZE = 1000
_SAMPLE_SIZE = 5


def fetch_all_chunks(client: Any, user_id: str) -> list[dict]:
    """모든 chunks fetch (PostgREST 1000 limit 회피, soft-deleted 문서 제외)."""
    all_rows: list[dict] = []
    offset = 0
    while True:
        end = offset + _FETCH_PAGE_SIZE - 1
        resp = (
            client.table("chunks")
            .select(
                "id, doc_id, chunk_idx, text, flags, "
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


def classify_all(rows: list[dict]) -> list[dict]:
    """chunks 에 룰 적용 → 마킹 대상 list. 각 element 에 'reason' 추가.

    chunk_filter 의 헬퍼를 직접 호출 — 동일한 알고리즘.
    header_footer 는 doc 내부 반복 기준이라 doc 별로 그룹화하여 _detect_header_footer_texts.
    """
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_doc[row["doc_id"]].append(row)

    targets: list[dict] = []
    for doc_id, doc_rows in by_doc.items():
        # ChunkRecord 형태로 변환 (헬퍼가 ChunkRecord 를 받음).
        chunk_records = [
            ChunkRecord(
                doc_id=doc_id,
                chunk_idx=r.get("chunk_idx") or 0,
                text=r.get("text") or "",
                flags=dict(r.get("flags") or {}),
            )
            for r in doc_rows
        ]
        header_footer_texts = _detect_header_footer_texts(chunk_records)
        for raw, rec in zip(doc_rows, chunk_records):
            reason = _classify_chunk(rec, header_footer_texts)
            if reason is None:
                continue
            existing_flags = dict(raw.get("flags") or {})
            # 이미 동일한 마킹이면 스킵 — UPDATE 호출 절약.
            if existing_flags.get("filtered_reason") == reason:
                continue
            targets.append({
                "id": raw["id"],
                "doc_id": doc_id,
                "doc_title": (raw.get("documents") or {}).get("title") or "",
                "doc_type": (raw.get("documents") or {}).get("doc_type") or "",
                "chunk_idx": raw.get("chunk_idx"),
                "text_preview": (raw.get("text") or "")[:120].replace("\n", " "),
                "reason": reason,
                "existing_flags": existing_flags,
            })
    return targets


def update_flags(client: Any, target: dict) -> None:
    """단일 chunk 의 flags 업데이트. 기존 flags 보존 + filtered_reason 추가."""
    new_flags = dict(target["existing_flags"])
    new_flags["filtered_reason"] = target["reason"]
    (
        client.table("chunks")
        .update({"flags": new_flags})
        .eq("id", target["id"])
        .execute()
    )


def render_dry_run_report(
    total_chunks: int, targets: list[dict]
) -> str:
    """dry-run 출력 — 마킹 비율 + sample 5건."""
    lines: list[str] = []
    lines.append("# G(3) chunk flags backfill — dry-run 결과")
    lines.append("")
    lines.append(f"- 전체 chunks: {total_chunks}")
    lines.append(f"- 마킹 대상: {len(targets)}")
    if total_chunks > 0:
        ratio_pct = len(targets) / total_chunks * 100
        lines.append(f"- 마킹 비율: {ratio_pct:.1f}%")
    lines.append("")

    if targets:
        reason_counter: Counter[str] = Counter(t["reason"] for t in targets)
        lines.append("## 사유별 분포")
        for reason, count in reason_counter.most_common():
            ratio_pct = (count / total_chunks * 100) if total_chunks else 0.0
            lines.append(f"- {reason}: {count} ({ratio_pct:.1f}%)")
        lines.append("")

        lines.append("## doc 별 마킹 breakdown")
        by_doc: dict[str, list[dict]] = defaultdict(list)
        for t in targets:
            by_doc[t["doc_id"]].append(t)
        rows = sorted(by_doc.items(), key=lambda kv: -len(kv[1]))
        for doc_id, items in rows:
            sample = items[0]
            doc_total_in_targets = len(items)
            lines.append(
                f"- doc={doc_id[:8]}… "
                f"({sample['doc_type']}, {sample['doc_title'][:30]}) "
                f"마킹 {doc_total_in_targets}건"
            )
        lines.append("")

        lines.append(f"## sample (최대 {_SAMPLE_SIZE}건)")
        for t in targets[:_SAMPLE_SIZE]:
            lines.append(
                f"- [{t['reason']:13s}] doc={t['doc_id'][:8]}… "
                f"({t['doc_type']}, {t['doc_title'][:30]}) "
                f"idx={t['chunk_idx']}"
            )
            lines.append(f"  text: {t['text_preview']!r}")
    else:
        lines.append("(마킹 대상 없음)")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--execute",
        action="store_true",
        help="실제 UPDATE (default: dry-run)",
    )
    ap.add_argument(
        "--user-id",
        default=None,
        help="대상 사용자 (default: settings.default_user_id)",
    )
    args = ap.parse_args()

    client = get_supabase_client()
    user_id = args.user_id or get_settings().default_user_id

    print(f"# fetching chunks for user_id={user_id}", file=sys.stderr)
    rows = fetch_all_chunks(client, user_id)
    print(f"# fetched {len(rows)} chunks", file=sys.stderr)

    targets = classify_all(rows)

    report = render_dry_run_report(len(rows), targets)
    sys.stdout.write(report)

    if not args.execute:
        sys.stdout.write(
            "\n--execute 미지정 (dry-run). 실제 UPDATE 하려면 --execute 추가.\n"
        )
        return 0

    if not targets:
        sys.stdout.write("\n마킹 대상 없음 — UPDATE 스킵.\n")
        return 0

    sys.stdout.write(f"\n# UPDATE 시작: {len(targets)} 건\n")
    ok = 0
    failed = 0
    for t in targets:
        try:
            update_flags(client, t)
            ok += 1
        except Exception as exc:  # noqa: BLE001 — 백필 batch 안전성
            print(
                f"  ✗ {t['id']} → {exc}", file=sys.stderr
            )
            failed += 1
    sys.stdout.write(f"# 결과: ok={ok}, failed={failed}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
