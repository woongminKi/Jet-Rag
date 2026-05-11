"""D5 chunks 회귀 -185 복구 (옵션 A) — 3 doc 한정 reingest.

배경:
    2026-05-11 통합 sprint Phase 4 D5 에서 sonata / 데이터센터 / sample-report
    3 doc 이 doc-level cost cap ($0.15) 초과로 vision_enrich 차단되어
    chunks 105건 (vision-derived) 이 적재되지 못함. 결과:
        - chunks DB: 1923/2463 → 2278 (-185 회귀)
        - documents.flags.vision_budget_exceeded=True 마킹된 상태
        - 전체 R@10: 0.6368 → 0.6049 (-0.0319pp 임계 초과)

복구 전제 (통합 핸드오프 §5.1):
    - vision_page_cache v2 row 148건 보존 → 3 doc 의 v2 결과 cache hit 100%
    - 따라서 vision API 호출 0, 추가 cost $0 기대
    - budget_guard 우회 ENV (`JETRAG_BUDGET_GUARD_DISABLE=1`) 만 process scope 로 설정

본 스크립트 = `_phase4_d5_reingest.py` 의 3 doc 한정 버전.
운영 코드 변경 0 — pipeline / extract / budget_guard 모두 그대로.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# ENV 임시 설정 — import 전 setdefault 로 lru_cache 정확 반영
# (실행 시 외부에서 export 한 값 우선)
os.environ.setdefault("JETRAG_BUDGET_GUARD_DISABLE", "1")
os.environ.setdefault("JETRAG_DOC_BUDGET_USD", "0.15")
os.environ.setdefault("JETRAG_VISION_PROMPT_VERSION", "v2")
os.environ.setdefault("JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS", "2")

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "api"))

# noqa: E402 — sys.path 확정 후 import
from app.db.client import get_supabase_client  # noqa: E402
from app.ingest.jobs import create_job  # noqa: E402
from app.ingest.pipeline import run_pipeline  # noqa: E402

# 3 doc 한정 — 회귀가 발생한 sonata / 데이터센터 / sample-report 만.
# 순서: 작은 것 → 큰 것 (실패 시 가장 비용 큰 마지막을 보존).
_TARGET_DOCS = [
    ("92c38315-659e-42f4-9043-8cec1d62823a", "sonata"),        # 27p
    ("b218e8a1-cb35-4540-b969-f0f4fac517fa", "데이터센터"),     # 41p
    ("d1259dfe-c402-4cd0-bb04-3e67d88a2773", "sample-report"),  # 93p
]


def _reset_doc_chunks(supabase, doc_id: str) -> int:
    """documents/_reset_doc_for_reingest 와 동일 동작 (원본 도구와 동일).

    flags.ingest_mode 보존 + chunks 전체 삭제 + 재계산 필드 reset.
    `vision_budget_exceeded` 는 재진행 시 budget_guard 가 다시 판단하므로 명시 reset 불필요
    (회복 후 chunks 정상 적재되면 의미상 자동 해소).
    """
    chunks_count_resp = (
        supabase.table("chunks")
        .select("id", count="exact")
        .eq("doc_id", doc_id)
        .execute()
    )
    chunks_deleted = chunks_count_resp.count or 0
    if chunks_deleted > 0:
        supabase.table("chunks").delete().eq("doc_id", doc_id).execute()

    existing = (
        supabase.table("documents")
        .select("flags")
        .eq("id", doc_id)
        .limit(1)
        .execute()
    )
    existing_flags = dict((existing.data or [{}])[0].get("flags") or {})
    preserved_flags: dict = {}
    if "ingest_mode" in existing_flags:
        preserved_flags["ingest_mode"] = existing_flags["ingest_mode"]
    preserved_flags.setdefault("ingest_mode", "default")

    supabase.table("documents").update(
        {
            "tags": [],
            "summary": None,
            "flags": preserved_flags,
            "doc_embedding": None,
        }
    ).eq("id", doc_id).execute()
    return chunks_deleted


def main() -> int:
    print(f"[INFO] ENV (process scope only — .env 변경 X)")
    print(f"  JETRAG_BUDGET_GUARD_DISABLE = {os.environ.get('JETRAG_BUDGET_GUARD_DISABLE')}")
    print(f"  JETRAG_DOC_BUDGET_USD = {os.environ.get('JETRAG_DOC_BUDGET_USD')}")
    print(f"  JETRAG_VISION_PROMPT_VERSION = {os.environ.get('JETRAG_VISION_PROMPT_VERSION')}")
    print(f"  JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS = {os.environ.get('JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS')}")

    client = get_supabase_client()

    # 사전 cost 스냅샷 (회복 후 차분 측정)
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    pre = client.table("vision_usage_log").select("estimated_cost, success").gte(
        "called_at", cutoff
    ).eq("success", True).execute()
    pre_cost = sum(float(r.get("estimated_cost") or 0) for r in (pre.data or []))
    pre_rows = len(pre.data or [])
    print(f"  pre-snapshot vision_usage_log 24h: cost=${pre_cost:.4f} rows={pre_rows}")
    print()

    results: list[dict] = []
    start_total = time.time()
    for doc_id, label in _TARGET_DOCS:
        print("=" * 72)
        print(f"[Reingest] {label} (doc_id={doc_id})")
        print("=" * 72)

        doc_row = (
            client.table("documents")
            .select("doc_type, storage_path, sha256, title")
            .eq("id", doc_id)
            .limit(1)
            .execute()
            .data
        )
        if not doc_row:
            print(f"[ERROR] doc_id={doc_id} not found")
            continue
        doc = doc_row[0]
        if doc["doc_type"] != "pdf":
            print(f"[SKIP] doc_type={doc['doc_type']} (PDF 아님)")
            continue
        print(f"  title={doc.get('title')} storage_path={doc['storage_path']}")

        t0 = time.time()
        deleted = _reset_doc_chunks(client, doc_id)
        print(f"  [reset] chunks_deleted={deleted}  ({time.time()-t0:.2f}s)")

        job = create_job(doc_id=doc_id)
        print(f"  [job] id={job.id}")

        t1 = time.time()
        try:
            run_pipeline(job.id, doc_id, page_cap_override=None)
            elapsed = time.time() - t1
            print(f"  [pipeline] OK ({elapsed:.1f}s)")

            new_chunks = (
                client.table("chunks")
                .select("id, page, section_title", count="exact")
                .eq("doc_id", doc_id)
                .execute()
            )
            n_chunks = new_chunks.count or 0
            vision_pages = {
                int(r["page"]) for r in (new_chunks.data or [])
                if (r.get("section_title") or "").startswith("(vision) p.")
                and r.get("page") is not None
            }
            # 회복 후 flag 상태
            after_flags = (
                client.table("documents")
                .select("flags")
                .eq("id", doc_id)
                .limit(1)
                .execute()
                .data
            )
            ve = bool(((after_flags or [{}])[0].get("flags") or {}).get(
                "vision_budget_exceeded"
            ))
            print(
                f"  [post] chunks={n_chunks} vision_pages={len(vision_pages)} "
                f"vision_budget_exceeded={ve}"
            )
            results.append({
                "label": label,
                "doc_id": doc_id,
                "deleted": deleted,
                "new_chunks": n_chunks,
                "vision_pages": len(vision_pages),
                "vision_budget_exceeded": ve,
                "elapsed_s": round(elapsed, 1),
                "status": "OK",
            })
        except Exception as exc:  # noqa: BLE001
            elapsed = time.time() - t1
            print(f"  [pipeline] FAIL ({elapsed:.1f}s): {exc}")
            results.append({
                "label": label,
                "doc_id": doc_id,
                "deleted": deleted,
                "status": "FAIL",
                "error": str(exc)[:200],
                "elapsed_s": round(elapsed, 1),
            })
        print()

    total_elapsed = time.time() - start_total

    # 사후 cost 스냅샷 (cache hit 시 차분 = 0)
    post = client.table("vision_usage_log").select("estimated_cost, success").gte(
        "called_at", cutoff
    ).eq("success", True).execute()
    post_cost = sum(float(r.get("estimated_cost") or 0) for r in (post.data or []))
    post_rows = len(post.data or [])

    print("=" * 72)
    print(f"[SUMMARY] total elapsed = {total_elapsed:.1f}s ({total_elapsed/60:.1f}m)")
    print("=" * 72)
    for r in results:
        print(
            f"  {r['label']:15s} {r['status']:6s} "
            f"deleted={r.get('deleted', '-'):>4} "
            f"new={r.get('new_chunks', '-'):>4} "
            f"vision_pg={r.get('vision_pages', '-'):>4} "
            f"ve={r.get('vision_budget_exceeded', '-')} "
            f"({r['elapsed_s']:.1f}s)"
        )
    print()
    cost_delta = post_cost - pre_cost
    print(
        f"[COST] pre=${pre_cost:.4f}({pre_rows} rows) "
        f"post=${post_cost:.4f}({post_rows} rows) "
        f"delta=${cost_delta:.4f}({post_rows-pre_rows} rows)"
    )
    if cost_delta > 0.05:
        print(f"[WARN] cost delta ${cost_delta:.4f} > $0.05 — 의뢰서 안전 가드 임계 초과")
        return 2

    return 0 if all(r.get("status") == "OK" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
