"""Task 2.5 채점기 — `search/intent.ts` · `search/mmr.ts` 동등성 대조.

`intent_router.route`·`mmr.rerank`·`_coerce_embedding` 은 전부 모듈 수준 함수라
**그대로 import 해서 부른다.** 복사본이 없으니 원본이 바뀌면 바로 드러난다.

## 왜 이 둘이 위험한가
신호 하나가 잘못 발화하면 결과가 통째로 달라진다.

| 신호 | 효과 |
|---|---|
| T1 단독 | MMR 재정렬 — **문서 순서가 바뀐다** |
| T1·T2·T7 | 미리보기 청크 cap 3 → 8 |

MMR 은 운영에서 **켜져 있다**(`JETRAG_MMR_DISABLE` 미설정). T1 질의에서 실제로 순서를
바꾸므로 greedy 선택의 동률 처리까지 맞아야 한다.

## 부동소수 — 이 스크립트가 답하는 질문
원본 `_cosine` 은 `x ** 0.5` 를 쓴다. 이건 libm `pow` 라 IEEE754 가 정확한 반올림을
요구하는 `sqrt` 와 다를 수 있다. 실제로 macOS 에서 재면 20 만 건 중 261 건이 **1 ulp**
어긋난다. 운영은 Linux/glibc 라 로컬 결과를 그대로 근거로 쓸 수 없다.

그래서 libm 종류를 묻지 않는 실험을 한다 — **Python 안에서** `**0.5` 판과
`math.sqrt` 판으로 각각 MMR 을 돌려 **선택 순서가 달라지는지** 본다. 안 달라지면
어느 libm 이든 결과가 같다는 뜻이라 질문 자체가 닫힌다. 결과는 아래 "sqrt 민감도"
절에 수치로 출력된다.

`--live` 는 운영 documents 의 실제 `doc_embedding`(1024 차원)으로 MMR 을 돌린다.

사용:
    api/.venv/bin/python api/scripts/verify_search_intent_mmr_parity.py [--live]
"""

from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SEARCH_DIR = os.path.join(ROOT, "supabase", "functions", "_shared", "search")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

# ---------------------------------------------------------------- 질의 케이스

BASE_QUERIES = [
    # T1 — cross-doc (원래 룰)
    "자료랑 문서 비교해줘", "보고서와 자료의 차이", "문서 및 자료",
    "자료" + "가" * 16 + "와 문서",          # .{0,15} 창 경계 (밖)
    "자료" + "가" * 15 + "와 문서",          # 창 경계 (안)
    # T1 — P1 보조 3종
    "기웅민 이력서와 이한주 포트폴리오", "law sample2와 law sample3 두 판결",
    "이력서와 김철수", "판결들에서 찾아줘", "문서들 중에서", "논문들에",
    "사업과 계획", "카탈로그와 매뉴얼", "내규와 규정",
    # T1 이 아니어야 하는 것
    "다른 사람", "과일", "랑데뷰", "사과와 배",
    # T2
    "차이가 뭐야", "비교해줘", "A vs B", "달라진 게 뭐지", "대비 분석",
    "무엇이 다른가", "어떻게 다르지", "다릅니다", "상이한 점", "다르게 처리",
    # T3
    "왜 그런가", "이유가 뭐야", "때문에", "원인 분석", "어째서 실패했나",
    # T4
    "달라진 점", "바뀐 내용", "변경 사항", "수정된 부분", "업데이트 내역",
    # T5 — 길이 임계
    "가" * 39, "가" * 40, "가" * 41,
    " ".join(["가"] * 11), " ".join(["가"] * 12), " ".join(["가"] * 13),
    "𝑖" * 39, "𝑖" * 40,                     # astral — 코드포인트로 세야 한다
    # T6
    "그거 어디있지", "그때 그 자료", "그 문서", "어디였더라", "뭐였지",
    "어떻게 됐더라", "그것", "그",           # "그 " 는 뒤 공백까지가 키워드
    # T7 — 조사 횟수
    "사과랑 배랑", "결과과 원인과", "랑랑", "과과", "사과랑 배",
    # T1 과 T7 이 동시에 성립하는 질의 — T7 은 T1 이 안 떴을 때만 봐야 한다.
    "자료랑 문서랑 비교", "자료랑 문서랑 보고서랑", "문서과 자료과 결과과",
    "이력서와 포트폴리오랑 판결랑",
    # 공백·정규화
    "  자료랑   문서  ", "자료랑\t문서", "자료랑\n문서", "자료랑　문서",
    "자료랑 문서", "자료랑문서", "자료랑﻿문서",
    # 빈 질의 — 원본은 ValueError
    "", " ", "\t", "　", "",
    # 실제로 들어오는 질의
    "세무", "정기결제 해지와 환불 절차가 어떻게 되는지",
    "데이터센터 지원 사업의 신청 자격", "전폭은 얼마인가요?",
]

LAMBDA_ENV_CASES = [
    None, "", "0.7", "0", "1", "0.0", "1.0", "0.5", " 0.5 ", "-0.1", "1.1",
    "abc", "1e-1", "0_5", "1_0", "inf", "-inf", "nan", "NaN", "  ", "+0.5",
    ".5", "5.", "1e400", "0x1", "０.５",
]
DISABLE_ENV_CASES = [None, "", "1", "0", " 1 ", "1 ", "true", "01", "2"]

EMBED_COERCE_CASES = [
    None, "", [], "[]", "[1.0,2.0,3.0]", "1.0,2.0", "[[1.0,2.0]]",
    "[1.0, 2.0, 3.0]", "[abc]", "[1.0,]", [1.0] * 1024, [1.0] * 3,
    ["1.0"] * 1024, "[nan,1.0]", "[inf,1.0]", 0, 1,
]


def synth_mmr_cases(seed: int) -> list[dict]:
    rng = random.Random(seed)
    cases: list[dict] = []

    def vec(n: int) -> list[float]:
        return [rng.uniform(-1, 1) for _ in range(n)]

    cases.append({"name": "빈 후보", "ids": [], "rel": {}, "emb": {}, "top_k": 5, "lam": 0.7})
    cases.append({"name": "top_k 0", "ids": ["a", "b"], "rel": {"a": 1.0, "b": 0.5},
                  "emb": {}, "top_k": 0, "lam": 0.7})
    cases.append({"name": "embedding 전무 → relevance 순", "ids": ["b", "a", "c"],
                  "rel": {"a": 0.3, "b": 0.9, "c": 0.6}, "emb": {}, "top_k": 3, "lam": 0.7})
    cases.append({"name": "relevance 전부 동률 → id 사전순", "ids": ["z", "a", "m"],
                  "rel": {"z": 0.5, "a": 0.5, "m": 0.5}, "emb": {}, "top_k": 3, "lam": 0.7})
    cases.append({"name": "relevance 결측 → 0.0", "ids": ["a", "b"], "rel": {"a": 0.1},
                  "emb": {}, "top_k": 2, "lam": 0.7})
    cases.append({"name": "λ=1.0 → 순수 relevance", "ids": ["a", "b", "c"],
                  "rel": {"a": 0.1, "b": 0.9, "c": 0.5},
                  "emb": {"a": vec(8), "b": vec(8), "c": vec(8)}, "top_k": 3, "lam": 1.0})
    cases.append({"name": "λ=0.0 → 순수 다양성", "ids": ["a", "b", "c"],
                  "rel": {"a": 0.1, "b": 0.9, "c": 0.5},
                  "emb": {"a": vec(8), "b": vec(8), "c": vec(8)}, "top_k": 3, "lam": 0.0})
    cases.append({"name": "차원 불일치 → sim None", "ids": ["a", "b"],
                  "rel": {"a": 0.5, "b": 0.4},
                  "emb": {"a": vec(8), "b": vec(4)}, "top_k": 2, "lam": 0.7})
    cases.append({"name": "영벡터 → sim None", "ids": ["a", "b"],
                  "rel": {"a": 0.5, "b": 0.4},
                  "emb": {"a": [0.0] * 8, "b": vec(8)}, "top_k": 2, "lam": 0.7})
    cases.append({"name": "동일 벡터 2개 (sim=1) — 다양성이 밀어낸다",
                  "ids": ["a", "b", "c"], "rel": {"a": 0.9, "b": 0.89, "c": 0.5},
                  "emb": (lambda v, w: {"a": v, "b": list(v), "c": w})(vec(8), vec(8)),
                  "top_k": 3, "lam": 0.7})
    cases.append({"name": "top_k 가 후보보다 작음", "ids": [f"d{i}" for i in range(8)],
                  "rel": {f"d{i}": rng.random() for i in range(8)},
                  "emb": {f"d{i}": vec(16) for i in range(8)}, "top_k": 3, "lam": 0.7})

    # 무작위 스트레스 — 동률·결측을 섞는다.
    for n in range(40):
        k = rng.randint(2, 9)
        ids = [f"id{rng.randrange(1000):04d}" for _ in range(k)]
        ids = list(dict.fromkeys(ids))
        rel = {i: rng.choice([0.0, 0.5, 0.5, rng.random()]) for i in ids}
        emb = {i: vec(24) for i in ids if rng.random() > 0.3}
        cases.append({"name": f"무작위 {n}", "ids": ids, "rel": rel, "emb": emb,
                      "top_k": rng.randint(1, k), "lam": rng.choice([0.0, 0.3, 0.7, 1.0])})
    return cases


RUNNER_TS = f"""
import {{ isCrossDocClassQuery, isCrossDocQuery, route }} from "file://{SEARCH_DIR}/intent.ts";
import {{
  coerceEmbedding, cosine, isDisabled, rerank, resolveLambda,
}} from "file://{SEARCH_DIR}/mmr.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));

const intents = input.queries.map((q: string) => {{
  const d = route(q);
  return d === null ? null : {{
    needs_decomposition: d.needsDecomposition,
    triggered_signals: d.triggeredSignals,
    confidence_score: d.confidenceScore,
    query_normalized: d.queryNormalized,
    matched_keywords: d.matchedKeywords,
    cross_doc: isCrossDocQuery(q, d),
    cross_doc_class: isCrossDocClassQuery(q, d),
  }};
}});

const lambdas = input.lambda_env.map((v: string | null) =>
  resolveLambda(() => (v === null ? undefined : v))
);
const disables = input.disable_env.map((v: string | null) =>
  isDisabled(() => (v === null ? undefined : v))
);
// NaN·Infinity 는 JSON 으로 나가면 null 이 돼 구분이 사라진다 — 문자열로 표시한다.
const enc = (x: number) =>
  Number.isNaN(x) ? "NaN" : (Number.isFinite(x) ? x : (x > 0 ? "Infinity" : "-Infinity"));
const coerced = input.coerce.map((v: unknown) => {{
  const r = coerceEmbedding(v);
  return r === null ? null : r.map(enc);
}});

// deno-lint-ignore no-explicit-any
const mmr = input.mmr.map((c: any) =>
  rerank(c.ids, {{
    relevance: new Map(Object.entries(c.rel as Record<string, number>)),
    embeddingsById: new Map(Object.entries(c.emb as Record<string, number[]>)),
    topK: c.top_k,
    lambda: c.lam,
  }})
);

const cosines = input.cosines.map(([a, b]: [number[], number[]]) => {{
  const v = cosine(a, b);
  return v === null ? null : enc(v);
}});

console.log(JSON.stringify({{ intents, lambdas, disables, coerced, mmr, cosines }}));
"""


def run_deno(payload: dict) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "cases.json"), os.path.join(tmp, "runner.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=600,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:2000]}")
    return json.loads(proc.stdout)


def live_mmr_cases() -> list[dict]:
    """운영 documents 의 실제 doc_embedding 으로 MMR 케이스를 만든다."""
    import os as _os

    from dotenv import load_dotenv
    from supabase import create_client

    from app.routers.search import _coerce_embedding

    load_dotenv(os.path.join(ROOT, ".env"))
    c = create_client(_os.environ["SUPABASE_URL"], _os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    rows = c.table("documents").select("id, doc_embedding").is_(
        "deleted_at", "null").limit(200).execute().data or []

    emb: dict[str, list[float]] = {}
    for r in rows:
        v = _coerce_embedding(r.get("doc_embedding"))
        if v is not None:
            emb[r["id"]] = v
    ids = sorted(emb)
    if len(ids) < 2:
        print(f"  (문서 임베딩이 {len(ids)}건뿐이라 live MMR 케이스를 못 만든다)")
        return []

    rng = random.Random(20260905)
    cases = []
    for n in range(12):
        k = min(len(ids), rng.randint(2, 8))
        pick = rng.sample(ids, k)
        # 실제 RRF 점수 규모 — 1/(60+rank) 근방.
        rel = {i: 1.0 / (60 + rng.randint(1, 50)) for i in pick}
        cases.append({"name": f"[live] 문서 {k}건 #{n}", "ids": pick, "rel": rel,
                      "emb": {i: emb[i] for i in pick},
                      "top_k": k, "lam": rng.choice([0.0, 0.5, 0.7, 1.0])})
    # 전부 동률 — 다양성 항만으로 순서가 정해지는 최악의 경우.
    pick = ids[: min(8, len(ids))]
    cases.append({"name": "[live] relevance 전부 동률", "ids": pick,
                  "rel": {i: 0.016 for i in pick}, "emb": {i: emb[i] for i in pick},
                  "top_k": len(pick), "lam": 0.7})
    return cases


# ---------------------------------------------------------------- sqrt 민감도

def sqrt_sensitivity(cases: list[dict]) -> tuple[int, int, float]:
    """`x ** 0.5` 판과 `math.sqrt` 판으로 각각 MMR 을 돌려 순서가 갈리는지 센다.

    (갈린 케이스 수, 코사인이 실제로 달랐던 케이스 수, 최소 결정 마진) 을 돌려준다.
    최소 마진은 greedy 각 단계에서 1 등과 2 등 점수의 차 중 최솟값이다 — 이게 1 ulp
    보다 훨씬 크면 sqrt 의 마지막 자리 차이가 순서를 못 바꾼다는 직접 근거가 된다.
    """
    def cos(a, b, root):
        if len(a) != len(b):
            return None
        dot = na = nb = 0.0
        for x, y in zip(a, b):
            dot += x * y
            na += x * x
            nb += y * y
        if na <= 0.0 or nb <= 0.0:
            return None
        return dot / (root(na) * root(nb))

    def run(case, root):
        ids, rel, emb = case["ids"], case["rel"], case["emb"]
        top_k, lam = case["top_k"], case["lam"]
        if not ids or top_k <= 0:
            return [], math.inf
        remaining = list(ids)
        selected = [sorted(remaining, key=lambda c: (-rel.get(c, 0.0), c))[0]]
        remaining.remove(selected[0])
        margin = math.inf
        while remaining and len(selected) < top_k:
            scored = []
            for cid in sorted(remaining):
                e = emb.get(cid)
                sim = 0.0
                if e is not None:
                    for sid in selected:
                        es = emb.get(sid)
                        if es is None:
                            continue
                        s = cos(e, es, root)
                        if s is not None and s > sim:
                            sim = s
                scored.append((lam * rel.get(cid, 0.0) - (1.0 - lam) * sim, cid))
            best = max(s for s, _ in scored)
            others = [s for s, _ in scored if s != best]
            if others:
                margin = min(margin, best - max(others))
            pick = next(c for s, c in scored if s == best)
            selected.append(pick)
            remaining.remove(pick)
        return selected, margin

    order_diff = 0
    cos_diff = 0
    min_margin = math.inf
    for c in cases:
        a, ma = run(c, lambda v: v ** 0.5)
        b, _ = run(c, math.sqrt)
        if a != b:
            order_diff += 1
        min_margin = min(min_margin, ma)
        # 코사인 자체가 달랐는지 — 순서가 같아도 값은 다를 수 있다.
        for e1 in c["emb"].values():
            for e2 in c["emb"].values():
                if cos(e1, e2, lambda v: v ** 0.5) != cos(e1, e2, math.sqrt):
                    cos_diff += 1
                    break
            else:
                continue
            break
    return order_diff, cos_diff, min_margin


def _enc(x):
    """NaN·Infinity 를 JSON 왕복에서 잃지 않게 문자열로 표시 (TS 쪽과 같은 규약)."""
    if x is None:
        return None
    if isinstance(x, float):
        if math.isnan(x):
            return "NaN"
        if math.isinf(x):
            return "Infinity" if x > 0 else "-Infinity"
    return x


def _ulp_diff(a, b):
    """두 double 의 ulp 차. 비교 불가면 None."""
    import struct
    if not isinstance(a, float) or not isinstance(b, (int, float)):
        return None
    ia = struct.unpack("<q", struct.pack("<d", a))[0]
    ib = struct.unpack("<q", struct.pack("<d", float(b)))[0]
    return ia - ib


def main() -> None:
    from app.routers.search import (
        _coerce_embedding,
        _is_cross_doc_class_query,
        _is_cross_doc_query,
    )
    from app.services import intent_router, mmr

    mmr_cases = synth_mmr_cases(20260905)
    live_n = 0
    if "--live" in sys.argv:
        live = live_mmr_cases()
        live_n = len(live)
        mmr_cases += live

    rng = random.Random(7)
    cosine_cases = [
        ([1.0, 0.0], [1.0, 0.0]), ([1.0, 0.0], [0.0, 1.0]), ([1.0], [1.0, 2.0]),
        ([0.0, 0.0], [1.0, 1.0]), ([1e-200] * 4, [1e-200] * 4),
        ([1e200] * 4, [1e200] * 4),
    ] + [([rng.uniform(-1, 1) for _ in range(64)],
          [rng.uniform(-1, 1) for _ in range(64)]) for _ in range(300)]

    ts = run_deno({
        "queries": BASE_QUERIES,
        "lambda_env": LAMBDA_ENV_CASES,
        "disable_env": DISABLE_ENV_CASES,
        "coerce": EMBED_COERCE_CASES,
        "mmr": mmr_cases,
        "cosines": [[a, b] for a, b in cosine_cases],
    })

    fails = 0

    print("=== intent_router.route ===")
    for q, tv in zip(BASE_QUERIES, ts["intents"]):
        try:
            d = intent_router.route(q)
            want = {
                "needs_decomposition": d.needs_decomposition,
                "triggered_signals": list(d.triggered_signals),
                "confidence_score": d.confidence_score,
                "query_normalized": d.query_normalized,
                "matched_keywords": list(d.matched_keywords),
                "cross_doc": _is_cross_doc_query(q, decision=d),
                "cross_doc_class": _is_cross_doc_class_query(q, decision=d),
            }
        except ValueError:
            want = None
        if want != tv:
            fails += 1
            print(f"  MISMATCH {q[:38]!r}")
            print(f"      py={want}")
            print(f"      ts={tv}")
    print(f"  {len(BASE_QUERIES)}건 대조")

    print()
    print("=== ENV 해석 ===")
    for v, tv in zip(LAMBDA_ENV_CASES, ts["lambdas"]):
        os.environ.pop(mmr._ENV_LAMBDA, None)
        if v is not None:
            os.environ[mmr._ENV_LAMBDA] = v
        pv = mmr.resolve_lambda()
        same = pv == tv or (isinstance(pv, float) and math.isnan(pv) and tv is None)
        if not same:
            fails += 1
            print(f"  MISMATCH lambda {v!r}: py={pv!r} ts={tv!r}")
    os.environ.pop(mmr._ENV_LAMBDA, None)
    for v, tv in zip(DISABLE_ENV_CASES, ts["disables"]):
        os.environ.pop(mmr._ENV_DISABLE, None)
        if v is not None:
            os.environ[mmr._ENV_DISABLE] = v
        pv = mmr.is_disabled()
        if pv != tv:
            fails += 1
            print(f"  MISMATCH disable {v!r}: py={pv} ts={tv}")
    os.environ.pop(mmr._ENV_DISABLE, None)
    print(f"  {len(LAMBDA_ENV_CASES) + len(DISABLE_ENV_CASES)}건 대조")

    print()
    print("=== _coerce_embedding ===")
    for v, tv in zip(EMBED_COERCE_CASES, ts["coerced"]):
        raw = _coerce_embedding(v)
        pv = None if raw is None else [_enc(x) for x in raw]
        if pv != tv:
            fails += 1
            print(f"  MISMATCH coerce {str(v)[:24]!r}: py={str(pv)[:40]} ts={str(tv)[:40]}")
    print(f"  {len(EMBED_COERCE_CASES)}건 대조")

    print()
    print("=== cosine ===")
    # libm 차이는 마지막 자리에서 1 ulp 까지 난다(위 docstring). 정확 일치 / 1 ulp /
    # 그 이상 을 나눠 세고, **1 ulp 를 넘으면 FAIL** 이다.
    exact = ulp1 = 0
    for (a, b), tv in zip(cosine_cases, ts["cosines"]):
        pv = _enc(mmr._cosine(a, b))
        if pv == tv:
            exact += 1
            continue
        d = _ulp_diff(pv, tv)
        if d is not None and abs(d) <= 1:
            ulp1 += 1
            continue
        fails += 1
        print(f"  MISMATCH cosine dim={len(a)}/{len(b)}: py={pv!r} ts={tv!r} (ulp={d})")
    print(f"  {len(cosine_cases)}건 대조 — 정확 일치 {exact} / 1 ulp 차 {ulp1} (libm, 허용)")

    print()
    print("=== MMR rerank ===")
    for c, tv in zip(mmr_cases, ts["mmr"]):
        pv = mmr.rerank(c["ids"], relevance=c["rel"], embeddings_by_id=c["emb"],
                        top_k=c["top_k"], lambda_=c["lam"])
        if pv != tv:
            fails += 1
            print(f"  MISMATCH {c['name']}: py={pv} ts={tv}")
        elif c["name"].startswith("[live]"):
            print(f"  OK   {c['name']:<32} → {len(tv)}건")
    print(f"  {len(mmr_cases)}건 대조 (합성 {len(mmr_cases) - live_n} + live {live_n})")

    print()
    print("=== sqrt 민감도 — `x ** 0.5` vs `math.sqrt` (libm 무관 실험) ===")
    od, cd, margin = sqrt_sensitivity(mmr_cases)
    print(f"  코사인 값이 달라진 케이스: {cd} / {len(mmr_cases)}")
    print(f"  **선택 순서가 달라진 케이스: {od}**")
    print(f"  greedy 결정 마진 최솟값: {margin:.3e}  (1 ulp ≈ 1e-17 규모)")
    if od:
        fails += od
        print("  → 순서가 갈린다. libm 차이가 순위에 전파되므로 별도 대응이 필요하다.")

    total = (len(BASE_QUERIES) + len(LAMBDA_ENV_CASES) + len(DISABLE_ENV_CASES)
             + len(EMBED_COERCE_CASES) + len(cosine_cases) + len(mmr_cases))
    print()
    print(f"케이스 {total}건 대조")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
