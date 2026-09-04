"""Task 2.2 채점기 — `search/guards.ts` · `search/filters.ts` 동등성 대조.

두 대상을 다른 방식으로 잰다.

1. **가드** — 원본의 `_is_cover_chunk` / `_is_toc_chunk` 는 `search()` 안의 **클로저**라
   그냥 import 할 수 없다. 그래서 본문을 이 파일에 복사하되, 판정을 실제로 좌우하는
   것들(`_TOC_PATTERN` · `_TOC_INTENT_PATTERN` · `_strip_vision_meta_prefix` ·
   `_COVER_GUARD_TEXT_LEN`)은 **모듈에서 직접 가져다 쓴다**. 거기가 바뀌면 바로 잡힌다.
   복사한 5 줄짜리 흐름이 조용히 낡는 걸 막으려고, `search.py` 에서 함수 소스를 떠와
   고정본과 대조한다(`assert_closure_unchanged`) — 원본이 고쳐지면 채점기가 먼저 죽는다.

2. **필터** — 순수 함수가 아니라 쿼리 빌더다. "같은 결과"의 기준은 PostgREST 에 나가는
   **쿼리스트링**이라 postgrest-py 와 supabase-js 가 만든 URL 을 바이트로 대조한다.
   값에 `,` 나 공백이 섞이면 따옴표·인코딩 규칙이 미묘해서 눈으로는 못 잡는다.

`--live` 는 위 케이스 대신(=추가로) **운영 DB 의 실제 청크**로 같은 대조를 한다.
손으로 지은 케이스는 내가 상상한 모양만 덮으므로, vision 청크 전량과 표지 후보 전량,
그리고 나머지 무작위 표본으로 한 번 더 잰다. 청크 본문은 사용자 문서라 **저장하지 않고**
판정 결과만 센다 — 불일치가 나면 그때만 해당 청크 id 와 앞 40 자를 보여준다.

사용:
    api/.venv/bin/python api/scripts/verify_search_guards_parity.py [--live [N]]
"""

from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SEARCH_DIR = os.path.join(ROOT, "supabase", "functions", "_shared", "search")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

# `search.py` 에서 떠온 클로저 본문의 고정본. 원본이 바뀌면 대조가 실패한다.
PINNED_COVER = """
    def _is_cover_chunk(chunk_id: str) -> bool:
        meta = cover_guard_meta.get(chunk_id)
        if not meta:
            return False
        if meta["text_len"] > _COVER_GUARD_TEXT_LEN:
            return False
        return meta["chunk_idx"] == 0 or meta["page"] == 1
"""

PINNED_TOC = """
    def _is_toc_chunk(chunk_id: str) -> bool:
        if not _toc_guard_enabled:
            return False
        if _query_wants_toc:
            return False
        meta = cover_guard_meta.get(chunk_id)
        if not meta:
            return False
        if not meta["section_title"].startswith("(vision)"):
            return False
        body_head = _strip_vision_meta_prefix(meta["text_head"])
        return bool(_TOC_PATTERN.search(body_head))
"""


def assert_closure_unchanged() -> None:
    """`search.py` 안의 클로저 본문이 고정본과 같은지 본다 (주석·빈 줄 제외)."""
    src = open(os.path.join(ROOT, "api", "app", "routers", "search.py"), encoding="utf-8").read()

    def norm(text: str) -> list[str]:
        out = []
        for line in text.strip("\n").split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                out.append(stripped)
        return out

    for name, pinned in (("_is_cover_chunk", PINNED_COVER), ("_is_toc_chunk", PINNED_TOC)):
        m = re.search(rf"^    def {name}\(.*?(?=\n\n)", src, re.S | re.M)
        if m is None:
            raise SystemExit(f"{name} 를 search.py 에서 못 찾았다 — 채점기를 고쳐야 한다.")
        if norm(m.group(0)) != norm(pinned):
            print(f"원본 {name} 가 고정본과 다르다. 실제 소스:")
            print(m.group(0))
            raise SystemExit("채점기의 복사본을 원본에 맞춰 갱신할 것.")


# ---------------------------------------------------------------- 케이스

# (chunk_idx, page, text, section_title)
CHUNK_CASES: list[tuple] = [
    # cover — 짧고 맨 앞
    (0, 1, "SONATA", ""),
    (0, 3, "SONATA", ""),
    (5, 1, "SONATA", ""),
    (5, 3, "SONATA", ""),
    (0, 1, "가" * 30, ""),
    (0, 1, "가" * 31, ""),
    (0, 1, "", ""),
    (None, 1, "짧은 표지", ""),
    (0, None, "짧은 표지", ""),
    (None, None, "짧은 표지", ""),
    # 코드포인트 vs UTF-16 — 이모지가 섞이면 길이 계산이 갈린다
    (0, 1, "📌" * 30, ""),
    (0, 1, "📌" * 16, ""),
    (0, 1, "📌" * 15 + "가", ""),
    # toc — vision 한정
    (2, 5, "목차\n1. 서론\n2. 본론", "(vision) 1쪽"),
    (2, 5, "목차\n1. 서론", "본문"),
    (2, 5, "목 차\n1. 서론", "(vision) 1쪽"),
    (2, 5, "목    차\n1. 서론", "(vision) 1쪽"),
    (2, 5, "차례\n1. 서론", "(vision) 1쪽"),
    (2, 5, "차 례\n1. 서론", "(vision) 1쪽"),
    (2, 5, "차  례\n1. 서론", "(vision) 1쪽"),
    (2, 5, "1. 서론\n차례", "(vision) 1쪽"),
    (2, 5, "가차례나", "(vision) 1쪽"),
    (2, 5, "절차례가 있다", "(vision) 1쪽"),
    (2, 5, "[문서] 이 문서는 목차를 담고 있다\n\n1. 서론", "(vision) 1쪽"),
    (2, 5, "[문서] 표지\n\n목차\n1. 서론", "(vision) 1쪽"),
    (2, 5, "[문서] 목차 설명만 있고 구분자 없음", "(vision) 1쪽"),
    (2, 5, "(vision) 이 아닌 제목", "vision 1쪽"),
    # head 100자 경계 — 101번째의 "목차" 는 안 보여야 한다
    (2, 5, "가" * 100 + "목차", "(vision) 1쪽"),
    (2, 5, "가" * 98 + "목차", "(vision) 1쪽"),
    (2, 5, "📌" * 100 + "목차", "(vision) 1쪽"),
    # Python 과 JS 의 `\\s` 가 갈리는 문자들
    (2, 5, "목차 1. 서론", "(vision) 1쪽"),
    (2, 5, "목차 1. 서론", "(vision) 1쪽"),
    (2, 5, "목﻿차 1. 서론", "(vision) 1쪽"),
    (2, 5, "목　차 1. 서론", "(vision) 1쪽"),
    (2, 5, "목 차 1. 서론", "(vision) 1쪽"),
    (2, 5, "\n차례\n1. 서론", "(vision) 1쪽"),
    (2, 5, ".차례 1. 서론", "(vision) 1쪽"),
    (2, 5, "차례", "(vision) 1쪽"),
    (2, 5, "차례\n", "(vision) 1쪽"),
    (2, 5, "차례가", "(vision) 1쪽"),
]

QUERY_CASES = [
    "목차 보여줘", "목차", "차례", "차 례", "목 차", "목차는", "목차를 알려줘",
    "목차가나다", "목차가나다라", "세무 신고", "이 문서의 목차?", "목차!", "목차.",
    "목차,", "가목차", "차례로 설명해줘", "절차례", "목차\n", "", "목차 ",
    "목　차", "목차", "목﻿차", "차례",
]

# (userId, candidateDocIds, docType, tags, fromDate, toDate)
FILTER_CASES: list[tuple] = [
    ("u1", ["a", "b"], None, None, None, None),
    ("u1", [], None, None, None, None),
    ("u1", ["a"], "pdf", None, None, None),
    ("u1", ["a"], None, ["세무"], None, None),
    ("u1", ["a"], None, ["세무", "보고서"], None, None),
    ("u1", ["a"], None, [], None, None),
    ("u1", ["a"], None, None, "2026-04-01T00:00:00+00:00", None),
    ("u1", ["a"], None, None, None, "2026-05-01T00:00:00+00:00"),
    ("u1", ["a"], "hwp", ["세무"], "2026-04-01T00:00:00+00:00", "2026-05-01T00:00:00+00:00"),
    # 인코딩이 미묘한 값들 — 쉼표·공백·따옴표·한글·유니코드
    ("u1", ["a,b", "c d", 'e"f', "한글-id"], None, ["태그,쉼표", "태그 공백"], None, None),
    ("사용자 1", ["a"], None, None, None, None),
    ("u1", ["a"], None, ["{중괄호}", "back\\slash"], None, None),
    ("u1", ["a"], None, None, "2026-04-01T09:00:00+09:00", "2026-04-01T09:00:00.123456+00:00"),
    ("u1", ["(paren)", "%percent", "+plus", "&amp"], None, None, None, None),
]

RUNNER_TS = f"""
import {{ createClient }} from "@supabase/supabase-js";
import {{
  applyGuards, buildGuardMeta, isCoverChunk, isTocChunk, queryWantsToc,
}} from "file://{SEARCH_DIR}/guards.ts";
import {{ buildDocumentsQuery }} from "file://{SEARCH_DIR}/filters.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const client = createClient("https://example.supabase.co", "anon");

const chunks = input.chunks.map((c: Record<string, unknown>) => {{
  const meta = buildGuardMeta(c);
  return {{
    meta_len: meta.textLen,
    cover: isCoverChunk(meta),
    toc_on: isTocChunk(meta, {{ enabled: true, queryWantsToc: false }}),
    toc_off: isTocChunk(meta, {{ enabled: false, queryWantsToc: false }}),
    toc_wanted: isTocChunk(meta, {{ enabled: true, queryWantsToc: true }}),
    score: applyGuards(1.0, meta, {{ skip: false, tocEnabled: true, queryWantsToc: false }}),
    score_skip: applyGuards(1.0, meta, {{ skip: true, tocEnabled: true, queryWantsToc: false }}),
  }};
}});

const queries = input.queries.map((q: string) => queryWantsToc(q));

// deno-lint-ignore no-explicit-any
const filters = input.filters.map((f: any) =>
  (buildDocumentsQuery(client, f) as any).url.searchParams.toString()
);

console.log(JSON.stringify({{ chunks, queries, filters }}));
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
            capture_output=True, text=True, timeout=300,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:2000]}")
    return json.loads(proc.stdout)


def load_live_chunks(sample: int) -> list[dict]:
    """vision 청크 전량 + 표지 후보 전량 + 나머지 무작위 표본."""
    import os as _os

    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(os.path.join(ROOT, ".env"))
    c = create_client(_os.environ["SUPABASE_URL"], _os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    cols = "id, chunk_idx, page, text, section_title"
    rows: dict[str, dict] = {}

    def add(res) -> None:
        for r in res.data or []:
            rows[r["id"]] = r

    # vision 청크 — toc 가드가 유일하게 발동하는 대상이라 전량 본다.
    add(c.table("chunks").select(cols).like("section_title", "(vision)%").limit(1000).execute())
    # 표지 후보 — cover 가드 대상.
    add(c.table("chunks").select(cols).or_("chunk_idx.eq.0,page.eq.1").limit(1000).execute())
    # 나머지 — 가드가 안 걸리는 게 정상인 대조군(음성 표본).
    # PostgREST 는 요청당 1000 행에서 자른다. 그냥 limit 을 키우면 조용히 잘리므로 쪽수를 넘긴다.
    page = 0
    while len(rows) < sample:
        res = c.table("chunks").select(cols).order("id").range(page * 1000, page * 1000 + 999).execute()
        if not res.data:
            break
        add(res)
        page += 1
    return list(rows.values())


def run_live(sample: int) -> int:
    from app.routers import search as S

    chunks = load_live_chunks(sample)
    payload = {
        "chunks": [
            {
                "chunk_idx": r.get("chunk_idx"), "page": r.get("page"),
                "text": r.get("text"), "section_title": r.get("section_title"),
            }
            for r in chunks
        ],
        "queries": [], "filters": [],
    }
    ts = run_deno(payload)

    fails = 0
    counts = {"cover": 0, "toc": 0, "both": 0}
    for r, tv in zip(chunks, ts["chunks"]):
        text = r.get("text") or ""
        meta = {
            "chunk_idx": r.get("chunk_idx"), "page": r.get("page"),
            "text_len": len(text), "section_title": r.get("section_title") or "",
            "text_head": text[: S._TOC_GUARD_HEAD_LEN],
        }
        cover = _is_cover(meta, S)
        toc = _is_toc(meta, True, False, S)
        if cover:
            counts["cover"] += 1
        if toc:
            counts["toc"] += 1
        if cover and toc:
            counts["both"] += 1
        if (cover, toc, meta["text_len"]) != (tv["cover"], tv["toc_on"], tv["meta_len"]):
            fails += 1
            if fails <= 10:
                print(f"  MISMATCH id={r['id']} {text[:40]!r}")
                print(f"      py=cover:{cover} toc:{toc} len:{meta['text_len']}")
                print(f"      ts=cover:{tv['cover']} toc:{tv['toc_on']} len:{tv['meta_len']}")

    print(f"실제 청크 {len(chunks)}건 대조")
    print(f"  가드 발동 — cover {counts['cover']} / toc {counts['toc']} / 둘 다 {counts['both']}")
    print("  FAIL 0" if fails == 0 else f"  FAIL {fails}")
    return fails


def _is_cover(meta: dict, S) -> bool:
    if not meta:
        return False
    if meta["text_len"] > S._COVER_GUARD_TEXT_LEN:
        return False
    return meta["chunk_idx"] == 0 or meta["page"] == 1


def _is_toc(meta: dict, enabled: bool, wants: bool, S) -> bool:
    if not enabled or wants or not meta:
        return False
    if not meta["section_title"].startswith("(vision)"):
        return False
    return bool(S._TOC_PATTERN.search(S._strip_vision_meta_prefix(meta["text_head"])))


def main() -> None:
    assert_closure_unchanged()

    if "--live" in sys.argv:
        i = sys.argv.index("--live")
        n = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 and sys.argv[i + 1].isdigit() else 3000
        sys.exit(1 if run_live(n) else 0)

    from app.routers import search as S

    def build_meta(chunk_idx, page, text, section_title) -> dict:
        return {
            "chunk_idx": chunk_idx,
            "page": page,
            "text_len": len(text or ""),
            "section_title": section_title or "",
            "text_head": (text or "")[: S._TOC_GUARD_HEAD_LEN],
        }

    # `_is_cover` / `_is_toc` 는 `search.py` 클로저의 복사본이다 (고정본과 대조했다).
    def is_cover(meta: dict) -> bool:
        return _is_cover(meta, S)

    def is_toc(meta: dict, enabled: bool, wants: bool) -> bool:
        return _is_toc(meta, enabled, wants, S)

    payload = {
        "chunks": [
            {"chunk_idx": ci, "page": p, "text": t, "section_title": st}
            for ci, p, t, st in CHUNK_CASES
        ],
        "queries": QUERY_CASES,
        "filters": [
            {
                "userId": u, "candidateDocIds": ids, "docType": dt,
                "tags": tags, "fromDate": fd, "toDate": td,
            }
            for u, ids, dt, tags, fd, td in FILTER_CASES
        ],
    }
    ts = run_deno(payload)

    fails = 0

    print("=== 가드 판정 ===")
    for (ci, p, t, st), tv in zip(CHUNK_CASES, ts["chunks"]):
        meta = build_meta(ci, p, t, st)
        want = {
            "meta_len": meta["text_len"],
            "cover": is_cover(meta),
            "toc_on": is_toc(meta, True, False),
            "toc_off": is_toc(meta, False, False),
            "toc_wanted": is_toc(meta, True, True),
        }
        got = {k: tv[k] for k in want}
        # 점수까지 대조 — 판정이 곱셈으로 제대로 이어지는지.
        score = 1.0
        if want["cover"]:
            score *= S._COVER_GUARD_PENALTY
        if want["toc_on"]:
            score *= S._TOC_GUARD_PENALTY
        if want != got or abs(tv["score"] - score) > 1e-12 or tv["score_skip"] != 1.0:
            fails += 1
            print(f"  MISMATCH {t[:26]!r} title={st!r}")
            print(f"      py={want} score={score}")
            print(f"      ts={got} score={tv['score']}")
    print(f"  {len(CHUNK_CASES)}건 대조")

    print()
    print("=== 질의 목차 의도 ===")
    for q, tv in zip(QUERY_CASES, ts["queries"]):
        pv = bool(S._TOC_INTENT_PATTERN.search(q))
        if pv != tv:
            fails += 1
            print(f"  MISMATCH {q!r}: py={pv} ts={tv}")
    print(f"  {len(QUERY_CASES)}건 대조")

    print()
    print("=== 메타 필터 (요청 URL) ===")
    from postgrest import SyncPostgrestClient

    client = SyncPostgrestClient("https://example.supabase.co/rest/v1", headers={})
    for (u, ids, dt, tags, fd, td), tv in zip(FILTER_CASES, ts["filters"]):
        q = (
            client.table("documents")
            .select("id, title, doc_type, tags, summary, created_at, doc_embedding")
            .in_("id", ids)
            .eq("user_id", u)
            .is_("deleted_at", "null")
        )
        if dt:
            q = q.eq("doc_type", dt)
        if tags:
            q = q.contains("tags", tags)
        if fd:
            q = q.gte("created_at", fd)
        if td:
            q = q.lte("created_at", td)
        pv = str(q.request.params)
        if pv != tv:
            fails += 1
            print(f"  MISMATCH ids={ids} tags={tags}")
            print(f"      py={pv}")
            print(f"      ts={tv}")
        else:
            print(f"  OK   {(dt or '-'):<5} tags={str(tags or '-'):<24} {pv[:52]}…")

    total = len(CHUNK_CASES) + len(QUERY_CASES) + len(FILTER_CASES)
    print()
    print(f"케이스 {total}건 대조")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
