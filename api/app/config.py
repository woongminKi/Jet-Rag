import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / ".env", override=False)

# M0-a W-14 — ingest_jobs 고아 running watchdog threshold (시) default + clamp 범위.
# Settings 필드 default 로도 쓰여서 클래스 정의 전에 둔다 (기존 테스트가 Settings(...) 를
# 직접 구성할 때 이 인자를 생략해도 깨지지 않게 — config.get_settings() 는 항상 clamp 적용).
_STALE_INGEST_JOB_HOURS_DEFAULT = 24
_STALE_INGEST_JOB_HOURS_MIN = 1
_STALE_INGEST_JOB_HOURS_MAX = 168  # 7일


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
    # S2 D1 (2026-05-08) — vision_need_score 운영 hook 토글. master plan §6 S2 D1.
    # PyMuPDF 분석 → 점수 계산 → OR rule needs_vision False 페이지는 vision 호출 회피.
    # default true (S1.5 D3 골든셋 recall 5/6 = 83.3% baseline 반영 채택).
    # false 시 모든 페이지 vision 호출 (S1.5 이전 동작 100% 보존, 회복 토글).
    vision_need_score_enabled: bool
    # S2 D2 (2026-05-08) — 문서당 vision call 페이지 cap. master plan §6 S2 D2.
    # cost cap (doc/daily/24h_sliding) 과 직교 — 둘 중 먼저 닿는 지점 stop.
    # default 50 (S0 D3 본 PC 측정 평균 21.5p/doc × 2.3배 안전 margin).
    # 0 또는 음수 시 무한 (회복 토글 — S2 D1 이전 동작 100% 보존).
    # in-memory 카운터 — DB SUM 불필요 (sweep 간 누적, needs_vision skip 제외).
    vision_page_cap_per_doc: int
    # M0-a W-14 (2026-05-13) — `ingest_jobs` 고아 running job watchdog threshold (시).
    # `started_at` 이 이 시간 이전인 status='running' job 을 stale 로 보고 failed 마킹.
    # ENV `JETRAG_STALE_INGEST_JOB_HOURS` 로 조정, `[1,168]` (1시간~7일) clamp.
    # default 24 (SLO §10.11 최장 = 이미지PDF20+<3분 << 24h 이므로 안전 margin).
    # 필드 default 보유 — 기존 Settings(...) 직접 구성 테스트 호환 (get_settings() 는 항상 clamp).
    stale_ingest_job_hours: int = _STALE_INGEST_JOB_HOURS_DEFAULT


# 잠정값 — 데이터 누적 부족 시 fallback. master plan §7.5 default 채택.
# avg ~$0.0045/page (S0 D2 시점 실측) × avg 22p/doc × 0.5 × 1.5 ≈ $0.075 → 안전 0.10.
_DOC_BUDGET_USD_DEFAULT = 0.10
_DAILY_BUDGET_USD_DEFAULT = 0.50  # 5 docs/일 가정.
_BUDGET_KRW_PER_USD_DEFAULT = 1380.0

# S2 D2 — page cap default. master plan §6 S2 D2 + 핸드오프 §6 Q-S2-4.
# S0 D3 본 PC 5 PDF 측정 평균 21.5p/doc × 2.3배 안전 margin = 49.5 → round 50.
_VISION_PAGE_CAP_PER_DOC_DEFAULT = 50

# (_STALE_INGEST_JOB_HOURS_* 는 Settings 필드 default 로 쓰여 파일 상단에 정의됨.)


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


def _parse_bool(env_key: str, default: bool) -> bool:
    """ENV bool parse — "true"/"1"/"yes"/"on" 만 True (대소문자 무관). 그 외 default fallback."""
    raw = os.environ.get(env_key)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in ("true", "1", "yes", "on"):
        return True
    if normalized in ("false", "0", "no", "off"):
        return False
    return default


def _parse_int(env_key: str, default: int) -> int:
    """ENV int parse — 비숫자 시 default fallback. 음수 허용 (S2 D2 page cap 무한 토글).

    S2 D2: `JETRAG_VISION_PAGE_CAP_PER_DOC=0` 또는 음수 → page_cap_per_doc <= 0 →
    budget_guard.check_doc_page_cap 이 무한 모드 진입 (회복 토글).
    """
    raw = os.environ.get(env_key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


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
        # S2 D1 — default true. invalid ENV 는 default 유지 (graceful).
        vision_need_score_enabled=_parse_bool(
            "JETRAG_VISION_NEED_SCORE_ENABLED", True
        ),
        # S2 D2 — default 50 (S0 D3 평균 21.5p/doc × 2.3배 안전 margin).
        # 0 또는 음수 시 budget_guard 가 무한 모드 (회복 토글).
        vision_page_cap_per_doc=_parse_int(
            "JETRAG_VISION_PAGE_CAP_PER_DOC", _VISION_PAGE_CAP_PER_DOC_DEFAULT
        ),
        # M0-a W-14 — invalid ENV 는 default 24, 그 외엔 [1,168] clamp (음수·0 → 1, >168 → 168).
        stale_ingest_job_hours=max(
            _STALE_INGEST_JOB_HOURS_MIN,
            min(
                _STALE_INGEST_JOB_HOURS_MAX,
                _parse_int(
                    "JETRAG_STALE_INGEST_JOB_HOURS", _STALE_INGEST_JOB_HOURS_DEFAULT
                ),
            ),
        ),
    )
