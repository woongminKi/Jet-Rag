"""S1.5 D2 분석 — D1 PoC CSV 분포 + 임계 조정안 + vision_diagram 정확도.

목적 (master plan §6 S1.5 D2)
- D1 의 `evals/results/vision_need_score_poc.csv` 를 stdlib 만으로 로드해 분포
  분석을 수행한다. **운영 코드 변경 0**, 의존성 추가 0 — 분석만.
- 산출:
  1. per-doc 분포 (페이지·needs 비율·신호별 percentile)
  2. 전체 페이지 분포 (115 페이지 score percentile / histogram)
  3. 신호 간 Pearson 상관 계수 (3 신호 × 3 신호) — 6 신호 중 D1 CSV 에 있는
     3 신호 (text_density / entity_hits / table_like_score) 한정.
  4. 임계 후보 시뮬레이션 — needs_vision 비율 30/50/70% target 시 임계 후보,
     각 임계별 신호 트리거 분포.
  5. 골든셋 v1 의 vision_diagram + table_lookup 매칭 페이지 정확도.

CSV detail 산출 — `evals/results/vision_need_score_d2_analysis.csv`:
- per-page row 에 score (composite) + 임계별 needs_vision 추가.

사용
    cd api && uv run python scripts/analyze_vision_need_score.py
        # default: D1 CSV + 골든셋 v1 자동 로드
    cd api && uv run python scripts/analyze_vision_need_score.py \\
        --poc-csv "../evals/results/vision_need_score_poc.csv" \\
        --golden  "../evals/golden_v1.csv" \\
        --out     "../evals/results/vision_need_score_d2_analysis.csv"

의존성: stdlib 만 (csv / statistics / math). pandas/numpy 추가 회피 (CLAUDE.md 정합).
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _API_ROOT.parent

logger = logging.getLogger(__name__)

# 기본 입력·출력 경로
_DEFAULT_POC_CSV = _REPO_ROOT / "evals" / "results" / "vision_need_score_poc.csv"
_DEFAULT_GOLDEN = _REPO_ROOT / "evals" / "golden_v1.csv"
_DEFAULT_OUT_CSV = (
    _REPO_ROOT / "evals" / "results" / "vision_need_score_d2_analysis.csv"
)

# 임계 후보 (D1 default = 0.5 가설을 D2 에서 분포 base 로 검증)
_SCORE_THRESHOLDS = (0.3, 0.5, 0.7)
# density 임계 후보 — D1 default 1e-3
_DENSITY_THRESHOLDS = (5e-4, 1e-3, 2e-3)
# table_like 임계 후보 — D1 default 0.5
_TABLE_THRESHOLDS = (0.3, 0.5, 0.7)
# 분포 percentile
_PERCENTILES = (10, 25, 50, 75, 90)
# histogram bins
_HIST_BINS = 10


@dataclass(frozen=True)
class PageRow:
    """D1 CSV 의 페이지별 row 구조체 (분석 전용)."""

    doc: str
    page: int
    text_chars: int
    page_area_pt2: float
    text_density: float
    entity_hits: int
    table_like_score: float
    needs_vision: bool
    signal_kinds: list[str]


@dataclass(frozen=True)
class GoldenRow:
    """골든셋 v1 의 분석 대상 row (vision_diagram / table_lookup)."""

    qid: str
    query: str
    query_type: str
    expected_doc_title: str
    source_hint: str  # 예: "p.6", "p.40 근처", ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="S1.5 D2 — vision_need_score PoC 분포 분석 + 임계 조정",
    )
    parser.add_argument("--poc-csv", type=Path, default=_DEFAULT_POC_CSV)
    parser.add_argument("--golden", type=Path, default=_DEFAULT_GOLDEN)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT_CSV)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    poc_csv: Path = args.poc_csv.resolve()
    if not poc_csv.exists():
        logger.error("PoC CSV 없음 — D1 ship 산출물 필요: %s", poc_csv)
        return 1

    rows = _load_poc_rows(poc_csv)
    logger.info("PoC CSV 로드 — %d 페이지 / %d docs", len(rows), len({r.doc for r in rows}))

    _print_per_doc_summary(rows)
    _print_aggregate_distribution(rows)
    _print_signal_correlation(rows)
    threshold_table = _print_threshold_simulation(rows)
    _print_signal_threshold_simulation(rows)

    # 골든셋 정확도 — 파일 없으면 skip (CLAUDE.md 가드: 가능한 경우만)
    golden_path: Path = args.golden.resolve()
    if golden_path.exists():
        golden_rows = _load_golden_vision_related(golden_path)
        _print_golden_accuracy(rows, golden_rows)
    else:
        logger.warning("골든셋 v1 없음 — vision_diagram 정확도 측정 skip: %s", golden_path)

    out_path: Path = args.out.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_detail_csv(out_path, rows, threshold_table)
    logger.info("분석 detail CSV 저장 — %s", out_path)
    return 0


def _load_poc_rows(path: Path) -> list[PageRow]:
    """D1 CSV 를 PageRow 로 파싱."""
    rows: list[PageRow] = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            kinds = [k for k in (r.get("signal_kinds") or "").split("|") if k]
            rows.append(
                PageRow(
                    doc=r["doc"],
                    page=int(r["page"]),
                    text_chars=int(r["text_chars"]),
                    page_area_pt2=float(r["page_area_pt2"]),
                    text_density=float(r["text_density"]),
                    entity_hits=int(r["entity_hits"]),
                    table_like_score=float(r["table_like_score"]),
                    needs_vision=r["needs_vision"].lower() == "true",
                    signal_kinds=kinds,
                )
            )
    return rows


def _load_golden_vision_related(path: Path) -> list[GoldenRow]:
    """골든셋 v1 에서 vision_diagram + table_lookup row 만 추출."""
    rows: list[GoldenRow] = []
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        # BOM 처리 — 첫 column 이 '\ufeffid' 로 들어올 수 있음
        id_key = "id"
        if reader.fieldnames and reader.fieldnames[0].endswith("id"):
            id_key = reader.fieldnames[0]
        for r in reader:
            qt = r.get("query_type", "")
            if qt not in ("vision_diagram", "table_lookup"):
                continue
            rows.append(
                GoldenRow(
                    qid=r.get(id_key, ""),
                    query=r.get("query", ""),
                    query_type=qt,
                    expected_doc_title=r.get("expected_doc_title", ""),
                    source_hint=r.get("source_hint", ""),
                )
            )
    return rows


# --- composite score (D2 분석 전용 합산식) ---------------------------------


def _composite_score(row: PageRow) -> float:
    """3 신호를 [0, 1] 가중 합산해 composite score 산출.

    D1 PoC 는 needs_vision boolean 만 산출해 score 분포가 직접 없음. D2 에선
    분포 분석을 위해 3 신호를 normalize + 가중 합산 (등가중 1/3) 으로 score 를
    산출한다. D3 에서 가중 조정.

    - density_signal = max(0, 1 - density / 2e-3)  (2e-3 이상이면 0)
    - table_signal   = clamp(table_like_score, 0, 1)
    - entity_signal  = 1.0 if entity_hits > 0 else 0.0
    """
    density_signal = max(0.0, min(1.0, 1.0 - row.text_density / 2e-3))
    table_signal = max(0.0, min(1.0, row.table_like_score))
    entity_signal = 1.0 if row.entity_hits > 0 else 0.0
    return (density_signal + table_signal + entity_signal) / 3.0


# --- per-doc / aggregate distribution -------------------------------------


def _print_per_doc_summary(rows: list[PageRow]) -> None:
    print("\n=== Per-doc distribution (composite score) ===")
    print(
        f"{'doc':<58} {'pages':>5} {'need%':>6} "
        f"{'sc_p50':>7} {'sc_p90':>7} {'sc_max':>7} "
        f"{'dens_p50':>10} {'tbl_p90':>8} {'ent_sum':>7}"
    )
    by_doc: dict[str, list[PageRow]] = {}
    for r in rows:
        by_doc.setdefault(r.doc, []).append(r)
    for doc, doc_rows in sorted(by_doc.items()):
        scores = [_composite_score(r) for r in doc_rows]
        densities = [r.text_density for r in doc_rows]
        tables = [r.table_like_score for r in doc_rows]
        entities = sum(r.entity_hits for r in doc_rows)
        needs = sum(1 for r in doc_rows if r.needs_vision)
        print(
            f"{doc[:58]:<58} {len(doc_rows):>5} "
            f"{needs / len(doc_rows):>6.1%} "
            f"{_pct(scores, 50):>7.2f} "
            f"{_pct(scores, 90):>7.2f} "
            f"{max(scores):>7.2f} "
            f"{_pct(densities, 50):>10.2e} "
            f"{_pct(tables, 90):>8.2f} "
            f"{entities:>7d}"
        )


def _print_aggregate_distribution(rows: list[PageRow]) -> None:
    scores = [_composite_score(r) for r in rows]
    densities = [r.text_density for r in rows]
    tables = [r.table_like_score for r in rows]
    entities = [r.entity_hits for r in rows]

    print("\n=== Aggregate distribution (115 pages) ===")
    print(f"composite score: {_format_percentiles(scores)}")
    print(f"  histogram: {_histogram_str(scores, 0.0, 1.0, _HIST_BINS)}")
    print(f"text_density:    {_format_percentiles(densities, fmt='{:.2e}')}")
    print(f"table_like:      {_format_percentiles(tables)}")
    print(f"entity_hits:     {_format_percentiles([float(e) for e in entities])}")


def _print_signal_correlation(rows: list[PageRow]) -> None:
    """3 신호 간 Pearson 상관 행렬 — 가중치 중복 진단."""
    signals = {
        "density(neg)": [-r.text_density for r in rows],  # 부호 반전 (낮을수록 vision)
        "table_like": [r.table_like_score for r in rows],
        "entity": [float(r.entity_hits) for r in rows],
    }
    keys = list(signals.keys())
    print("\n=== Signal Pearson correlation (3x3) ===")
    print("              " + "".join(f"{k:>14}" for k in keys))
    for k1 in keys:
        line = f"{k1:<14}"
        for k2 in keys:
            r = _pearson(signals[k1], signals[k2])
            line += f"{r:>14.3f}"
        print(line)
    print(
        "  주: density 는 'lower → needs_vision' 이라 부호 반전해 표기. "
        "절대값 ≥ 0.5 면 신호 중복 의심."
    )


def _print_threshold_simulation(rows: list[PageRow]) -> dict[float, int]:
    """composite score 임계 후보별 needs_vision 비율 시뮬레이션."""
    scores = [(r, _composite_score(r)) for r in rows]
    print("\n=== Threshold simulation (composite score) ===")
    print(f"{'threshold':>10} {'needs_n':>9} {'needs%':>8}")
    counts: dict[float, int] = {}
    for thr in _SCORE_THRESHOLDS:
        n = sum(1 for _, s in scores if s >= thr)
        counts[thr] = n
        print(f"{thr:>10.2f} {n:>9d} {n / len(scores):>8.1%}")

    # target 비율 역산 — needs_vision 30/50/70% 일 때 임계
    print("\n  target 비율 역산 — composite score 임계:")
    sorted_scores = sorted((s for _, s in scores), reverse=True)
    n_total = len(sorted_scores)
    for ratio in (0.3, 0.5, 0.7):
        idx = max(0, min(n_total - 1, int(round(ratio * n_total)) - 1))
        thr = sorted_scores[idx]
        print(f"    needs_vision = {ratio:.0%}  →  threshold ≈ {thr:.3f}")
    return counts


def _print_signal_threshold_simulation(rows: list[PageRow]) -> None:
    """density / table_like 임계 후보별 트리거 비율 시뮬레이션."""
    print("\n=== Density threshold simulation ===")
    print(f"{'density<':>10} {'pages':>6} {'%':>6}")
    for thr in _DENSITY_THRESHOLDS:
        n = sum(1 for r in rows if r.page_area_pt2 > 0 and r.text_density < thr)
        print(f"{thr:>10.0e} {n:>6d} {n / len(rows):>6.1%}")

    print("\n=== Table-like threshold simulation ===")
    print(f"{'table≥':>10} {'pages':>6} {'%':>6}")
    for thr in _TABLE_THRESHOLDS:
        n = sum(1 for r in rows if r.table_like_score >= thr)
        print(f"{thr:>10.2f} {n:>6d} {n / len(rows):>6.1%}")

    # sonata + portfolio doc 한정 density 분포 — D1 senior-developer 관찰 (b) 검증
    sonata = [r for r in rows if "sonata" in r.doc.lower()]
    portfolio = [r for r in rows if "포트폴리오" in r.doc]
    if sonata:
        n_low = sum(1 for r in sonata if r.text_density < 1e-3)
        print(
            f"\n  sonata catalog ({len(sonata)} pages) "
            f"density<1e-3: {n_low}/{len(sonata)} ({n_low / len(sonata):.0%})"
        )
    if portfolio:
        n_low = sum(1 for r in portfolio if r.text_density < 1e-3)
        print(
            f"  포트폴리오 ({len(portfolio)} pages) "
            f"density<1e-3: {n_low}/{len(portfolio)} ({n_low / len(portfolio):.0%})"
        )


# --- golden v1 정확도 (vision_diagram + table_lookup) ----------------------


def _print_golden_accuracy(
    page_rows: list[PageRow], golden_rows: list[GoldenRow]
) -> None:
    """골든셋 v1 vision_diagram + table_lookup 매칭 페이지 정확도.

    매칭 정책:
    - expected_doc_title 의 substring 으로 D1 CSV 의 doc 파일명 매칭
    - source_hint 에서 'p.{N}' 패턴으로 페이지 추출 (예: "p.6", "p.40 근처")
    - 매칭된 페이지의 needs_vision (D1 default) + composite score 측정
    """
    print("\n=== Golden v1 정확도 (vision_diagram + table_lookup) ===")
    print(
        f"{'qid':<10} {'type':<14} {'doc match':<32} {'page':>5} "
        f"{'in_PoC':>7} {'needs':>6} {'score':>6}"
    )

    n_total = 0
    n_in_poc = 0
    n_needs = 0
    n_pass_score: dict[float, int] = {thr: 0 for thr in _SCORE_THRESHOLDS}

    for g in golden_rows:
        page = _extract_page_hint(g.source_hint)
        match = _match_doc(g.expected_doc_title, page_rows)
        if match is None:
            print(
                f"{g.qid:<10} {g.query_type:<14} {g.expected_doc_title[:32]:<32} "
                f"{(page or '-'):>5} {'OUT':>7} {'-':>6} {'-':>6}"
            )
            continue

        n_total += 1
        if page is None:
            # page hint 없으면 doc 의 needs_vision 비율로 대체
            doc_pages = [r for r in page_rows if r.doc == match]
            doc_needs = sum(1 for r in doc_pages if r.needs_vision)
            avg_score = statistics.mean(_composite_score(r) for r in doc_pages)
            ratio = doc_needs / len(doc_pages)
            n_needs += 1 if ratio >= 0.5 else 0
            n_in_poc += 1
            for thr in _SCORE_THRESHOLDS:
                if avg_score >= thr:
                    n_pass_score[thr] += 1
            print(
                f"{g.qid:<10} {g.query_type:<14} {match[:32]:<32} "
                f"{'AVG':>5} {'IN':>7} "
                f"{ratio:>6.1%} {avg_score:>6.2f}"
            )
            continue

        target = next(
            (r for r in page_rows if r.doc == match and r.page == page), None
        )
        if target is None:
            print(
                f"{g.qid:<10} {g.query_type:<14} {match[:32]:<32} "
                f"{page:>5} {'OOB':>7} {'-':>6} {'-':>6}"
            )
            continue
        n_in_poc += 1
        score = _composite_score(target)
        if target.needs_vision:
            n_needs += 1
        for thr in _SCORE_THRESHOLDS:
            if score >= thr:
                n_pass_score[thr] += 1
        print(
            f"{g.qid:<10} {g.query_type:<14} {match[:32]:<32} "
            f"{page:>5} {'IN':>7} "
            f"{('Y' if target.needs_vision else 'N'):>6} {score:>6.2f}"
        )

    if n_in_poc == 0:
        print("\n  매칭된 페이지 없음 — 정확도 측정 불가")
        return

    print(
        f"\n  매칭 N={n_in_poc}/{n_total} | "
        f"needs_vision (D1 default OR rule) recall = "
        f"{n_needs}/{n_in_poc} = {n_needs / n_in_poc:.1%}"
    )
    print("  composite score 임계별 recall (TP / 매칭 N):")
    for thr, n in n_pass_score.items():
        print(f"    score ≥ {thr:.1f}  →  {n}/{n_in_poc} = {n / n_in_poc:.1%}")
    print(
        "  주: ground-truth = vision_diagram + table_lookup → '본래 vision 후보' 가정. "
        "N 매우 작음 (≤5) — 통계적 신뢰도 낮음, 정성 분석 우선."
    )


def _extract_page_hint(hint: str) -> int | None:
    """source_hint 에서 'p.{N}' 패턴 추출 (예: 'p.6 근처', 'p.40' → N)."""
    import re

    m = re.search(r"p\.?\s*(\d+)", hint or "")
    return int(m.group(1)) if m else None


def _match_doc(title: str, page_rows: list[PageRow]) -> str | None:
    """expected_doc_title 의 substring 으로 D1 CSV doc 파일명 매칭.

    macOS 파일명은 NFD (자모 분리), 골든셋은 NFC (조합형) 가 일반적이라
    Unicode 정규화 후 비교한다. 정규화 미수행 시 한국어 row 가 모두 OUT 처리됨.
    파일명에 `.pdf` 확장자가 붙어 있어 substring 검색이 자연스럽다.
    """
    import unicodedata as ud

    if not title:
        return None
    title_n = ud.normalize("NFC", title)
    candidates = {r.doc for r in page_rows}
    # 정확 매칭 우선 — 부분 일치는 첫 token 기준
    for doc in candidates:
        if title_n in ud.normalize("NFC", doc):
            return doc
    # 더 짧은 prefix 로 fallback (예: "(붙임2) 2025년 데이터센터..." vs "2025년 데이터센터...")
    title_core = title_n.split("(", 1)[0].strip() or title_n
    for doc in candidates:
        if title_core and title_core in ud.normalize("NFC", doc):
            return doc
    # 마지막 fallback — 골든셋 truncation 등으로 substring 이 어긋난 경우
    # title 의 첫 N=10 글자 prefix 로 doc 매칭 (한국어 noise 단어 회피용 길이)
    prefix = title_n[:10]
    if len(prefix) >= 6:
        for doc in candidates:
            if prefix in ud.normalize("NFC", doc):
                return doc
    return None


# --- helpers -------------------------------------------------------------


def _pct(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    qs = statistics.quantiles(values, n=100, method="inclusive")
    idx = max(0, min(len(qs) - 1, int(pct) - 1))
    return float(qs[idx])


def _format_percentiles(values: list[float], fmt: str = "{:.3f}") -> str:
    parts = [f"p{p}={fmt.format(_pct(values, p))}" for p in _PERCENTILES]
    return " ".join(parts) + f" max={fmt.format(max(values) if values else 0.0)}"


def _histogram_str(
    values: list[float], lo: float, hi: float, bins: int
) -> str:
    if not values:
        return "(empty)"
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        idx = min(bins - 1, max(0, int((v - lo) / width)))
        counts[idx] += 1
    return " | ".join(
        f"[{lo + i * width:.2f}-{lo + (i + 1) * width:.2f}]={c}"
        for i, c in enumerate(counts)
    )


def _pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson 상관 — stdlib 만. xs, ys 동일 길이 가정."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denom = math.sqrt(sx * sy)
    return 0.0 if denom == 0.0 else sxy / denom


def _write_detail_csv(
    path: Path, rows: list[PageRow], _threshold_table: dict[float, int]
) -> None:
    """per-page detail + composite score + 임계별 needs_vision."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "doc",
                "page",
                "text_chars",
                "text_density",
                "entity_hits",
                "table_like_score",
                "needs_vision_d1",
                "signal_kinds",
                "composite_score",
                *(f"needs@{thr:.1f}" for thr in _SCORE_THRESHOLDS),
            ]
        )
        for r in rows:
            score = _composite_score(r)
            writer.writerow(
                [
                    r.doc,
                    r.page,
                    r.text_chars,
                    f"{r.text_density:.6e}",
                    r.entity_hits,
                    f"{r.table_like_score:.4f}",
                    r.needs_vision,
                    "|".join(r.signal_kinds),
                    f"{score:.4f}",
                    *(score >= thr for thr in _SCORE_THRESHOLDS),
                ]
            )


if __name__ == "__main__":
    sys.exit(main())
