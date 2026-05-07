import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_key: str
    supabase_service_role_key: str
    supabase_storage_bucket: str
    gemini_api_key: str
    hf_api_token: str
    default_user_id: str
    # S0 D3 (2026-05-07) — vision 비용 cap 메커니즘 (D4) 의 의존성.
    # master plan §6 S0 D3 + §7.5 공식: avg_cost/page × avg_pages/doc × 0.5 × 1.5
    # 데이터 누적 부족 시 (n<30 row 또는 unique_doc<5) 잠정값 — scripts/compute_budget.py 로 재산정.
    doc_budget_usd: float
    daily_budget_usd: float
    # S0 D5 (2026-05-07) — 24h sliding window cap. master plan §6 S0 D5 + §7.4.
    # default = daily_budget_usd 와 동일 (자정 직전/직후 폭주 방어). 별도 ENV 분리는
    # 사용자가 sliding 만 더 보수적으로 잡고 싶을 때 활용.
    sliding_24h_budget_usd: float
    budget_krw_per_usd: float


# 잠정값 — 데이터 누적 부족 시 fallback. master plan §7.5 default 채택.
# avg ~$0.0045/page (S0 D2 시점 실측) × avg 22p/doc × 0.5 × 1.5 ≈ $0.075 → 안전 0.10.
_DOC_BUDGET_USD_DEFAULT = 0.10
_DAILY_BUDGET_USD_DEFAULT = 0.50  # 5 docs/일 가정.
_BUDGET_KRW_PER_USD_DEFAULT = 1380.0


def _parse_float(env_key: str, default: float) -> float:
    """ENV 가 비숫자/음수면 default fallback. 사이드 이펙트: stderr WARN 없이 silent — 운영 graceful."""
    raw = os.environ.get(env_key)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value < 0:
        return default
    return value


@lru_cache
def get_settings() -> Settings:
    daily = _parse_float("JETRAG_DAILY_BUDGET_USD", _DAILY_BUDGET_USD_DEFAULT)
    return Settings(
        supabase_url=os.environ.get("SUPABASE_URL", ""),
        supabase_key=os.environ.get("SUPABASE_KEY", ""),
        supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
        supabase_storage_bucket=os.environ.get("SUPABASE_STORAGE_BUCKET", "documents"),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        hf_api_token=os.environ.get("HF_API_TOKEN", ""),
        default_user_id=os.environ.get(
            "DEFAULT_USER_ID", "00000000-0000-0000-0000-000000000001"
        ),
        doc_budget_usd=_parse_float("JETRAG_DOC_BUDGET_USD", _DOC_BUDGET_USD_DEFAULT),
        daily_budget_usd=daily,
        # D5 — 별도 ENV 미지정 시 daily 와 동일 값. 운영자가 보수적 cap 분리 가능.
        sliding_24h_budget_usd=_parse_float(
            "JETRAG_24H_BUDGET_USD", daily
        ),
        budget_krw_per_usd=_parse_float(
            "JETRAG_BUDGET_KRW_PER_USD", _BUDGET_KRW_PER_USD_DEFAULT
        ),
    )
