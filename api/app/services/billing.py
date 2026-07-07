# api/app/services/billing.py
"""수익화 W5-6 — 카카오페이 정기결제 서비스 로직.

lifecycle: start_subscription(ready) → approve_subscription(SID 저장·active)
배치: charge_due_subscriptions(만료 자동결제) + sweep_past_due(7일 grace 후 canceled)
해지: cancel_subscription(KakaoPay inactive + canceled)

상태 머신: active → (결제 실패) past_due (7일 grace) → canceled (Free 강등, 데이터 보존).
past_due 구독도 매일 재시도(current_period_end<=now) — 성공 시 즉시 active 복귀.
유저별 격리 — 1건 실패가 나머지를 막지 않는다.
멱등성 — 결제 성공 후 기간 갱신이 실패해도 payment_history charge_success 마커(period_key)로
다음 run 이 재청구하지 않는다.
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.adapters.payment import ReadyResult
from app.adapters.payment_factory import get_payment_provider
from app.config import get_settings
from app.db import get_supabase_client
from app.services.billing_crypto import decrypt_sid, encrypt_sid

logger = logging.getLogger(__name__)

_PRICE_KRW = 6900
_GRACE_DAYS = 7  # 결제 실패 후 canceled 까지 grace (결정 이력 #7)


class SubscriptionNotPendingError(Exception):
    """approve 호출인데 pending_tid 가 없음 (ready 미선행/중복 승인)."""


@dataclass(frozen=True)
class ChargeReport:
    charged: int
    failed: int
    user_ids_charged: list[str]
    user_ids_failed: list[str]


@dataclass(frozen=True)
class SweepReport:
    canceled: int
    user_ids: list[str]


def _now(now: datetime | None) -> datetime:
    """tz-aware UTC 보장 — naive 를 넘겨도 UTC 로 간주 (Postgres TIMESTAMPTZ 정합)."""
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def _add_one_month(dt: datetime) -> datetime:
    """월 1회 결제 주기 — 말일 clamp (stdlib only, dateutil 의존 회피)."""
    year = dt.year + (dt.month // 12)
    month = dt.month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _amount_for(event: str) -> int | None:
    return _PRICE_KRW if event in ("subscribe", "charge_success") else None


def _insert_history(user_id: str, event: str, detail: str) -> None:
    """payment_history INSERT — 실패 시 예외 전파 (호출자가 처리). 멱등 마커(charge_success)용."""
    get_supabase_client().table("payment_history").insert(
        {
            "user_id": user_id,
            "event": event,
            "amount_krw": _amount_for(event),
            "detail": detail[:500] or None,
        }
    ).execute()


def _log_history(user_id: str, event: str, *, detail: str = "") -> None:
    """payment_history 기록 (best-effort — 실패해도 흐름 막지 않음). 비-멱등 이벤트용."""
    try:
        _insert_history(user_id, event, detail)
    except Exception as exc:  # noqa: BLE001
        logger.warning("payment_history 기록 실패 (user=%s, event=%s): %s", user_id, event, exc)


def _redirect_urls(user_id: str) -> tuple[str, str, str]:
    base = get_settings().billing_redirect_base.rstrip("/")
    # approval_url 에 KakaoPay 가 ?pg_token=... 을 append 한다. tid 는 서버(pending_tid)에서 조회.
    return (
        f"{base}/billing/success",
        f"{base}/billing/cancel",
        f"{base}/billing/fail",
    )


def start_subscription(user_id: str) -> ReadyResult:
    """결제창 준비 — ready 호출 후 tid 를 pending_tid 에 보관.

    기존 구독자(active/past_due)가 재클릭해도 status/plan_code/billing_key 를 건드리지
    않는다 — pending_tid 만 갱신 (재클릭으로 Pro 접근이 끊기는 사고 방지, W5 코드리뷰 CRITICAL).
    신규 유저만 미활성 placeholder(status=canceled=Free) 행을 생성.
    """
    approval_url, cancel_url, fail_url = _redirect_urls(user_id)
    result = get_payment_provider().ready(
        partner_order_id=user_id,
        partner_user_id=user_id,
        approval_url=approval_url,
        cancel_url=cancel_url,
        fail_url=fail_url,
    )
    client = get_supabase_client()
    existing = (
        client.table("subscriptions")
        .select("status")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    ) or []
    if existing:
        client.table("subscriptions").update(
            {"pending_tid": result.tid, "updated_at": _now(None).isoformat()}
        ).eq("user_id", user_id).execute()
    else:
        client.table("subscriptions").insert(
            {
                "user_id": user_id,
                "plan_code": "free",
                "status": "canceled",
                "pending_tid": result.tid,
                "updated_at": _now(None).isoformat(),
            }
        ).execute()
    return result


def approve_subscription(user_id: str, pg_token: str) -> None:
    """결제 승인 — pending_tid 로 approve → SID 암호화 저장 + active 전환."""
    client = get_supabase_client()
    rows = (
        client.table("subscriptions")
        .select("pending_tid")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    ) or []
    tid = rows[0].get("pending_tid") if rows else None
    if not tid:
        raise SubscriptionNotPendingError(f"진행 중인 결제 요청 없음 (user={user_id})")

    approved = get_payment_provider().approve(
        tid=tid,
        partner_order_id=user_id,
        partner_user_id=user_id,
        pg_token=pg_token,
    )
    period_end = _add_one_month(_now(None))
    client.table("subscriptions").update(
        {
            "plan_code": "pro",
            "status": "active",
            "billing_key": encrypt_sid(approved.sid),
            "current_period_end": period_end.isoformat(),
            "pending_tid": None,
            "past_due_since": None,
            "updated_at": _now(None).isoformat(),
        }
    ).eq("user_id", user_id).execute()
    _log_history(user_id, "subscribe", detail="구독 등록 완료")


def _mark_past_due(client, user_id: str, row: dict, at: datetime, detail: str) -> None:
    """결제 실패 → past_due. past_due_since 는 최초 실패만 기록(grace clock 리셋 방지)."""
    update = {"status": "past_due", "updated_at": at.isoformat()}
    if not row.get("past_due_since"):
        update["past_due_since"] = at.isoformat()
    client.table("subscriptions").update(update).eq("user_id", user_id).execute()
    _log_history(user_id, "charge_failed", detail=detail)


def _already_charged(client, user_id: str, period_key: str | None) -> bool:
    """이번 결제주기(period_key)에 이미 charge_success 마커가 있으면 True (멱등성).

    조회 실패는 False (보수적으로 재청구 시도) — 마커 부재와 동일 취급.
    """
    if not period_key:
        return False
    try:
        rows = (
            client.table("payment_history")
            .select("id")
            .eq("user_id", user_id)
            .eq("event", "charge_success")
            .eq("detail", period_key)
            .limit(1)
            .execute()
            .data
        ) or []
        return bool(rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("멱등 조회 실패 (user=%s): %s", user_id, exc)
        return False


def charge_due_subscriptions(now: datetime | None = None) -> ChargeReport:
    """만료 도래(current_period_end<=now) active/past_due 구독 자동결제.

    선결제 실패(billing_key 없음·decline)는 past_due. 결제 성공 후 DB 오류는 재청구를
    유발하지 않는다 (charge_success 마커 + 다음 run 멱등 처리).
    """
    at = _now(now)
    client = get_supabase_client()
    due = (
        client.table("subscriptions")
        .select("user_id, billing_key, status, current_period_end, past_due_since")
        .in_("status", ["active", "past_due"])
        .lte("current_period_end", at.isoformat())
        .execute()
        .data
    ) or []

    charged: list[str] = []
    failed: list[str] = []
    provider = get_payment_provider()
    for row in due:
        user_id = str(row["user_id"])
        period_key = row.get("current_period_end")
        enc = row.get("billing_key")

        # 1) 선결제 검증 — billing_key 없음 = past_due (조용한 무한 skip 방지).
        if not enc:
            try:
                _mark_past_due(client, user_id, row, at, "billing_key 없음")
            except Exception as exc:  # noqa: BLE001
                logger.error("past_due 처리 실패 (user=%s): %s", user_id, exc)
            failed.append(user_id)
            continue

        # 2) 결제 (이번 주기 이미 결제됐으면 skip — 멱등).
        if not _already_charged(client, user_id, period_key):
            try:
                sid = decrypt_sid(enc)
            except Exception as exc:  # noqa: BLE001 — 복호화 실패 = 설정 오류. grace clock 안 건드림.
                logger.error("SID 복호화 실패 — skip (user=%s): %s", user_id, exc)
                failed.append(user_id)
                continue
            try:
                provider.subscribe(
                    sid=sid,
                    partner_order_id=f"{user_id}-{at.strftime('%Y%m%d')}",
                    partner_user_id=user_id,
                )
            except Exception as exc:  # noqa: BLE001 — 결제 실패(decline/network) = past_due.
                try:
                    _mark_past_due(client, user_id, row, at, str(exc)[:400])
                except Exception as inner:  # noqa: BLE001
                    logger.error("past_due 처리 실패 (user=%s): %s", user_id, inner)
                failed.append(user_id)
                continue
            # 결제 성공 — 멱등 마커 기록. 실패해도 결제는 됨(수동 확인 로그).
            try:
                _insert_history(user_id, "charge_success", period_key or "")
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "결제 성공했으나 이력 기록 실패 (user=%s, period=%s): %s — 수동 확인 필요",
                    user_id, period_key, exc,
                )

        # 3) 후처리 — 기간 갱신 + active 복귀. 실패해도 재청구 안 됨(멱등 마커/다음 run).
        try:
            new_end = _add_one_month(_parse_ts(period_key) or at)
            client.table("subscriptions").update(
                {
                    "status": "active",
                    "current_period_end": new_end.isoformat(),
                    "past_due_since": None,
                    "updated_at": at.isoformat(),
                }
            ).eq("user_id", user_id).execute()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "결제 성공했으나 기간 갱신 실패 (user=%s): %s — 다음 배치가 멱등 처리",
                user_id, exc,
            )
        charged.append(user_id)

    logger.info("billing charge — 성공 %d, 실패 %d", len(charged), len(failed))
    return ChargeReport(
        charged=len(charged), failed=len(failed),
        user_ids_charged=charged, user_ids_failed=failed,
    )


def sweep_past_due(now: datetime | None = None) -> SweepReport:
    """past_due_since 7일 초과 → canceled (Free 강등). 유저별 격리."""
    at = _now(now)
    threshold = (at - timedelta(days=_GRACE_DAYS)).isoformat()
    client = get_supabase_client()
    overdue = (
        client.table("subscriptions")
        .select("user_id, billing_key, past_due_since")
        .eq("status", "past_due")
        .lte("past_due_since", threshold)
        .execute()
        .data
    ) or []

    canceled: list[str] = []
    provider = get_payment_provider()
    for row in overdue:
        user_id = str(row["user_id"])
        try:
            enc = row.get("billing_key")
            if enc:
                try:
                    provider.inactivate(sid=decrypt_sid(enc))
                except Exception as exc:  # noqa: BLE001 — 원격 실패해도 로컬 canceled 진행.
                    logger.warning("SID inactive 실패 (user=%s): %s", user_id, exc)
            client.table("subscriptions").update(
                {"status": "canceled", "updated_at": at.isoformat()}
            ).eq("user_id", user_id).execute()
            canceled.append(user_id)
            _log_history(user_id, "cancel", detail="7일 grace 초과 자동 해지")
        except Exception as exc:  # noqa: BLE001 — 유저 격리.
            logger.error("sweep 처리 실패 (user=%s): %s", user_id, exc)
            continue

    logger.info("billing sweep — %d건 canceled", len(canceled))
    return SweepReport(canceled=len(canceled), user_ids=canceled)


def cancel_subscription(user_id: str) -> None:
    """사용자 요청 해지 — KakaoPay inactive + canceled (즉시 Free, 데이터 보존)."""
    client = get_supabase_client()
    rows = (
        client.table("subscriptions")
        .select("billing_key")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    ) or []
    enc = rows[0].get("billing_key") if rows else None
    if enc:
        try:
            get_payment_provider().inactivate(sid=decrypt_sid(enc))
        except Exception as exc:  # noqa: BLE001 — 원격 실패해도 로컬 해지 진행.
            logger.warning("해지 시 SID inactive 실패 (user=%s): %s", user_id, exc)
    client.table("subscriptions").update(
        {"status": "canceled", "updated_at": _now(None).isoformat()}
    ).eq("user_id", user_id).execute()
    _log_history(user_id, "cancel", detail="사용자 요청 해지")
