# api/scripts/billing_charge.py
"""수익화 W5-6 — 정기결제 배치 (Railway cron 진입점).

만료 도래 구독 자동결제 + 7일 초과 past_due 자동 해지 sweep.
HTTP 없이 서비스 직접 호출 (주 배치 경로 — /billing/run 은 외부 cron fallback).

사용
    cd api && uv run python scripts/billing_charge.py

전제 ENV (Railway cron 서비스에 주입)
    SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / SUPABASE_KEY
    JETRAG_KAKAOPAY_SECRET_KEY / JETRAG_KAKAOPAY_CID
    JETRAG_BILLING_KEY_ENCRYPTION_KEY
"""
from __future__ import annotations

import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.services import billing  # noqa: E402


def main() -> None:
    charge = billing.charge_due_subscriptions()
    sweep = billing.sweep_past_due()
    print(
        f"[billing] charged={charge.charged} failed={charge.failed} "
        f"canceled={sweep.canceled}"
    )
    if charge.user_ids_failed:
        print(f"[billing] failed users: {charge.user_ids_failed}")
    if sweep.user_ids:
        print(f"[billing] canceled users: {sweep.user_ids}")


if __name__ == "__main__":
    main()
