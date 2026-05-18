"""v1.5 W-0 — HF Inference Providers BGE-M3 vs DeepInfra BGE-M3 결정성 시험.

목적
----
HF 자체 호스팅(현재 production) 과 DeepInfra (v1.5 W-1 swap 후보) 의 BGE-M3 dense
vector 가 같은 텍스트에 대해 얼마나 일치하는지 cosine similarity 로 측정.

PASS/FAIL gate
--------------
- PASS: 100 sample 의 **min cosine ≥ 0.999** → v1.5 W-1 어댑터 swap 안전. 기존
  `embed_query_cache`(마이그 016) + `chunks.dense_vec` 재사용 가능. 재인제스트 불필요.
- FAIL: min cosine < 0.999 → 옵션 A (HF Dedicated CPU $24/월) fallback 직행.
  embed_query_cache 도 무효 (모델 차이로 검색 회귀 우려).

비용
----
- DeepInfra: 100 chunk × 평균 500 token ≈ 50K token × $0.01/M = **<$0.001/실행**
- HF Inference Providers: free tier (무료, cold-start 가능)

API spec (OpenAI-compatible, native 대신 채택 — 어댑터 swap 일관성)
---------------------------------------------------------------
- Endpoint: `POST https://api.deepinfra.com/v1/openai/embeddings`
- Auth: `Authorization: Bearer <DEEPINFRA_API_TOKEN>`
- Body: `{"model": "BAAI/bge-m3", "input": "텍스트", "encoding_format": "float"}`
- Resp: `{"data": [{"embedding": [1024 floats], "index": 0}], "model": "...", "usage": {...}}`

실행
----
    cd api && uv run python ../evals/run_v1_5_w0_determinism.py --sample 100

exit code: PASS=0 / FAIL=1
"""

from __future__ import annotations

import argparse
import logging
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

# api/ 를 import path 에 추가 — Settings / HF provider 직접 호출.
_API_PATH = Path(__file__).resolve().parents[1] / "api"
sys.path.insert(0, str(_API_PATH))

from app.adapters.impl.bgem3_hf_embedding import (  # noqa: E402
    get_bgem3_provider,
)
from app.config import get_settings  # noqa: E402
from app.db.client import get_supabase_client  # noqa: E402

logger = logging.getLogger("v1_5_w0")

# DeepInfra API spec — OpenAI-compatible 채택.
# 이유:
# (a) v1.5 W-1 에 어댑터 swap 시 OpenAI 어댑터(W6 portfolio commit) 와 동일 인터페이스
#     → swap 검증 일관성.
# (b) Together / Anyscale 등 다른 OpenAI-compatible provider 도 같은 코드로 fallback.
_DEEPINFRA_URL = "https://api.deepinfra.com/v1/openai/embeddings"
_DEEPINFRA_MODEL = "BAAI/bge-m3"
_DENSE_DIM = 1024

# BGE-M3 max seq = 8192 token. token 측정은 어렵고 환경 의존성 추가 회피 — 4000 chars
# 클램프. 한국어 평균 1 char ≈ 0.6 token → 4000 chars ≈ 2400 token 안전.
_MAX_TEXT_CHARS = 4000

# Chunk text 길이 필터 — 너무 짧으면 의미 없고, 너무 길면 클램프 영향 큼.
_MIN_TEXT_LEN = 100
_MAX_TEXT_LEN = 2000

# Retry — DeepInfra 도 HF 만큼은 아니지만 5xx 가능. HF cold-start 는 provider 내부 retry
# 가 이미 처리하므로 여기선 DeepInfra 만 retry.
_DEEPINFRA_MAX_ATTEMPTS = 3
_DEEPINFRA_BASE_BACKOFF = 5.0
_DEEPINFRA_TIMEOUT = 60.0

# PASS/FAIL 임계.
_PASS_MIN_COSINE = 0.999

# 통계 보고 임계 — 미만 row 수 카운트.
_THRESHOLDS: tuple[float, ...] = (0.999, 0.99, 0.95)


@dataclass(frozen=True)
class SampleResult:
    chunk_id: str
    text_preview: str
    text_len: int
    hf_norm: float
    deepinfra_norm: float
    cosine: float


def _fetch_sample_chunks(n: int) -> list[dict]:
    """Supabase `chunks` 테이블에서 `text` 길이 100~2000 자 row N개 random sample.

    `ORDER BY random()` 은 행 수가 많을 때 비싸지만 본 corpus 는 ~2500 row 이라 무시 가능.
    """
    client = get_supabase_client()
    # PostgREST 는 ORDER BY random() 을 직접 지원하지 않음 — RPC 또는 OFFSET 트릭 필요.
    # 가장 단순: 전체 가져와 Python 측에서 random.sample.
    import random as _random  # 지역 import — 의존성 명확화.

    resp = (
        client.table("chunks")
        .select("id, text")
        # NULL byte / 빈 text 회피.
        .not_.is_("text", "null")
        .execute()
    )
    rows: list[dict] = list(resp.data or [])
    # 길이 필터.
    eligible = [
        r for r in rows
        if r.get("text") and _MIN_TEXT_LEN <= len(r["text"]) <= _MAX_TEXT_LEN
    ]
    if len(eligible) < n:
        logger.warning(
            "필터 통과 chunk 가 %d 개 (요청 %d) — 전체 사용",
            len(eligible),
            n,
        )
        return eligible
    return _random.sample(eligible, n)


def _embed_deepinfra(
    client: httpx.Client, headers: dict[str, str], text: str
) -> list[float]:
    """DeepInfra OpenAI-compatible `/v1/openai/embeddings` 호출 — retry 포함."""
    body = {
        "model": _DEEPINFRA_MODEL,
        "input": text,
        "encoding_format": "float",
    }
    last_exc: Exception | None = None
    for attempt in range(1, _DEEPINFRA_MAX_ATTEMPTS + 1):
        try:
            resp = client.post(_DEEPINFRA_URL, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            # 응답 schema: {"data": [{"embedding": [...], "index": 0}], ...}
            if not isinstance(data, dict) or "data" not in data:
                raise RuntimeError(
                    f"DeepInfra 응답 스키마 비정상: keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}"
                )
            items = data["data"]
            if not isinstance(items, list) or not items:
                raise RuntimeError(f"DeepInfra data 배열 비어있음: {items!r}")
            emb = items[0].get("embedding")
            if not isinstance(emb, list):
                raise RuntimeError(
                    f"DeepInfra embedding 타입 비정상: {type(emb).__name__}"
                )
            if len(emb) != _DENSE_DIM:
                raise RuntimeError(
                    f"DeepInfra 차원 불일치: got={len(emb)}, expect={_DENSE_DIM}"
                )
            return [float(x) for x in emb]
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            # 4xx 는 retry 무의미 (auth / spec 에러).
            if isinstance(exc, httpx.HTTPStatusError):
                code = exc.response.status_code
                if code in (401, 403):
                    raise RuntimeError(
                        f"DeepInfra 인증 실패 ({code}) — DEEPINFRA_API_TOKEN 확인. "
                        f"https://deepinfra.com/dash/api_keys"
                    ) from exc
                if 400 <= code < 500 and code != 429:
                    raise RuntimeError(
                        f"DeepInfra 요청 오류 ({code}): {exc.response.text[:200]}"
                    ) from exc
            if attempt == _DEEPINFRA_MAX_ATTEMPTS:
                break
            delay = _DEEPINFRA_BASE_BACKOFF * (2 ** (attempt - 1))
            logger.warning(
                "DeepInfra transient 실패 (attempt=%d/%d, %.1fs 후 재시도): %s",
                attempt,
                _DEEPINFRA_MAX_ATTEMPTS,
                delay,
                exc,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _l2_norm(v: list[float]) -> float:
    """L2 norm — pure-Python (numpy 의존성 추가 0)."""
    return math.sqrt(sum(x * x for x in v))


def _cosine(a: list[float], b: list[float]) -> float:
    """cosine = dot(a, b) / (||a|| · ||b||). 둘 다 nonzero 가정 (embed 결과)."""
    if len(a) != len(b):
        raise ValueError(f"차원 불일치: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = _l2_norm(a)
    nb = _l2_norm(b)
    if na == 0.0 or nb == 0.0:
        raise ValueError("zero-norm vector — embed 응답 비정상")
    return dot / (na * nb)


def _quantile(values: list[float], q: float) -> float:
    """단순 quantile — len>=1 가정. statistics.quantiles 는 n>=2 필요."""
    if not values:
        return float("nan")
    if len(values) == 1:
        return values[0]
    sorted_v = sorted(values)
    # nearest-rank — 작은 sample 에 충분.
    idx = max(0, min(len(sorted_v) - 1, int(round(q * (len(sorted_v) - 1)))))
    return sorted_v[idx]


def _run(sample_n: int) -> int:
    """본 routine — return: exit code (0=PASS / 1=FAIL)."""
    settings = get_settings()
    if not settings.hf_api_token:
        raise RuntimeError(
            "HF_API_TOKEN 이 .env 에 설정되지 않았습니다 — 기존 production 토큰 필요."
        )
    if not settings.deepinfra_api_token:
        raise RuntimeError(
            "DEEPINFRA_API_TOKEN 이 .env 에 설정되지 않았습니다 — "
            "https://deepinfra.com 가입 후 API key 발급 → .env 에 추가"
        )

    print(f"[setup] sample N={sample_n}, Supabase chunks 에서 길이 {_MIN_TEXT_LEN}~{_MAX_TEXT_LEN} 자 random 추출…")
    chunks = _fetch_sample_chunks(sample_n)
    if not chunks:
        raise RuntimeError("sample 가능한 chunk 가 0개 — DB 인제스트 상태 확인.")
    print(f"[setup] {len(chunks)} chunk 확보\n")

    # HF provider — singleton. in-process LRU/DB 캐시 우회 위해 매 호출 clear.
    hf_provider = get_bgem3_provider()

    # DeepInfra client.
    di_headers = {
        "Authorization": f"Bearer {settings.deepinfra_api_token}",
        "Content-Type": "application/json",
    }
    di_client = httpx.Client(timeout=_DEEPINFRA_TIMEOUT)

    results: list[SampleResult] = []
    errors: list[tuple[str, str]] = []
    try:
        for i, row in enumerate(chunks, start=1):
            chunk_id = str(row["id"])
            text = row["text"][:_MAX_TEXT_CHARS]  # BGE-M3 max seq 보호.
            preview = text[:50].replace("\n", " ")
            try:
                # HF — embed_query 의 캐시(LRU + DB) 를 우회하려면 매번 clear.
                # 캐시 사용 시 같은 결과를 반복 측정하게 되어 결정성 검증 의미 없음.
                hf_provider.clear_embed_cache()
                hf_vec = hf_provider.embed_query(text)
                di_vec = _embed_deepinfra(di_client, di_headers, text)
                cos = _cosine(hf_vec, di_vec)
                results.append(
                    SampleResult(
                        chunk_id=chunk_id,
                        text_preview=preview,
                        text_len=len(text),
                        hf_norm=_l2_norm(hf_vec),
                        deepinfra_norm=_l2_norm(di_vec),
                        cosine=cos,
                    )
                )
                if i % 10 == 0 or i == len(chunks):
                    print(
                        f"[progress] {i}/{len(chunks)} — last cosine={cos:.6f}"
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append((chunk_id, str(exc)))
                logger.exception("chunk=%s 측정 실패", chunk_id)
    finally:
        di_client.close()

    if not results:
        print("\n[FAIL] 측정 성공한 sample 0개 — 위 에러 로그 확인.")
        for cid, msg in errors[:5]:
            print(f"  - {cid}: {msg}")
        return 1

    # 통계.
    cosines = [r.cosine for r in results]
    stats_summary = {
        "n": len(cosines),
        "mean": statistics.fmean(cosines),
        "median": statistics.median(cosines),
        "p95": _quantile(cosines, 0.95),
        "p99": _quantile(cosines, 0.99),
        "min": min(cosines),
        "max": max(cosines),
    }
    below_counts = {
        thr: sum(1 for c in cosines if c < thr) for thr in _THRESHOLDS
    }

    # 마크다운 출력.
    print("\n" + "=" * 70)
    print("v1.5 W-0 — HF vs DeepInfra BGE-M3 cosine similarity")
    print("=" * 70)
    print(f"\n## 측정 요약 (n={stats_summary['n']})\n")
    print("| metric | value |")
    print("|---|---|")
    print(f"| mean   | {stats_summary['mean']:.6f} |")
    print(f"| median | {stats_summary['median']:.6f} |")
    print(f"| p95    | {stats_summary['p95']:.6f} |")
    print(f"| p99    | {stats_summary['p99']:.6f} |")
    print(f"| min    | {stats_summary['min']:.6f} |")
    print(f"| max    | {stats_summary['max']:.6f} |")

    print("\n## 임계 미만 row 수\n")
    print("| threshold | below count | ratio |")
    print("|---|---|---|")
    for thr in _THRESHOLDS:
        cnt = below_counts[thr]
        ratio = cnt / len(cosines)
        print(f"| < {thr} | {cnt} | {ratio:.1%} |")

    # 5 worst.
    worst = sorted(results, key=lambda r: r.cosine)[:5]
    print("\n## 5 worst row (cosine 오름차순)\n")
    print("| chunk_id | text_len | hf_norm | di_norm | cosine | preview |")
    print("|---|---|---|---|---|---|")
    for r in worst:
        print(
            f"| {r.chunk_id[:8]}… | {r.text_len} | {r.hf_norm:.4f} | "
            f"{r.deepinfra_norm:.4f} | {r.cosine:.6f} | {r.text_preview}… |"
        )

    if errors:
        print(f"\n## 측정 실패 {len(errors)} 건 (전체 {len(chunks)} 중)\n")
        for cid, msg in errors[:10]:
            print(f"  - {cid[:8]}…: {msg[:150]}")
        if len(errors) > 10:
            print(f"  … and {len(errors) - 10} more")

    # PASS/FAIL.
    min_cos = stats_summary["min"]
    passed = min_cos >= _PASS_MIN_COSINE
    print("\n" + "=" * 70)
    if passed:
        print(f"[PASS] min cosine = {min_cos:.6f} ≥ {_PASS_MIN_COSINE}")
        print("→ v1.5 W-1 어댑터 swap 안전. embed_query_cache + dense_vec 재사용 가능.")
    else:
        print(f"[FAIL] min cosine = {min_cos:.6f} < {_PASS_MIN_COSINE}")
        print("→ 옵션 A (HF Dedicated CPU $24/월) fallback 권장.")
        print("  embed_query_cache 무효화 (모델 차이로 검색 회귀 우려).")
    print("=" * 70)
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="v1.5 W-0 — HF vs DeepInfra BGE-M3 결정성 시험 (cosine ≥ 0.999 = PASS)"
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=100,
        help="측정 sample 수 (default 100). 작은 값(5~10)으로 smoke 가능.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="DEBUG 로그 활성.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if args.sample < 1:
        print("[error] --sample 은 1 이상.", file=sys.stderr)
        return 2

    return _run(args.sample)


if __name__ == "__main__":
    sys.exit(main())
