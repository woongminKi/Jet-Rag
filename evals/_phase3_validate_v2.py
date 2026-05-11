"""Phase 3 일회용 — golden_v2.csv 의 doc_id 무결성 검증.

cross_doc row 는 doc_id 컬럼이 비어 있어 skip. 단일 doc row 는 docs DB 에
실제 존재하는 활성 doc 인지 확인. stale_doc_id_count == 0 이면 통과.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "api"))

from app.db.client import get_supabase_client


def main() -> int:
    csv_path = _REPO / "evals" / "golden_v2.csv"
    client = get_supabase_client()

    rows = (
        client.table("documents")
        .select("id")
        .is_("deleted_at", "null")
        .execute()
        .data
        or []
    )
    active_doc_ids = {r["id"] for r in rows}
    print(f"[INFO] 활성 doc {len(active_doc_ids)}건")

    stale: list[tuple[str, str]] = []
    cross_doc_count = 0
    single_doc_count = 0
    negative_count = 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            negative = (row.get("negative") or "").lower() == "true"
            doc_id = row.get("doc_id") or ""
            title = row.get("expected_doc_title") or ""
            qid = row.get("id") or ""

            if negative:
                negative_count += 1
                continue
            if not doc_id:
                # cross_doc 또는 doc_id 미부착
                if "|" in title:
                    cross_doc_count += 1
                continue
            single_doc_count += 1
            if doc_id not in active_doc_ids:
                stale.append((qid, doc_id))

    print(f"[STATS] cross_doc={cross_doc_count} / single_doc={single_doc_count} "
          f"/ negative={negative_count}")
    print(f"[STATS] stale_doc_id_count={len(stale)}")
    if stale:
        for qid, doc_id in stale:
            print(f"  [STALE] {qid} → {doc_id}")
        return 1
    print("[OK] 무결성 검증 통과 (stale_doc_id_count=0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
