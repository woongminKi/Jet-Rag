"""S4-B chunks.metadata.entities backfill — 기존 chunks 룰 기반 entities 추가.

motivation
----------
2026-05-10 S4-B ingest 통합 — 새 ingest 부터 chunks.metadata.entities 자동 채워짐.
단 기존 chunks (~수천 rows across 13 docs) 는 entities 없음. backfill 으로 채움.

설계 원칙
- **read-only default** — `--apply` 명시 없으면 dry-run (변경 0)
- **batch update** — 100 chunks 씩 batch (Supabase API 부담 최소화)
- **idempotent** — 이미 entities 키 있는 chunks 는 skip (re-run 안전)
- **graceful** — 추출 실패 시 skip (해당 chunk 만, 전체 차단 X)
- **외부 cost 0** — 룰 기반 (정규식만)

사용
----
    # dry-run (모든 chunks 의 추정 entities 출력 — DB 변경 X)
    cd api && uv run python ../evals/backfill_chunk_entities.py --dry-run

    # 실 적용 (사용자 명시 후)
    cd api && uv run python ../evals/backfill_chunk_entities.py --apply

    # 단일 doc backfill (테스트용)
    uv run python ../evals/backfill_chunk_entities.py --apply --doc-id <UUID>
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

# api/ 를 import path 에 추가
_API_PATH = Path(__file__).resolve().parents[0].parent / "api"
if (_API_PATH / "app").exists():
    sys.path.insert(0, str(_API_PATH))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="S4-B chunks.metadata.entities backfill (룰 기반)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실 DB 변경 (default OFF — dry-run). 사용자 명시 권고.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="DB 변경 0, 추정만 출력 (default).",
    )
    parser.add_argument(
        "--doc-id",
        default=None,
        help="단일 doc 만 backfill (UUID). 미지정 시 전체.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="batch update 크기 (default 100).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="처리 chunks cap (0 = 전체, 디버깅용).",
    )
    args = parser.parse_args(argv)

    # api/app/config.py → .env 자동 로드
    try:
        from app import config as _app_config  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] app.config import 실패 ({exc})", file=sys.stderr)

    from app.db import get_supabase_client
    from app.services.entity_extract import extract_entities

    if args.apply and args.dry_run:
        print("[FAIL] --apply 와 --dry-run 동시 지정 불가", file=sys.stderr)
        return 1
    apply_mode = args.apply  # default dry-run

    client = get_supabase_client()
    print(f"[mode] {'APPLY (DB 변경)' if apply_mode else 'DRY-RUN (변경 0)'}", file=sys.stderr)

    # 1) chunks 페이지 단위 fetch (id, text, metadata)
    page_size = max(args.batch_size, 100)
    offset = 0
    total_processed = 0
    total_skipped = 0  # entities 키 이미 있음
    total_updated = 0
    total_empty = 0  # extract_entities 결과 빈
    update_batch: list[dict] = []

    counters: Counter = Counter()
    while True:
        if args.limit and total_processed >= args.limit:
            break
        q = client.table("chunks").select("id, text, metadata, doc_id")
        if args.doc_id:
            q = q.eq("doc_id", args.doc_id)
        q = q.range(offset, offset + page_size - 1)
        try:
            resp = q.execute()
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] fetch error at offset={offset}: {exc}", file=sys.stderr)
            return 1
        rows = resp.data or []
        if not rows:
            break
        print(
            f"[fetch] offset={offset} +{len(rows)} (총 처리 {total_processed})",
            file=sys.stderr,
        )

        for r in rows:
            if args.limit and total_processed >= args.limit:
                break
            total_processed += 1
            chunk_id = r["id"]
            metadata = r.get("metadata") or {}
            if "entities" in metadata:
                total_skipped += 1
                continue
            text = r.get("text") or ""
            try:
                ents = extract_entities(text)
            except Exception:  # noqa: BLE001
                continue
            if ents.is_empty():
                total_empty += 1
                continue
            new_metadata = dict(metadata)
            new_metadata["entities"] = ents.to_dict()
            counters["dates"] += len(ents.dates)
            counters["amounts"] += len(ents.amounts)
            counters["percentages"] += len(ents.percentages)
            counters["identifiers"] += len(ents.identifiers)
            update_batch.append({"id": chunk_id, "metadata": new_metadata})
            total_updated += 1

            # batch update flush
            if apply_mode and len(update_batch) >= args.batch_size:
                _flush_batch(client, update_batch)
                update_batch = []
                print(f"  [apply] flushed {args.batch_size} updates", file=sys.stderr)

        offset += len(rows)
        if len(rows) < page_size:
            break

    # 마지막 batch flush
    if apply_mode and update_batch:
        _flush_batch(client, update_batch)
        print(f"  [apply] flushed {len(update_batch)} updates (final)", file=sys.stderr)

    print(file=sys.stderr)
    print("=== 종료 요약 ===", file=sys.stderr)
    print(f"  total processed: {total_processed}", file=sys.stderr)
    print(f"  skipped (entities 이미 있음): {total_skipped}", file=sys.stderr)
    print(f"  empty (entities 0건 추출): {total_empty}", file=sys.stderr)
    print(f"  updated: {total_updated} {'(applied to DB)' if apply_mode else '(dry-run, DB 변경 0)'}", file=sys.stderr)
    print(file=sys.stderr)
    print("=== 추출 entities 분포 (cumulative) ===", file=sys.stderr)
    for cat, count in counters.most_common():
        print(f"  {cat}: {count}", file=sys.stderr)
    return 0


def _flush_batch(client, updates: list[dict]) -> None:
    """batch update — 1 chunk 씩 update (Supabase REST 제약)."""
    for u in updates:
        try:
            client.table("chunks").update({"metadata": u["metadata"]}).eq(
                "id", u["id"]
            ).execute()
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] update {u['id']} 실패: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
