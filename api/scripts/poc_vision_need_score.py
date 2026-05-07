"""S1.5 D1 PoC — 로컬 PDF directory 의 페이지별 vision_need_score 분포 측정.

목적 (master plan §6 S1.5 D1)
- Hand-tuned 초기값 (entity regex / table-like / text_density 1e-3) 그대로 적용해
  사용자 11 docs (또는 임의 PDF dir) 의 페이지 점수 **분포** 를 한 번 찍는다.
- 분포가 PoC 산출물 자체. D2/D3 에서 본 값을 보고 임계·가중을 본격 조정.

사용
    cd api && uv run python scripts/poc_vision_need_score.py
        # 기본: 프로젝트 루트의 PDF 11개 + work-log 대상 디렉토리 후보 자동 탐지
    cd api && uv run python scripts/poc_vision_need_score.py \\
        --pdf-dir "../" \\
        --csv "../evals/results/vision_need_score_poc.csv"

의존성: stdlib + PyMuPDF (이미 설치). 외부 API 0, DB 0.
"""

from __future__ import annotations

import argparse
import csv
import logging
import statistics
import sys
from dataclasses import asdict
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
_DEFAULT_CSV = _API_ROOT.parent / "evals" / "results" / "vision_need_score_poc.csv"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="S1.5 D1 vision_need_score PoC — 페이지별 점수 분포 측정",
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
    entity_total = sum(s.entity_hits for s in scores)
    return {
        "doc": doc_name,
        "pages": pages,
        "needs_vision_pages": needs,
        "needs_vision_ratio": (needs / pages) if pages else 0.0,
        "density_p50": _percentile(densities, 50),
        "density_p10": _percentile(densities, 10),
        "table_p90": _percentile(table_scores, 90),
        "entity_hits_total": entity_total,
    }


def _print_distribution_report(
    per_doc: list[dict[str, object]],
    all_scores: list[tuple[str, PageScore]],
) -> None:
    print("\n=== Per-doc summary ===")
    print(
        f"{'doc':<60} {'pages':>5} {'need':>5} {'ratio':>6} "
        f"{'dens_p50':>10} {'dens_p10':>10} {'tbl_p90':>8} {'ent':>4}"
    )
    for row in per_doc:
        print(
            f"{str(row['doc'])[:60]:<60} "
            f"{int(row['pages']):>5} "
            f"{int(row['needs_vision_pages']):>5} "
            f"{float(row['needs_vision_ratio']):>6.2%} "
            f"{float(row['density_p50']):>10.2e} "
            f"{float(row['density_p10']):>10.2e} "
            f"{float(row['table_p90']):>8.2f} "
            f"{int(row['entity_hits_total']):>4}"
        )

    total_pages = len(all_scores)
    total_needs = sum(1 for _, s in all_scores if s.needs_vision)
    densities = [s.text_density for _, s in all_scores]
    tables = [s.table_like_score for _, s in all_scores]

    # 신호 종류별 카운트 (OR 분해 — 어느 신호가 needs_vision 을 끌어올렸는지)
    kind_counts = {"entity": 0, "table_like": 0, "low_density": 0}
    for _, s in all_scores:
        for kind in s.signal_kinds():
            kind_counts[kind] = kind_counts.get(kind, 0) + 1

    print("\n=== Aggregate (all pages) ===")
    print(f"total pages: {total_pages}")
    print(
        f"needs_vision: {total_needs} "
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
    print(f"signal kinds: {kind_counts}")


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    # statistics.quantiles 의 method='inclusive' 가 numpy 의 linear interpolation 과 동치.
    # n=100 으로 100분위 분할 후 1-based index 의 (pct-1) 번째.
    qs = statistics.quantiles(values, n=100, method="inclusive")
    idx = max(0, min(len(qs) - 1, int(pct) - 1))
    return float(qs[idx])


def _write_csv(path: Path, rows: list[tuple[str, PageScore]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "doc",
                "page",
                "text_chars",
                "page_area_pt2",
                "text_density",
                "entity_hits",
                "table_like_score",
                "needs_vision",
                "signal_kinds",
            ],
        )
        writer.writeheader()
        for doc_name, score in rows:
            row = asdict(score)
            row["doc"] = doc_name
            row["signal_kinds"] = "|".join(score.signal_kinds())
            writer.writerow(row)


if __name__ == "__main__":
    raise SystemExit(main())
