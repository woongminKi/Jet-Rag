"""KPI #8 출처 일치율 — 50건 검수 표 자동 생성 (PRD §1.5 #8 정합).

목적
- golden_v2 에서 stratified 50건 sample (qtype 비례 + relevant_chunks 있는 row 우선,
  out_of_scope 제외).
- 각 row: /search hybrid mode 호출 → top-1 chunk_idx + page 추출.
- relevant_chunks 의 chunk_idx → chunks DB 에서 page 조회.
- 자동 1차 채점: predicted top-1 page ∈ relevant pages → match_auto=1, else 0.
- 사용자 수동 최종 검수용 CSV 출력 (match_manual 컬럼 공백 — 사용자가 채움).

PRD §1.5 #8 채점 기준
- predicted top-1 chunk 의 page 가 golden 의 정답 page (relevant_chunks 가 가리키는
  chunk 의 page) 와 일치 = 1, 불일치 = 0.
- 일치율 = Σ match / 50. 게이트 ≥ 0.95.

설계
- paid 호출 0 (search API + Supabase read only).
- 멱등 — 동일 seed 로 같은 sample.
- N<50 fallback — eligible row 부족 시 가능한 만큼 출력 + 사유 기록.

CLI
    JETRAG_API_BASE_URL=http://localhost:8001 \
      uv run python evals/sample_source_match.py \
        --out evals/results/m3_kpi8_source_match_audit_50_$(date +%Y%m%d_%H%M%S).csv \
        --seed 42 --n 50
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import random
import sys
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_API_PATH = _REPO_ROOT / "api"
if (_API_PATH / "app").exists() and str(_API_PATH) not in sys.path:
    sys.path.insert(0, str(_API_PATH))

_API_BASE = os.environ.get("JETRAG_API_BASE_URL", "http://localhost:8001").rstrip("/")
_GOLDEN_CSV = _REPO_ROOT / "evals" / "golden_v2.csv"

# qtype 9종 중 측정 부적합 2종 제외:
# - out_of_scope: 정답 doc 없음
# - cross_doc: multi-doc 라 "top-1 chunk page = expected page" 채점 fit 안 됨
#   (PRD §1.5 #8 채점 기준이 single-doc top-1 가정)
_QTYPE_ELIGIBLE = (
    "exact_fact",
    "fuzzy_memory",
    "table_lookup",
    "numeric_lookup",
    "synonym_mismatch",
    "vision_diagram",
    "summary",
)

logger = logging.getLogger("sample_source_match")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def load_golden(path: Path) -> list[dict]:
    """utf-8-sig BOM 처리 + NFC 정규화."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if r.get("query"):
            r["query"] = unicodedata.normalize("NFC", r["query"])
    return rows


def parse_chunk_ids(s: str) -> list[int]:
    """'43,390,58' → [43, 390, 58]. 빈 셀 → []."""
    s = (s or "").strip()
    if not s:
        return []
    return [int(t.strip()) for t in s.split(",") if t.strip().isdigit()]


def stratified_sample(rows: list[dict], n: int, seed: int) -> list[dict]:
    """qtype 비례 + relevant_chunks 있는 row 우선 sampling.

    1. out_of_scope 제외
    2. relevant_chunks empty row 후순위 (eligible 부족 시만 채택)
    3. qtype 별 비례 할당 (round-up 으로 최소 1 보장)
    """
    rng = random.Random(seed)
    by_qtype_primary: dict[str, list[dict]] = defaultdict(list)
    by_qtype_fallback: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        qt = (r.get("query_type") or "").strip()
        if qt not in _QTYPE_ELIGIBLE:
            continue
        if parse_chunk_ids(r.get("relevant_chunks") or ""):
            by_qtype_primary[qt].append(r)
        else:
            by_qtype_fallback[qt].append(r)

    total_primary = sum(len(v) for v in by_qtype_primary.values())
    logger.info("eligible primary (relevant_chunks 있음) = %d", total_primary)
    for qt in _QTYPE_ELIGIBLE:
        logger.info(
            "  qtype=%s primary=%d fallback=%d",
            qt,
            len(by_qtype_primary[qt]),
            len(by_qtype_fallback[qt]),
        )

    # 비례 할당 (primary 기준)
    quota: dict[str, int] = {}
    if total_primary == 0:
        return []
    for qt in _QTYPE_ELIGIBLE:
        share = len(by_qtype_primary[qt]) / total_primary
        quota[qt] = max(1, round(share * n)) if by_qtype_primary[qt] else 0

    # 합계 조정 — round-up 누적으로 n 초과 가능
    while sum(quota.values()) > n:
        # 가장 많이 할당된 qtype 1 감소
        qt_max = max(quota, key=lambda k: quota[k])
        if quota[qt_max] <= 1:
            break
        quota[qt_max] -= 1
    while sum(quota.values()) < n:
        # 가장 적게 할당된 + 표본 여유 있는 qtype 1 증가
        candidates = [
            qt
            for qt in _QTYPE_ELIGIBLE
            if quota[qt] < len(by_qtype_primary[qt])
        ]
        if not candidates:
            break
        qt_min = min(candidates, key=lambda k: quota[k])
        quota[qt_min] += 1

    logger.info("quota (sum=%d) = %s", sum(quota.values()), dict(quota))

    sampled: list[dict] = []
    for qt, q in quota.items():
        pool = by_qtype_primary[qt][:]
        rng.shuffle(pool)
        take = pool[:q]
        sampled.extend(take)
        # 풀 부족 시 fallback 으로 보충
        if len(take) < q:
            need = q - len(take)
            fb = by_qtype_fallback[qt][:]
            rng.shuffle(fb)
            sampled.extend(fb[:need])

    return sampled


def http_get_json(url: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        import json

        return json.loads(resp.read().decode("utf-8"))


def call_search(query: str, mode: str = "hybrid", limit: int = 10) -> dict | None:
    """hybrid /search 호출. predicted top-1 chunk_idx + page 추출."""
    qs = urllib.parse.urlencode({"q": query, "mode": mode, "limit": limit})
    url = f"{_API_BASE}/search?{qs}"
    try:
        return http_get_json(url, timeout=30.0)
    except Exception as e:
        logger.warning("/search fail: %s — %s", query[:30], e)
        return None


def extract_top1(resp: dict | None) -> tuple[str | None, int | None, int | None, str]:
    """response → (top1_doc_id, top1_chunk_idx, top1_page, top1_text_snippet).

    top1 = items 중 첫 doc 의 matched_chunks[0] 으로 정의 (search 의 doc grouping
    응답 형식 정합 — relevance 정렬 순서).
    """
    if not resp:
        return None, None, None, ""
    items = resp.get("items") or []
    if not items:
        return None, None, None, ""
    first_doc = items[0]
    doc_id = first_doc.get("doc_id")
    matched = first_doc.get("matched_chunks") or []
    if not matched:
        return doc_id, None, None, ""
    top = matched[0]
    return (
        doc_id,
        top.get("chunk_idx"),
        top.get("page"),
        (top.get("text") or "")[:100],
    )


def fetch_chunk_pages(supabase, doc_id: str, chunk_idxs: list[int]) -> dict[int, int | None]:
    """chunks DB 에서 (doc_id, chunk_idx) → page 매핑."""
    if not chunk_idxs:
        return {}
    resp = (
        supabase.table("chunks")
        .select("chunk_idx,page")
        .eq("doc_id", doc_id)
        .in_("chunk_idx", chunk_idxs)
        .execute()
    )
    return {r["chunk_idx"]: r.get("page") for r in (resp.data or [])}


def judge_match(
    predicted_doc_id: str | None,
    predicted_page: int | None,
    expected_doc_id: str,
    expected_pages: set[int],
) -> tuple[int, str]:
    """자동 1차 채점.

    - doc_id mismatch → 0 ("doc mismatch")
    - predicted_page is None → 0 ("predicted page None")
    - expected_pages 비어있음 → -1 ("no expected page label — manual only")
    - predicted_page ∈ expected_pages → 1
    - else → 0
    """
    if predicted_doc_id != expected_doc_id:
        return 0, "doc mismatch"
    if predicted_page is None:
        return 0, "predicted page None"
    if not expected_pages:
        return -1, "no expected page label"
    if predicted_page in expected_pages:
        return 1, "page match"
    return 0, f"page mismatch (predicted={predicted_page}, expected={sorted(expected_pages)})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KPI #8 source match audit sampling")
    parser.add_argument("--out", required=True, type=Path, help="output CSV path")
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--mode", choices=("hybrid", "dense", "sparse"), default="hybrid"
    )
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)

    # supabase client — .env 로드
    env_path = _REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    from supabase import create_client

    sb = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )

    # 1) golden 로드 + sampling
    golden = load_golden(_GOLDEN_CSV)
    logger.info("golden_v2 로드: %d row", len(golden))
    sampled = stratified_sample(golden, args.n, args.seed)
    logger.info("sample 추출: %d row", len(sampled))

    # 2) 각 row 처리
    out_rows: list[dict] = []
    for i, row in enumerate(sampled, 1):
        qid = row["id"]
        query = row["query"]
        qtype = row["query_type"]
        expected_doc_id = row["doc_id"]
        relevant_idxs = parse_chunk_ids(row.get("relevant_chunks") or "")
        acceptable_idxs = parse_chunk_ids(row.get("acceptable_chunks") or "")

        # /search top-1
        resp = call_search(query, mode=args.mode, limit=args.limit)
        pred_doc, pred_idx, pred_page, pred_snippet = extract_top1(resp)

        # expected page 조회 — relevant_chunks chunk_idx → page
        all_expected_idxs = sorted(set(relevant_idxs + acceptable_idxs))
        idx_to_page = fetch_chunk_pages(sb, expected_doc_id, all_expected_idxs)
        relevant_pages = {idx_to_page.get(idx) for idx in relevant_idxs}
        relevant_pages.discard(None)
        acceptable_pages = {idx_to_page.get(idx) for idx in acceptable_idxs}
        acceptable_pages.discard(None)
        expected_pages = relevant_pages | acceptable_pages

        match_auto, note = judge_match(
            pred_doc, pred_page, expected_doc_id, expected_pages
        )

        out_rows.append(
            {
                "no": i,
                "query_id": qid,
                "qtype": qtype,
                "query": query,
                "expected_doc_id": expected_doc_id[:8],
                "expected_doc_title": row.get("expected_doc_title", ""),
                "relevant_chunks": ",".join(map(str, relevant_idxs)),
                "relevant_pages": ",".join(map(str, sorted(relevant_pages))),
                "acceptable_chunks": ",".join(map(str, acceptable_idxs)),
                "acceptable_pages": ",".join(map(str, sorted(acceptable_pages))),
                "source_hint": row.get("source_hint", ""),
                "predicted_doc_id": (pred_doc or "")[:8],
                "predicted_top1_chunk_idx": pred_idx if pred_idx is not None else "",
                "predicted_page": pred_page if pred_page is not None else "",
                "predicted_snippet": pred_snippet.replace("\n", " "),
                "match_auto": match_auto,
                "auto_note": note,
                "match_manual": "",  # 사용자 수동 검수 컬럼
                "manual_note": "",
            }
        )

        if i % 10 == 0:
            logger.info(
                "진행 %d/%d (last qid=%s auto=%s)",
                i,
                len(sampled),
                qid,
                match_auto,
            )

    # 3) 1차 자동 집계
    n_total = len(out_rows)
    n_auto_1 = sum(1 for r in out_rows if r["match_auto"] == 1)
    n_auto_0 = sum(1 for r in out_rows if r["match_auto"] == 0)
    n_auto_unmeasurable = sum(1 for r in out_rows if r["match_auto"] == -1)
    n_measurable = n_auto_1 + n_auto_0
    auto_rate = (n_auto_1 / n_measurable) if n_measurable else 0.0

    # qtype 분포
    qtype_dist = Counter(r["qtype"] for r in out_rows)

    # 4) CSV 출력
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(out_rows[0].keys()) if out_rows else []
    with args.out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out_rows)

    # 5) 보고
    print()
    print("=" * 60)
    print(f"KPI #8 출처 일치율 — 1차 자동 채점")
    print("=" * 60)
    print(f"  표본 N: {n_total}")
    print(f"  qtype 분포: {dict(qtype_dist)}")
    print(f"  match_auto=1 : {n_auto_1}")
    print(f"  match_auto=0 : {n_auto_0}")
    print(f"  unmeasurable (no expected page label): {n_auto_unmeasurable}")
    print(f"  자동 일치율: {auto_rate:.4f} ({n_auto_1}/{n_measurable})")
    print(f"  ※ 게이트: ≥ 0.95 (사용자 수동 최종 검수 후 확정)")
    print()
    print(f"CSV 저장: {args.out}")
    print(f"수동 검수 가이드: match_manual 컬럼 채우기 (1=일치, 0=불일치, -1=측정불가)")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
