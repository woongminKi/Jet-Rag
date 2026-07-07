# api/app/adapters/payment.py
"""수익화 W5-6 — 결제 공급자 Protocol (KakaoPay 기본, 토스/Stripe swap 대비).

기존 LLMProvider/VisionCaptioner 와 동일한 5-part 어댑터 패턴:
Protocol(본 파일) + impl/kakaopay.py + payment_factory.get_payment_provider().
호출처는 impl 을 직접 import 하지 말고 factory 로 받는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class PaymentError(Exception):
    """결제 공급자 호출 실패 (비 2xx 응답·네트워크·응답 형식 오류).

    배치(charge)는 이 예외를 잡아 해당 유저를 past_due 로 판정하고 다음 유저로 진행한다.
    """


@dataclass(frozen=True)
class ReadyResult:
    """결제창 준비 결과 — tid(승인 시 필요) + 사용자 redirect URL."""

    tid: str
    redirect_url: str


@dataclass(frozen=True)
class ApproveResult:
    """결제 승인 결과 — 정기결제 SID(빌링키) + tid."""

    sid: str
    tid: str


class PaymentProvider(Protocol):
    """정기결제 공급자. 4 메소드: ready→approve(등록) / subscribe(월결제) / inactivate(해지)."""

    def ready(
        self,
        *,
        partner_order_id: str,
        partner_user_id: str,
        approval_url: str,
        cancel_url: str,
        fail_url: str,
    ) -> ReadyResult: ...

    def approve(
        self,
        *,
        tid: str,
        partner_order_id: str,
        partner_user_id: str,
        pg_token: str,
    ) -> ApproveResult: ...

    def subscribe(
        self,
        *,
        sid: str,
        partner_order_id: str,
        partner_user_id: str,
    ) -> None: ...

    def inactivate(self, *, sid: str) -> None: ...
