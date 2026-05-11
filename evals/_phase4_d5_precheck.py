"""Phase 4 D5 사전 점검 — read-only.

1) vision_page_cache PK / 인덱스 구조
2) per-doc page count / cost estimate (vision_usage_log SUM)
3) 24h sliding budget 사용량 (daily cap 70% 임계)
4) 대상 6 doc 의 chunks count / vision pages count 스냅샷
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "api"))

from app.db.client import get_supabase_client  # noqa: E402

_TARGET_DOCS = [
    ("d1259dfe-c402-4cd0-bb04-3e67d88a2773", "sample-report"),
    ("b218e8a1-cb35-4540-b969-f0f4fac517fa", "데이터센터"),
    ("92c38315-659e-42f4-9043-8cec1d62823a", "sonata"),
    ("2303ef36-7502-407a-8a88-3c0dc63c8464", "보건의료"),
    ("9878d7bd-4766-40fa-bebb-7da45f879768", "포트폴리오"),
    ("51dd2cc0-4105-43c4-9585-6026a012a9cc", "기웅민 이력서"),
]

_DAILY_CAP_USD = 0.50  # 의뢰서 명시
_PER_DOC_CAP_USD = 0.10  # default — 의뢰서: 0.15 로 상향 예정
_VISION_ENRICH_TITLE_PREFIX = "(vision) p."


def _print_block(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def main() -> int:
    client = get_supabase_client()

    # 1) vision_page_cache 상태
    _print_block("1) vision_page_cache row 분포")
    resp = (
        client.table("vision_page_cache")
        .select("sha256, page, prompt_version")
        .limit(2000)
        .execute()
    )
    cache_rows = resp.data or []
    total = len(cache_rows)
    by_pv: dict[str, int] = {}
    by_sha_pv: dict[tuple[str, str], int] = {}
    for r in cache_rows:
        pv = r.get("prompt_version") or "?"
        by_pv[pv] = by_pv.get(pv, 0) + 1
        key = (r.get("sha256") or "?", pv)
        by_sha_pv[key] = by_sha_pv.get(key, 0) + 1
    print(f"total rows = {total}")
    for pv, n in sorted(by_pv.items()):
        print(f"  prompt_version={pv}: {n}")
    # sha-level 분포 (어느 doc 이 v1/v2 가지고 있는지)
    print("  --- sha × prompt_version (top) ---")
    for (sha, pv), n in sorted(by_sha_pv.items(), key=lambda x: -x[1])[:20]:
        print(f"    {sha[:16]}...  pv={pv}  pages={n}")

    # 2) 대상 doc sha256 / page count
    _print_block("2) 대상 doc sha256 / page count")
    docs = (
        client.table("documents")
        .select("id, sha256, title, doc_type, flags")
        .in_("id", [d for d, _ in _TARGET_DOCS])
        .execute()
        .data or []
    )
    doc_by_id = {d["id"]: d for d in docs}
    sha_by_id: dict[str, str] = {}
    for doc_id, label in _TARGET_DOCS:
        d = doc_by_id.get(doc_id)
        if not d:
            print(f"  {label}: MISSING from documents")
            continue
        sha = d.get("sha256") or ""
        sha_by_id[doc_id] = sha
        flags = d.get("flags") or {}
        print(
            f"  {label}: id={doc_id} sha={sha[:16]}... type={d.get('doc_type')} "
            f"vision_budget_exceeded={flags.get('vision_budget_exceeded', False)} "
            f"ingest_mode={flags.get('ingest_mode')}"
        )

    # 2b) per-doc cache rows by prompt_version
    _print_block("2b) 대상 doc per-prompt_version cache rows")
    for doc_id, label in _TARGET_DOCS:
        sha = sha_by_id.get(doc_id)
        if not sha:
            continue
        rows = (
            client.table("vision_page_cache")
            .select("page, prompt_version")
            .eq("sha256", sha)
            .execute()
            .data or []
        )
        v1 = sorted(r["page"] for r in rows if r["prompt_version"] == "v1")
        v2 = sorted(r["page"] for r in rows if r["prompt_version"] == "v2")
        print(
            f"  {label}: total={len(rows)} v1={len(v1)} v2={len(v2)} "
            f"v1_pages={v1[:10]}{'...' if len(v1) > 10 else ''} "
            f"v2_pages_head={v2[:5]}"
        )

    # 3) 24h sliding cost (vision_usage_log)
    _print_block("3) 24h sliding cost (전체 + per-doc)")
    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    log_rows = (
        client.table("vision_usage_log")
        .select("doc_id, estimated_cost, called_at")
        .gte("called_at", cutoff_24h)
        .execute()
        .data or []
    )
    total_24h = sum(float(r.get("estimated_cost") or 0) for r in log_rows)
    by_doc: dict[str, float] = {}
    for r in log_rows:
        did = r.get("doc_id") or ""
        by_doc[did] = by_doc.get(did, 0) + float(r.get("estimated_cost") or 0)
    print(f"  24h total cost = ${total_24h:.4f}  ({len(log_rows)} rows)")
    print(f"  daily cap = ${_DAILY_CAP_USD:.4f}  used% = {total_24h / _DAILY_CAP_USD * 100:.1f}%")
    print(f"  remaining = ${_DAILY_CAP_USD - total_24h:.4f}")
    for doc_id, label in _TARGET_DOCS:
        cost = by_doc.get(doc_id, 0)
        print(f"    {label}: $ {cost:.4f}")

    # 4) per-doc historical vision call & avg cost (전체 기간)
    _print_block("4) per-doc 누적 vision call & 평균 cost (전체 기간)")
    for doc_id, label in _TARGET_DOCS:
        rows = (
            client.table("vision_usage_log")
            .select("page, estimated_cost")
            .eq("doc_id", doc_id)
            .execute()
            .data or []
        )
        if not rows:
            print(f"  {label}: no vision_usage_log rows")
            continue
        n_calls = len(rows)
        pages_called = sorted({int(r["page"]) for r in rows if r.get("page") is not None})
        total_cost = sum(float(r.get("estimated_cost") or 0) for r in rows)
        avg = total_cost / n_calls if n_calls else 0
        print(
            f"  {label}: calls={n_calls} distinct_pages={len(pages_called)} "
            f"sum_cost=$ {total_cost:.4f} avg=$ {avg:.4f}"
        )

    # 5) 대상 doc 의 chunks / vision pages 스냅샷 (post-reingest diff 비교용)
    _print_block("5) 대상 doc chunks 스냅샷 (pre-reingest baseline)")
    for doc_id, label in _TARGET_DOCS:
        rows = (
            client.table("chunks")
            .select("chunk_idx, page, section_title")
            .eq("doc_id", doc_id)
            .order("chunk_idx", desc=False)
            .limit(2000)
            .execute()
            .data or []
        )
        total = len(rows)
        vision_pages = {
            int(r["page"]) for r in rows
            if (r.get("section_title") or "").startswith(_VISION_ENRICH_TITLE_PREFIX)
            and r.get("page") is not None
        }
        all_pages = {int(r["page"]) for r in rows if r.get("page") is not None}
        print(
            f"  {label}: chunks={total} vision_pages={len(vision_pages)} "
            f"distinct_pages={len(all_pages)}"
        )

    # 6) cost estimate — reingest 시 실제 vision API 호출이 일어날 페이지 수
    _print_block("6) Reingest 예상 vision API 호출")
    print(
        "  설정: PROMPT_VERSION=v2, full reingest path.\n"
        "  full reingest 는 PDF 전체 페이지 vision 호출을 시도하지만, vision_page_cache 의 \n"
        "  (sha256, page, v2) hit 시 호출 0. v2 미스 페이지만 실 호출."
    )
    avg_cost_per_call = 0.014  # sample-report 8 호출 $0.111 ≒ $0.014/call
    total_estimate = 0.0
    for doc_id, label in _TARGET_DOCS:
        sha = sha_by_id.get(doc_id)
        if not sha:
            continue
        v2_rows = (
            client.table("vision_page_cache")
            .select("page")
            .eq("sha256", sha)
            .eq("prompt_version", "v2")
            .execute()
            .data or []
        )
        v2_pages = {int(r["page"]) for r in v2_rows}

        # 현재 chunks 의 distinct page 중 vision_pages 가 PDF vision 호출 대상 후보
        chunk_rows = (
            client.table("chunks")
            .select("page, section_title")
            .eq("doc_id", doc_id)
            .execute()
            .data or []
        )
        existing_vision_pages = {
            int(r["page"]) for r in chunk_rows
            if (r.get("section_title") or "").startswith(_VISION_ENRICH_TITLE_PREFIX)
            and r.get("page") is not None
        }
        # full reingest 는 PDF 모든 페이지 시도 — 정확한 cost 는 PDF 페이지 수 / vision_need_score 결과에 따라 다름
        # 보수적 추정: 기존 chunks 의 vision page 수 가 vision 호출 대상의 lower bound
        likely_calls_pages = sorted(existing_vision_pages - v2_pages)
        est_cost = len(likely_calls_pages) * avg_cost_per_call
        total_estimate += est_cost
        print(
            f"  {label}: prior_vision_pages={len(existing_vision_pages)} v2_cache_pages={len(v2_pages)} "
            f"likely_new_calls={len(likely_calls_pages)} est_cost=$ {est_cost:.4f}"
        )
        if likely_calls_pages and len(likely_calls_pages) <= 15:
            print(f"    pages: {likely_calls_pages}")
    print(f"\n  TOTAL estimated cost = $ {total_estimate:.4f}")
    print(f"  per-doc cap (default $ {_PER_DOC_CAP_USD:.2f}, 의뢰서 상향 $0.15)")
    print(f"  24h sliding remaining = $ {_DAILY_CAP_USD - total_24h:.4f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
