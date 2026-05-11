"""Phase 4 D5 — 6 vision-enrich PDF full reingest 실행.

목적:
1. v2 prompt 결과 + S4-A D2 chunk.text 합성을 chunks DB 에 통합 반영
2. vision_page_cache hit 으로 vision API 호출 0 (사전 점검 결과)
3. chunks 재구성으로 retrieval 회귀 측정 가능

흐름 (router 의 POST /documents/{id}/reingest 를 in-process 로 재현):
  - documents 의 doc_type=pdf 확인
  - _reset_doc_for_reingest 호출 → chunks 전부 삭제
  - flags.ingest_mode='default' 유지
  - run_pipeline 동기 호출 (BackgroundTasks 없이)

ENV 임시 설정 (이 process 안에서만):
  - JETRAG_DOC_BUDGET_USD=0.15 (per-doc cap 상향)
  - JETRAG_VISION_PROMPT_VERSION=v2 (cache key)
  - JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS=2 (default 유지)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# ENV 임시 설정 — 모듈 import 전에 설정해야 lru_cache 가 정확히 반영
os.environ.setdefault("JETRAG_DOC_BUDGET_USD", "0.15")
os.environ.setdefault("JETRAG_VISION_PROMPT_VERSION", "v2")
os.environ.setdefault("JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS", "2")

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "api"))

# noqa: E402 — sys.path 확정 후 import
from app.db.client import get_supabase_client  # noqa: E402
from app.ingest.jobs import create_job  # noqa: E402
from app.ingest.pipeline import run_pipeline  # noqa: E402

_TARGET_DOCS = [
    ("9878d7bd-4766-40fa-bebb-7da45f879768", "포트폴리오"),  # 15p (작은 것부터)
    ("51dd2cc0-4105-43c4-9585-6026a012a9cc", "기웅민 이력서"),  # 2p
    ("92c38315-659e-42f4-9043-8cec1d62823a", "sonata"),  # 27p
    ("2303ef36-7502-407a-8a88-3c0dc63c8464", "보건의료"),  # 26p
    ("b218e8a1-cb35-4540-b969-f0f4fac517fa", "데이터센터"),  # 41p
    ("d1259dfe-c402-4cd0-bb04-3e67d88a2773", "sample-report"),  # 93p (가장 큼, 마지막)
]


def _reset_doc_chunks(supabase, doc_id: str) -> int:
    """documents/_reset_doc_for_reingest 와 동일 동작.

    flags.ingest_mode 보존 + chunks 전체 삭제 + 재계산 필드 reset.
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
    # default mode 강제 (의뢰서 §Step 2)
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
    print(f"[INFO] ENV 임시 설정")
    print(f"  JETRAG_DOC_BUDGET_USD = {os.environ.get('JETRAG_DOC_BUDGET_USD')}")
    print(f"  JETRAG_VISION_PROMPT_VERSION = {os.environ.get('JETRAG_VISION_PROMPT_VERSION')}")
    print(f"  JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS = {os.environ.get('JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS')}")

    client = get_supabase_client()
    results: list[dict] = []

    start_total = time.time()
    for doc_id, label in _TARGET_DOCS:
        print()
        print("=" * 72)
        print(f"[Reingest] {label} (doc_id={doc_id})")
        print("=" * 72)

        # 1. doc_type 확인
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

        # 2. chunks reset
        t0 = time.time()
        deleted = _reset_doc_chunks(client, doc_id)
        print(f"  [reset] chunks_deleted={deleted}  ({time.time()-t0:.2f}s)")

        # 3. job 생성 + pipeline 동기 실행
        job = create_job(doc_id=doc_id)
        print(f"  [job] id={job.id}")

        t1 = time.time()
        try:
            run_pipeline(job.id, doc_id, page_cap_override=None)
            elapsed = time.time() - t1
            print(f"  [pipeline] OK ({elapsed:.1f}s)")

            # 4. post chunks 확인
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
            print(f"  [post] chunks={n_chunks} vision_pages={len(vision_pages)}")
            results.append({
                "label": label,
                "doc_id": doc_id,
                "deleted": deleted,
                "new_chunks": n_chunks,
                "vision_pages": len(vision_pages),
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

    total_elapsed = time.time() - start_total
    print()
    print("=" * 72)
    print(f"[SUMMARY] total elapsed = {total_elapsed:.1f}s ({total_elapsed/60:.1f}m)")
    print("=" * 72)
    for r in results:
        print(f"  {r['label']:20s} {r['status']:6s} "
              f"deleted={r.get('deleted', '-'):>4} "
              f"new={r.get('new_chunks', '-'):>4} "
              f"vision_pg={r.get('vision_pages', '-'):>4} "
              f"({r['elapsed_s']:.1f}s)")
    return 0 if all(r.get("status") == "OK" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
