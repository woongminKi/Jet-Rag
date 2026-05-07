"""S0 D3 (2026-05-07) — vision_usage_log 기반 budget 초기값 산정 CLI.

master plan §6 S0 D3 + §7.5 의 공식을 vision_usage_log 누적 데이터에 적용해
문서당 / 일별 cost cap 의 운영 초기값을 추출. S0 D4 budget_guard 의 의존성.

실행:
    cd api
    uv run python scripts/compute_budget.py
    uv run python scripts/compute_budget.py --lookback-days 14 --daily-docs 10
    uv run python scripts/compute_budget.py --output ../work-log/budget-snapshot.md
    uv run python scripts/compute_budget.py --source-type all  # source_type 필터 해제

ENV:
    BUDGET_LOOKBACK_DAYS    — default 7
    BUDGET_DAILY_DOCS       — default 5
    BUDGET_KRW_PER_USD      — default 1380

설계
- thin wrapper — 실제 계산은 app.services.budget_calculator 에 위임 (단위 테스트 재사용)
- 외부 의존성 0 — argparse + 기존 supabase client + budget_calculator
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# api/ 루트를 sys.path 에 추가 (uv run python scripts/... 패턴 — verify_phase1.py 참조)
_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.services.budget_calculator import (  # noqa: E402
    DEFAULT_DAILY_DOCS,
    DEFAULT_KRW_PER_USD,
    DEFAULT_LOOKBACK_DAYS,
    aggregate_rows,
    compute_budget,
    fetch_recent_rows,
    render_markdown,
)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if not raw:
        return default
    try:
        value = float(raw)
        return value if value > 0 else default
    except ValueError:
        return default


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="vision_usage_log 기반 doc/일 budget 초기값 산정"
    )
    p.add_argument(
        "--lookback-days",
        type=int,
        default=_env_int("BUDGET_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS),
        help=f"최근 N일 데이터 (default {DEFAULT_LOOKBACK_DAYS}, ENV BUDGET_LOOKBACK_DAYS)",
    )
    p.add_argument(
        "--daily-docs",
        type=int,
        default=_env_int("BUDGET_DAILY_DOCS", DEFAULT_DAILY_DOCS),
        help=f"일일 인제스트 doc 가정 (default {DEFAULT_DAILY_DOCS}, ENV BUDGET_DAILY_DOCS)",
    )
    p.add_argument(
        "--krw-per-usd",
        type=float,
        default=_env_float("BUDGET_KRW_PER_USD", DEFAULT_KRW_PER_USD),
        help=f"환율 (default {DEFAULT_KRW_PER_USD}, ENV BUDGET_KRW_PER_USD)",
    )
    p.add_argument(
        "--source-type",
        default="pdf_vision_enrich",
        help="vision_usage_log.source_type 필터. 'all' 시 전체 (default pdf_vision_enrich)",
    )
    p.add_argument(
        "--output",
        "-o",
        default=None,
        help="markdown 출력 경로 (미지정 시 stdout)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # 사이드 이펙트 격리: client import 는 main 에서만 — 단위 테스트는 fetch_recent_rows mock 가능.
    from app.config import get_settings  # noqa: E402
    from app.db import get_supabase_client  # noqa: E402

    settings = get_settings()
    client = get_supabase_client()

    source_type: str | None
    source_type = None if args.source_type == "all" else args.source_type

    try:
        rows = fetch_recent_rows(
            client, lookback_days=args.lookback_days, source_type=source_type
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[error] vision_usage_log fetch 실패: {exc}", file=sys.stderr)
        return 2

    stats = aggregate_rows(rows)
    estimate = compute_budget(
        stats,
        daily_docs=args.daily_docs,
        krw_per_usd=args.krw_per_usd,
        fallback_doc_budget_usd=settings.doc_budget_usd,
        fallback_daily_budget_usd=settings.daily_budget_usd,
    )
    md = render_markdown(
        estimate,
        lookback_days=args.lookback_days,
        source_type=source_type,
        fetched_at=datetime.now(timezone.utc),
    )

    if args.output:
        out_path = Path(args.output).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md + "\n", encoding="utf-8")
        print(f"[ok] markdown 작성: {out_path}", file=sys.stderr)
    else:
        print(md)

    # exit code: 데이터 부족(잠정값) 시 1 — CI gate 호환 (운영 신호)
    return 1 if estimate.is_provisional else 0


if __name__ == "__main__":
    raise SystemExit(main())
