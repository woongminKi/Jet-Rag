"""`/admin/*` 를 Python 원본과 대조.

## 실 데이터로는 절반밖에 못 태운다
2026-09-06 실측: `answer_feedback` 이 **0 행**이다. 실 DB 만 보면 feedback 쪽은 전부 빈 값
경로만 지나가고 집계 분기가 하나도 안 태워진다. 그래서 순수 집계 함수는 **합성 행**으로
따로 대조하고, 검사기가 "분기를 실제로 태웠는지" 스스로 확인한다.

## POST /admin/subscriptions 는 실행하지 않는다
구독을 실제로 바꾸는 쓰기다. `/me` 의 rotate 와 같은 방식으로 **행 + 질의 모양만** 본다.
422 본문은 운영에 무효 본문을 보내 받은 실측(`ADMIN_422_EXPECTED`)과 대조한다 —
검증에서 멈추는 본문이라 DB 에는 쓰이지 않는다.

사용:
    api/.venv/bin/python api/scripts/verify_admin_parity.py
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

# `POST /admin/subscriptions` 의 `updated_at` 대조에만 쓰는 고정 시각.
FIXED_NOW_MS = 1789000000000  # 2026-09-10T00:26:40Z

# **합성 행의 날짜는 "지금" 기준 상대로 만든다.**
# 처음엔 2026-09-10 으로 박았는데, Python 헬퍼(`_build_daily_buckets`)가 `datetime.now(KST)`
# 를 내부에서 부르는 탓에 7 일 창이 실제 오늘 기준이라 합성 행이 통째로 **창 밖**이었다.
# daily 카운트 분기가 하나도 안 태워졌는데 초록이었다 — zero-fill 순서를 뒤집는 음성 대조가
# 잡힌 건 날짜 라벨 때문이지 카운트 때문이 아니었다.
import datetime as _dt  # noqa: E402

_KST = _dt.timezone(_dt.timedelta(hours=9))


def _at(days_ago: float, *, suffix: str = "+00:00", micro: bool = False) -> str:
    """지금으로부터 `days_ago` 일 전의 UTC ISO 문자열."""
    t = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days_ago)
    if not micro:
        t = t.replace(microsecond=0)
    iso = t.isoformat()
    if suffix == "Z":
        return iso.replace("+00:00", "Z")
    return iso


def _kst_boundary_utc() -> str:
    """KST 로는 **오늘**이지만 UTC 로는 어제인 순간 — 오늘 KST 00:30."""
    today_kst = _dt.datetime.now(_KST).date()
    m = _dt.datetime.combine(today_kst, _dt.time(0, 30), tzinfo=_KST)
    return m.astimezone(_dt.timezone.utc).isoformat()

# --- 합성 metrics 행 -------------------------------------------------------
# 각 행이 어떤 분기를 태우는지 주석으로 남긴다. 기대값은 적지 않는다(원본에서 뽑는다).
METRICS_ROWS = [
    # 성공 — fused > 0
    {"recorded_at": _at(0.1), "took_ms": 100, "fused": 5,
     "fallback_reason": None, "query_text": "휴가 규정"},
    # 실패 — fallback_reason
    {"recorded_at": _at(0.2), "took_ms": 200, "fused": 3,
     "fallback_reason": "permanent_4xx", "query_text": "다이어그램"},
    {"recorded_at": _at(1.1), "took_ms": 300, "fused": 2,
     "fallback_reason": "transient_5xx", "query_text": "표 목록"},
    # 실패 — no_hits (fused == 0)
    {"recorded_at": _at(1.2), "took_ms": 400, "fused": 0,
     "fallback_reason": None, "query_text": "3개월 지원금"},
    # fused 가 None → 성공 아님, 그런데 reason 도 None (샘플에 안 들어간다)
    {"recorded_at": _at(2.1), "took_ms": None, "fused": None,
     "fallback_reason": None, "query_text": "요약해줘"},
    # 알 수 없는 fallback_reason → 실패지만 reason 분류는 no_hits/None
    {"recorded_at": _at(2.2), "took_ms": 500, "fused": 7,
     "fallback_reason": "weird_reason", "query_text": "비교"},
    # KST 날짜 경계 — UTC 로는 9/9, KST 로는 9/10
    {"recorded_at": _kst_boundary_utc(), "took_ms": 50, "fused": 1,
     "fallback_reason": None, "query_text": "그때 뭐였지"},
    # query_text 가 공백/None → 분포에서 제외
    {"recorded_at": _at(0.3), "took_ms": 60, "fused": 1,
     "fallback_reason": None, "query_text": "   "},
    {"recorded_at": _at(0.4), "took_ms": 70, "fused": 1,
     "fallback_reason": None, "query_text": None},
    # recorded_at 이 깨짐 → 버킷에서 제외 (하지만 total 에는 들어간다)
    {"recorded_at": "not-a-date", "took_ms": 80, "fused": 1,
     "fallback_reason": None, "query_text": "얼마"},
    {"recorded_at": None, "took_ms": 90, "fused": 1,
     "fallback_reason": None, "query_text": "핵심"},
    # 창 밖(30d 보다 오래됨) — DB 가 걸러 주지만 함수는 그대로 센다
    {"recorded_at": _at(300), "took_ms": 1000, "fused": 1,
     "fallback_reason": None, "query_text": "옛날"},
    # `Z` 접미사 / 마이크로초 / 오프셋 없음
    {"recorded_at": _at(0.5, suffix="Z"), "took_ms": 10, "fused": 1,
     "fallback_reason": None, "query_text": "사진"},
    {"recorded_at": _at(0.6, micro=True), "took_ms": 11, "fused": 1,
     "fallback_reason": None, "query_text": "구조도"},
    {"recorded_at": _at(0.7).replace("+00:00", ""), "took_ms": 12, "fused": 1,
     "fallback_reason": None, "query_text": "개요"},
]
# 실패 샘플 10 건 컷을 태우려면 실패가 11 건 이상 필요하다.
METRICS_ROWS += [
    {"recorded_at": _at(0.8 + i * 0.1), "took_ms": i,
     "fused": 0, "fallback_reason": None, "query_text": f"실패{i}"}
    for i in range(12)
]

# --- 합성 feedback 행 ------------------------------------------------------
FEEDBACK_ROWS = [
    {"created_at": _at(0.1), "helpful": True,
     "comment": "출처가 이상해요", "query": "q1"},          # source_issue
    {"created_at": _at(0.2), "helpful": False,
     "comment": "검색 결과가 관련 없음", "query": "q2"},      # search_issue
    {"created_at": _at(1.1), "helpful": False,
     "comment": "답변이 틀린 것 같아요", "query": "q3"},      # answer_issue
    {"created_at": _at(1.2), "helpful": True,
     "comment": "좋아요", "query": "q4"},                    # other
    # 우선순위 — 출처 > 검색 > 답변
    {"created_at": _at(1.3), "helpful": True,
     "comment": "검색 답변 출처 다 이상", "query": "q5"},
    # 코멘트 없음 → 카테고리·노출 모두 제외
    {"created_at": _at(2.1), "helpful": True,
     "comment": None, "query": "q6"},
    {"created_at": _at(2.2), "helpful": False,
     "comment": "   ", "query": "q7"},
    # **helpful 이 null** — 카운트에선 빠지지만 rating 라벨은 truthy 검사라 "down"
    {"created_at": _at(2.3), "helpful": None,
     "comment": "널이에요", "query": "q8"},
    # KST 경계
    {"created_at": _kst_boundary_utc(), "helpful": True,
     "comment": "경계", "query": "q9"},
    # 대문자 키워드 — lower() 를 태운다
    {"created_at": _at(0.3), "helpful": False,
     "comment": "CHUNK 가 안 잡힘", "query": "q10"},
    # 잘림 — query 200, comment 500
    {"created_at": _at(0.4), "helpful": True,
     "comment": "출처" + "가" * 600, "query": "긴질의" * 100},
    # created_at 깨짐
    {"created_at": "nope", "helpful": True, "comment": "깨진 날짜", "query": "q11"},
]
# 최근 코멘트 10 건 컷.
FEEDBACK_ROWS += [
    {"created_at": _at(0.5 + i * 0.1), "helpful": i % 2 == 0,
     "comment": f"코멘트{i}", "query": f"qq{i}"}
    for i in range(8)
]

# `round(x, 4)` 는 **은행가 반올림**이고 `int()` 는 0 방향 절단이다. 둘 다 5 번째 자리가
# 5 로 딱 떨어질 때만 `Math.round` 와 갈린다 — 그런 값이 없으면 음성 대조가 0 건이 된다
# (실제로 그랬다). 그래서 경계를 만들어 넣는다.
#   32 행 중 1 성공 → 0.03125 → 은행가 0.0312 / 통상 0.0313
ROUND_CASES: list[list[dict]] = [
    [],
    [{"fused": 1, "took_ms": 1}, {"fused": 1, "took_ms": 2}],           # 평균 1.5
    [{"fused": 1, "took_ms": 2}, {"fused": 1, "took_ms": 3}],           # 평균 2.5
    [{"fused": 1, "took_ms": -3}, {"fused": 1, "took_ms": -2}],         # 평균 -2.5 (절단 방향)
    [{"fused": 1 if i == 0 else 0, "took_ms": None} for i in range(32)],  # 1/32 = 0.03125
    [{"fused": 1 if i < 3 else 0, "took_ms": None} for i in range(32)],   # 3/32 = 0.09375
    [{"fused": 1, "took_ms": 7}, {"fused": 0, "took_ms": 7}, {"fused": 0, "took_ms": 8}],
]

COMMENT_CASES = [
    "", "   ", "출처", "검색", "답변", "출처 검색 답변", "검색 답변",
    "아무 말", "CHUNK", "Chunk", "근거 없음", "이상한 자료", "페이지 번호",
    "İ",  # Python lower() 와 JS toLowerCase() 가 갈릴 수 있는 문자
    "İstanbul", "ẞ", "İチャンク",
]

RUNNER_TS = f"""
import {{ createClient }} from "@supabase/supabase-js";
import {{
  avgLatencyMs, buildCommentAnalysis, buildDailyBuckets, buildFeedbackDailyBuckets,
  buildQueryTypeDistribution, classifyComment, classifyFailureReason,
  extractFailedSamples, parseRecordedAtKstMs, rowIsSuccess, satisfactionRate,
  successRate,
}} from "file://{SHARED}/admin/aggregate.ts";
import {{
  buildFeedbackStats, buildQueriesStats, buildSubscriptionRow, buildSubscriptionUpsertQuery,
  listSubscriptions, validateRange,
}} from "file://{SHARED}/admin/pipeline.ts";
import {{ parseSubscriptionUpsert }} from "file://{SHARED}/admin/body.ts";
import {{ classifyQueryType, QUERY_TYPE_LABELS }} from "file://{SHARED}/query_classifier.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const env: Record<string, string> = input.env;
const client = createClient(env.SUPABASE_URL, env.SUPABASE_SERVICE_ROLE_KEY, {{
  auth: {{ persistSession: false }},
}});
const now = () => input.now_ms;
const deps = {{ client, now }};

const M = input.metrics_rows;
const F = input.feedback_rows;

const upCount = F.filter((r: any) => r.helpful === true).length;
const downCount = F.filter((r: any) => r.helpful === false).length;

// 실제 응답 (실 DB) — queries 3 범위 + 구독 목록.
const queriesStats: Record<string, unknown> = {{}};
for (const r of ["7d", "14d", "30d"]) {{
  queriesStats[r] = await buildQueriesStats(r, deps);
}}

// POST 는 **실행하지 않는다** — 행과 질의만.
const subRow = buildSubscriptionRow(input.sub_payload, input.fixed_now_ms);
const subQuery = buildSubscriptionUpsertQuery(client, subRow).url.searchParams.toString();

// 422 — Request 를 만들어 파서에 넣는다.
const bodies: Record<string, unknown> = {{}};
for (const [name, raw] of input.body_cases) {{
  const req = new Request("https://x/admin/subscriptions", {{
    method: "POST", body: raw, headers: {{ "content-type": "application/json" }},
  }});
  const r = await parseSubscriptionUpsert(req);
  bodies[name] = r.ok ? {{ ok: true, payload: r.payload }} : {{ ok: false, detail: r.detail }};
}}

console.log(JSON.stringify({{
  daily: buildDailyBuckets(M, 7, input.now_ms),
  daily30: buildDailyBuckets(M, 30, input.now_ms),
  distribution: buildQueryTypeDistribution(M, QUERY_TYPE_LABELS, (q) => classifyQueryType(q)),
  failed: extractFailedSamples(M),
  success_rate: successRate(M),
  avg_latency: avgLatencyMs(M),
  is_success: M.map((r: any) => rowIsSuccess(r)),
  fail_reason: M.map((r: any) => classifyFailureReason(r)),
  kst_ms: M.map((r: any) => parseRecordedAtKstMs(r.recorded_at)),
  fb_daily: buildFeedbackDailyBuckets(F, 7, input.now_ms),
  fb_analysis: buildCommentAnalysis(F),
  fb_satisfaction: satisfactionRate(upCount, downCount),
  comments: input.comment_cases.map((t: string) => classifyComment(t)),
  round_rate: input.round_cases.map((rows: any) => successRate(rows)),
  round_lat: input.round_cases.map((rows: any) => avgLatencyMs(rows)),
  // 응답 조립 — **가짜 클라이언트로 실제 핸들러를 태운다.** 합성 행이 없으면
  // `answer_feedback` 이 0 행이라 조립 분기가 하나도 안 태워진다(실측).
  fb_stats: await buildFeedbackStats("7d", {{
    now,
    client: {{
      from: () => ({{
        select: () => ({{
          gte: () => ({{ order: () => Promise.resolve({{ data: F, error: null }}) }}),
        }}),
      }}),
    }} as any,
  }}),
  range_ok: validateRange(new URLSearchParams("range=14d")),
  range_bad: validateRange(new URLSearchParams("range=zzz")),
  queries_stats: queriesStats,
  subscriptions: await listSubscriptions(deps),
  sub_row: subRow,
  sub_query: subQuery,
  bodies,
}}));
"""


def run_deno(payload: dict, timeout: int = 600) -> dict:
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
    from datetime import datetime, timezone

    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))

    import app.routers.admin as A

    fails = 0

    def check(name: str, py, ts) -> None:
        nonlocal fails
        if py != ts:
            fails += 1
            print(f"  MISMATCH {name}")
            print(f"      py={json.dumps(py, ensure_ascii=False, default=str)[:400]}")
            print(f"      ts={json.dumps(ts, ensure_ascii=False, default=str)[:400]}")
        else:
            print(f"  {name:<34} OK")

    body_cases = [
        ("빈 객체", "{}"),
        ("plan_code 값 오류", '{"user_id":"x","plan_code":"bad"}'),
        ("user_id 타입 오류", '{"user_id":123,"plan_code":"free"}'),
        ("status 값 오류", '{"user_id":"x","plan_code":"free","status":"bogus"}'),
        ("period 타입 오류", '{"user_id":"x","plan_code":"free","current_period_end":123}'),
        ("배열 본문", "[1,2]"),
        ("문자열 본문", '"hello"'),
        ("null 본문", "null"),
        ("여분 필드", '{"user_id":123,"plan_code":"bad","status":"bogus","zzz":1}'),
        ("유효 — 최소", '{"user_id":"u1","plan_code":"pro"}'),
        ("유효 — 전체", '{"user_id":"u1","plan_code":"free","status":"canceled","current_period_end":"2026-12-31"}'),
        ("유효 — period null", '{"user_id":"u1","plan_code":"pro","current_period_end":null}'),
    ]
    sub_payload = {
        "user_id": "00000000-0000-0000-0000-0000000000ff",
        "plan_code": "pro",
        "status": "active",
        "current_period_end": None,
    }

    env = {k: os.environ[k] for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
    now_ms = int(time.time() * 1000)
    ts = run_deno({
        "env": env,
        "now_ms": now_ms,
        "fixed_now_ms": FIXED_NOW_MS,
        "metrics_rows": METRICS_ROWS,
        "feedback_rows": FEEDBACK_ROWS,
        "comment_cases": COMMENT_CASES,
        "round_cases": ROUND_CASES,
        "body_cases": [list(c) for c in body_cases],
        "sub_payload": sub_payload,
    })

    print("=== 순수 집계 — queries (합성 행) ===")
    # Python 헬퍼는 `datetime.now(KST)` 를 직접 부른다 — TS 에도 같은 시각을 준다.
    check("daily 7d", [b.model_dump() for b in A._build_daily_buckets(METRICS_ROWS, 7)], ts["daily"])
    check("daily 30d", [b.model_dump() for b in A._build_daily_buckets(METRICS_ROWS, 30)], ts["daily30"])
    dist, _ = A._build_query_type_distribution(METRICS_ROWS)
    check("query_type 분포", dist, ts["distribution"])
    check("실패 샘플", [s.model_dump() for s in A._extract_failed_samples(METRICS_ROWS)], ts["failed"])
    check("행별 성공 판정", [A._row_is_success(r) for r in METRICS_ROWS], ts["is_success"])
    check("행별 실패 사유", [A._classify_failure_reason(r) for r in METRICS_ROWS], ts["fail_reason"])
    total = len(METRICS_ROWS)
    succ = sum(1 for r in METRICS_ROWS if A._row_is_success(r))
    check("success_rate", round(succ / total, 4) if total else None, ts["success_rate"])
    lat = [r.get("took_ms") for r in METRICS_ROWS if r.get("took_ms") is not None]
    check("avg_latency_ms", int(sum(lat) / len(lat)) if lat else None, ts["avg_latency"])

    # KST 파싱 — Python 은 datetime, TS 는 벽시계 epoch(ms) 다. 같은 축으로 맞춰 본다.
    py_kst = []
    for r in METRICS_ROWS:
        dt = A._parse_recorded_at_kst(r.get("recorded_at"))
        py_kst.append(None if dt is None else int(dt.timestamp() * 1000) + 9 * 3600 * 1000)
    check("recorded_at → KST 벽시계", py_kst, ts["kst_ms"])

    print()
    print("=== 순수 집계 — feedback (합성 행) ===")
    check("일별 👍/👎", [b.model_dump() for b in A._build_feedback_daily_buckets(FEEDBACK_ROWS, 7)], ts["fb_daily"])
    cats, comms = A._build_comment_analysis(FEEDBACK_ROWS)
    check("코멘트 분석", {"categories": cats, "comments": [c.model_dump() for c in comms]}, ts["fb_analysis"])
    up = sum(1 for r in FEEDBACK_ROWS if r.get("helpful") is True)
    down = sum(1 for r in FEEDBACK_ROWS if r.get("helpful") is False)
    check("satisfaction_rate", round(up / (up + down), 4) if up + down else None, ts["fb_satisfaction"])
    check("classify_comment", [A.classify_comment(t) for t in COMMENT_CASES], ts["comments"])

    # **분기를 실제로 태웠는지 검사기가 스스로 본다.**
    print()
    print("=== 반올림 경계 ===")
    py_rate, py_lat = [], []
    for rows in ROUND_CASES:
        t = len(rows)
        sc = sum(1 for r in rows if A._row_is_success(r))
        py_rate.append(round(sc / t, 4) if t else None)
        xs = [r.get("took_ms") for r in rows if r.get("took_ms") is not None]
        py_lat.append(int(sum(xs) / len(xs)) if xs else None)
    check("success_rate 경계", py_rate, ts["round_rate"])
    check("avg_latency 경계", py_lat, ts["round_lat"])

    print()
    print("=== 응답 조립 — /admin/feedback/stats (합성 행 주입) ===")
    # 원본 핸들러를 통째로 태운다 — `get_supabase_client` 만 가짜로 바꾼다.
    import types

    class _FakeQ:
        def __init__(self, rows): self._rows = rows
        def select(self, *a, **k): return self
        def gte(self, *a, **k): return self
        def order(self, *a, **k): return self
        def execute(self): return types.SimpleNamespace(data=self._rows)

    class _FakeClient:
        def __init__(self, rows): self._rows = rows
        def table(self, _name): return _FakeQ(self._rows)

    _orig = A.get_supabase_client
    A.get_supabase_client = lambda: _FakeClient(FEEDBACK_ROWS)
    try:
        py_fb = A.admin_feedback_stats(range="7d").model_dump()
    finally:
        A.get_supabase_client = _orig
    got_fb = dict(ts["fb_stats"])
    py_fb.pop("generated_at"), got_fb.pop("generated_at")
    check("feedback/stats 응답", py_fb, got_fb)
    if py_fb["total_feedback"] == len(FEEDBACK_ROWS):
        fails += 1
        print("  케이스 무효 — total_feedback 이 전체 행 수와 같아 up+down 분기를 못 가른다")

    print()
    print("=== 케이스가 분기를 태웠는가 ===")
    reasons = {A._classify_failure_reason(r) for r in METRICS_ROWS}
    for label, cond in (
        ("실패 사유 3종 전부", {"permanent_4xx", "transient_5xx", "no_hits"} <= reasons),
        ("실패 샘플 10건 컷", len(A._extract_failed_samples(METRICS_ROWS)) == 10),
        ("코멘트 4종 전부", all(v > 0 for v in cats.values())),
        ("코멘트 10건 컷", len(comms) == 10),
        ("helpful null 행 존재", any(r.get("helpful") is None for r in FEEDBACK_ROWS)),
        ("daily 에 0 인 날 존재", any(b.count == 0 for b in A._build_daily_buckets(METRICS_ROWS, 7))),
        ("분포에 0 인 라벨 존재", any(v == 0 for v in dist.values())),
    ):
        print(f"  {label:<24}{'예' if cond else '**아니오 — 케이스 무효**'}")
        if not cond:
            fails += 1

    print()
    print("=== range 검증 ===")
    check("range=14d", {"ok": True, "range": "14d"}, ts["range_ok"])
    expected_bad = {
        "ok": False,
        "detail": [{
            "type": "literal_error", "loc": ["query", "range"],
            "msg": "Input should be '7d', '14d' or '30d'", "input": "zzz",
            "ctx": {"expected": "'7d', '14d' or '30d'"},
        }],
    }
    check("range=zzz → 422", expected_bad, ts["range_bad"])

    print()
    print("=== 실 DB 응답 — /admin/queries/stats ===")
    for r in ("7d", "14d", "30d"):
        py = A.admin_queries_stats(range=r).model_dump()
        got = dict(ts["queries_stats"][r])
        py.pop("generated_at"), got.pop("generated_at")
        check(f"range={r}", py, got)

    print()
    print("=== 실 DB 응답 — GET /admin/subscriptions ===")
    check("목록", A.admin_list_subscriptions().model_dump(), ts["subscriptions"])

    print()
    print("=== POST /admin/subscriptions — 실행하지 않고 모양만 ===")
    py_row = dict(sub_payload)
    py_row["updated_at"] = datetime.fromtimestamp(
        FIXED_NOW_MS / 1000, tz=timezone.utc
    ).isoformat()
    check("upsert 행", py_row, ts["sub_row"])
    from postgrest import SyncPostgrestClient
    pg = SyncPostgrestClient("https://example.supabase.co/rest/v1", headers={})
    check("upsert 질의",
          str(pg.table("subscriptions").upsert(py_row, on_conflict="user_id").request.params),
          ts["sub_query"])

    print()
    print("=== 본문 422 — 운영 실측과 대조 ===")
    with open(os.path.join(HERE, "fixtures", "admin_422_measured.json"), encoding="utf-8") as f:
        measured = json.load(f)
    for name, raw in body_cases:
        got = ts["bodies"][name]
        if name in measured:
            want = {"ok": False, "detail": measured[name]}
            check(f"{name} (실측)", want, got)
        else:
            # 유효 본문 — 실측은 못 한다(쓰기가 일어난다). pydantic 으로 직접 만든다.
            py = A.SubscriptionUpsertRequest(**json.loads(raw)).model_dump()
            check(f"{name}", {"ok": True, "payload": py}, got)

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
