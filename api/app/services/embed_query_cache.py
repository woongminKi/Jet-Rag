"""S4-B 후속 (2026-05-12) — `embed_query_cache` lookup / upsert 헬퍼.

배경
    S4-B ablation — HF BGE-M3 Inference API 가 같은 query 텍스트에 미세하게
    다른 dense 벡터를 반환 → dense_rank → RRF 재정렬 → 세션 간 top-10 순위
    churn (집계 점수는 ±0.005 상쇄, 회귀 아님. 평가셋 측정 재현성 결함).
    + D6 의 HF Inference free-tier scale-to-zero cold-start.
    → query 텍스트(sha256) → dense 벡터 영구 캐시 = 첫 fetch 벡터를 canonical
    로 freeze → 워밍 후 측정 결정적 + HF 호출 0회 (cold-start 부수 완화).
    in-process LRU 512 (`bgem3_hf_embedding`) 는 프로세스 종료 시 소실 →
    eval 매 실행마다 HF 재호출 → 비결정. 영구 캐시로 보강 (LRU 의 아래 계층).

설계 (vision_cache.py 패턴 복제)
    - `(text_sha256, model_id)` 2-tuple UNIQUE — 정확히 같은 입력에만 hit.
    - `text_sha256` = sha256(NFC(text.strip())) 64 hex — PII 우려로 query 원문
      미저장 (페르소나 A query 에 개인·업무 정보 섞임. vision_page_cache 도 sha256만).
    - `model_id` = `_MODEL_ID` ('BAAI/bge-m3') — 모델 교체 시 자동 invalidate.
    - `vector` JSONB float array — 이 테이블에선 벡터 연산 안 함 (pgvector 의존 최소화).
    - read 시 `len(vector) == _DENSE_DIM` 검증 — 불일치 row 는 무시 → 호출자가 HF 재호출.
    - DB 부재 / 마이그 016 미적용 시 graceful — `vision_cache` 와 동일 패턴.
    - upsert 는 `ON CONFLICT (text_sha256, model_id) DO NOTHING` 으로 동시성 안전
      (race 시 먼저 저장된 row 가 canonical — 결과 동일성 영향 없음).

호출 위치
    - `app.adapters.impl.bgem3_hf_embedding.BGEM3HFEmbeddingProvider.embed_query`
      — in-process LRU 의 아래 계층 (LRU miss → 영구 캐시 lookup → miss 면 HF).

ENV 토글
    `JETRAG_EMBED_QUERY_CACHE` (기본 "1") — "0" 으로 disable. 새 측정 baseline 이
    필요할 때 / eval ablation 에서 비결정성을 의도적으로 측정할 때.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# 임베딩 모델 ID — 캐시 키의 일부 (모델 교체 시 자동 invalidate).
# bgem3_hf_embedding 의 `_URL` (BAAI/bge-m3) 과 일치해야 함 — 그 모듈이 본 상수를 import.
_MODEL_ID = "BAAI/bge-m3"

# 벡터 차원 (BGE-M3 dense). read 시 길이 검증용 — 불일치 row 는 무시.
_DENSE_DIM = 1024

# 캐시 lookup / upsert 활성 여부 — 기본 활성. 단위 테스트나 새 baseline 측정 시 "0" 으로 disable.
_CACHE_ENV_KEY = "JETRAG_EMBED_QUERY_CACHE"

# 첫 1회만 warn (이후 debug) — 마이그 016 미적용 환경 노이즈 방지 (vision_cache 패턴).
_first_warn_logged: bool = False


def model_id() -> str:
    """현재 임베딩 모델 ID 상수. 단위 테스트가 monkey-patch 가능하도록 함수로 노출."""
    return _MODEL_ID


def is_enabled() -> bool:
    """ENV 토글. 단위 테스트나 새 측정 baseline 시 "0" 으로 cache 자체를 disable 가능."""
    return os.environ.get(_CACHE_ENV_KEY, "1") != "0"


def lookup(text_sha256: str, model_id: str) -> list[float] | None:
    """`(text_sha256, model_id)` 매칭 row → dense 벡터(list[float]) 복원.

    miss / 비활성 / DB 실패 / dim 불일치 / parse 실패 시 None — 호출자는 HF 직호출로 fallback.
    """
    if not is_enabled():
        return None
    if not text_sha256 or not model_id:
        return None

    try:
        # lazy import — 단위 테스트가 supabase 의존성 없이도 동작 가능하도록.
        from app.db import get_supabase_client

        client = get_supabase_client()
        resp = (
            client.table("embed_query_cache")
            .select("vector, dim")
            .eq("text_sha256", text_sha256)
            .eq("model_id", model_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — DB 부재 / 마이그 016 미적용 graceful
        _warn_first(f"embed_query_cache lookup 실패 (graceful): {exc}")
        return None

    rows = resp.data or []
    if not rows:
        return None

    return _deserialize(rows[0])


def upsert(text_sha256: str, model_id: str, dim: int, vector: list[float]) -> None:
    """`(text_sha256, model_id)` 키로 row insert.

    `ON CONFLICT DO NOTHING` 으로 동시성 race 안전 (먼저 저장된 row 가 canonical).
    DB 실패는 graceful (best-effort) — 다음 호출 시 다시 시도. raise 안 함.
    """
    if not is_enabled():
        return
    if not text_sha256 or not model_id or not vector:
        return

    row = {
        "text_sha256": text_sha256,
        "model_id": model_id,
        "dim": dim,
        "vector": [float(x) for x in vector],
    }
    try:
        from app.db import get_supabase_client

        client = get_supabase_client()
        # supabase-py 는 PostgREST 의 Prefer: resolution=ignore-duplicates 로
        # ON CONFLICT DO NOTHING 동등 동작 — on_conflict 컬럼 명시 + ignore_duplicates=True.
        (
            client.table("embed_query_cache")
            .upsert(
                row,
                on_conflict="text_sha256,model_id",
                ignore_duplicates=True,
            )
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — DB 부재 / 016 미적용 graceful (best-effort)
        # write 는 best-effort — 첫 1회만 표면화하되 lookup 과 동일 채널로 통일.
        _warn_first(f"embed_query_cache upsert 실패 (graceful): {exc}")


def _deserialize(row: dict) -> list[float] | None:
    """캐시 row → dense 벡터. dim 불일치 / 잘못된 형식은 graceful None (HF 재호출 유도)."""
    if not isinstance(row, dict):
        return None
    raw_vec = row.get("vector")
    if not isinstance(raw_vec, list) or not raw_vec:
        return None
    if len(raw_vec) != _DENSE_DIM:
        # 모델/차원 변경 등으로 stale 한 row — 무시하고 호출자가 HF 재호출.
        return None
    try:
        return [float(x) for x in raw_vec]
    except (TypeError, ValueError):
        return None


def _warn_first(msg: str) -> None:
    """첫 1회만 warn, 이후는 debug — vision_cache 의 _first_warn_logged 패턴."""
    global _first_warn_logged
    if not _first_warn_logged:
        _first_warn_logged = True
        logger.warning("%s — 마이그 016(embed_query_cache) 적용 후 자동 회복.", msg)
    else:
        logger.debug(msg)


def _reset_first_warn_for_test() -> None:
    """단위 테스트용 — 첫 warn flag 초기화."""
    global _first_warn_logged
    _first_warn_logged = False
