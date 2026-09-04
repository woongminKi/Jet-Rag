"""Task 2.8 채점기 — Edge `/search` 파이프라인 전체를 Python `search()` 와 응답 대조.

## 왜 HTTP 를 안 태우나
운영 `/search` 를 그냥 부르면 **비인증이라 문서가 0 건**이라 아무것도 비교되지 않는다.
소유자 토큰을 만들면 되지만, 그러면 인증·네트워크 변수가 대조에 섞인다.
인증은 Phase 1 에서 이미 따로 검증했으므로, 여기서는 **양쪽 다 in-process 로** 돌린다 —
Python 은 `search()` 를 직접 부르고, TS 는 `pipeline.ts` 의 `runSearch()` 를 부른다.
그래서 `api-search/index.ts` 를 HTTP 껍데기와 파이프라인으로 나눠 뒀다.

## 임베딩 비결정성을 먼저 없앤다
DeepInfra 는 같은 질의에도 벡터가 미세하게 흔들린다(Task 2.7 실측). 그대로 두면 두 쪽이
서로 다른 dense 벡터를 받아 **순위가 달라지고, 그게 이식 오류인지 API 흔들림인지
구분이 안 된다.** 그래서 질의마다 Python 을 먼저 한 번 돌려 `embed_query_cache` 를 채우고,
그 다음 양쪽이 **같은 캐시 벡터**를 읽게 한다. 운영도 캐시가 채워진 상태로 도므로
이게 실제 상태에 더 가깝다.

## 비교 방법
`took_ms` 만 빼고 **응답 전체를 파싱된 값으로** 비교한다 — 문서 순서·relevance·
matched_chunks·스니펫·하이라이트·query_parsed·meta 전부. 바이트가 아니라 값으로 비교하는
이유는 Task 2.4 에서 실측한 대로 pydantic 이 `1.0`, JS 가 `1` 을 쓰기 때문이다.

## 이 스크립트가 아직 못 덮는 것
`meta_filter_fast_path` 가 뜨는 질의는 원본이 임베딩·RPC 없이 답하는데 Edge 는 아직
그 분기가 없다(Task 2.6 미이식). 그런 질의는 **건너뛰되 몇 건인지 출력한다** — 조용히
빼면 "전부 통과" 로 읽힌다.

사용:
    api/.venv/bin/python api/scripts/verify_search_pipeline_parity.py [--limit N]
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

# (이름, 쿼리스트링) — 파라미터 조합까지 흔든다.
CASES: list[tuple[str, dict]] = [
    ("단순 질의", {"q": "세무"}),
    ("한 단어", {"q": "계약서"}),
    ("여러 토큰", {"q": "정기결제 해지와 환불 절차"}),
    ("조사 포함", {"q": "전폭은 얼마인가요?"}),
    ("결과 없음", {"q": "존재하지않는질의문자열xyz"}),
    ("cross-doc (MMR 발화)", {"q": "이력서와 포트폴리오 비교"}),
    ("cross-doc class (cap 8)", {"q": "차이가 뭐야"}),
    ("limit 1", {"q": "매출", "limit": "1"}),
    ("limit 50", {"q": "매출", "limit": "50"}),
    ("offset 1", {"q": "보고서", "offset": "1"}),
    ("offset 이 범위 밖", {"q": "보고서", "offset": "999"}),
    ("mode dense", {"q": "매출", "mode": "dense"}),
    ("mode sparse", {"q": "매출", "mode": "sparse"}),
    ("doc_type 필터", {"q": "매출", "doc_type": "pdf"}),
    ("from_date 필터", {"q": "매출", "from_date": "2020-01-01"}),
    ("to_date 필터 (과거)", {"q": "매출", "to_date": "2020-01-01"}),
    ("from+to 범위", {"q": "매출", "from_date": "2020-01-01", "to_date": "2030-01-01"}),
    ("tags 필터 (없는 태그)", {"q": "매출", "tags": "존재하지않는태그"}),
    ("목차 질의 (toc 가드 skip)", {"q": "목차"}),
    # 가드가 실제로 발동하는 질의 — 후보에 가드 대상 청크가 들어오고, 질의가 목차를
    # 요구하지 않아 penalty 가 skip 되지 않는다(실측으로 골랐다). 이게 없으면
    # "가드 미적용" 음성 대조가 한 건도 안 잡힌다.
    ("cover+toc 가드 발동", {"q": "어두운 녹색 배경"}),
    ("cover 가드 발동", {"q": "부문별 온도차"}),
    ("toc 가드 발동", {"q": "점선 목록"}),
    ("긴 질의", {"q": "데이터센터 지원 사업의 신청 자격과 제출 서류는 무엇인가요"}),
]

RUNNER_TS = f"""
import {{ createClient }} from "@supabase/supabase-js";
import {{ validateSearchParams }} from "file://{SEARCH_DIR}/params.ts";
import {{ runSearch, SearchHttpError }} from "file://{SEARCH_DIR}/pipeline.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const env: Record<string, string> = input.env;
const read = (k: string) => env[k];

const client = createClient(env.SUPABASE_URL, env.SUPABASE_SERVICE_ROLE_KEY, {{
  auth: {{ persistSession: false }},
}});

const out: unknown[] = [];
for (const qs of input.cases as Record<string, string>[]) {{
  const sp = new URLSearchParams(qs);
  const v = validateSearchParams(sp);
  if (!v.ok) {{
    out.push({{ error: `검증 실패 ${{v.status}}`, detail: v.detail }});
    continue;
  }}
  try {{
    const r = await runSearch(v.params, input.user_id, {{ client, read }});
    out.push({{ body: r.body, headers: r.headers }});
  }} catch (e) {{
    out.push({{
      error: e instanceof SearchHttpError ? `HTTP ${{e.status}}` : String(e),
      detail: e instanceof SearchHttpError ? e.detail : undefined,
    }});
  }}
}}
console.log(JSON.stringify(out));
"""


def run_deno(payload: dict, timeout: int = 900) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "cases.json"), os.path.join(tmp, "runner.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=timeout,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:3000]}")
    return json.loads(proc.stdout)


def owner_user_id() -> str:
    """문서를 가진 사용자. 없으면 비교할 게 없다."""
    explicit = os.environ.get("OWNER_USER_ID")
    if explicit:
        return explicit
    from supabase import create_client

    c = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )
    rows = (
        c.table("documents").select("user_id").is_("deleted_at", "null")
        .limit(1).execute().data
    )
    if not rows:
        raise SystemExit("문서를 가진 사용자를 못 찾았다 — 대조할 대상이 없다.")
    return rows[0]["user_id"]


def call_python(qs: dict, user_id: str) -> dict:
    from app.auth.dependencies import CurrentUser
    from app.routers.search import search

    def _int(key, default):
        return int(qs[key]) if key in qs else default

    resp = search(
        q=qs["q"],
        limit=_int("limit", 10),
        offset=_int("offset", 0),
        tags=[qs["tags"]] if "tags" in qs else None,
        doc_type=qs.get("doc_type"),
        from_date=qs.get("from_date"),
        to_date=qs.get("to_date"),
        doc_id=qs.get("doc_id"),
        mode=qs.get("mode", "hybrid"),
        current_user=CurrentUser(user_id=user_id, email=None, is_authenticated=True),
    )
    return resp.model_dump()


def fires_meta_fast_path(q: str, qs: dict) -> bool:
    """원본이 fast path 로 빠지는 질의인지 — 그러면 Edge 와 비교가 성립하지 않는다."""
    if qs.get("doc_id") is not None:
        return False
    if qs.get("mode", "hybrid") != "hybrid":
        return False
    from app.services import meta_filter_fast_path

    import unicodedata
    return meta_filter_fast_path.is_meta_only(unicodedata.normalize("NFC", q.strip())) is not None


def diff(a, b, path="") -> list[str]:
    """파싱된 값 기준 깊은 비교. `1.0` 과 `1` 은 같다고 본다."""
    out: list[str] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: py 에 없음 (ts={b[k]!r})")
            elif k not in b:
                out.append(f"{path}.{k}: ts 에 없음 (py={a[k]!r})")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: 길이 py={len(a)} ts={len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                out += diff(x, y, f"{path}[{i}]")
    elif isinstance(a, bool) != isinstance(b, bool):
        out.append(f"{path}: py={a!r} ts={b!r}")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a != b:
            out.append(f"{path}: py={a!r} ts={b!r}")
    elif a != b:
        out.append(f"{path}: py={a!r} ts={b!r}")
    return out


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
    # 대조 때문에 지표 테이블을 더럽히지 않는다.
    os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
    os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"

    limit_n = None
    if "--limit" in sys.argv:
        limit_n = int(sys.argv[sys.argv.index("--limit") + 1])
    cases = CASES[:limit_n] if limit_n else CASES

    user_id = owner_user_id()
    print(f"대상 사용자: {user_id}")

    # ① Python 을 먼저 돌린다 — 응답도 얻고, embed_query_cache 도 채운다.
    py_results: list[dict | None] = []
    skipped: list[str] = []
    runnable: list[tuple[str, dict]] = []
    for name, qs in cases:
        if fires_meta_fast_path(qs["q"], qs):
            skipped.append(name)
            py_results.append(None)
            continue
        py_results.append(call_python(qs, user_id))
        runnable.append((name, qs))

    # ② 같은 질의를 TS 로. 캐시가 채워졌으므로 같은 dense 벡터를 읽는다.
    env = {
        k: os.environ[k]
        for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
    }
    for k in ("HF_API_TOKEN", "DEEPINFRA_API_TOKEN", "JETRAG_EMBED_PROVIDER"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    env["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"

    ts_results = run_deno({
        "cases": [qs for name, qs in cases if not fires_meta_fast_path(qs["q"], qs)],
        "user_id": user_id,
        "env": env,
    })

    fails = 0
    ti = 0
    print()
    print("=== 응답 대조 (took_ms 제외 전 필드) ===")
    for (name, qs), pv in zip(cases, py_results):
        if pv is None:
            continue
        tv = ts_results[ti]
        ti += 1
        if "error" in tv:
            fails += 1
            print(f"  {name:<26} TS 오류: {tv['error']} {tv.get('detail')}")
            continue
        body = dict(tv["body"])
        pv2 = {k: v for k, v in pv.items() if k != "took_ms"}
        tv2 = {k: v for k, v in body.items() if k != "took_ms"}
        d = diff(pv2, tv2)
        if d:
            fails += 1
            print(f"  {name:<26} MISMATCH ({len(d)}건)")
            for line in d[:6]:
                print(f"      {line}")
            if len(d) > 6:
                print(f"      ... 외 {len(d) - 6}건")
        else:
            n = len(pv.get("items") or [])
            print(f"  {name:<26} OK   total={pv['total']} items={n} "
                  f"fused={pv['query_parsed']['fused']}")

    print()
    print(f"대조 {len(runnable)}건 / 건너뜀 {len(skipped)}건")
    if skipped:
        print("  건너뛴 질의 — 원본이 meta fast path 로 빠진다 (Task 2.6 미이식):")
        for n in skipped:
            print(f"    - {n}")
    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
