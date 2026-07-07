# api/app/services/billing_crypto.py
"""수익화 W5-6 — 카카오페이 SID(빌링키) 대칭 암호화.

subscriptions.billing_key 에는 Fernet 암호문만 저장한다 (평문 SID 금지).
키 = ENV JETRAG_BILLING_KEY_ENCRYPTION_KEY (Fernet.generate_key() 결과).
v1 단일 key — rotation 은 W7 이후 (재암호화 배치 필요).
"""
from __future__ import annotations

from cryptography.fernet import Fernet

from app.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().billing_key_encryption_key
    if not key:
        raise RuntimeError(
            "JETRAG_BILLING_KEY_ENCRYPTION_KEY 미설정 — SID 암호화 불가. "
            "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' "
            "로 생성 후 ENV 설정 필요."
        )
    return Fernet(key.encode("utf-8"))


def encrypt_sid(sid: str) -> str:
    """SID 평문 → Fernet 암호문(str)."""
    return _fernet().encrypt(sid.encode("utf-8")).decode("utf-8")


def decrypt_sid(token: str) -> str:
    """Fernet 암호문 → SID 평문(str)."""
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
