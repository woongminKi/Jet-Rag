"""수익화 W4 — 이메일 인제스트 webhook (Cloudflare Email Worker → 백엔드).

인증 = X-Jetrag-Webhook-Secret 공유 secret (JWT 아님 — 발신자는 Worker).
거절 정책 = 조용히 무시(200 + warning 로그) — Worker 재시도·반송 메일 회피.
단 secret 불일치는 401 (Worker 설정 오류는 시끄럽게 실패해야 발견됨).
"""
from __future__ import annotations

import base64
import binascii
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.db import get_supabase_client
from app.services import email_ingest, quota

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["email-ingest"])


class EmailAttachmentIn(BaseModel):
    filename: str = "attachment"
    content_type: str = "application/octet-stream"
    content_base64: str


class EmailWebhookPayload(BaseModel):
    to: str
    from_: str = Field(alias="from")
    subject: str = ""
    attachments: list[EmailAttachmentIn] = []


class EmailWebhookResponse(BaseModel):
    status: str  # "processed" | "ignored"
    results: list[dict] = []


def _increment_docs_counter(user_id: str) -> None:
    """W2 abuse cap 카운터와 정합 — 이메일 인제스트도 docs 로 센다 (best-effort)."""
    try:
        get_supabase_client().rpc(
            "increment_usage_counter",
            {
                "p_user_key": user_id,
                "p_metric": "docs",
                "p_period_date": datetime.now(timezone.utc).date().isoformat(),
            },
        ).execute()
    except Exception as exc:  # noqa: BLE001 — 카운터 실패가 인제스트를 막지 않음
        logger.warning("email_ingest docs 카운터 실패 (user=%s): %s", user_id, exc)


@router.post("/email", response_model=EmailWebhookResponse)
def email_webhook(
    payload: EmailWebhookPayload,
    background_tasks: BackgroundTasks,
    x_jetrag_webhook_secret: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> EmailWebhookResponse:
    if not settings.email_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="이메일 인제스트가 비활성 상태입니다 (JETRAG_EMAIL_WEBHOOK_SECRET 미설정).",
        )
    if x_jetrag_webhook_secret != settings.email_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="webhook secret 불일치",
        )

    token = email_ingest.parse_token(payload.to)
    if token is None:
        logger.warning("email_ingest ignore — 잘못된 수신 주소: %s", payload.to)
        return EmailWebhookResponse(status="ignored")

    addr = email_ingest.lookup_by_token(token)
    if addr is None:
        logger.warning("email_ingest ignore — 알 수 없는 토큰: %s...", token[:4])
        return EmailWebhookResponse(status="ignored")

    if not email_ingest.sender_allowed(payload.from_, addr.get("owner_email")):
        logger.warning(
            "email_ingest ignore — 발신자 불일치 (user=%s, from=%s)",
            addr["user_id"], payload.from_,
        )
        return EmailWebhookResponse(status="ignored")

    plan = quota.get_effective_plan(str(addr["user_id"]))
    if plan is None or plan.code != "pro":
        # 쓰기 경로 — 플랜 조회 실패도 거절 (fail-closed).
        logger.warning(
            "email_ingest ignore — Pro 아님 (user=%s, plan=%s)",
            addr["user_id"], getattr(plan, "code", None),
        )
        return EmailWebhookResponse(status="ignored")

    if not payload.attachments:
        logger.warning("email_ingest ignore — 첨부 없음 (user=%s)", addr["user_id"])
        return EmailWebhookResponse(status="ignored")

    user_id = str(addr["user_id"])
    results: list[dict] = []
    for att in payload.attachments:
        try:
            raw = base64.b64decode(att.content_base64, validate=True)
        except (binascii.Error, ValueError):
            results.append({"status": "skipped", "filename": att.filename, "reason": "base64 오류"})
            continue
        result = email_ingest.ingest_email_attachment(
            user_id=user_id,
            filename=att.filename,
            content_type=att.content_type,
            raw=raw,
            background_tasks=background_tasks,
        )
        if result["status"] == "accepted":
            _increment_docs_counter(user_id)
        results.append(result)

    logger.info(
        "email_ingest processed — user=%s, 첨부 %d건: %s",
        user_id, len(results), [r["status"] for r in results],
    )
    return EmailWebhookResponse(status="processed", results=results)
