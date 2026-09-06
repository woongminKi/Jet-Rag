"""`/answer` 본체를 Python 원본과 대조.

## LLM 은 부르지 않는다
같은 프롬프트를 줘도 Gemini 출력은 매번 다르다 — 응답 문자열 대조는 성립하지 않는다.
대신 **LLM 에 들어가기 직전까지**를 대조한다:
  ① 파라미터 검증(422) ② 프롬프트 문자열 ③ 검색 결과·query_parsed ④ 응답 조립
  ⑤ Gemini 요청 바디(SDK 가 실제로 보내는 것과) ⑥ 응답 텍스트 조립 규칙
프롬프트가 같고 요청 바디가 같으면 그 뒤는 같은 API 다.

## 임베딩은 **같은 벡터를 양쪽에 주입**한다
`_gather_chunks` 는 내부에서 임베딩을 부르는데, DeepInfra 는 같은 입력에도 벡터가
미세하게 달라진다(2026-09-05 실측: self-cosine 0.999999). 벡터가 다르면 RPC 결과가
달라질 수 있어 대조가 무의미해진다. 그래서 한 번만 임베딩하고 **양쪽에 같은 벡터**를 넣어
RPC 입력을 고정한다.

## 카운터를 올리지 않는다
`/answer` HTTP 호출은 `usage_counters` 를 +1 하고 LLM 비용도 든다. 이 스크립트는
in-process 로만 돌며 HTTP 를 치지 않는다. 422 는 이미 떠 둔 실측 fixture 와 대조한다.

사용:
    api/.venv/bin/python api/scripts/verify_answer_parity.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

# 검색 대조에 쓸 질의. 결과 0 건 / 다수 / doc_id 필터를 골고루 태운다.
SEARCH_QUERIES = [
    ("휴가 규정", None, 5),
    ("휴가 규정", None, 1),
    ("zzzz절대없는말zzzz", None, 5),
    ("문서 비교해줘", None, 10),
    ("계약", None, 3),
    # **검색 0 건 분기를 태운다.** 질의만으로는 sparse 가 뭐라도 잡아서 0 건이 안 나온다
    # ("zzzz절대없는말zzzz" 도 결과가 있었다) — 없는 doc_id 로 필터해야 비로소 0 행이 된다.
    ("휴가 규정", "00000000-0000-0000-0000-000000000000", 5),
]

# NFD 로 쓴 한글 — 원본이 NFC 로 정규화하는지 본다. 정규화를 빼면 DB 매칭이 깨진다.
import unicodedata as _ud  # noqa: E402
NFD_QUERY = _ud.normalize("NFD", "휴가 규정")

# 임베딩이 죽었을 때 경로 — 원본은 `search_sparse_only_pgroonga` 를 부른다.
# dense 가 늘 성공해서 이 분기가 안 태워졌다(음성 대조 0 건).
SPARSE_ONLY_QUERIES = [("휴가 규정", None, 5), ("계약", None, 3)]

# 프롬프트 조립 대조용 합성 청크 — 잘림·페이지 없음·제목 없음을 태운다.
PROMPT_CHUNKS = [
    {"chunk_id": "c1", "doc_id": "d1", "doc_title": "규정집", "chunk_idx": 0,
     "text": "  앞뒤 공백이 있는 본문  ", "page": 3, "section_title": None, "score": 1.0},
    {"chunk_id": "c2", "doc_id": "d2", "doc_title": None, "chunk_idx": 1,
     "text": "제목이 없는 문서", "page": None, "section_title": "s", "score": 0.5},
    {"chunk_id": "c3", "doc_id": "d3", "doc_title": "긴문서", "chunk_idx": 2,
     "text": "가" * 1500, "page": 0, "section_title": None, "score": 0.1},
    {"chunk_id": "c4", "doc_id": "d4", "doc_title": "경계", "chunk_idx": 3,
     "text": "나" * 1200, "page": 1, "section_title": None, "score": 0.05},
    {"chunk_id": "c5", "doc_id": "d5", "doc_title": "이모지", "chunk_idx": 4,
     "text": "🙂" * 1300, "page": 2, "section_title": None, "score": 0.01},
]

# `validateAnswerParams` 대조 — 실측 fixture 의 키와 맞춘다.
PARAM_CASES = [
    ("q 없음", ""),
    ("q 빈문자", "q="),
    ("q 201자", "q=" + "가" * 201),
    ("top_k=0", "q=x&top_k=0"),
    ("top_k=11", "q=x&top_k=11"),
    ("top_k 비숫자", "q=x&top_k=abc"),
    ("top_k=''", "q=x&top_k="),
    ("q+top_k 동시 오류", "q=&top_k=99"),
]

# 길이 임계는 **코드포인트** 기준이다. 이모지는 UTF-16 으로 2 칸이라 `.length` 를 쓰면
# 200 개짜리가 400 으로 세어져 잘못 거절된다. 실측 fixture 에는 없으므로 pydantic 으로
# 직접 기준값을 만든다.
ASTRAL_CASES = [
    ("이모지 200개(통과해야)", "🙂" * 200),
    ("이모지 201개(거절돼야)", "🙂" * 201),
    ("한자확장 200개", "\U00020000" * 200),
]

RUNNER_TS = f"""
import {{ createClient }} from "@supabase/supabase-js";
import {{ validateAnswerParams, pydanticInt }} from "file://{SHARED}/answer/params.ts";
import {{ buildMessages }} from "file://{SHARED}/answer/prompt.ts";
import {{ buildAnswer, buildSources, isQuotaExhausted }} from "file://{SHARED}/answer/pipeline.ts";
import {{ gatherChunks }} from "file://{SHARED}/answer/chunks.ts";
import {{ buildRequestBody, responseText }} from "file://{SHARED}/llm/gemini.ts";
import {{ buildPgroongaQuery }} from "file://{SHARED}/search/pgroonga.ts";
import {{ buildUserKey, clientIp, utcPeriodDate }} from "file://{SHARED}/rate_limit.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const env: Record<string, string> = input.env;
const client = createClient(env.SUPABASE_URL, env.SUPABASE_SERVICE_ROLE_KEY, {{
  auth: {{ persistSession: false }},
}});

// 파라미터 검증
const params: Record<string, unknown> = {{}};
for (const [name, qs] of input.param_cases) {{
  const r = validateAnswerParams(new URLSearchParams(qs));
  params[name] = r.ok ? {{ ok: true, params: r.params }} : {{ ok: false, detail: r.detail }};
}}
const ints = input.int_cases.map((s: string) => pydanticInt(s));
const astral = input.astral_cases.map(([, q]: [string, string]) => {{
  const sp = new URLSearchParams();
  sp.set("q", q);
  const r = validateAnswerParams(sp);
  return r.ok ? {{ ok: true, len: [...r.params.q].length }} : {{ ok: false, detail: r.detail }};
}});

// 프롬프트
const prompts = buildMessages(input.prompt_query, input.prompt_chunks);

// 검색 — **주입된 벡터**를 쓴다. 임베딩을 다시 부르지 않는다.
const searches: unknown[] = [];
for (const [i, [q, docId, topK]] of input.search_queries.entries()) {{
  const vec = input.vectors[i];
  const r = await gatherChunks(
    {{ query: q, docId, topK, userId: input.user_id }},
    {{ client, embedQuery: () => Promise.resolve(vec), buildPgQuery: buildPgroongaQuery }},
  );
  searches.push(r);
}}

// 응답 조립 — LLM 은 고정 문자열을 돌려주는 가짜로 대체한다.
const answers: unknown[] = [];
for (const [i, [q, docId, topK]] of input.search_queries.entries()) {{
  const vec = input.vectors[i];
  const r = await buildAnswer({{ q, topK, docId }}, input.user_id, {{
    client,
    embedQuery: () => Promise.resolve(vec),
    llm: {{ apiKey: "x", model: input.model }},
    readEnv: () => undefined,
    now: () => input.now_ms,
    completeFn: () => Promise.resolve(input.fake_llm_text),
  }});
  answers.push(r);
}}

// 임베딩 실패 경로 — embedQuery 가 null 을 돌려준다.
const sparseOnly: unknown[] = [];
for (const [q, docId, topK] of input.sparse_queries) {{
  sparseOnly.push(await gatherChunks(
    {{ query: q, docId, topK, userId: input.user_id }},
    {{ client, embedQuery: () => Promise.resolve(null), buildPgQuery: buildPgroongaQuery }},
  ));
}}

// NFD 질의 — 정규화 전후로 결과가 갈린다.
const nfdAnswer = await buildAnswer(
  {{ q: input.nfd_query, topK: 3, docId: null }},
  input.user_id,
  {{
    client,
    embedQuery: () => Promise.resolve(input.nfd_vector),
    llm: {{ apiKey: "x", model: input.model }},
    readEnv: () => undefined,
    now: () => input.now_ms,
    completeFn: () => Promise.resolve(input.fake_llm_text),
  }},
);

console.log(JSON.stringify({{
  params, ints, astral, prompts, searches, answers, sparseOnly, nfdAnswer,
  pg_queries: input.search_queries.map(([q]: [string]) => buildPgroongaQuery(q)),
  gemini_body: buildRequestBody(prompts, {{ temperature: 0.2 }}),
  response_text: input.resp_cases.map((r: any) => responseText(r)),
  quota_flags: input.quota_cases.map((m: string) => isQuotaExhausted(new Error(m))),
  sources_only: buildSources(input.prompt_chunks),
  rate_keys: input.rate_cases.map(([xff, auth, uid]: [string | null, boolean, string]) => {{
    const h = new Headers();
    if (xff !== null) h.set("X-Forwarded-For", xff);
    const req = new Request("https://x/answer", {{ headers: h }});
    return {{ ip: clientIp(req), key: buildUserKey({{ userId: uid, isAuthenticated: auth }}, req) }};
  }}),
  period_date: utcPeriodDate(input.now_ms),
}}));
"""


def run_deno(payload: dict, timeout: int = 900) -> dict:
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


def main() -> None:
    import time
    import unicodedata

    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))

    import app.routers.answer as A
    from app.auth.dependencies import CurrentUser
    from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
    from app.services import quota as Q

    fails = 0

    def check(name, py, ts):
        nonlocal fails
        if py != ts:
            fails += 1
            print(f"  MISMATCH {name}")
            print(f"      py={json.dumps(py, ensure_ascii=False, default=str)[:500]}")
            print(f"      ts={json.dumps(ts, ensure_ascii=False, default=str)[:500]}")
        else:
            print(f"  {name:<34} OK")

    user_id = "2af8fca5-03ab-421b-94b8-53d4fe9d8046"
    now_ms = int(time.time() * 1000)
    # **fallback 상수와 다른 값이어야 한다.** 같은 값이면 "검색 0 건일 때 fallback 을
    # 쓰는가" 분기가 가려지지 않는다(음성 대조 0 건으로 드러났다).
    model = "test-model-xyz"
    fake_text = "  가짜 답변입니다 [1].  "

    # --- 임베딩 1회씩만 — 양쪽에 같은 벡터를 넣는다 -------------------------
    print("임베딩 수집 중...")
    provider = get_bgem3_provider()
    vectors = []
    for q, _doc, _tk in SEARCH_QUERIES:
        clean = unicodedata.normalize("NFC", q.strip())
        vectors.append(provider.embed_query(clean))
    nfd_vector = provider.embed_query(unicodedata.normalize("NFC", NFD_QUERY.strip()))
    print(f"  {len(vectors) + 1}건 (차원 {len(vectors[0])})\n")

    resp_cases = [
        {"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "T", "thought": True}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "", "thought": False}]}}]},
        {"candidates": [{"content": {"parts": [{"inlineData": {}}]}}]},
        {"candidates": [{"content": {"parts": []}}]},
        {"candidates": []},
        {},
    ]
    quota_cases = ["RESOURCE_EXHAUSTED", "Gemini 429: x", "quota exceeded",
                   "QUOTA", "그냥 오류", "", "4290"]
    rate_cases = [
        ["1.2.3.4", False, "u1"],
        ["1.2.3.4, 5.6.7.8", False, "u1"],
        ["  9.9.9.9  , 1.1.1.1", False, "u1"],
        [",", False, "u1"],
        [None, False, "u1"],
        ["1.2.3.4", True, "u1"],
    ]
    int_cases = ["5", " 5 ", "+5", "5.0", "5.5", "", "abc", "1e1", "-3", "0x5"]

    ts = run_deno({
        "env": {k: os.environ[k] for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")},
        "user_id": user_id,
        "now_ms": now_ms,
        "model": model,
        "fake_llm_text": fake_text,
        "param_cases": [list(c) for c in PARAM_CASES],
        "int_cases": int_cases,
        "astral_cases": [list(c) for c in ASTRAL_CASES],
        "prompt_query": "휴가 규정이 어떻게 되나요?",
        "prompt_chunks": PROMPT_CHUNKS,
        "search_queries": [[q, d, t] for q, d, t in SEARCH_QUERIES],
        "vectors": vectors,
        "sparse_queries": [list(c) for c in SPARSE_ONLY_QUERIES],
        "nfd_query": NFD_QUERY,
        "nfd_vector": nfd_vector,
        "resp_cases": resp_cases,
        "quota_cases": quota_cases,
        "rate_cases": rate_cases,
    })

    # --- 1. 파라미터 검증 (실측 fixture 와) ---------------------------------
    print("=== 파라미터 검증 — 운영 실측과 대조 ===")
    with open(os.path.join(HERE, "fixtures", "answer_422_measured.json"), encoding="utf-8") as f:
        measured = json.load(f)
    for name, _qs in PARAM_CASES:
        got = ts["params"][name]
        want_status, want_body = measured[name]
        if want_status != 422:
            fails += 1
            print(f"  케이스 무효 {name} — fixture 가 422 가 아니다({want_status})")
            continue
        check(f"{name} (실측)", {"ok": False, "detail": want_body["detail"]}, got)

    print()
    print("=== pydantic int 강제 변환 ===")
    # Python 쪽 기준값은 pydantic 으로 직접 만든다.
    from pydantic import TypeAdapter, ValidationError
    ta = TypeAdapter(int)
    py_ints = []
    for s in int_cases:
        try:
            py_ints.append({"ok": True, "value": ta.validate_python(s)})
        except ValidationError as e:
            py_ints.append({"ok": False, "kind": "parsing"})
    check("int 파싱 10건", py_ints, ts["ints"])

    print()
    print("=== 길이 임계 — astral 문자 (코드포인트 기준) ===")
    from pydantic import Field
    from typing import Annotated
    QAdapter = TypeAdapter(Annotated[str, Field(min_length=1, max_length=200)])
    for (name, q), got in zip(ASTRAL_CASES, ts["astral"]):
        try:
            QAdapter.validate_python(q)
            want = {"ok": True, "len": len(q)}
        except ValidationError as e:
            err = e.errors()[0]
            want = {"ok": False, "detail": [{
                "type": err["type"], "loc": ["query", "q"], "msg": err["msg"],
                "input": q, "ctx": err.get("ctx", {}),
            }]}
        check(name, want, got)

    # --- 2. 프롬프트 --------------------------------------------------------
    print()
    print("=== 프롬프트 문자열 ===")
    py_msgs = [
        {"role": m.role, "content": m.content}
        for m in A._build_messages("휴가 규정이 어떻게 되나요?", PROMPT_CHUNKS)
    ]
    check("system + user", py_msgs, ts["prompts"])

    # --- 3. 검색 ------------------------------------------------------------
    print()
    print("=== PGroonga 질의 ===")
    check(f"{len(SEARCH_QUERIES)}건", [A._build_pgroonga_query(q) for q, _, _ in SEARCH_QUERIES], ts["pg_queries"])

    print()
    print("=== _gather_chunks (같은 벡터 주입) ===")
    _orig_provider = A.get_bgem3_provider
    for i, (q, doc_id, top_k) in enumerate(SEARCH_QUERIES):
        vec = vectors[i]
        A.get_bgem3_provider = lambda v=vec: type("P", (), {"embed_query": staticmethod(lambda _q: v)})()
        try:
            chunks, qp = A._gather_chunks(query=q, doc_id=doc_id, top_k=top_k, user_id=user_id)
        finally:
            A.get_bgem3_provider = _orig_provider
        got = ts["searches"][i]
        check(f"q={q!r} top_k={top_k}", {"chunks": chunks, "queryParsed": qp},
              {"chunks": got["chunks"], "queryParsed": got["queryParsed"]})

    print()
    print("=== 임베딩 실패 → sparse-only 경로 ===")
    for i, (q, doc_id, top_k) in enumerate(SPARSE_ONLY_QUERIES):
        class _Boom:
            @staticmethod
            def embed_query(_q):
                # 원본은 **transient** 오류일 때만 sparse-only 로 내려간다
                # (`is_transient_hf_error` = `_is_retryable`). 5xx HTTPStatusError 가 그 조건.
                import httpx
                raise httpx.HTTPStatusError(
                    "의도적 transient",
                    request=httpx.Request("POST", "https://x"),
                    response=httpx.Response(503, request=httpx.Request("POST", "https://x")),
                )
        A.get_bgem3_provider = lambda: _Boom()
        try:
            chunks, qp = A._gather_chunks(query=q, doc_id=doc_id, top_k=top_k, user_id=user_id)
        finally:
            A.get_bgem3_provider = _orig_provider
        check(f"sparse-only q={q!r}", {"chunks": chunks, "queryParsed": qp},
              {"chunks": ts["sparseOnly"][i]["chunks"],
               "queryParsed": ts["sparseOnly"][i]["queryParsed"]})

    print()
    print("=== NFD 질의 → NFC 정규화 ===")
    A.get_bgem3_provider = lambda: type("P", (), {"embed_query": staticmethod(lambda _q: nfd_vector)})()
    _orig_llm2 = A._get_llm
    A._get_llm = lambda: type("L", (), {"model": model, "complete": staticmethod(lambda *_a, **_k: fake_text)})()
    try:
        py_nfd = A.answer(q=NFD_QUERY, top_k=3, doc_id=None, response=None,
                          current_user=CurrentUser(user_id=user_id, email=None,
                                                   is_authenticated=True)).model_dump()
    finally:
        A.get_bgem3_provider = _orig_provider
        A._get_llm = _orig_llm2
    got_nfd = dict(ts["nfdAnswer"])
    py_nfd.pop("took_ms"), got_nfd.pop("took_ms")
    check("NFD 입력", py_nfd, got_nfd)
    if py_nfd["query"] == NFD_QUERY:
        fails += 1
        print("  케이스 무효 — 정규화가 일어나지 않아 분기를 못 가린다")

    # --- 4. 응답 조립 -------------------------------------------------------
    print()
    print("=== 응답 조립 (LLM 은 고정 문자열 주입) ===")
    hit = miss = 0
    for i, (q, doc_id, top_k) in enumerate(SEARCH_QUERIES):
        vec = vectors[i]
        A.get_bgem3_provider = lambda v=vec: type("P", (), {"embed_query": staticmethod(lambda _q: v)})()
        _orig_llm = A._get_llm
        A._get_llm = lambda: type("L", (), {
            "model": model, "complete": staticmethod(lambda *_a, **_k: fake_text)
        })()
        try:
            # **`LEGACY_DEFAULT_USER` 를 그대로 넘기면 안 된다** — 그건 FastAPI 의
            # Depends 객체라 `current_user.user_id` 가 엉뚱한 값이 되고, RPC 가 0 행을
            # 돌려줘 전부 "검색 결과 없음"으로 통과해 버린다(실제로 그렇게 초록이었다).
            resp = A.answer(q=q, top_k=top_k, doc_id=doc_id, response=None,
                            current_user=CurrentUser(user_id=user_id, email=None,
                                                     is_authenticated=True))
            py = resp.model_dump()
        finally:
            A.get_bgem3_provider = _orig_provider
            A._get_llm = _orig_llm
        got = dict(ts["answers"][i])
        py.pop("took_ms"), got.pop("took_ms")
        if py["has_search_results"]:
            hit += 1
        else:
            miss += 1
        check(f"q={q!r}", py, got)
    print(f"  검색 있음 {hit}건 / 0건 {miss}건")
    if hit == 0 or miss == 0:
        fails += 1
        print("  케이스 무효 — 검색 유/무 두 분기를 다 태우지 못했다")

    # --- 5. Gemini 요청 바디 (SDK 캡처와) -----------------------------------
    print()
    print("=== Gemini 요청 바디 — SDK 가 실제로 만드는 것과 ===")
    import google.genai._api_client as ac
    from google.genai import types as gt
    cap = {}
    _orig_req = ac.BaseApiClient.request

    def _spy(self, http_method, path, request_dict, http_options=None):
        cap["body"] = json.loads(json.dumps(request_dict, default=str))
        raise RuntimeError("의도적 중단 — 실제 호출은 하지 않는다")

    ac.BaseApiClient.request = _spy
    try:
        from app.adapters.impl.gemini_llm import GeminiLLMProvider
        prov = GeminiLLMProvider(model=model)
        try:
            prov.complete(A._build_messages("휴가 규정이 어떻게 되나요?", PROMPT_CHUNKS), temperature=0.2)
        except Exception:
            pass
    finally:
        ac.BaseApiClient.request = _orig_req
    py_body = {k: v for k, v in cap.get("body", {}).items() if k != "_url"}
    check("contents/systemInstruction/generationConfig", py_body, ts["gemini_body"])

    # --- 6. 응답 텍스트 조립 + quota 판정 + rate key -------------------------
    print()
    print("=== 응답 텍스트 조립 규칙 ===")
    from google.genai import types as T
    py_texts = []
    for rc in resp_cases:
        try:
            py_texts.append(T.GenerateContentResponse.model_validate(rc).text)
        except Exception as e:
            py_texts.append(f"<파싱 실패: {type(e).__name__}>")
    check(f"{len(resp_cases)}건", py_texts, ts["response_text"])

    print()
    print("=== quota 판정 ===")
    check("메시지 7건", [Q.is_quota_exhausted(m) for m in quota_cases], ts["quota_flags"])

    print()
    print("=== rate limit 키 ===")
    from app.services.rate_limit import build_user_key
    class _Req:
        def __init__(self, xff):
            self.headers = {"X-Forwarded-For": xff} if xff is not None else {}
            self.client = None

    py_keys = []
    for xff, auth, uid in rate_cases:
        r = _Req(xff)
        cu = CurrentUser(user_id=uid, email=None, is_authenticated=auth)
        from app.services.rate_limit import _client_ip
        py_keys.append({"ip": _client_ip(r), "key": build_user_key(cu, r)})
    check(f"{len(rate_cases)}건", py_keys, ts["rate_keys"])

    print()
    print("=== sources 조립 (합성 청크) ===")
    py_src = [
        {"chunk_id": c["chunk_id"], "doc_id": c["doc_id"], "doc_title": c["doc_title"],
         "chunk_idx": c["chunk_idx"], "page": c["page"], "section_title": c["section_title"],
         "score": c["score"], "snippet": (c.get("text") or "")[:200]}
        for c in PROMPT_CHUNKS
    ]
    check(f"{len(PROMPT_CHUNKS)}건", py_src, ts["sources_only"])

    from datetime import datetime, timezone
    check("period_date (UTC)",
          datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).date().isoformat(),
          ts["period_date"])

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
