"""W2 Day 7 — `flags.extract_skipped=true` 마킹된 doc 들 일괄 reingest.

배경
- W2 Day 4 이전에 업로드된 비-PDF (HWPX/이미지/URL/HWP) 들은 extract 스테이지의
  graceful skip 정책으로 `documents.flags.extract_skipped=true` 마킹된 채 chunks 0
  상태로 남아 있음.
- Day 3~4 에 5종 파서 (PDF/HWPX/Image/URL/HWP) 디스패처 등록 후, 새 업로드는
  정상 처리되지만 기존 skipped doc 들은 수동 reingest 필요.

사용
    cd api && uv run python scripts/backfill_extract_skipped.py
        --dry-run     # 대상 list 만 출력, 실제 reingest 안 함 (default)
        --execute     # 실제 reingest 호출 (BackgroundTask 큐잉)
        --doc-id ID   # 특정 doc 만 (반복 가능)
        --base-url U  # API base url (default: http://localhost:8000)

주의
- Vision API (이미지) 무료 한도 (RPD 20) — 이미지 doc 가 많으면 분할 실행 권장
- Storage 의 blob 은 그대로, chunks 만 재생성
- 진행 중 (queued/running) job 이 있으면 skip — 완료 후 재시도
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

# api/ 루트를 sys.path 에 추가 — `uv run python scripts/...` 로 실행 시
# 자동 추가되지 않음
_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_supabase_client  # noqa: E402


def find_extract_skipped_docs(supabase: Any, *, target_doc_ids: list[str] | None) -> list[dict]:
    """`flags.extract_skipped=true` AND `flags.failed!=true` 인 doc 들 반환.

    failed 인 doc 는 별도 운영 절차 (재업로드 또는 /reingest) 권장 — 자동 백필 대상에서 제외.
    """
    settings = get_settings()
    query = (
        supabase.table("documents")
        .select("id, title, doc_type, storage_path, flags")
        .eq("user_id", settings.default_user_id)
        .is_("deleted_at", "null")
    )
    if target_doc_ids:
        query = query.in_("id", target_doc_ids)
    rows = query.execute().data or []

    out: list[dict] = []
    for row in rows:
        flags = row.get("flags") or {}
        if not flags.get("extract_skipped"):
            continue
        if flags.get("failed"):
            continue
        out.append(row)
    return out


def reingest_via_http(*, base_url: str, doc_id: str, timeout: float = 30.0) -> dict:
    """HTTP `POST /documents/{id}/reingest` — BG 흐름과 정합."""
    url = f"{base_url.rstrip('/')}/documents/{doc_id}/reingest"
    resp = httpx.post(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="실제 reingest 호출")
    ap.add_argument("--dry-run", action="store_true", help="대상 list 만 출력 (default)")
    ap.add_argument("--doc-id", action="append", default=None, help="특정 doc_id (반복)")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--delay-seconds", type=float, default=2.0,
                    help="reingest 호출 사이 sleep — Vision API 한도 보호 (default 2s)")
    args = ap.parse_args()

    if args.execute and args.dry_run:
        print("ERROR: --execute 와 --dry-run 동시 지정 불가", file=sys.stderr)
        return 2
    execute = args.execute and not args.dry_run

    supabase = get_supabase_client()
    targets = find_extract_skipped_docs(
        supabase, target_doc_ids=args.doc_id
    )

    if not targets:
        print("백필 대상 0건 — 모든 doc 이 정상 처리됨")
        return 0

    print(f"백필 대상: {len(targets)} 건\n")
    for d in targets:
        flags = d.get("flags") or {}
        reason = flags.get("extract_skipped_reason") or "-"
        print(
            f"  - {d['id']}  [{d['doc_type']:5s}] {d['title'][:40]:42s}  reason: {reason[:50]}"
        )

    if not execute:
        print("\n--dry-run (default). 실제 호출하려면 --execute")
        return 0

    print(f"\n{len(targets)} 건 reingest 시작 (간격 {args.delay_seconds}s)")
    ok = 0
    failed = 0
    for d in targets:
        doc_id = d["id"]
        try:
            res = reingest_via_http(base_url=args.base_url, doc_id=doc_id)
            print(f"  ✓ {doc_id} → job_id={res['job_id']} chunks_deleted={res['chunks_deleted']}")
            ok += 1
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail", str(exc))
            except Exception:
                detail = str(exc)
            print(f"  ✗ {doc_id} → {exc.response.status_code} {detail}")
            failed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {doc_id} → {exc}")
            failed += 1
        time.sleep(args.delay_seconds)

    print(f"\n결과: ok={ok}, failed={failed}")
    print("BG 파이프라인 완료 확인은 /documents/{id}/status 또는 /doc/{id} 에서.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
