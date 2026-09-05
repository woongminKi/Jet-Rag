"""Task 2.6 채점기 — `search/meta_fast_path.ts` 를 `meta_filter_fast_path.py` 와 대조.

## 왜 이게 위험한가
이건 **ENV 토글이 아니라 항상 켜진 분기**다. 판정이 한 건만 갈려도 그 질의는 통째로
다른 경로를 타고(임베딩·RPC 없이 documents 만) 결과가 달라진다. `unsupported.ts` 로
막을 수도 없다.

## 세 층으로 잰다
1. **plan 판정** — `is_meta_only` 는 모듈 수준 함수라 그대로 import 한다. `today` 를
   주입해 상대 날짜(`어제`·`지난주`)를 결정적으로 만든다. 날짜 범위는 ISO 문자열까지
   완전 일치로 본다 — 그 값이 그대로 `created_at` 필터가 된다.
2. **요청 URL** — plan 이 같아도 쿼리 조립이 다르면 결과가 갈린다. postgrest-py 와
   supabase-js 가 바이트 단위로 같은 쿼리스트링을 만드는 걸 Task 2.2 에서 확인했으므로,
   실행하지 않고 **URL 을 대조**한다.
3. **`--live` 실행** — 실제 documents 에 질의해 행 id 목록을 대조한다.

2) 를 따로 두는 이유가 있다. 처음엔 1) + 3) 만 있었는데, 음성 대조로 `lt`→`lte`,
`contains`→`overlaps`, `limit 20`→`5` 를 각각 심었을 때 **셋 다 한 건도 안 잡혔다.**
운영 데이터에 경계 시각 문서가 없고, 태그 질의가 단일 태그라 AND/OR 이 같고, 결과가
5 건을 넘는 질의가 없어서다. 데이터가 없어서 못 잡는 걸 "통과" 로 읽으면 안 된다.

## `\\d` 와 `int()` 가 유니코드를 받는다
Python `re` 의 `\\d` 는 Nd 문자 전부를, `int()` 도 그 값을 받는다 — `２０２６년 ４월` 이
실제로 매칭된다(실측). JS `\\d` 는 ASCII 뿐이라 `\\p{Nd}` + ASCII 변환으로 맞췄고,
케이스에 전각 숫자를 넣어 고정했다.

사용:
    api/.venv/bin/python api/scripts/verify_meta_fast_path_parity.py [--live]
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

# 상대 날짜를 결정적으로 만들기 위한 고정 기준일.
TODAY = (2026, 9, 5)

QUERIES = [
    # planner 명세 §C 8 케이스
    "#투자",
    "어제 받은 문서",
    "2025년 3월 회의록",
    "프로젝트 X 기획서",
    "왜 이 펀드가 손실났나",
    "#투자 수익률 어떻게 계산",
    # 절대 날짜 표기 변형
    "2026-04-01 문서", "2026.4.1 문서", "2026/4/1 문서", "2026-4-1 문서",
    "2026년 4월 1일 자료", "2026년 4월 자료", "2026년 12월 자료",
    "2026년 13월 자료", "2026-02-30 자료", "2028-02-29 자료", "2026-02-29 자료",
    "0000-01-01 자료", "9999-12-31 자료",
    # 전각 숫자 — Python 은 받는다
    "２０２６년 ４월 자료", "２０２６-０４-０１ 자료",
    # 상대 날짜 (오늘=2026-09-05 고정)
    "오늘 문서", "어제 문서", "그저께 문서", "이번주 문서", "지난주 문서",
    "이번달 문서", "지난달 문서",
    "어제 오늘 문서",          # 순서상 오늘이 먼저 걸린다
    "지난주 지난달 문서",
    # 태그
    "#a #b #a", "#한글_태그-1", "#", "# ", "#투자 문서", "#투자 어제",
    "#a#b",
    # 문서유형 접미어 / 단독 명사
    "계약서", "계약서 보여줘", "보고서를 찾아줘", "문서의 요약",
    "결론", "시트 종류", "소나타 시트 종류", "매출",
    "회의록", "리포트", "공문", "파일", "요약",
    # 토큰 수 경계 (5 이하)
    "가 나 다 라 문서", "가 나 다 라 마 문서",
    # 의문/서술 어구
    "어떤 문서", "문서 어디", "문서 몇 개", "언제 받은 문서", "누가 쓴 보고서",
    # stopword 만 남는 경우
    "#투자 보여줘", "어제 알려줘", "#투자 주세요",
    # 공백·정규화
    "  #투자   문서  ", "#투자\t문서", "#투자　문서",
    # 빈 질의
    "", " ", "　",
    # 조사
    "문서를", "자료의", "보고서에", "문서랑",
    # 섞임
    "2026년 4월 #투자 보고서", "어제 #급여 문서 보여줘",
    # --- 실제 문서에 매칭되는 질의 (live 실행에서 쿼리 조립을 실제로 태운다) ---
    # 이게 없으면 54 건 중 2 건만 결과가 있어서 날짜·태그·ILIKE·정렬이 사실상 미검증이다.
    "#대법원", "#AWS", "#대법원 보여줘",
    "2026년 5월 문서",            # 정렬(created_at desc) 이 걸리는 다건 결과
    "2026년 7월 자료",
    "2026-05-05 문서",
    "사업보고서",                  # 접미어 단독 → title ILIKE
    "2026년 5월 이력서",           # 날짜 가드 + 단독 명사 → title ILIKE
    "2026년 5월 규정",
    "#대법원 2026년 5월",          # tag + date
    "2026년 5월 #대법원 판결",      # date + tag + title
    "law 자료",
]

RUNNER_TS = f"""
import {{ isMetaOnly }} from "file://{SEARCH_DIR}/meta_fast_path.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const t = input.today as [number, number, number];
console.log(JSON.stringify(input.queries.map((q: string) => {{
  let p;
  try {{
    p = isMetaOnly(q, () => t);
  }} catch (_e) {{
    return "ERR";  // 원본이 죽는 입력 — 양쪽 다 죽어야 한다
  }}
  return p === null ? null : {{
    date_range: p.dateRange,
    tags: p.tags,
    title_ilike: p.titleIlike,
    matched_kind: p.matchedKind,
    residual_tokens: p.residualTokens,
  }};
}})));
"""

LIVE_RUNNER_TS = f"""
import {{ createClient }} from "@supabase/supabase-js";
import {{ isMetaOnly, runFastPath }} from "file://{SEARCH_DIR}/meta_fast_path.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const t = input.today as [number, number, number];
const client = createClient(input.url, input.key, {{ auth: {{ persistSession: false }} }});
const out: (string[] | null)[] = [];
for (const q of input.queries as string[]) {{
  let p;
  try {{
    p = isMetaOnly(q, () => t);
  }} catch (_e) {{
    out.push(null);
    continue;
  }}
  if (p === null) {{ out.push(null); continue; }}
  const rows = await runFastPath(client, p, input.user_id);
  out.push(rows.map((r) => r.id));
}}
console.log(JSON.stringify(out));
"""


# 요청 URL 대조용 plan — 데이터에 의존하지 않으므로 경계 조합을 마음대로 넣는다.
QUERY_PLANS: list[tuple[str, dict]] = [
    ("날짜만", {"dateRange": ["2026-05-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00"],
                "tags": [], "titleIlike": None, "matchedKind": "date", "residualTokens": []}),
    ("태그 1개", {"dateRange": None, "tags": ["대법원"], "titleIlike": None,
                  "matchedKind": "tag", "residualTokens": []}),
    # 태그 2개 — `contains`(AND) 와 `overlaps`(OR) 이 갈리는 유일한 자리다.
    ("태그 2개", {"dateRange": None, "tags": ["대법원", "소멸시효"], "titleIlike": None,
                  "matchedKind": "tag", "residualTokens": []}),
    ("제목만", {"dateRange": None, "tags": [], "titleIlike": "사업보고서",
                "matchedKind": "title", "residualTokens": []}),
    ("제목에 특수문자", {"dateRange": None, "tags": [], "titleIlike": "a,b%c_d",
                        "matchedKind": "title", "residualTokens": []}),
    ("셋 다", {"dateRange": ["2026-05-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00"],
               "tags": ["대법원", "판결"], "titleIlike": "law",
               "matchedKind": "date+tag+title", "residualTokens": []}),
]

QUERY_RUNNER_TS = f"""
import {{ createClient }} from "@supabase/supabase-js";
import {{ buildFastPathQuery }} from "file://{SEARCH_DIR}/meta_fast_path.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const client = createClient("https://example.supabase.co", "anon");

// **`buildFastPathQuery` 를 직접 부른다.** 조립을 여기 복붙하면 그 함수의 버그를
// 못 잡는다 — 실제로 처음엔 복붙해 뒀다가 음성 대조 4 종을 전부 놓쳤다.
// deno-lint-ignore no-explicit-any
const out = input.plans.map((plan: any) =>
  buildFastPathQuery(client, plan, input.user_id).url.searchParams.toString()
);
console.log(JSON.stringify(out));
"""


def run_deno(script: str, payload: dict, timeout: int = 600) -> list:
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
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:2500]}")
    return json.loads(proc.stdout)


def main() -> None:
    from datetime import date

    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
    from app.services import meta_filter_fast_path as M

    today = date(*TODAY)
    ts = run_deno(RUNNER_TS, {"queries": QUERIES, "today": list(TODAY)})

    fails = 0
    fired = 0
    print(f"=== plan 판정 (오늘 = {today}) ===")
    for q, tv in zip(QUERIES, ts):
        try:
            plan = M.is_meta_only(q, today=today)
        except Exception:
            # 원본이 잡히지 않은 예외로 죽는 입력 (운영에서 500). 재현이 맞다.
            plan = "ERR"
        if plan == "ERR":
            pv = "ERR"
        elif plan is None:
            pv = None
        else:
            pv = {
                "date_range": (
                    [plan.date_range[0].isoformat(), plan.date_range[1].isoformat()]
                    if plan.date_range else None
                ),
                "tags": list(plan.tags),
                "title_ilike": plan.title_ilike,
                "matched_kind": plan.matched_kind,
                "residual_tokens": list(plan.residual_tokens),
            }
        if pv is not None and pv != "ERR":
            fired += 1
        if pv != tv:
            fails += 1
            print(f"  MISMATCH {q!r}")
            print(f"      py={pv}")
            print(f"      ts={tv}")
    print(f"  {len(QUERIES)}건 대조 — fast path 발화 {fired}건 / RAG {len(QUERIES) - fired}건")

    fails += compare_query_urls()

    live_n = 0
    if "--live" in sys.argv:
        live_n = run_live(today, ts)
        fails += live_n[1]
        live_n = live_n[0]

    print()
    print(f"케이스 {len(QUERIES) + len(QUERY_PLANS) + live_n}건 대조")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


PINNED_RUN = """
    client = get_supabase_client()
    q = (
        client.table("documents")
        .select("id, title, doc_type, tags, summary, created_at")
        .eq("user_id", user_id)
        .is_("deleted_at", "null")
    )
    if plan.date_range is not None:
        from_dt, to_dt = plan.date_range
        q = q.gte("created_at", from_dt.isoformat()).lt(
            "created_at", to_dt.isoformat()
        )
    if plan.tags:
        q = q.contains("tags", list(plan.tags))
    if plan.title_ilike:
        q = q.ilike("title", f"%{plan.title_ilike}%")
    q = q.order("created_at", desc=True).limit(_FAST_PATH_LIMIT)
"""


def compare_query_urls() -> int:
    """실행하지 않고 요청 URL 만 대조한다. 데이터 유무와 무관하게 조립을 검사한다."""
    import re

    from postgrest import SyncPostgrestClient

    # 원본 `run()` 의 조립부가 바뀌면 아래 복사본이 낡는다 — 소스로 고정한다.
    src = open(
        os.path.join(ROOT, "api", "app", "services", "meta_filter_fast_path.py"),
        encoding="utf-8",
    ).read()
    m = re.search(
        r"^    client = get_supabase_client\(\).*?"
        r'^    q = q\.order\("created_at", desc=True\)\.limit\(_FAST_PATH_LIMIT\)$',
        src, re.S | re.M,
    )

    def norm(t: str) -> list[str]:
        return [ln.strip() for ln in t.strip("\n").split("\n")
                if ln.strip() and not ln.strip().startswith("#")]

    if m is None or norm(m.group(0)) != norm(PINNED_RUN):
        print("원본 `run()` 의 쿼리 조립이 고정본과 다르다 — 채점기를 갱신할 것.")
        if m:
            print(m.group(0))
        return 1

    user_id = "u-fixed"
    ts = run_deno(QUERY_RUNNER_TS, {"plans": [p for _, p in QUERY_PLANS],
                                    "user_id": user_id})
    client = SyncPostgrestClient("https://example.supabase.co/rest/v1", headers={})

    print()
    print("=== 요청 URL (실행 없이 조립만) ===")
    fails = 0
    for (name, plan), tv in zip(QUERY_PLANS, ts):
        q = (
            client.table("documents")
            .select("id, title, doc_type, tags, summary, created_at")
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
        )
        if plan["dateRange"] is not None:
            q = q.gte("created_at", plan["dateRange"][0]).lt(
                "created_at", plan["dateRange"][1])
        if plan["tags"]:
            q = q.contains("tags", list(plan["tags"]))
        if plan["titleIlike"]:
            q = q.ilike("title", f"%{plan['titleIlike']}%")
        q = q.order("created_at", desc=True).limit(20)
        pv = str(q.request.params)
        if pv != tv:
            fails += 1
            print(f"  MISMATCH {name}")
            print(f"      py={pv}")
            print(f"      ts={tv}")
        else:
            print(f"  {name:<16} OK   {pv[:64]}…")
    print(f"  {len(QUERY_PLANS)}건 대조")
    return fails


def run_live(today, ts_plans) -> tuple[int, int]:
    """실제 documents 에 질의해 행 id 목록을 대조한다."""
    from supabase import create_client

    from app.services import meta_filter_fast_path as M

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    c = create_client(url, key)
    rows = (
        c.table("documents").select("user_id").is_("deleted_at", "null")
        .limit(1).execute().data
    )
    if not rows:
        print("  (문서를 가진 사용자가 없어 live 를 건너뛴다)")
        return 0, 0
    user_id = rows[0]["user_id"]

    ts_ids = run_deno(LIVE_RUNNER_TS, {
        "queries": QUERIES, "today": list(TODAY),
        "url": url, "key": key, "user_id": user_id,
    })

    print()
    print("=== live 실행 결과 (documents 행 id) ===")
    fails = 0
    n = 0
    nonempty = 0
    for q, tv in zip(QUERIES, ts_ids):
        try:
            plan = M.is_meta_only(q, today=today)
        except Exception:
            continue  # 위 plan 대조에서 이미 다뤘다
        if plan is None:
            if tv is not None:
                fails += 1
                print(f"  MISMATCH {q!r}: py=RAG ts=fast({len(tv)}행)")
            continue
        n += 1
        pv = [r["id"] for r in M.run(plan, user_id=user_id)]
        if pv:
            nonempty += 1
        if pv != tv:
            fails += 1
            print(f"  MISMATCH {q!r}: py={len(pv)}행 ts={len(tv or [])}행")
            print(f"      py={pv[:4]}")
            print(f"      ts={(tv or [])[:4]}")
    print(f"  {n}건 실행 — 그중 결과가 있는 질의 {nonempty}건")
    return n, fails


if __name__ == "__main__":
    main()
