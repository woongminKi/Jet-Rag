"""W25 D14+1 (E) — 검색 retrieval 메트릭 측정 + reranker on/off 비교.

골든셋 (`evals/golden_v0.4_sonata.csv`) 의 query / expected_chunk_idx 활용.
in-process 직접 search() 호출 — 단일 process 에서 reranker on/off 두 번 측정.

메트릭:
  - Recall@10 — 정답 chunks 중 top-10 에 잡힌 비율 (chunk-level)
  - MRR — top-10 내 첫 정답 chunk 의 1/rank
  - nDCG@10 — binary relevance 가중 ranking 정확성

전제:
  - `SUPABASE_*` / `HF_API_TOKEN` env 설정
  - sonata catalog (3b901245-...) 적재 완료
  - 실행: `cd api && uv run python ../evals/eval_retrieval_metrics.py`
  - reranker 비교: `--compare-reranker` (default false 만 측정)

산출:
  - stdout markdown table (per-query + 집계)
  - `--output <path>` 시 파일 저장
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import time
import unicodedata
from pathlib import Path

# api/ 를 import path 에 추가 — search() 직접 호출 위해
_API_PATH = Path(__file__).resolve().parents[1] / "api"
sys.path.insert(0, str(_API_PATH))

from app.services.retrieval_metrics import (  # noqa: E402
    aggregate_metrics,
    mrr,
    ndcg_at_k,
    recall_at_k,
)

_GOLDEN_CSV_V04 = Path(__file__).parent / "golden_v0.4_sonata.csv"
_GOLDEN_CSV_V05 = Path(__file__).parent / "golden_v0.5_auto.csv"
_GOLDEN_CSV_V07 = Path(__file__).parent / "golden_v0.7_auto.csv"
_GOLDEN_CSV_V1 = Path(__file__).parent / "golden_v1.csv"
_SONATA_DOC_ID_PREFIX = "3b901245"

# v1·v0.7·v0.5 모두 12·7 컬럼 schema 로 `doc_id` 컬럼 존재 — `_load_golden()` 의
# v0.5 분기가 자동 cover. v0.4 만 legacy `expected_chunk_idx_hints` 분기.
_GOLDEN_FALLBACK_CHAIN: tuple[Path, ...] = (
    _GOLDEN_CSV_V1,
    _GOLDEN_CSV_V07,
    _GOLDEN_CSV_V05,
    _GOLDEN_CSV_V04,
)


def _load_golden(csv_path: Path) -> list[dict]:
    """golden CSV 로드 — v0.4 (sonata, hints 만) / v0.5 (auto) / v0.7 / v1 모두 지원.

    schema auto-detect:
    - v0.5+ / v0.7 / v1: 컬럼 `doc_id` + `relevant_chunks` + `acceptable_chunks` (선택) 존재
    - v0.4: 컬럼 `expected_chunk_idx_hints` 존재 (legacy)
    - v1 의 user row (G-U-***) 는 doc_id 빈 값 → retrieval 평가 불가, skip.
      (사용자 query 의 answer 평가는 `run_v06_user_answer.py` 가 별도 담당)
    """
    out: list[dict] = []
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # v0.5+ schema 우선 (v1 / v0.7 도 동일 — doc_id 컬럼 존재 + 채워짐)
            if "doc_id" in row and row.get("doc_id"):
                relv_str = (row.get("relevant_chunks") or "").strip()
                accept_str = (row.get("acceptable_chunks") or "").strip()
                relevant = {int(x.strip()) for x in relv_str.split(",") if x.strip().isdigit()}
                acceptable = {int(x.strip()) for x in accept_str.split(",") if x.strip().isdigit()}
                if not relevant:
                    continue
                out.append({
                    "id": row["id"],
                    "query": row["query"].strip(),
                    "doc_id": row["doc_id"].strip(),
                    "doc_title": (row.get("doc_title") or "").strip(),
                    "relevant_chunks": relevant,
                    "acceptable_chunks": acceptable,
                })
            else:
                # v0.4 legacy
                chunk_idx_hints = row.get("expected_chunk_idx_hints", "").strip()
                if not chunk_idx_hints:
                    continue
                relevant = {int(x.strip()) for x in chunk_idx_hints.split(",") if x.strip().isdigit()}
                out.append({
                    "id": row["id"],
                    "query": row["query"].strip(),
                    "expected_pages": row.get("expected_pages", "").strip(),
                    "relevant_chunks": relevant,
                    "acceptable_chunks": set(),
                    "answer": row.get("answer", "").strip(),
                })
    return out


def _resolve_sonata_doc_id() -> str | None:
    """sonata catalog 의 full doc_id (UUID) 를 documents 테이블에서 조회.

    title 에 'sonata' 또는 'SONATA' 포함 + doc_type='pdf' + 첫 8자가 _SONATA_DOC_ID_PREFIX.
    """
    from app.config import get_settings  # noqa: E402
    from app.db import get_supabase_client  # noqa: E402

    client = get_supabase_client()
    settings = get_settings()
    # UUID column 은 LIKE 미지원 → 모든 docs fetch 후 client-side prefix 매칭.
    # 사용자 doc 수 < 100건 가정 → 비용 미미.
    resp = (
        client.table("documents")
        .select("id, title")
        .eq("user_id", settings.default_user_id)
        .is_("deleted_at", "null")
        .execute()
    )
    rows = resp.data or []
    for row in rows:
        if row["id"].startswith(_SONATA_DOC_ID_PREFIX):
            return row["id"]
    return None


def _call_search(
    query: str, doc_id: str | None, limit: int = 50
) -> dict:
    """search() 직접 호출. doc_id 명시 시 doc-scope, None 시 multi-doc 검색.

    - doc-scope: _RPC_TOP_K_DOC_FILTER (200) + chunk cap 으로 단일 doc 내 chunks 다수 노출.
    - multi-doc: doc_id 미지정, top-K docs (doc-level ranking) + 각 doc 의 cap 3 chunks.
    """
    from app.routers.search import search  # noqa: E402
    resp = search(
        q=query,
        limit=limit,
        offset=0,
        tags=None,
        doc_type=None,
        from_date=None,
        to_date=None,
        doc_id=doc_id,
        mode="hybrid",
    )
    return resp.model_dump()


def _extract_predicted_chunk_idxs(search_response: dict, doc_id: str) -> list[int]:
    """search 응답에서 doc_id 의 매칭 chunks 의 chunk_idx list (rrf_score 내림차순)."""
    items = search_response.get("items", [])
    for item in items:
        if item.get("doc_id") == doc_id:
            chunks = item.get("matched_chunks", [])
            # doc_id 스코프 시 chunks 가 score 내림차순 정렬됨 (search.py is_doc_scope 분기).
            # backward-safe 위해 rrf_score 명시 정렬.
            sorted_chunks = sorted(
                chunks,
                key=lambda c: c.get("rrf_score") or 0.0,
                reverse=True,
            )
            return [c["chunk_idx"] for c in sorted_chunks]
    return []


def _evaluate_one(query_record: dict, doc_id: str, k: int = 10) -> dict:
    """단일 query → search (doc-scope) → chunk-level 메트릭 (graded relevance 지원)."""
    query = unicodedata.normalize("NFC", query_record["query"])
    relevant = query_record["relevant_chunks"]
    acceptable = query_record.get("acceptable_chunks", set())
    t0 = time.monotonic()
    try:
        resp = _call_search(query, doc_id, limit=50)
    except Exception as exc:  # noqa: BLE001
        return {
            "id": query_record["id"],
            "query": query,
            "error": str(exc),
            "took_ms": int((time.monotonic() - t0) * 1000),
        }
    took_ms = int((time.monotonic() - t0) * 1000)
    predicted = _extract_predicted_chunk_idxs(resp, doc_id)
    return {
        "id": query_record["id"],
        "query": query,
        "relevant_chunks": sorted(relevant),
        "acceptable_chunks": sorted(acceptable),
        "predicted_top10": predicted[:k],
        "took_ms": took_ms,
        "reranker_used": resp.get("query_parsed", {}).get("reranker_used", False),
        "recall_at_10": recall_at_k(predicted, relevant, k=k, acceptable_chunks=acceptable),
        "mrr": mrr(predicted, relevant, k=k, acceptable_chunks=acceptable),
        "ndcg_at_10": ndcg_at_k(predicted, relevant, k=k, acceptable_chunks=acceptable),
    }


def _evaluate_one_multi_doc(
    query_record: dict, expected_doc_id: str, k: int = 10
) -> dict:
    """단일 query → search (multi-doc, doc_id 미지정) → doc-level 메트릭.

    expected_doc_id 가 응답 items 의 몇 위인지 → doc-level top-1 / top-3 / MRR.
    추가로 expected doc 의 matched_chunks (cap 3) 의 chunk-level R@10 도 측정.
    """
    query = unicodedata.normalize("NFC", query_record["query"])
    relevant = query_record["relevant_chunks"]
    t0 = time.monotonic()
    try:
        resp = _call_search(query, doc_id=None, limit=k)
    except Exception as exc:  # noqa: BLE001
        return {
            "id": query_record["id"],
            "query": query,
            "error": str(exc),
            "took_ms": int((time.monotonic() - t0) * 1000),
        }
    took_ms = int((time.monotonic() - t0) * 1000)
    items = resp.get("items", [])
    doc_ids_top_k = [item.get("doc_id") for item in items[:k]]

    # doc-level: expected_doc 가 몇 위?
    try:
        rank = doc_ids_top_k.index(expected_doc_id) + 1
        doc_top1 = rank == 1
        doc_top3 = rank <= 3
        doc_mrr = 1.0 / rank
    except ValueError:
        rank = None
        doc_top1 = False
        doc_top3 = False
        doc_mrr = 0.0

    # chunk-level (expected doc 의 matched_chunks 만 — cap 3 노출)
    chunk_idxs_in_response: list[int] = []
    for item in items:
        if item.get("doc_id") == expected_doc_id:
            for mc in item.get("matched_chunks", []):
                chunk_idxs_in_response.append(mc["chunk_idx"])
            break

    return {
        "id": query_record["id"],
        "query": query,
        "expected_doc_id": expected_doc_id,
        "doc_rank": rank,
        "doc_top1": doc_top1,
        "doc_top3": doc_top3,
        "doc_mrr": doc_mrr,
        "took_ms": took_ms,
        "doc_embedding_rrf_used": resp.get("query_parsed", {}).get("doc_embedding_rrf_used", False),
        "doc_embedding_hits": resp.get("query_parsed", {}).get("doc_embedding_hits", 0),
        "reranker_used": resp.get("query_parsed", {}).get("reranker_used", False),
        # chunk-level (cap 3 chunks 한계 — D1 정정: 명명을 정의에 맞게 수정).
        # 응답의 matched_chunks 는 doc 당 cap 3 노출이라 분모 K 가 클수록 ceiling 0.75.
        # 본 metric 은 "응답에 노출된 chunks 안에서의 recall" 을 의미.
        "predicted_chunks_in_response": chunk_idxs_in_response[:3],
        "chunk_recall_in_response": recall_at_k(chunk_idxs_in_response, relevant, k=3),
    }


def _run_batch(
    golden: list[dict], doc_id: str | None, k: int = 10, label: str = ""
) -> tuple[list[dict], dict]:
    """doc-scope chunk-level metrics. doc_id=None 시 query record 의 per-query doc_id 사용."""
    print(f"[{label}] {len(golden)}건 측정 시작...", file=sys.stderr)
    per_query = []
    for i, q in enumerate(golden, start=1):
        target_doc = doc_id or q.get("doc_id")
        if not target_doc:
            print(f"  [{i}/{len(golden)}] {q['id']} doc_id 없음 — skip", file=sys.stderr)
            continue
        res = _evaluate_one(q, target_doc, k=k)
        per_query.append(res)
        if "error" in res:
            print(f"  [{i}/{len(golden)}] {res['id']} ERROR: {res['error']}", file=sys.stderr)
        else:
            print(
                f"  [{i}/{len(golden)}] {res['id']} R@10={res['recall_at_10']:.3f} "
                f"MRR={res['mrr']:.3f} nDCG@10={res['ndcg_at_10']:.3f} "
                f"({res['took_ms']}ms, reranker={res['reranker_used']})",
                file=sys.stderr,
            )
    successful = [r for r in per_query if "error" not in r]
    agg = aggregate_metrics(successful) if successful else {
        "recall_at_10": 0.0, "mrr": 0.0, "ndcg_at_10": 0.0, "n": 0
    }
    return per_query, agg


def _format_markdown(
    per_query_off: list[dict] | None,
    agg_off: dict | None,
    per_query_on: list[dict] | None,
    agg_on: dict | None,
    doc_id: str,
) -> str:
    lines: list[str] = []
    lines.append("# Retrieval Metrics — Recall@10 / MRR / nDCG@10")
    lines.append("")
    lines.append(f"- 골든셋: `evals/golden_v0.4_sonata.csv` (sonata catalog 10건)")
    lines.append(f"- 대상 doc_id: `{doc_id}`")
    lines.append("")

    # 비교 표
    if per_query_on is not None and agg_on is not None:
        lines.append("## reranker on / off 비교 (집계 평균)")
        lines.append("")
        lines.append("| 메트릭 | reranker OFF | reranker ON | Δ (on - off) |")
        lines.append("|---|---:|---:|---:|")
        for key in ("recall_at_10", "mrr", "ndcg_at_10"):
            v_off = agg_off[key] if agg_off else 0.0
            v_on = agg_on[key]
            delta = v_on - v_off
            sign = "+" if delta >= 0 else ""
            lines.append(f"| {key} | {v_off:.4f} | {v_on:.4f} | {sign}{delta:.4f} |")
        lines.append(f"| n (성공 query) | {agg_off['n'] if agg_off else 0} | {agg_on['n']} | — |")
        lines.append("")

    # 단독 요약
    for label, agg, per_query in (
        ("reranker OFF", agg_off, per_query_off),
        ("reranker ON", agg_on, per_query_on),
    ):
        if agg is None or per_query is None:
            continue
        lines.append(f"## {label} 상세")
        lines.append("")
        lines.append(
            f"- 평균: R@10 {agg['recall_at_10']:.4f} / MRR {agg['mrr']:.4f} / nDCG@10 {agg['ndcg_at_10']:.4f} (n={agg['n']})"
        )
        successful = [r for r in per_query if "error" not in r]
        if successful:
            took = [r["took_ms"] for r in successful]
            lines.append(
                f"- latency: avg {statistics.mean(took):.0f}ms · "
                f"p50 {statistics.median(took):.0f}ms · "
                f"max {max(took):.0f}ms"
            )
        lines.append("")
        lines.append("| id | query | relevant | predicted top-5 | R@10 | MRR | nDCG | ms |")
        lines.append("|---|---|---|---|---:|---:|---:|---:|")
        for r in per_query:
            if "error" in r:
                lines.append(f"| {r['id']} | `{r['query']}` | - | ⚠️ {r['error'][:30]} | - | - | - | {r.get('took_ms',0)} |")
                continue
            relv = ",".join(map(str, r["relevant_chunks"]))
            pred = ",".join(map(str, r["predicted_top10"][:5]))
            lines.append(
                f"| {r['id']} | `{r['query']}` | {relv} | {pred} | "
                f"{r['recall_at_10']:.3f} | {r['mrr']:.3f} | {r['ndcg_at_10']:.3f} | {r['took_ms']} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="검색 retrieval 메트릭 측정")
    parser.add_argument("--output", "-o", help="markdown 출력 경로")
    parser.add_argument(
        "--compare-reranker",
        action="store_true",
        help="reranker on/off 둘 다 측정해 비교 (default: 현재 ENV 만 측정)",
    )
    parser.add_argument(
        "--multi-doc",
        action="store_true",
        help="multi-doc mode — doc_id 미지정 검색에서 expected_doc 의 doc-level top-K 측정",
    )
    parser.add_argument(
        "--compare-doc-embedding",
        action="store_true",
        help="JETRAG_DOC_EMBEDDING_RRF on/off 비교 — multi-doc 시나리오에서 효과 측정",
    )
    parser.add_argument("--k", type=int, default=10, help="top-K (default 10)")
    parser.add_argument(
        "--goldenset", type=str,
        default=None,
        help="골든셋 CSV 경로 (default fallback chain: v1 → v0.7 → v0.5 → v0.4)",
    )
    args = parser.parse_args()

    # golden CSV 결정 — args > v1 > v0.7 > v0.5 > v0.4 (가장 신선한 것 우선)
    if args.goldenset:
        golden_path = Path(args.goldenset)
    else:
        golden_path = next(
            (p for p in _GOLDEN_FALLBACK_CHAIN if p.exists()),
            _GOLDEN_CSV_V04,
        )
    if not golden_path.exists():
        print(f"[ERROR] 골든셋 미발견: {golden_path}", file=sys.stderr)
        return 1
    golden = _load_golden(golden_path)
    if not golden:
        print(f"[ERROR] 골든셋 비어있음", file=sys.stderr)
        return 1
    print(f"[OK] 골든셋 로드: {golden_path.name} ({len(golden)}건)", file=sys.stderr)

    # doc_id 결정 — v0.5+ / v0.7 / v1 은 query 마다 doc_id 다름, v0.4 는 sonata 단일
    is_per_query_doc = bool(golden[0].get("doc_id"))
    if is_per_query_doc:
        doc_id = None  # per-query doc_id 사용
        print(f"[OK] v0.5+ 형식 (per-query doc_id)", file=sys.stderr)
    else:
        doc_id = _resolve_sonata_doc_id()
        if not doc_id:
            print(
                f"[ERROR] sonata catalog 미발견 (prefix={_SONATA_DOC_ID_PREFIX})",
                file=sys.stderr,
            )
            return 2
        print(f"[OK] v0.4 sonata doc_id 조회: {doc_id}", file=sys.stderr)

    per_query_off: list[dict] | None = None
    agg_off: dict | None = None
    per_query_on: list[dict] | None = None
    agg_on: dict | None = None

    if args.multi_doc or args.compare_doc_embedding:
        return _run_multi_doc(golden, doc_id, args)

    if args.compare_reranker:
        # ENV 토글 — 단일 process 에서 두 번 측정.
        # D1 정정 — search.py 가 매 요청 시 env 읽지만 reranker LRU cache 가 OFF run 의 결과를
        # 보존할 수 있어 ON run 시 cache hit 로 OFF 의 score 재사용 위험. cache_clear 강제.
        from app.adapters.impl.bge_reranker_hf import get_reranker_provider
        os.environ["JETRAG_RERANKER_ENABLED"] = "false"
        get_reranker_provider.cache_clear()
        per_query_off, agg_off = _run_batch(golden, doc_id, k=args.k, label="reranker OFF")
        os.environ["JETRAG_RERANKER_ENABLED"] = "true"
        get_reranker_provider.cache_clear()
        per_query_on, agg_on = _run_batch(golden, doc_id, k=args.k, label="reranker ON")
    else:
        current = os.environ.get("JETRAG_RERANKER_ENABLED", "false").lower() == "true"
        label = "reranker ON" if current else "reranker OFF"
        per_query, agg = _run_batch(golden, doc_id, k=args.k, label=label)
        if current:
            per_query_on, agg_on = per_query, agg
        else:
            per_query_off, agg_off = per_query, agg

    md = _format_markdown(per_query_off, agg_off, per_query_on, agg_on, doc_id)
    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"[OK] {args.output}", file=sys.stderr)
    else:
        print(md)
    return 0


def _run_multi_doc_batch(
    golden: list[dict], expected_doc_id: str | None, k: int, label: str
) -> tuple[list[dict], dict]:
    """multi-doc batch. expected_doc_id=None 시 query record 의 per-query doc_id 사용."""
    print(f"[{label}] {len(golden)}건 multi-doc 측정...", file=sys.stderr)
    per_query = []
    for i, q in enumerate(golden, start=1):
        target_doc = expected_doc_id or q.get("doc_id")
        if not target_doc:
            print(f"  [{i}/{len(golden)}] {q['id']} doc_id 없음 — skip", file=sys.stderr)
            continue
        res = _evaluate_one_multi_doc(q, target_doc, k=k)
        per_query.append(res)
        if "error" in res:
            print(f"  [{i}/{len(golden)}] {res['id']} ERROR: {res['error']}", file=sys.stderr)
        else:
            rank_str = f"rank={res['doc_rank']}" if res["doc_rank"] else "rank=N/A"
            print(
                f"  [{i}/{len(golden)}] {res['id']} {rank_str} top1={res['doc_top1']} "
                f"top3={res['doc_top3']} MRR={res['doc_mrr']:.3f} "
                f"({res['took_ms']}ms, doc_emb_rrf={res['doc_embedding_rrf_used']})",
                file=sys.stderr,
            )
    successful = [r for r in per_query if "error" not in r]
    if not successful:
        return per_query, {"top1": 0.0, "top3": 0.0, "doc_mrr": 0.0, "n": 0}
    n = len(successful)
    agg = {
        "top1": sum(1 for r in successful if r["doc_top1"]) / n,
        "top3": sum(1 for r in successful if r["doc_top3"]) / n,
        "doc_mrr": sum(r["doc_mrr"] for r in successful) / n,
        "chunk_recall_in_response": sum(r["chunk_recall_in_response"] for r in successful) / n,
        "n": n,
    }
    return per_query, agg


def _format_multi_doc_md(
    per_query_off: list[dict] | None,
    agg_off: dict | None,
    per_query_on: list[dict] | None,
    agg_on: dict | None,
    doc_id: str,
) -> str:
    lines: list[str] = []
    lines.append("# Multi-doc Retrieval Metrics — doc-level top-1 / top-3 / MRR")
    lines.append("")
    lines.append(f"- 골든셋: `evals/golden_v0.4_sonata.csv` (sonata 10건)")
    lines.append(f"- expected_doc_id: `{doc_id}`")
    lines.append("- 측정: doc_id 미지정 검색 → expected_doc 의 doc-level rank")
    lines.append("")
    if per_query_on is not None and agg_on is not None:
        lines.append("## doc_embedding_rrf on / off 비교")
        lines.append("")
        lines.append("| 메트릭 | OFF | ON | Δ |")
        lines.append("|---|---:|---:|---:|")
        for key, label in (
            ("top1", "doc-level top-1 hit"),
            ("top3", "doc-level top-3 hit"),
            ("doc_mrr", "doc-level MRR"),
            ("chunk_recall_in_response", "chunk-level recall (응답 cap 3 한계)"),
        ):
            v_off = agg_off[key] if agg_off else 0.0
            v_on = agg_on[key]
            delta = v_on - v_off
            sign = "+" if delta >= 0 else ""
            lines.append(f"| {label} | {v_off:.4f} | {v_on:.4f} | {sign}{delta:.4f} |")
        lines.append(f"| n | {agg_off['n'] if agg_off else 0} | {agg_on['n']} | — |")
        lines.append("")

    for label, agg, per_query in (
        ("OFF", agg_off, per_query_off),
        ("ON", agg_on, per_query_on),
    ):
        if agg is None or per_query is None:
            continue
        lines.append(f"## doc_embedding_rrf {label} 상세")
        lines.append("")
        lines.append(
            f"- 평균: top-1 {agg['top1']:.4f} / top-3 {agg['top3']:.4f} / MRR {agg['doc_mrr']:.4f}"
        )
        lines.append("")
        lines.append("| id | query | rank | top1 | top3 | MRR | chunk R@3 | ms |")
        lines.append("|---|---|---:|:---:|:---:|---:|---:|---:|")
        for r in per_query:
            if "error" in r:
                lines.append(f"| {r['id']} | `{r['query']}` | err | - | - | - | - | {r.get('took_ms',0)} |")
                continue
            t1 = "✓" if r["doc_top1"] else "✗"
            t3 = "✓" if r["doc_top3"] else "✗"
            rank_s = str(r["doc_rank"]) if r["doc_rank"] else "—"
            lines.append(
                f"| {r['id']} | `{r['query']}` | {rank_s} | {t1} | {t3} | "
                f"{r['doc_mrr']:.3f} | {r['chunk_recall_in_response']:.3f} | {r['took_ms']} |"
            )
        lines.append("")
    return "\n".join(lines)


def _run_multi_doc(golden: list[dict], doc_id: str, args) -> int:
    if args.compare_doc_embedding:
        # D1 정정 — embedding LRU cache 가 OFF run 의 cosine 계산 결과 보존 가능. 안전하게 비움.
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
        os.environ["JETRAG_DOC_EMBEDDING_RRF"] = "false"
        get_bgem3_provider().clear_embed_cache()
        per_query_off, agg_off = _run_multi_doc_batch(
            golden, doc_id, args.k, "doc_embedding_rrf OFF"
        )
        os.environ["JETRAG_DOC_EMBEDDING_RRF"] = "true"
        get_bgem3_provider().clear_embed_cache()
        per_query_on, agg_on = _run_multi_doc_batch(
            golden, doc_id, args.k, "doc_embedding_rrf ON"
        )
    else:
        current = (
            os.environ.get("JETRAG_DOC_EMBEDDING_RRF", "false").lower() == "true"
        )
        label = "doc_embedding_rrf ON" if current else "doc_embedding_rrf OFF"
        per_query, agg = _run_multi_doc_batch(golden, doc_id, args.k, label)
        if current:
            per_query_off, agg_off = None, None
            per_query_on, agg_on = per_query, agg
        else:
            per_query_off, agg_off = per_query, agg
            per_query_on, agg_on = None, None

    md = _format_multi_doc_md(per_query_off, agg_off, per_query_on, agg_on, doc_id)
    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"[OK] {args.output}", file=sys.stderr)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
