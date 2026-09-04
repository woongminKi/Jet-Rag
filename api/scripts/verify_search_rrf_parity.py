"""Task 2.3 채점기 — `search/rrf.ts` (문서 그룹·dedupe·정렬) 동등성 대조.

## 무엇을 재는가
RRF 융합 자체는 Postgres RPC 안에 있다. 여기서 옮긴 건 그 결과 row 를 문서 단위로
접고 순서를 정하는 부분이라, 대조 대상은 **정렬된 doc_id 목록과 문서·청크 점수**다.

원본 3)·5) 단계는 함수가 아니라 `search()` 안의 인라인 블록이라 import 가 안 된다.
가드 때(`verify_search_guards_parity.py`)와 같은 방식으로 본문을 복사하되, `search.py`
에서 소스를 떠와 고정본과 대조한다 — 원본이 고쳐지면 채점기가 먼저 죽는다.

## 동점이 이 대조의 핵심이다
정렬 키가 점수 하나뿐이라 동점이 흔하다. 동점의 순서는 안정 정렬에 기대는데, 그건
**입력 순서가 같을 때만** 성립한다. 그래서 합성 케이스에 동점을 여러 모양으로 넣고,
`--live` 에서는 운영 RPC 가 실제로 돌려준 row 순서를 그대로 쓴다.

`--live` 는 운영 DB 에 `search_sparse_only` RPC 를 직접 걸어 **실제 row** 를 받는다.
sparse 를 쓰는 이유는 dense 벡터가 있어야 하는 hybrid 와 달리 임베딩 API 호출이 필요
없어서다 — 그룹·정렬 로직은 row 가 어느 경로에서 왔는지 보지 않는다.

사용:
    api/.venv/bin/python api/scripts/verify_search_rrf_parity.py [--live]
"""

from __future__ import annotations

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

PINNED_GROUP = """
    doc_score: dict[str, float] = {}
    doc_chunk_scores: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rpc_rows:
        doc_id = r["doc_id"]
        chunk_id = r["chunk_id"]
        score = float(r["rrf_score"])
        cover_guard_skip = reranker_used or reranker_path == _RERANKER_PATH_CACHED
        if not cover_guard_skip and _is_cover_chunk(chunk_id):
            score *= _COVER_GUARD_PENALTY
        if not cover_guard_skip and _is_toc_chunk(chunk_id):
            score *= _TOC_GUARD_PENALTY
        if _entity_match_chunk(chunk_id):
            score *= _ENTITY_BOOST_FACTOR
        doc_score[doc_id] = max(doc_score.get(doc_id, 0.0), score)
        existing = doc_chunk_scores[doc_id].get(chunk_id)
        if existing is None or score > existing:
            doc_chunk_scores[doc_id][chunk_id] = score
"""

PINNED_SORT = """
    sorted_doc_ids = sorted(
        docs_meta.keys(), key=lambda did: doc_score[did], reverse=True
    )
"""


def _norm(text: str) -> list[str]:
    out = []
    for line in text.strip("\n").split("\n"):
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def assert_pinned_unchanged() -> None:
    src = open(os.path.join(ROOT, "api", "app", "routers", "search.py"), encoding="utf-8").read()

    group = re.search(
        r"^    doc_score: dict\[str, float\] = \{\}.*?"
        r"^            doc_chunk_scores\[doc_id\]\[chunk_id\] = score$",
        src, re.S | re.M,
    )
    srt = re.search(r"^    sorted_doc_ids = sorted\(.*?^    \)$", src, re.S | re.M)
    for name, m, pinned in (("3) 그룹", group, PINNED_GROUP), ("5) 정렬", srt, PINNED_SORT)):
        if m is None:
            raise SystemExit(f"{name} 블록을 search.py 에서 못 찾았다 — 채점기를 고쳐야 한다.")
        if _norm(m.group(0)) != _norm(pinned):
            print(f"원본 {name} 블록이 고정본과 다르다. 실제 소스:")
            print(m.group(0))
            raise SystemExit("채점기의 복사본을 원본에 맞춰 갱신할 것.")


# ---------------------------------------------------------------- 합성 케이스

def row(cid: str, did: str, score: float) -> dict:
    return {"chunk_id": cid, "doc_id": did, "rrf_score": score,
            "dense_rank": None, "sparse_rank": None}


# (이름, rpc_rows, docs_meta 순서, guard_meta)
SYNTH: list[tuple[str, list[dict], list[str], dict]] = [
    ("빈 입력", [], [], {}),
    ("단일 행", [row("c1", "d1", 0.016)], ["d1"], {}),
    ("문서 점수는 최댓값 (합 아님)",
     [row("c1", "d1", 0.01), row("c2", "d1", 0.03), row("c3", "d1", 0.02)], ["d1"], {}),
    ("같은 chunk_id 중복 → 최댓값으로 접힘",
     [row("c1", "d1", 0.01), row("c1", "d1", 0.03)], ["d1"], {}),
    ("중복이 역순으로 와도 같은 결과",
     [row("c1", "d1", 0.03), row("c1", "d1", 0.01)], ["d1"], {}),
    ("동점 2건 — 입력 순서 유지",
     [row("c1", "d1", 0.02), row("c2", "d2", 0.02)], ["d1", "d2"], {}),
    ("동점 2건 — docs_meta 순서가 뒤집히면 결과도 뒤집힌다",
     [row("c1", "d1", 0.02), row("c2", "d2", 0.02)], ["d2", "d1"], {}),
    # 실제 doc_id 는 UUID 라 동점 순서가 알파벳 순과 무관하다. 2 차 정렬키를 몰래 넣는
    # 실수를 잡으려면 입력 순서가 알파벳 순과 어긋나는 케이스가 있어야 한다.
    ("동점 — 입력이 역알파벳 순",
     [row("c1", "z1", 0.02), row("c2", "a2", 0.02)], ["z1", "a2"], {}),
    ("동점 3건 — 입력 순서가 뒤섞임",
     [row("c1", "m1", 0.02), row("c2", "z2", 0.02), row("c3", "a3", 0.02)],
     ["m1", "z2", "a3"], {}),
    ("동점 3건 — docs_meta 만 다른 순서",
     [row("c1", "m1", 0.02), row("c2", "z2", 0.02), row("c3", "a3", 0.02)],
     ["z2", "a3", "m1"], {}),
    ("가드로 동점이 만들어지는 경우",
     [row("c1", "z1", 0.06), row("c2", "a2", 0.018)], ["z1", "a2"],
     {"c1": {"chunk_idx": 0, "page": 1, "text": "표지", "section_title": ""}}),
    ("동점 여러 건 + 비동점 섞임",
     [row("c1", "d1", 0.02), row("c2", "d2", 0.05), row("c3", "d3", 0.02),
      row("c4", "d4", 0.05), row("c5", "d5", 0.01)],
     ["d1", "d2", "d3", "d4", "d5"], {}),
    ("docs_meta 에서 걸러진 문서는 빠진다",
     [row("c1", "d1", 0.05), row("c2", "d2", 0.02)], ["d2"], {}),
    ("점수 0",
     [row("c1", "d1", 0.0), row("c2", "d2", 0.01)], ["d1", "d2"], {}),
    ("음수 점수 — 0 으로 바닥이 깔린다",
     [row("c1", "d1", -0.5), row("c2", "d2", 0.0)], ["d1", "d2"], {}),
    ("표지 가드가 순위를 뒤집는다",
     [row("c1", "d1", 0.030), row("c2", "d2", 0.020)], ["d1", "d2"],
     {"c1": {"chunk_idx": 0, "page": 1, "text": "SONATA", "section_title": ""}}),
    ("목차 가드가 순위를 뒤집는다",
     [row("c1", "d1", 0.030), row("c2", "d2", 0.020)], ["d1", "d2"],
     {"c1": {"chunk_idx": 3, "page": 2, "text": "목차\\n1. 서론", "section_title": "(vision) 1쪽"}}),
    ("가드 두 개가 겹치면 0.09",
     [row("c1", "d1", 1.0)], ["d1"],
     {"c1": {"chunk_idx": 0, "page": 1, "text": "목차", "section_title": "(vision) 1쪽"}}),
    ("가드 걸린 청크가 같은 문서의 다른 청크에 밀린다",
     [row("c1", "d1", 0.030), row("c2", "d1", 0.020)], ["d1"],
     {"c1": {"chunk_idx": 0, "page": 1, "text": "SONATA", "section_title": ""}}),
    ("한 문서에 청크 다수 + 중복 + 가드",
     [row("c1", "d1", 0.03), row("c2", "d1", 0.02), row("c1", "d1", 0.04),
      row("c3", "d2", 0.035), row("c3", "d2", 0.01)], ["d1", "d2"],
     {"c1": {"chunk_idx": 0, "page": 1, "text": "표지", "section_title": ""}}),
]

# 페이지네이션 — (전체 수, offset, limit)
PAGE_CASES = [(0, 0, 10), (5, 0, 10), (5, 0, 2), (5, 2, 2), (5, 4, 2), (5, 5, 2),
              (5, 6, 2), (5, 0, 0), (5, 3, 100), (12, 10, 10)]


RUNNER_TS = f"""
import {{ groupByDoc, paginate, sortDocIds }} from "file://{SEARCH_DIR}/rrf.ts";
import {{ buildGuardMeta, type GuardMeta }} from "file://{SEARCH_DIR}/guards.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));

// deno-lint-ignore no-explicit-any
const cases = input.cases.map((c: any) => {{
  const guardMeta = new Map<string, GuardMeta>();
  for (const [cid, chunk] of Object.entries(c.guard_meta ?? {{}})) {{
    guardMeta.set(cid, buildGuardMeta(chunk as Record<string, never>));
  }}
  const g = groupByDoc(c.rows, {{
    guardMeta,
    coverGuardSkip: c.cover_guard_skip ?? false,
    tocEnabled: true,
    queryWantsToc: false,
  }});
  return {{
    doc_score: Object.fromEntries(g.docScore),
    doc_chunk_scores: Object.fromEntries(
      [...g.docChunkScores].map(([d, m]) => [d, Object.fromEntries(m)]),
    ),
    candidate_doc_ids: g.candidateDocIds,
    sorted: sortDocIds(c.docs_meta_order, g.docScore),
  }};
}});

// deno-lint-ignore no-explicit-any
const pages = input.pages.map((p: any) =>
  paginate(Array.from({{ length: p[0] }}, (_, i) => `d${{i}}`), p[1], p[2])
);

console.log(JSON.stringify({{ cases, pages }}));
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


# ---------------------------------------------------------------- 원본 복사본

def py_group(rows: list[dict], guard_meta_raw: dict, cover_guard_skip: bool = False) -> tuple:
    """`search.py` 3) 단계의 복사본 (위에서 고정본과 대조했다).

    엔티티 boost 는 운영에서 꺼져 있어 옮기지 않았으므로 여기서도 뺐다 (플랜 §3).
    """
    from collections import defaultdict

    from app.routers import search as S

    meta = {
        cid: {
            "chunk_idx": c.get("chunk_idx"),
            "page": c.get("page"),
            "text_len": len(c.get("text") or ""),
            "section_title": c.get("section_title") or "",
            "text_head": (c.get("text") or "")[: S._TOC_GUARD_HEAD_LEN],
        }
        for cid, c in guard_meta_raw.items()
    }

    def is_cover(cid: str) -> bool:
        m = meta.get(cid)
        if not m:
            return False
        if m["text_len"] > S._COVER_GUARD_TEXT_LEN:
            return False
        return m["chunk_idx"] == 0 or m["page"] == 1

    def is_toc(cid: str) -> bool:
        m = meta.get(cid)
        if not m:
            return False
        if not m["section_title"].startswith("(vision)"):
            return False
        return bool(S._TOC_PATTERN.search(S._strip_vision_meta_prefix(m["text_head"])))

    doc_score: dict[str, float] = {}
    doc_chunk_scores: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        doc_id = r["doc_id"]
        chunk_id = r["chunk_id"]
        score = float(r["rrf_score"])
        if not cover_guard_skip and is_cover(chunk_id):
            score *= S._COVER_GUARD_PENALTY
        if not cover_guard_skip and is_toc(chunk_id):
            score *= S._TOC_GUARD_PENALTY
        doc_score[doc_id] = max(doc_score.get(doc_id, 0.0), score)
        existing = doc_chunk_scores[doc_id].get(chunk_id)
        if existing is None or score > existing:
            doc_chunk_scores[doc_id][chunk_id] = score
    return doc_score, dict(doc_chunk_scores)


def py_sort(docs_meta_order: list[str], doc_score: dict) -> list[str]:
    """`search.py` 5) 단계의 복사본."""
    return sorted(docs_meta_order, key=lambda did: doc_score[did], reverse=True)


# ---------------------------------------------------------------- live

LIVE_QUERIES = [
    "세무", "계약서", "매출", "보고서", "정기결제 해지", "데이터센터 지원 사업",
    "전폭은 얼마인가요?", "시트 종류", "목차", "환불 절차", "신청 자격", "요약",
]


def live_rows() -> list[tuple[str, list[dict], list[str]]]:
    """운영 RPC 를 걸어 실제 row 를 받는다. docs_meta 순서도 실제 응답 순서를 쓴다."""
    import os as _os

    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(os.path.join(ROOT, ".env"))
    c = create_client(_os.environ["SUPABASE_URL"], _os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    owner = (
        c.table("documents").select("user_id").is_("deleted_at", "null")
        .limit(1).execute().data
    )
    if not owner:
        raise SystemExit("문서가 있는 사용자를 못 찾았다.")
    user_id = owner[0]["user_id"]

    from app.routers.search import _build_pgroonga_query

    out = []
    for q in LIVE_QUERIES:
        rows = c.rpc("search_sparse_only", {
            "query_text": _build_pgroonga_query(q),
            "k_rrf": 60, "top_k": 50, "user_id_arg": str(user_id),
        }).execute().data or []
        if not rows:
            continue
        doc_ids = list(dict.fromkeys(r["doc_id"] for r in rows))
        # documents 응답 순서를 그대로 쓴다 — 동점 순서가 여기에 달려 있다.
        meta = c.table("documents").select("id").in_("id", doc_ids).eq(
            "user_id", user_id).is_("deleted_at", "null").execute().data or []
        out.append((q, rows, [m["id"] for m in meta]))
    return out


def live_guard_meta(rows: list[dict]) -> dict:
    import os as _os

    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(os.path.join(ROOT, ".env"))
    c = create_client(_os.environ["SUPABASE_URL"], _os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    cids = list({r["chunk_id"] for r in rows})
    out: dict = {}
    for i in range(0, len(cids), 200):
        res = c.table("chunks").select("id, chunk_idx, page, text, section_title").in_(
            "id", cids[i:i + 200]).execute()
        for r in res.data or []:
            out[r["id"]] = r
    return out


def main() -> None:
    assert_pinned_unchanged()

    cases = [
        {"rows": rows, "docs_meta_order": order, "guard_meta": gm}
        for _, rows, order, gm in SYNTH
    ]
    names = [n for n, _, _, _ in SYNTH]

    live: list[tuple[str, list[dict], list[str]]] = []
    if "--live" in sys.argv:
        live = live_rows()
        for q, rows, order in live:
            gm = live_guard_meta(rows)
            cases.append({"rows": rows, "docs_meta_order": order, "guard_meta": gm})
            names.append(f"[live] {q}")

    ts = run_deno({"cases": cases, "pages": PAGE_CASES})
    fails = 0

    print("=== 그룹·dedupe·정렬 ===")
    for name, case, tv in zip(names, cases, ts["cases"]):
        ds, dcs = py_group(case["rows"], case["guard_meta"])
        want = {
            "doc_score": ds,
            "doc_chunk_scores": dcs,
            "candidate_doc_ids": list(ds.keys()),
            "sorted": py_sort(case["docs_meta_order"], ds),
        }
        ok = (
            want["candidate_doc_ids"] == tv["candidate_doc_ids"]
            and want["sorted"] == tv["sorted"]
            and _close(want["doc_score"], tv["doc_score"])
            and all(_close(v, tv["doc_chunk_scores"].get(d, {})) for d, v in dcs.items())
        )
        if not ok:
            fails += 1
            print(f"  MISMATCH {name}")
            print(f"      py sorted={want['sorted']} score={_r(want['doc_score'])}")
            print(f"      ts sorted={tv['sorted']} score={_r(tv['doc_score'])}")
        elif name.startswith("[live]"):
            print(f"  OK   {name:<34} rows={len(case['rows']):>3} docs={len(tv['sorted'])}")
    print(f"  {len(cases)}건 대조 (합성 {len(SYNTH)} + live {len(live)})")

    print()
    print("=== 페이지네이션 ===")
    for (n, off, lim), tv in zip(PAGE_CASES, ts["pages"]):
        pv = [f"d{i}" for i in range(n)][off:off + lim]
        if pv != tv:
            fails += 1
            print(f"  MISMATCH total={n} offset={off} limit={lim}: py={pv} ts={tv}")
    print(f"  {len(PAGE_CASES)}건 대조")

    print()
    print(f"케이스 {len(cases) + len(PAGE_CASES)}건 대조")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


def _close(a: dict, b: dict) -> bool:
    if set(a) != set(b):
        return False
    return all(abs(a[k] - b[k]) <= 1e-12 for k in a)


def _r(d: dict) -> dict:
    return {k: round(v, 6) for k, v in d.items()}


if __name__ == "__main__":
    main()
