# api/app/adapters/payment_factory.py
"""수익화 W5-6 — PaymentProvider 팩토리. ENV JETRAG_PAYMENT_PROVIDER (default kakaopay).

LLM factory(app/adapters/factory.py) 와 동일 — lazy import 로 impl/httpx 로딩을
호출 시점으로 미룬다(단위 테스트가 불필요한 import 비용 회피).
"""
from __future__ import annotations

from app.adapters.payment import PaymentProvider
from app.config import get_settings


def get_payment_provider() -> PaymentProvider:
    settings = get_settings()
    provider = (settings.payment_provider or "kakaopay").strip().lower()
    if provider == "kakaopay":
        from app.adapters.impl.kakaopay import KakaoPayImpl

        return KakaoPayImpl(
            secret_key=settings.kakaopay_secret_key,
            cid=settings.kakaopay_cid,
        )
    raise ValueError(f"알 수 없는 결제 provider: {provider!r}")
