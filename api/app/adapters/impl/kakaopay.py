# api/app/adapters/impl/kakaopay.py
"""수익화 W5-6 — KakaoPay open-api 정기결제 어댑터.

Base: https://open-api.kakaopay.com
Auth: Authorization: SECRET_KEY {secret}
flow: ready → approve(sid 발급) → subscription(월 배치) / inactive(해지).
sandbox CID = TCSUBSCRIP (정기결제 테스트). production CID 는 심사 후 ENV 교체.
"""
from __future__ import annotations

import logging

import httpx

from app.adapters.payment import ApproveResult, PaymentError, ReadyResult

logger = logging.getLogger(__name__)

_BASE_URL = "https://open-api.kakaopay.com"
_ITEM_NAME = "Jet-Rag Pro 구독"
_TOTAL_AMOUNT = 6900  # plans.price_krw=6900 과 정합 (결정 이력 #2)
_TIMEOUT = 15.0


class KakaoPayImpl:
    """KakaoPay open-api 정기결제 클라이언트. PaymentProvider Protocol 구현."""

    def __init__(self, *, secret_key: str, cid: str, base_url: str = _BASE_URL) -> None:
        if not secret_key:
            raise RuntimeError(
                "KakaoPay secret_key 미설정 — JETRAG_KAKAOPAY_SECRET_KEY 필요."
            )
        self._cid = cid
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"SECRET_KEY {secret_key}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, body: dict) -> dict:
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.post(
                    f"{self._base_url}{path}", headers=self._headers, json=body
                )
        except httpx.HTTPError as exc:
            logger.warning("KakaoPay 네트워크 오류 (%s): %s", path, exc)
            raise PaymentError(f"KakaoPay 네트워크 오류 ({path}): {exc}") from exc
        if resp.status_code >= 400:
            logger.warning("KakaoPay %s 오류 (%s)", resp.status_code, path)
            raise PaymentError(
                f"KakaoPay {resp.status_code} ({path}): {resp.text[:200]}"
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise PaymentError(
                f"KakaoPay 응답 파싱 실패 ({path}): {resp.text[:200]}"
            ) from exc
        if not isinstance(data, dict):
            raise PaymentError(
                f"KakaoPay 예상치 못한 응답 타입 ({path}): {type(data).__name__}"
            )
        return data

    def ready(
        self, *, partner_order_id: str, partner_user_id: str,
        approval_url: str, cancel_url: str, fail_url: str,
    ) -> ReadyResult:
        data = self._post(
            "/online/v1/payment/ready",
            {
                "cid": self._cid,
                "partner_order_id": partner_order_id,
                "partner_user_id": partner_user_id,
                "item_name": _ITEM_NAME,
                "quantity": 1,
                "total_amount": _TOTAL_AMOUNT,
                "tax_free_amount": 0,
                "approval_url": approval_url,
                "cancel_url": cancel_url,
                "fail_url": fail_url,
            },
        )
        redirect = data.get("next_redirect_pc_url") or data.get("next_redirect_mobile_url")
        if not data.get("tid") or not redirect:
            raise PaymentError(f"KakaoPay ready 응답 불완전: keys={list(data.keys())}")
        return ReadyResult(tid=data["tid"], redirect_url=redirect)

    def approve(
        self, *, tid: str, partner_order_id: str, partner_user_id: str, pg_token: str,
    ) -> ApproveResult:
        data = self._post(
            "/online/v1/payment/approve",
            {
                "cid": self._cid,
                "tid": tid,
                "partner_order_id": partner_order_id,
                "partner_user_id": partner_user_id,
                "pg_token": pg_token,
            },
        )
        sid = data.get("sid")
        if not sid:
            raise PaymentError(
                "KakaoPay approve 응답에 sid 없음 — 정기결제 CID(TCSUBSCRIP 계열) 확인 필요."
            )
        return ApproveResult(sid=sid, tid=tid)

    def subscribe(self, *, sid: str, partner_order_id: str, partner_user_id: str) -> None:
        self._post(
            "/online/v1/payment/subscription",
            {
                "cid": self._cid,
                "sid": sid,
                "partner_order_id": partner_order_id,
                "partner_user_id": partner_user_id,
                "item_name": _ITEM_NAME,
                "quantity": 1,
                "total_amount": _TOTAL_AMOUNT,
                "tax_free_amount": 0,
            },
        )

    def inactivate(self, *, sid: str) -> None:
        self._post(
            "/online/v1/payment/manage/subscription/inactive",
            {"cid": self._cid, "sid": sid},
        )
