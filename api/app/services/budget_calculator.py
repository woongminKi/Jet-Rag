"""S0 D3 (2026-05-07) — vision_usage_log 기반 budget 초기값 계산.

master plan §6 S0 D3 + §7.5 공식 그대로 구현:

    doc_budget_usd  = avg_cost_per_page × avg_pages_per_doc × 0.5 × 1.5
    daily_budget_usd = doc_budget_usd × daily_docs

- 0.5 = "50% 페이지만 vision 적용" 가정 (페이지 선별 PoC 가 S2 에서 본격화 — §6 참조)
- 1.5 = 안전계수 (페르소나 A 가 갑자기 큰 PDF 올릴 수 있음 → 폭주 차단 여유)

데이터 누적 부족 시 (n < 30 row 또는 unique_doc < 5) 잠정값만 출력하고 WARN 표시.
잠정값은 config.Settings 의 default 와 동일 (운영 graceful 일관).

설계 원칙
- 외부 의존성 0 — stdlib + 기존 supabase client
- pure function — 입력은 row list, 출력은 dataclass. DB I/O 는 fetch_* 헬퍼에 격리
- CLI 와 단위 테스트 양쪽에서 같은 코드 재사용 (scripts/compute_budget.py 가 thin wrapper)
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

# master plan §7.5 공식 상수 — 변경 시 plan 정합 깨짐.
_VISION_COVERAGE_RATIO = 0.5  # 50% 페이지만 vision 적용 가정
_SAFETY_FACTOR = 1.5  # 안전계수

# 누적 충분도 임계 — 1주 + 5 doc 기준 (master plan §6 D3 함의).
_MIN_SAMPLE_ROWS = 30
_MIN_UNIQUE_DOCS = 5

# scripts/compute_budget.py 와 단위 테스트 양쪽에서 사용.
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_DAILY_DOCS = 5
DEFAULT_KRW_PER_USD = 1380.0


@dataclass(frozen=True)
class BudgetSampleStats:
    """vision_usage_log 집계 결과 — pure 계산 입력."""

    sample_rows: int  # success=True && estimated_cost NOT NULL row 수
    success_rows: int  # success=True row 수 (cost NULL 포함 가능)
    failed_rows: int  # success=False row 수
    unique_docs: int  # doc_id 채워진 row 의 distinct count (NULL 분리)
    null_doc_rows: int  # doc_id NULL 인 row (image_parser 단독 호출 등)
    avg_cost_per_page_usd: float | None  # mean(estimated_cost) — sample 0 시 None
    avg_pages_per_doc: float | None  # mean(unique pages count per doc_id) — doc 0 시 None
    cost_p50_usd: float | None  # 분포 sanity check 용 — sample 0 시 None
    cost_max_usd: float | None  # outlier 점검 — sample 0 시 None
    pages_p50: float | None  # doc 별 page count 의 median


@dataclass(frozen=True)
class BudgetEstimate:
    """budget 산정 결과."""

    stats: BudgetSampleStats
    doc_budget_usd: float
    daily_budget_usd: float
    daily_docs: int
    krw_per_usd: float
    is_provisional: bool  # 누적 부족 시 True — fallback 잠정값
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def doc_budget_krw(self) -> float:
        return self.doc_budget_usd * self.krw_per_usd

    @property
    def daily_budget_krw(self) -> float:
        return self.daily_budget_usd * self.krw_per_usd


def aggregate_rows(rows: Iterable[Mapping[str, object]]) -> BudgetSampleStats:
    """vision_usage_log row → 통계.

    입력 row 는 다음 필드를 가정 (마이그 005 + 014):
      success: bool, estimated_cost: float|None, doc_id: str|None, page: int|None

    NULL 처리:
      - success=False → failed_rows 만 증가, sample 제외
      - estimated_cost NULL → sample_rows 제외 (단가 평균 왜곡 방지)
      - doc_id NULL → null_doc_rows 만 증가, unique_docs 분리 (image_parser 단독 등)
    """
    rows = list(rows)
    success_rows = 0
    failed_rows = 0
    null_doc_rows = 0
    cost_values: list[float] = []
    # doc_id → set(page) — doc 별 unique page 수 산정 (재시도 row 가 page 중복 만들어도 1개로 카운트)
    doc_pages: dict[str, set[int]] = {}

    for row in rows:
        success = bool(row.get("success"))
        if not success:
            failed_rows += 1
            continue
        success_rows += 1
        cost = row.get("estimated_cost")
        if cost is not None:
            try:
                cost_values.append(float(cost))
            except (TypeError, ValueError):
                pass
        doc_id = row.get("doc_id")
        if doc_id is None:
            null_doc_rows += 1
            continue
        page = row.get("page")
        bucket = doc_pages.setdefault(str(doc_id), set())
        if page is not None:
            try:
                bucket.add(int(page))
            except (TypeError, ValueError):
                pass

    avg_cost = statistics.fmean(cost_values) if cost_values else None
    cost_p50 = statistics.median(cost_values) if cost_values else None
    cost_max = max(cost_values) if cost_values else None

    pages_per_doc = [len(p) for p in doc_pages.values() if p]
    avg_pages = statistics.fmean(pages_per_doc) if pages_per_doc else None
    pages_p50 = statistics.median(pages_per_doc) if pages_per_doc else None

    return BudgetSampleStats(
        sample_rows=len(cost_values),
        success_rows=success_rows,
        failed_rows=failed_rows,
        unique_docs=len(doc_pages),
        null_doc_rows=null_doc_rows,
        avg_cost_per_page_usd=avg_cost,
        avg_pages_per_doc=avg_pages,
        cost_p50_usd=cost_p50,
        cost_max_usd=cost_max,
        pages_p50=pages_p50,
    )


def compute_budget(
    stats: BudgetSampleStats,
    *,
    daily_docs: int = DEFAULT_DAILY_DOCS,
    krw_per_usd: float = DEFAULT_KRW_PER_USD,
    fallback_doc_budget_usd: float,
    fallback_daily_budget_usd: float,
) -> BudgetEstimate:
    """master plan §7.5 공식 적용 + 데이터 부족 시 fallback.

    fallback_* 는 config.Settings 의 default 와 일치시킬 것 — 운영 일관성.
    """
    warnings: list[str] = []

    # 충분도 평가
    is_provisional = (
        stats.sample_rows < _MIN_SAMPLE_ROWS
        or stats.unique_docs < _MIN_UNIQUE_DOCS
        or stats.avg_cost_per_page_usd is None
        or stats.avg_pages_per_doc is None
    )

    if stats.sample_rows < _MIN_SAMPLE_ROWS:
        warnings.append(
            f"sample 부족 (n={stats.sample_rows} < {_MIN_SAMPLE_ROWS}) — 평균 단가 신뢰성 낮음"
        )
    if stats.unique_docs < _MIN_UNIQUE_DOCS:
        warnings.append(
            f"unique doc 부족 (doc={stats.unique_docs} < {_MIN_UNIQUE_DOCS}) — 페이지 평균 분산 큼"
        )
    if stats.failed_rows > 0:
        warnings.append(
            f"실패 row {stats.failed_rows}건 존재 — 실패 cost 는 평균 제외 (성공만 집계)"
        )
    if stats.null_doc_rows > 0:
        warnings.append(
            f"doc_id NULL row {stats.null_doc_rows}건 — image_parser 단독 호출 또는 doc 삭제 (SET NULL)"
        )

    if is_provisional:
        warnings.append(
            "잠정값 사용 — 누적 충분 시점 (≥30 row, ≥5 doc) 에 재산정 권고"
        )
        return BudgetEstimate(
            stats=stats,
            doc_budget_usd=fallback_doc_budget_usd,
            daily_budget_usd=fallback_daily_budget_usd,
            daily_docs=daily_docs,
            krw_per_usd=krw_per_usd,
            is_provisional=True,
            warnings=tuple(warnings),
        )

    # 공식 적용
    avg_cost = stats.avg_cost_per_page_usd or 0.0
    avg_pages = stats.avg_pages_per_doc or 0.0
    doc_budget = avg_cost * avg_pages * _VISION_COVERAGE_RATIO * _SAFETY_FACTOR
    daily_budget = doc_budget * daily_docs
    return BudgetEstimate(
        stats=stats,
        doc_budget_usd=doc_budget,
        daily_budget_usd=daily_budget,
        daily_docs=daily_docs,
        krw_per_usd=krw_per_usd,
        is_provisional=False,
        warnings=tuple(warnings),
    )


def fetch_recent_rows(
    client,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    source_type: str = "pdf_vision_enrich",
) -> list[dict]:
    """vision_usage_log 에서 최근 N일 row fetch.

    source_type='pdf_vision_enrich' default — image_parser 단독 호출 (doc_id NULL) 은 분석 대상 외.
    None 전달 시 모든 source 포함.

    네트워크 / DB 실패는 caller 에 raise — CLI 가 정확한 에러 메시지 출력.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    query = (
        client.table("vision_usage_log")
        .select(
            "call_id,success,estimated_cost,doc_id,page,called_at,source_type,model_used"
        )
        .gte("called_at", since)
    )
    if source_type is not None:
        query = query.eq("source_type", source_type)
    resp = query.execute()
    return list(resp.data or [])


def render_markdown(
    estimate: BudgetEstimate,
    *,
    lookback_days: int,
    source_type: str | None,
    fetched_at: datetime | None = None,
) -> str:
    """CLI stdout / work-log 양쪽에서 재사용 가능한 markdown 렌더."""
    fetched_at = fetched_at or datetime.now(timezone.utc)
    s = estimate.stats
    lines: list[str] = []
    lines.append("# Jet-Rag budget 초기값 산정 — vision_usage_log 기반")
    lines.append("")
    lines.append(f"- 측정 시각 (UTC): `{fetched_at.isoformat(timespec='seconds')}`")
    lines.append(f"- lookback: **{lookback_days}일**")
    lines.append(f"- source_type 필터: `{source_type or '(전체)'}`")
    lines.append("")
    lines.append("## sample 분포")
    lines.append("")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---|")
    lines.append(f"| 성공 row (cost NOT NULL) | {s.sample_rows} |")
    lines.append(f"| 성공 row (전체) | {s.success_rows} |")
    lines.append(f"| 실패 row | {s.failed_rows} |")
    lines.append(f"| unique doc | {s.unique_docs} |")
    lines.append(f"| doc_id NULL row | {s.null_doc_rows} |")
    lines.append("")
    lines.append("## 비용 통계 (성공 row, USD)")
    lines.append("")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---|")
    lines.append(f"| 평균 cost/page | {_fmt_money(s.avg_cost_per_page_usd)} |")
    lines.append(f"| 중앙값 cost/page | {_fmt_money(s.cost_p50_usd)} |")
    lines.append(f"| 최대 cost/page | {_fmt_money(s.cost_max_usd)} |")
    lines.append(f"| 평균 페이지/doc | {_fmt_pages(s.avg_pages_per_doc)} |")
    lines.append(f"| 중앙값 페이지/doc | {_fmt_pages(s.pages_p50)} |")
    lines.append("")
    lines.append("## 산정 결과")
    lines.append("")
    status = "**잠정값 (누적 부족)**" if estimate.is_provisional else "**측정값 (운영 채택 가능)**"
    lines.append(f"- 상태: {status}")
    lines.append(f"- 일일 인제스트 doc 가정: {estimate.daily_docs}건")
    lines.append(f"- 환율: {estimate.krw_per_usd:.0f} KRW/USD")
    lines.append("")
    lines.append("| 항목 | USD | KRW |")
    lines.append("|---|---|---|")
    lines.append(
        f"| 문서당 budget | ${estimate.doc_budget_usd:.4f} | "
        f"{estimate.doc_budget_krw:,.1f} 원 |"
    )
    lines.append(
        f"| 일일 budget | ${estimate.daily_budget_usd:.4f} | "
        f"{estimate.daily_budget_krw:,.1f} 원 |"
    )
    lines.append("")
    if estimate.warnings:
        lines.append("## 경고")
        lines.append("")
        for w in estimate.warnings:
            lines.append(f"- WARN: {w}")
        lines.append("")
    lines.append("## settings 반영 가이드")
    lines.append("")
    lines.append("```bash")
    lines.append(f"export JETRAG_DOC_BUDGET_USD={estimate.doc_budget_usd:.4f}")
    lines.append(f"export JETRAG_DAILY_BUDGET_USD={estimate.daily_budget_usd:.4f}")
    lines.append(f"export JETRAG_BUDGET_KRW_PER_USD={estimate.krw_per_usd:.0f}")
    lines.append("```")
    return "\n".join(lines)


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "(N/A)"
    return f"${value:.6f}"


def _fmt_pages(value: float | None) -> str:
    if value is None:
        return "(N/A)"
    return f"{value:.1f}"
