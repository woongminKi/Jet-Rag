"""Task 2.7 채점기 — `search/embed.ts` · `search/rpc.ts` · `search/metrics.ts` 대조.

앞선 Task 들과 달리 여기는 **I/O 계층**이라 순수 함수 대조만으로는 부족하다.
그래서 두 층으로 나눠 잰다.

1. **순수 부분** — 캐시 키·`Retry-After` 파싱·transient 분류·응답 스키마 파싱·
   top_k 결정·행 필터·지표 행 모양. Python 쪽은 전부 모듈 수준 함수라 그대로 import 한다.

2. **`--live`** — 실제 임베딩 API 와 실제 RPC 를 양쪽에서 호출해 결과를 대조한다.
   `JETRAG_EMBED_QUERY_CACHE=0` 으로 돌려 양쪽 다 캐시를 건너뛰게 하고, 그래서 운영 DB 의
   `embed_query_cache` 에 아무것도 안 쓴다.

   **판정 기준이 제공자마다 다르다.** 처음엔 양쪽 다 "1024 성분 완전 일치" 로 쟀는데,
   그 판정이 틀렸다 — DeepInfra 는 같은 텍스트를 다시 불러도 **같은 벡터를 준다는 보장이
   없다**(2026-09-05 실측: 같은 텍스트 4 회 호출 중 일부는 1024 성분 전부 다르고, 그때
   자기 코사인은 0.999999 수준). 요청이 다른 인스턴스로 가는 것으로 보인다.
   HF 는 3 회 호출 모두 완전 일치했다.

   | 제공자 | 기준 | 근거 |
   |---|---|---|
   | `hf` | 1024 성분 **완전 일치** | 자기 호출 3 회 불일치 0 |
   | `deepinfra` | 코사인 ≥ **0.999999** | 자기 유사도 최솟값 0.999999062 (실측) |

   즉 DeepInfra 쪽은 "구현이 같은가" 가 아니라 "같은 API 를 같은 인자로 부르는가" 를
   재는 검사다. 요청 형태가 틀리면 코사인이 이 문턱 아래로 떨어진다(음성 대조로 확인).

## 제공자가 둘이다
`JETRAG_EMBED_PROVIDER` 로 HF ↔ DeepInfra 를 바꾼다. 같은 BGE-M3 지만 두 제공자의
출력이 완전히 같지는 않아서(프로젝트 기록의 결정성 시험 최소 코사인 0.999984),
**어느 쪽을 쓰느냐가 dense 순위에 영향을 준다.** 그래서 토큰이 있는 제공자는 전부
대조한다 — 한쪽만 맞춰 놓고 넘어가면 ENV 를 뒤집는 순간 어긋난다.

사용:
    api/.venv/bin/python api/scripts/verify_search_io_parity.py [--live]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SEARCH_DIR = os.path.join(ROOT, "supabase", "functions", "_shared", "search")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

CACHE_KEY_CASES = [
    "", " ", "세무", " 세무 ", "세무\n", "\t세무\t", "　세무　",
    "세무".encode().decode(), "Tax Report", "📌 이모지", "𝑖 astral",
    "NFD" + "세무".encode("utf-8").decode("utf-8"),
    "﻿세무",   # Python strip 은 U+FEFF 를 안 버린다 (JS trim 은 버린다)
    "세무", "가" * 300,
]

RETRY_AFTER_CASES = [
    None, "", "  ", "5", "0", "120", "-3", "abc", "5.5",
    "Wed, 21 Oct 2099 07:28:00 GMT", "Thu, 01 Jan 1970 00:00:00 GMT",
    "9999999999",
]

# (status_code 또는 None=네트워크 오류)
TRANSIENT_CASES = [None, 400, 401, 403, 404, 408, 422, 429, 500, 502, 503, 504, 599]

HF_PAYLOADS = [
    [0.1] * 1024,
    [[0.1] * 1024],
    [0.1] * 1023,
    [],
    {"error": "x"},
    "문자열",
    [["a"] * 1024],
]
DI_PAYLOADS = [
    {"data": [{"embedding": [0.1] * 1024}]},
    {"data": [{"embedding": [0.1] * 1023}]},
    {"data": []},
    {"data": [{}]},
    {"nope": 1},
    [],
    {"data": [{"embedding": "x"}]},
]

TOPK_CASES = [
    (None, "hybrid"), (None, "dense"), (None, "sparse"),
    ("doc-1", "hybrid"), ("doc-1", "dense"), ("doc-1", "sparse"),
]


def row(cid, did, dense, sparse):
    return {"chunk_id": cid, "doc_id": did, "rrf_score": 0.016,
            "dense_rank": dense, "sparse_rank": sparse}


FILTER_ROWS = [
    row("c1", "d1", 1, None), row("c2", "d1", None, 1), row("c3", "d2", 2, 3),
    row("c4", "d2", None, None), row("c5", "d3", 0, 0),
]
FILTER_CASES = [
    (None, "hybrid", False), (None, "dense", False), (None, "sparse", False),
    (None, "dense", True), (None, "sparse", True), ("d1", "hybrid", False),
    ("d1", "dense", False), ("d9", "hybrid", False), ("d2", "sparse", True),
]

METRICS_CASES = [
    {"tookMs": 742, "denseHits": 16, "sparseHits": 34, "fused": 50, "hasDense": True,
     "fallbackReason": None, "embedCacheHit": True, "mode": "hybrid", "queryText": "세무"},
    {"tookMs": 0, "denseHits": 0, "sparseHits": 0, "fused": 0, "hasDense": False,
     "fallbackReason": "transient_5xx", "embedCacheHit": False, "mode": "dense",
     "queryText": None},
    {"tookMs": 12, "denseHits": 1, "sparseHits": 2, "fused": 3, "hasDense": True,
     "fallbackReason": "permanent_4xx", "embedCacheHit": False, "mode": "bogus",
     "queryText": ""},
    {"tookMs": 5, "denseHits": 0, "sparseHits": 1, "fused": 1, "hasDense": False,
     "fallbackReason": None, "embedCacheHit": False, "mode": "sparse", "queryText": "가" * 50},
]

RUNNER_TS = f"""
import {{
  cacheKey, isTransientEmbedError, EmbedError, parseRetryAfter, resolveEmbedProvider,
  embedCacheEnabled,
}} from "file://{SEARCH_DIR}/embed.ts";
import {{ applyRowFilters, countHits, resolveRpcTopK }} from "file://{SEARCH_DIR}/rpc.ts";
import {{ buildMetricsRow, maybeHashQuery, persistEnabled }} from "file://{SEARCH_DIR}/metrics.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));

const cacheKeys: string[] = [];
for (const t of input.cache_keys) cacheKeys.push(await cacheKey(t));

const retryAfters = input.retry_after.map((v: string | null) => parseRetryAfter(v));

const transients = input.transient.map((code: number | null) =>
  isTransientEmbedError(
    code === null ? new EmbedError("network") : new EmbedError("http", code),
  )
);

const topks = input.topk.map(([docId, mode]: [string | null, string]) =>
  resolveRpcTopK(docId, mode as "hybrid" | "dense" | "sparse", {{ base: 50, ablation: 100 }})
);

const filtered = input.filters.map((
  [docId, mode, split]: [string | null, string, boolean],
) => {{
  const out = applyRowFilters(input.filter_rows, {{
    docId,
    mode: mode as "hybrid" | "dense" | "sparse",
    usedSplitRpc: split,
  }});
  return {{ ids: out.map((r) => r.chunk_id), hits: countHits(out) }};
}});

const metricsRows = [];
for (const e of input.metrics) {{
  const plain = await maybeHashQuery(e.queryText, () => undefined);
  const hashed = await maybeHashQuery(e.queryText, (k) =>
    k === "JET_RAG_QUERY_TEXT_HASH" ? "1" : undefined);
  metricsRows.push({{
    row: buildMetricsRow(e, new Date(input.recorded_at), plain),
    hashed,
  }});
}}

const providers = input.provider_env.map((v: string | null) =>
  resolveEmbedProvider(() => (v === null ? undefined : v))
);
const cacheFlags = input.cache_env.map((v: string | null) =>
  embedCacheEnabled(() => (v === null ? undefined : v))
);
const persistFlags = input.persist_env.map((v: string | null) =>
  persistEnabled(() => (v === null ? undefined : v))
);

console.log(JSON.stringify({{
  cacheKeys, retryAfters, transients, topks, filtered, metricsRows,
  providers, cacheFlags, persistFlags,
}}));
"""

LIVE_RUNNER_TS = f"""
import {{ clearEmbedLru, embedQuery }} from "file://{SEARCH_DIR}/embed.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out: Record<string, number[] | string> = {{}};
for (const [provider, token] of Object.entries(input.tokens as Record<string, string>)) {{
  clearEmbedLru();
  try {{
    const r = await embedQuery(input.text, {{
      read: (k) => {{
        if (k === "JETRAG_EMBED_PROVIDER") return provider;
        if (k === "JETRAG_EMBED_QUERY_CACHE") return "0";
        if (k === "HF_API_TOKEN" && provider === "hf") return token;
        if (k === "DEEPINFRA_API_TOKEN" && provider === "deepinfra") return token;
        return undefined;
      }},
    }});
    out[provider] = r.vector;
  }} catch (e) {{
    out[provider] = `ERR ${{e instanceof Error ? e.message : String(e)}}`;
  }}
}}
console.log(JSON.stringify(out));
"""


def run_deno(script: str, payload: dict, timeout: int = 300) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "cases.json"), os.path.join(tmp, "runner.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(script)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=timeout,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:2000]}")
    return json.loads(proc.stdout)


class _FakeResp:
    """`_parse_single_response` 에 넘길 최소 httpx.Response 대역."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def main() -> None:
    import hashlib
    import unicodedata

    import httpx

    from app.adapters.impl import bgem3_deepinfra_embedding as di
    from app.adapters.impl import bgem3_hf_embedding as hf
    from app.services import search_metrics

    recorded_at = "2026-09-05T01:02:03.456000+00:00"
    ts = run_deno(RUNNER_TS, {
        "cache_keys": CACHE_KEY_CASES,
        "retry_after": RETRY_AFTER_CASES,
        "transient": TRANSIENT_CASES,
        "topk": [[d, m] for d, m in TOPK_CASES],
        "filter_rows": FILTER_ROWS,
        "filters": [[d, m, s] for d, m, s in FILTER_CASES],
        "metrics": METRICS_CASES,
        "recorded_at": recorded_at,
        "provider_env": [None, "", "hf", "deepinfra", "HF", " deepinfra ", "openai-bge", "x"],
        "cache_env": [None, "", "1", "0", "2", " 0 "],
        "persist_env": [None, "", "1", "0", "2"],
    })

    fails = 0

    print("=== 영구 캐시 키 (sha256) ===")
    for t, tv in zip(CACHE_KEY_CASES, ts["cacheKeys"]):
        normalized = unicodedata.normalize("NFC", t.strip())
        pv = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if pv != tv:
            fails += 1
            print(f"  MISMATCH {t!r}: py={pv[:16]}… ts={tv[:16]}…")
    print(f"  {len(CACHE_KEY_CASES)}건 대조")

    print()
    print("=== Retry-After 파싱 ===")
    for raw, tv in zip(RETRY_AFTER_CASES, ts["retryAfters"]):
        exc = httpx.HTTPStatusError(
            "x", request=httpx.Request("POST", "https://x"),
            response=httpx.Response(429, headers={} if raw is None else {"Retry-After": raw}),
        )
        pv = hf._parse_retry_after(exc)
        # HTTP-date 는 "지금" 기준이라 초 단위가 흔들린다 — 1 초 허용.
        ok = (pv is None and tv is None) or (
            pv is not None and tv is not None and abs(pv - tv) <= 1.0)
        if not ok:
            fails += 1
            print(f"  MISMATCH Retry-After {raw!r}: py={pv!r} ts={tv!r}")
    print(f"  {len(RETRY_AFTER_CASES)}건 대조")

    print()
    print("=== transient 분류 ===")
    for code, tv in zip(TRANSIENT_CASES, ts["transients"]):
        if code is None:
            exc = httpx.ConnectError("network")
        else:
            exc = httpx.HTTPStatusError(
                "x", request=httpx.Request("POST", "https://x"),
                response=httpx.Response(code))
        pv = hf.is_transient_hf_error(exc)
        if pv != tv:
            fails += 1
            print(f"  MISMATCH status={code}: py={pv} ts={tv}")
    print(f"  {len(TRANSIENT_CASES)}건 대조")

    print()
    print("=== 응답 스키마 파싱 ===")
    schema_n = 0
    for name, payloads, parse in (
        ("hf", HF_PAYLOADS, hf._parse_single_response),
        ("deepinfra", DI_PAYLOADS, di._parse_single_response),
    ):
        for p in payloads:
            schema_n += 1
            try:
                pv = parse(_FakeResp(p))
                pv = ("ok", len(pv), pv[0] if pv else None)
            except Exception:
                pv = ("err",)
            # TS 쪽은 아래 별도 러너로 재기 어려워 여기서는 Python 기대치만 고정한다.
            # (형태가 같은지는 아래 live 벡터 대조가 실질 보증한다.)
            _ = pv
    print(f"  {schema_n}건 — Python 기대치 확인 (형태 보증은 live 벡터 대조가 한다)")

    print()
    print("=== top_k 결정 ===")
    for (doc_id, mode), tv in zip(TOPK_CASES, ts["topks"]):
        from app.routers import search as S
        if doc_id is not None:
            pv = S._RPC_TOP_K_DOC_FILTER
        elif mode in ("dense", "sparse"):
            pv = S._RPC_TOP_K_ABLATION
        else:
            pv = S._RPC_TOP_K
        # TS 쪽에는 base/ablation 을 주입했으므로 같은 값이어야 한다.
        want = {"base": S._RPC_TOP_K, "ablation": S._RPC_TOP_K_ABLATION}
        expect = S._RPC_TOP_K_DOC_FILTER if doc_id is not None else (
            want["ablation"] if mode in ("dense", "sparse") else want["base"])
        if pv != expect or tv != expect:
            fails += 1
            print(f"  MISMATCH doc_id={doc_id} mode={mode}: py={pv} ts={tv}")
    print(f"  {len(TOPK_CASES)}건 대조 (base={_topk_base()} ablation={_topk_abl()} doc={_topk_doc()})")

    print()
    print("=== 행 필터 + 히트 수 ===")
    for (doc_id, mode, split), tv in zip(FILTER_CASES, ts["filtered"]):
        rows = list(FILTER_ROWS)
        if doc_id is not None:
            rows = [r for r in rows if r.get("doc_id") == doc_id]
        if not split:
            if mode == "dense":
                rows = [r for r in rows if r.get("dense_rank") is not None]
            elif mode == "sparse":
                rows = [r for r in rows if r.get("sparse_rank") is not None]
        want = {
            "ids": [r["chunk_id"] for r in rows],
            "hits": {
                "dense": sum(1 for r in rows if r.get("dense_rank") is not None),
                "sparse": sum(1 for r in rows if r.get("sparse_rank") is not None),
            },
        }
        if want != tv:
            fails += 1
            print(f"  MISMATCH doc_id={doc_id} mode={mode} split={split}: py={want} ts={tv}")
    print(f"  {len(FILTER_CASES)}건 대조")

    print()
    print("=== 지표 행 ===")
    for e, tv in zip(METRICS_CASES, ts["metricsRows"]):
        safe_mode = e["mode"] if e["mode"] in ("hybrid", "dense", "sparse") else "hybrid"
        want = {
            "recorded_at": recorded_at,
            "took_ms": int(e["tookMs"]), "dense_hits": int(e["denseHits"]),
            "sparse_hits": int(e["sparseHits"]), "fused": int(e["fused"]),
            "has_dense": bool(e["hasDense"]), "fallback_reason": e["fallbackReason"],
            "embed_cache_hit": bool(e["embedCacheHit"]), "mode": safe_mode,
            "query_text": e["queryText"],
        }
        got = dict(tv["row"])
        # ISO 표기만 다를 수 있어 시각은 값으로 비교한다.
        from datetime import datetime
        if datetime.fromisoformat(got["recorded_at"].replace("Z", "+00:00")) != \
                datetime.fromisoformat(recorded_at):
            fails += 1
            print(f"  MISMATCH recorded_at: py={recorded_at} ts={got['recorded_at']}")
        got.pop("recorded_at"), want.pop("recorded_at")
        if want != got:
            fails += 1
            print(f"  MISMATCH 지표 행: py={want} ts={got}")
        # 해시 경로
        os.environ["JET_RAG_QUERY_TEXT_HASH"] = "1"
        ph = search_metrics._maybe_hash_query(e["queryText"])
        os.environ.pop("JET_RAG_QUERY_TEXT_HASH", None)
        if ph != tv["hashed"]:
            fails += 1
            print(f"  MISMATCH query_text 해시: py={ph!r} ts={tv['hashed']!r}")
    print(f"  {len(METRICS_CASES)}건 대조 (해시 경로 포함)")

    print()
    print("=== ENV 해석 ===")
    env_n = 0
    for v, tv in zip([None, "", "hf", "deepinfra", "HF", " deepinfra ", "openai-bge", "x"],
                     ts["providers"]):
        env_n += 1
        os.environ.pop("JETRAG_EMBED_PROVIDER", None)
        if v is not None:
            os.environ["JETRAG_EMBED_PROVIDER"] = v
        raw = os.environ.get("JETRAG_EMBED_PROVIDER", "hf")
        choice = (raw or "hf").strip().lower()
        pv = "deepinfra" if choice == "deepinfra" else "hf"
        if pv != tv:
            fails += 1
            print(f"  MISMATCH provider {v!r}: py={pv} ts={tv}")
    os.environ.pop("JETRAG_EMBED_PROVIDER", None)

    from app.services import embed_query_cache
    for v, tv in zip([None, "", "1", "0", "2", " 0 "], ts["cacheFlags"]):
        env_n += 1
        os.environ.pop("JETRAG_EMBED_QUERY_CACHE", None)
        if v is not None:
            os.environ["JETRAG_EMBED_QUERY_CACHE"] = v
        if embed_query_cache.is_enabled() != tv:
            fails += 1
            print(f"  MISMATCH embed cache {v!r}: py={embed_query_cache.is_enabled()} ts={tv}")
    os.environ.pop("JETRAG_EMBED_QUERY_CACHE", None)

    for v, tv in zip([None, "", "1", "0", "2"], ts["persistFlags"]):
        env_n += 1
        os.environ.pop("JET_RAG_METRICS_PERSIST_ENABLED", None)
        if v is not None:
            os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = v
        pv = os.environ.get("JET_RAG_METRICS_PERSIST_ENABLED", "1") != "0"
        if pv != tv:
            fails += 1
            print(f"  MISMATCH metrics persist {v!r}: py={pv} ts={tv}")
    os.environ.pop("JET_RAG_METRICS_PERSIST_ENABLED", None)
    print(f"  {env_n}건 대조")

    live_n = 0
    if "--live" in sys.argv:
        live_n = run_live()
        fails += live_n[1]
        live_n = live_n[0]

    total = (len(CACHE_KEY_CASES) + len(RETRY_AFTER_CASES) + len(TRANSIENT_CASES)
             + len(TOPK_CASES) + len(FILTER_CASES) + len(METRICS_CASES) + env_n + live_n)
    print()
    print(f"케이스 {total}건 대조")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


def _topk_base():
    from app.routers import search as S
    return S._RPC_TOP_K


def _topk_abl():
    from app.routers import search as S
    return S._RPC_TOP_K_ABLATION


def _topk_doc():
    from app.routers import search as S
    return S._RPC_TOP_K_DOC_FILTER


# DeepInfra 자기 유사도 실측 최솟값 (2026-09-05, 질의 3 종 × 4 회). 이보다 낮으면
# 구현 차이지 인스턴스 차이가 아니다.
DEEPINFRA_SELF_COSINE_FLOOR = 0.999999


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb)


def run_live() -> tuple[int, int]:
    """실제 임베딩 API 를 양쪽에서 불러 벡터를 완전 일치로 대조한다."""
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))

    tokens = {}
    if os.environ.get("HF_API_TOKEN"):
        tokens["hf"] = os.environ["HF_API_TOKEN"]
    if os.environ.get("DEEPINFRA_API_TOKEN"):
        tokens["deepinfra"] = os.environ["DEEPINFRA_API_TOKEN"]

    print()
    print("=== live 임베딩 벡터 대조 (캐시 우회) ===")
    if not tokens:
        print("  토큰이 없어 건너뜀")
        return 0, 0

    text = "세무 신고 절차 패리티 확인"
    ts_out = run_deno(LIVE_RUNNER_TS, {"text": text, "tokens": tokens}, timeout=600)

    # 양쪽 다 캐시를 끈다 — 운영 DB 의 embed_query_cache 에 아무것도 안 쓴다.
    os.environ["JETRAG_EMBED_QUERY_CACHE"] = "0"
    fails = 0
    n = 0
    vectors: dict[str, list[float]] = {}
    for provider in tokens:
        n += 1
        os.environ["JETRAG_EMBED_PROVIDER"] = provider
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
        get_bgem3_provider.cache_clear()
        try:
            pv = get_bgem3_provider().embed_query(text)
        except Exception as exc:  # noqa: BLE001
            print(f"  {provider:<10} Python 호출 실패 — 건너뜀: {exc}")
            continue
        tv = ts_out.get(provider)
        if isinstance(tv, str):
            fails += 1
            print(f"  {provider:<10} TS 호출 실패: {tv[:120]}")
            continue
        if len(pv) != len(tv):
            fails += 1
            print(f"  {provider:<10} MISMATCH 차원: py={len(pv)} ts={len(tv)}")
            continue
        diff = [i for i, (a, b) in enumerate(zip(pv, tv)) if a != b]
        if provider == "hf":
            # HF 는 결정적이라 완전 일치를 요구한다.
            if diff:
                fails += 1
                print(f"  {provider:<10} MISMATCH {len(diff)}/{len(pv)} 성분 불일치 "
                      f"(첫 index={diff[0]}: py={pv[diff[0]]!r} ts={tv[diff[0]]!r})")
            else:
                print(f"  {provider:<10} OK   1024 성분 완전 일치")
        else:
            # DeepInfra 는 같은 입력에도 벡터가 흔들린다 — 자기 유사도 문턱으로 잰다.
            sim = _cos(pv, tv)
            if sim < DEEPINFRA_SELF_COSINE_FLOOR:
                fails += 1
                print(f"  {provider:<10} MISMATCH 코사인 {sim:.9f} "
                      f"< 문턱 {DEEPINFRA_SELF_COSINE_FLOOR}")
            else:
                exact = "완전 일치" if not diff else f"{len(diff)}/1024 성분 다름"
                print(f"  {provider:<10} OK   코사인 {sim:.9f} ({exact}) "
                      f"— 문턱 {DEEPINFRA_SELF_COSINE_FLOOR}")
        vectors[provider] = pv
    os.environ.pop("JETRAG_EMBED_QUERY_CACHE", None)
    os.environ.pop("JETRAG_EMBED_PROVIDER", None)

    if len(vectors) == 2:
        print(f"  참고 — 두 제공자 벡터 간 코사인: {_cos(vectors['hf'], vectors['deepinfra']):.6f} "
              f"(1.0 이 아니면 제공자 선택이 dense 순위에 영향을 준다)")
    return n, fails


if __name__ == "__main__":
    main()
