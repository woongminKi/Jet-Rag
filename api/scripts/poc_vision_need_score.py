"""S1.5 D3 — vision_need_score PoC 스크립트 v2 (master plan §6 S1.5).

D1 (3 신호) → D3 (6 신호 + composite + OR rule trigger flag).

D3 변경 (work-log 2026-05-07 S1.5 D3 §3)
- 측정 신호 6종 — D1 의 text_density / entity_hits / table_like_score 에 더해
  image_area_ratio / text_quality / caption_score 추가.
- needs_vision_or — D3 OR rule (entity 제외, table 0.3, image_area / text_quality /
  caption 추가) 산출.
- composite_score — 가중 합산 (D3 default weights, entity 0).
- trigger_* 컬럼 5개 — 각 OR rule 신호별 boolean.
- D1 CSV 와 호환 — 기존 컬럼 (doc / page / text_chars / page_area_pt2 / text_density
  / entity_hits / table_like_score / needs_vision / signal_kinds) 보존.

사용
    cd api && uv run python scripts/poc_vision_need_score.py
        # 기본: 프로젝트 루트의 PDF 자동 탐지 → CSV 출력
    cd api && uv run python scripts/poc_vision_need_score.py \\
        --pdf-dir "../" \\
        --csv "../evals/results/vision_need_score_d3.csv"

의존성: stdlib + PyMuPDF (이미 설치). 외부 API 0, DB 0.
"""

from __future__ import annotations

import argparse
import csv
import logging
import statistics
import sys
from pathlib import Path

import fitz  # PyMuPDF — 운영 코드 (pymupdf_parser.py) 와 동일 패키지

_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.services.vision_need_score import PageScore, score_page  # noqa: E402

logger = logging.getLogger(__name__)

# 기본 PDF dir — 본 프로젝트 루트 (사용자 자료 11 docs 가 위치).
_DEFAULT_PDF_DIR = _API_ROOT.parent
# 기본 CSV 출력 — evals/results/ (gitignore `*` + `!.gitignore` 정책으로 자동 제외).
# D3 ship 시 D1 CSV 는 보존 (D2 분석 스크립트와 호환), v2 결과는 별 파일에 저장.
_DEFAULT_CSV = _API_ROOT.parent / "evals" / "results" / "vision_need_score_d3.csv"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="S1.5 D3 vision_need_score PoC v2 — 6 신호 + OR rule + composite",
    )
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=_DEFAULT_PDF_DIR,
        help=f"스캔할 PDF directory (default: {_DEFAULT_PDF_DIR})",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=_DEFAULT_CSV,
        help=f"페이지별 raw 결과 CSV 저장 경로 (default: {_DEFAULT_CSV})",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="하위 디렉토리도 재귀 스캔 (default: 직접 디렉토리만)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    pdf_dir: Path = args.pdf_dir.resolve()
    if not pdf_dir.is_dir():
        logger.error("pdf-dir 가 디렉토리가 아닙니다: %s", pdf_dir)
        return 1

    pdfs = _discover_pdfs(pdf_dir, recursive=args.recursive)
    if not pdfs:
        logger.error("PDF 파일을 찾지 못했습니다: %s", pdf_dir)
        return 1

    logger.info("PDF %d 개 발견 — %s", len(pdfs), pdf_dir)
    all_scores: list[tuple[str, PageScore]] = []
    per_doc_summary: list[dict[str, object]] = []

    for pdf_path in pdfs:
        try:
            scores = _score_pdf(pdf_path)
        except Exception as exc:  # noqa: BLE001 — 파일 단위 부분 실패 허용
            logger.warning("PDF 처리 실패 — %s: %s", pdf_path.name, exc)
            continue
        all_scores.extend((pdf_path.name, s) for s in scores)
        per_doc_summary.append(_summarize_doc(pdf_path.name, scores))

    if not all_scores:
        logger.error("점수 산출된 페이지가 0 — 처리 가능한 PDF 없음")
        return 1

    _print_distribution_report(per_doc_summary, all_scores)

    csv_path: Path = args.csv.resolve()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(csv_path, all_scores)
    logger.info("페이지별 CSV 저장: %s", csv_path)
    return 0


def _discover_pdfs(root: Path, *, recursive: bool) -> list[Path]:
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted(p for p in root.glob(pattern) if p.is_file())


def _score_pdf(pdf_path: Path) -> list[PageScore]:
    """단일 PDF 의 모든 페이지를 score_page 로 채점."""
    scores: list[PageScore] = []
    with fitz.open(str(pdf_path)) as doc:
        for page_num, page in enumerate(doc, start=1):
            try:
                page_dict = page.get_text("dict")
            except Exception as exc:  # noqa: BLE001 — 페이지 단위 부분 실패 허용
                logger.warning(
                    "page %d dict 추출 실패 (%s): %s",
                    page_num,
                    pdf_path.name,
                    exc,
                )
                continue
            page_area = float(page.rect.width) * float(page.rect.height)
            scores.append(
                score_page(page_dict, page_num=page_num, page_area_pt2=page_area)
            )
    return scores


def _summarize_doc(
    doc_name: str, scores: list[PageScore]
) -> dict[str, object]:
    pages = len(scores)
    needs = sum(1 for s in scores if s.needs_vision)
    densities = [s.text_density for s in scores]
    table_scores = [s.table_like_score for s in scores]
    image_ratios = [s.image_area_ratio for s in scores]
    qualities = [s.text_quality for s in scores]
    captions = [s.caption_score for s in scores]
    composites = [s.composite_score for s in scores]
    entity_total = sum(s.entity_hits for s in scores)
    return {
        "doc": doc_name,
        "pages": pages,
        "needs_vision_pages": needs,
        "needs_vision_ratio": (needs / pages) if pages else 0.0,
        "density_p50": _percentile(densities, 50),
        "density_p10": _percentile(densities, 10),
        "table_p90": _percentile(table_scores, 90),
        "image_p90": _percentile(image_ratios, 90),
        "quality_p10": _percentile(qualities, 10),
        "caption_p90": _percentile(captions, 90),
        "composite_p90": _percentile(composites, 90),
        "composite_max": max(composites) if composites else 0.0,
        "entity_hits_total": entity_total,
    }


def _print_distribution_report(
    per_doc: list[dict[str, object]],
    all_scores: list[tuple[str, PageScore]],
) -> None:
    print("\n=== Per-doc summary (D3 v2 — 6 signals) ===")
    print(
        f"{'doc':<58} {'pages':>5} {'need%':>6} "
        f"{'dens_p50':>10} {'tbl_p90':>8} {'img_p90':>8} {'qual_p10':>9} "
        f"{'cap_p90':>8} {'sc_p90':>7} {'sc_max':>7}"
    )
    for row in per_doc:
        print(
            f"{str(row['doc'])[:58]:<58} "
            f"{int(row['pages']):>5} "
            f"{float(row['needs_vision_ratio']):>6.1%} "
            f"{float(row['density_p50']):>10.2e} "
            f"{float(row['table_p90']):>8.2f} "
            f"{float(row['image_p90']):>8.2f} "
            f"{float(row['quality_p10']):>9.2f} "
            f"{float(row['caption_p90']):>8.2f} "
            f"{float(row['composite_p90']):>7.2f} "
            f"{float(row['composite_max']):>7.2f}"
        )

    total_pages = len(all_scores)
    total_needs = sum(1 for _, s in all_scores if s.needs_vision)
    densities = [s.text_density for _, s in all_scores]
    tables = [s.table_like_score for _, s in all_scores]
    images = [s.image_area_ratio for _, s in all_scores]
    qualities = [s.text_quality for _, s in all_scores]
    captions = [s.caption_score for _, s in all_scores]
    composites = [s.composite_score for _, s in all_scores]

    # OR rule trigger 분해 — 어느 신호가 needs_vision 을 끌어올렸는가
    trigger_counts = {
        "low_density": 0,
        "table_like": 0,
        "image_area": 0,
        "text_quality_low": 0,
        "caption": 0,
    }
    for _, s in all_scores:
        for trig in s.triggers:
            trigger_counts[trig] = trigger_counts.get(trig, 0) + 1

    print("\n=== Aggregate (all pages) ===")
    print(f"total pages: {total_pages}")
    print(
        f"needs_vision (D3 OR rule): {total_needs} "
        f"({total_needs / total_pages:.1%})"
    )
    print(
        f"density: p10={_percentile(densities, 10):.2e}, "
        f"p50={_percentile(densities, 50):.2e}, "
        f"p90={_percentile(densities, 90):.2e}"
    )
    print(
        f"table_score: p50={_percentile(tables, 50):.2f}, "
        f"p90={_percentile(tables, 90):.2f}, "
        f"max={max(tables):.2f}"
    )
    print(
        f"image_area: p50={_percentile(images, 50):.2f}, "
        f"p90={_percentile(images, 90):.2f}, "
        f"max={max(images):.2f}"
    )
    print(
        f"text_quality: p10={_percentile(qualities, 10):.2f}, "
        f"p50={_percentile(qualities, 50):.2f}, "
        f"min={min(qualities):.2f}"
    )
    print(
        f"caption_score: p50={_percentile(captions, 50):.2f}, "
        f"p90={_percentile(captions, 90):.2f}, "
        f"max={max(captions):.2f}"
    )
    print(
        f"composite_score: p50={_percentile(composites, 50):.2f}, "
        f"p90={_percentile(composites, 90):.2f}, "
        f"max={max(composites):.2f}"
    )
    print(f"trigger counts (D3 OR rule): {trigger_counts}")


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    qs = statistics.quantiles(values, n=100, method="inclusive")
    idx = max(0, min(len(qs) - 1, int(pct) - 1))
    return float(qs[idx])


# CSV 컬럼 — D1 호환 (앞 9개) + D3 신규 (10개). D2 분석 스크립트는 D1 컬럼만 읽음.
_CSV_COLUMNS = (
    # D1 호환 컬럼 (D2 분석 스크립트가 읽는 컬럼)
    "doc",
    "page",
    "text_chars",
    "page_area_pt2",
    "text_density",
    "entity_hits",
    "table_like_score",
    "needs_vision",
    "signal_kinds",
    # D3 신규 — 6 신호 중 추가 3종
    "image_area_ratio",
    "text_quality",
    "caption_score",
    # D3 신규 — composite + OR rule trigger flag
    "composite_score",
    "trigger_density",
    "trigger_table",
    "trigger_image",
    "trigger_quality",
    "trigger_caption",
)


def _write_csv(path: Path, rows: list[tuple[str, PageScore]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(_CSV_COLUMNS))
        writer.writeheader()
        for doc_name, score in rows:
            triggers = set(score.triggers)
            writer.writerow(
                {
                    "doc": doc_name,
                    "page": score.page,
                    "text_chars": score.text_chars,
                    "page_area_pt2": score.page_area_pt2,
                    "text_density": score.text_density,
                    "entity_hits": score.entity_hits,
                    "table_like_score": score.table_like_score,
                    "needs_vision": score.needs_vision,
                    "signal_kinds": "|".join(score.triggers),
                    "image_area_ratio": score.image_area_ratio,
                    "text_quality": score.text_quality,
                    "caption_score": score.caption_score,
                    "composite_score": score.composite_score,
                    "trigger_density": "low_density" in triggers,
                    "trigger_table": "table_like" in triggers,
                    "trigger_image": "image_area" in triggers,
                    "trigger_quality": "text_quality_low" in triggers,
                    "trigger_caption": "caption" in triggers,
                }
            )


if __name__ == "__main__":
    raise SystemExit(main())
