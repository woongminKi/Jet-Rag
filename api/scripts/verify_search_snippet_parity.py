"""Task 2.4 채점기 — `search/snippet.ts` · `search/assemble.ts` 동등성 대조.

## 네 가지를 각각 다르게 잰다

1. **스니펫·하이라이트** — `_make_snippet_with_highlights` 는 모듈 수준 함수라 그대로
   import 해서 부른다. 복사본이 없으니 원본이 바뀌면 바로 드러난다.
   대조는 **문자열과 인덱스 전부 완전 일치**다. 프론트가 `[start, end]` 로 스니펫을 잘라
   강조하므로 하나만 밀려도 엉뚱한 글자가 강조된다.

2. **동의어 마커 제거** — `strip_synonym_marker` 도 import 해서 부른다.

3. **`pyRound`** — Python `round()` 는 은행가 반올림이고 JS 는 절반을 위로 올린다.
   손으로 고른 값으로는 갈리는 지점을 못 덮으므로, 소수 4 자리에서 **정확히 절반이 되는
   값 전체**(`m/32`, m 홀수)를 넣고 거기에 무작위 값을 대량으로 섞는다.

4. **조립** — `search()` 안의 인라인 블록이라 본문을 복사하되, `search.py` 에서 소스를
   떠와 고정본과 대조한다(다른 채점기와 같은 방식).

## `1.0` vs `1` — 바이트는 다르고 값은 같다
`relevance` 를 Python(pydantic)은 `1.0` 으로, JS(`JSON.stringify`)는 `1` 로 쓴다(실측).
**1 위 문서의 relevance 는 항상 1.0** 이라 모든 응답에 나타난다. 파싱하면 같은 수라
프론트 동작에는 차이가 없지만, Task 2.8 의 엔드투엔드 대조는 **바이트가 아니라 파싱한
값으로** 비교해야 한다. 여기서도 그렇게 한다.

`--live` 는 운영 청크로 1) 을 한 번 더 돌린다. **astral 문자(U+FFFF 초과)가 든 청크가
실제로 20 건 있고**, 거기서 Python(코드포인트)과 JS(UTF-16)의 인덱스가 갈린다.
그래서 표본을 무작위로 뽑지 않고 **astral 청크를 전량 포함**시킨다.

사용:
    api/.venv/bin/python api/scripts/verify_search_snippet_parity.py [--live]
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SEARCH_DIR = os.path.join(ROOT, "supabase", "functions", "_shared", "search")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

PINNED_ASSEMBLE = '''
    top_score = doc_score[sorted_doc_ids[0]] if sorted_doc_ids else 1.0
    normalize = top_score if top_score > 0 else 1.0
    items: list[SearchHit] = []
    for doc_id in page_doc_ids:
        meta = docs_meta[doc_id]
        all_matches = doc_chunk_scores[doc_id]
        matched_count = len(all_matches)
        top_ids = [
            cid
            for cid, _ in sorted(
                all_matches.items(), key=lambda x: x[1], reverse=True
            )[:chunk_cap]
        ]
        if is_doc_scope:
            top_chunks = [
                chunks_by_id[cid] for cid in top_ids if cid in chunks_by_id
            ]
        elif is_cross_doc_resp:
            top_chunks = [
                chunks_by_id[cid] for cid in top_ids if cid in chunks_by_id
            ]
        else:
            top_chunks = sorted(
                (chunks_by_id[cid] for cid in top_ids if cid in chunks_by_id),
                key=lambda c: c["chunk_idx"],
            )
'''

PINNED_RELEVANCE = "relevance=round(min(1.0, doc_score[doc_id] / normalize), 4),"


def _norm(text: str) -> list[str]:
    out = []
    for line in text.strip("\n").split("\n"):
        # 줄 끝 주석도 떼어낸다 — 원본은 `all_matches = ...  # dict[...]` 처럼 붙어 있다.
        s = re.sub(r"\s+#.*$", "", line).strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def assert_pinned_unchanged() -> None:
    src = open(os.path.join(ROOT, "api", "app", "routers", "search.py"), encoding="utf-8").read()
    m = re.search(
        r"^    top_score = doc_score\[sorted_doc_ids\[0\]\].*?"
        r"^                key=lambda c: c\[\"chunk_idx\"\],\n^            \)$",
        src, re.S | re.M,
    )
    if m is None:
        raise SystemExit("7) 조립 블록을 search.py 에서 못 찾았다 — 채점기를 고쳐야 한다.")
    if _norm(m.group(0)) != _norm(PINNED_ASSEMBLE):
        print("원본 7) 조립 블록이 고정본과 다르다. 실제 소스:")
        print(m.group(0))
        raise SystemExit("채점기의 복사본을 원본에 맞춰 갱신할 것.")
    if PINNED_RELEVANCE not in src:
        raise SystemExit(f"relevance 식이 바뀌었다 — 기대: {PINNED_RELEVANCE}")


# ---------------------------------------------------------------- 1) 스니펫

_LONG = "가나다라마바사아자차카타파하" * 40  # 560자

SNIPPET_CASES: list[tuple[str, str]] = [
    ("", "세무"),
    ("본문", ""),
    ("", ""),
    ("세무 신고 안내", "세무"),
    ("세무 신고 안내", "없는말"),
    ("세무 신고와 세무 대리, 세무 조정", "세무"),          # 하이라이트 여러 개
    ("세무세무세무", "세무세무"),                          # 겹치는 매칭
    ("SEMU tax", "semu"),                                  # 대소문자
    ("Semu SEMU semu", "SEMU"),
    ("ıI İi", "i"),                                        # 터키어 점 있는/없는 I
    ("İstanbul", "istanbul"),                              # lower 가 길이를 바꾼다
    ("ẞ 대문자 에스체트", "ß"),
    ("Σ SIGMA ς σ", "σ"),                                  # 그리스어 최종 시그마
    (_LONG + "세무" + _LONG, "세무"),                       # 앞뒤 모두 잘림 → … …
    ("세무" + _LONG, "세무"),                               # 앞은 안 잘림
    (_LONG + "세무", "세무"),                               # 뒤는 안 잘림
    ("가" * 480 + "세무", "세무"),
    ("가" * 481 + "세무", "세무"),
    # 질의를 소문자화하면 **길어지는** 경우 — `İ`(U+0130) 는 1 자인데 소문자는 2 자다.
    # 원본은 하이라이트 길이로 소문자화 **이전** 길이를 쓴다.
    ("İstanbul İzmir", "İ"),
    ("İ 하나", "İ"),
    ("aİb", "İ"),
    # 본문 소문자화가 길이를 바꿔 자르는 위치가 밀리는 경우 (원본의 어긋남을 그대로 재현).
    ("İ" * 300 + "세무" + "가" * 300, "세무"),
    ("İ" * 10 + "세무", "세무"),
    ("𝑖𝜎 수식 세무 안내", "세무"),                          # astral — 인덱스가 밀리는 자리
    ("𝑖" * 300 + "세무" + "𝑖" * 300, "세무"),
    ("📌📌📌 목차 📌", "목차"),
    ("a𝑖b", "𝑖"),                                          # 질의 자체가 astral
    ("\n\n세무\n\n", "세무"),
    ("  세무  ", " 세무 "),
    ("세무", "세무 신고"),                                  # 질의가 본문보다 김
    ("여러\n줄\n세무\n본문", "세무"),
]

MARKER_CASES = [
    "본문입니다",
    "본문입니다\n\n[검색어: 세무 신고]",
    "본문입니다\n\n[검색어: 세무 신고]\n",
    "본문입니다\n\n[검색어: 세무 신고]   ",
    "본문입니다[검색어: 세무]",
    "본문입니다\n\n\n[검색어: 세무]",
    "본문입니다\n\n[검색어: ]",
    "본문입니다\n\n[검색어: 세무] 뒤에 글자",
    "[검색어: 세무]",
    "본문 [검색어: 세무] 가운데",
    "본문\n\n[검색어: 대괄호] 없음]",
    "",
    "[검색어:",
    "본문\n\n[검색어: 세무]",   # Python `\s` 만 잡는 문자
    "본문\n\n[검색어: 세무]　",   # 전각 공백
    "본문\n\n[검색어: 세무]﻿",   # JS `\s` 만 잡는 문자
]


def round_cases(seed: int) -> list[float]:
    vals: list[float] = []
    # 소수 4 자리에서 정확히 절반이 되는 값 전체 — 은행가 반올림이 갈리는 유일한 자리.
    vals += [m / 32 for m in range(1, 32, 2)]
    vals += [0.0, 1.0, 0.5, 0.25, 0.125, 1 / 3, 2 / 3, 1e-9, 0.99995, 0.00005]
    vals += [m / 3200 for m in range(1, 40)]
    rng = random.Random(seed)
    vals += [rng.random() for _ in range(20000)]
    vals += [rng.random() * 1e-4 for _ in range(2000)]
    return vals


# (이름, page_doc_ids, doc_score, doc_chunk_scores, chunks, cap, order)
ASSEMBLE_CASES: list[dict] = [
    {
        "name": "목록 모드 — chunk_idx 오름차순, cap 3",
        "page_doc_ids": ["d1"],
        "doc_score": {"d1": 0.03, "d2": 0.02},
        "doc_chunk_scores": {"d1": {"c3": 0.03, "c1": 0.02, "c2": 0.025, "c4": 0.001}},
        "chunks": {
            "c1": {"id": "c1", "chunk_idx": 1, "text": "세무 하나"},
            "c2": {"id": "c2", "chunk_idx": 2, "text": "세무 둘"},
            "c3": {"id": "c3", "chunk_idx": 3, "text": "세무 셋"},
            "c4": {"id": "c4", "chunk_idx": 4, "text": "세무 넷"},
        },
        "cap": 3, "order": "chunk_idx", "top_score": 0.03,
    },
    {
        "name": "doc 스코프 — 점수 내림차순",
        "page_doc_ids": ["d1"],
        "doc_score": {"d1": 0.03},
        "doc_chunk_scores": {"d1": {"c3": 0.03, "c1": 0.02, "c2": 0.025}},
        "chunks": {
            "c1": {"id": "c1", "chunk_idx": 1, "text": "세무 하나"},
            "c2": {"id": "c2", "chunk_idx": 2, "text": "세무 둘"},
            "c3": {"id": "c3", "chunk_idx": 3, "text": "세무 셋"},
        },
        "cap": 200, "order": "score", "top_score": 0.03,
    },
    {
        "name": "cap 이 순서보다 먼저 적용된다 (점수 상위 2개를 뽑은 뒤 idx 정렬)",
        "page_doc_ids": ["d1"],
        "doc_score": {"d1": 0.05},
        "doc_chunk_scores": {"d1": {"c1": 0.01, "c2": 0.05, "c3": 0.04}},
        "chunks": {
            "c1": {"id": "c1", "chunk_idx": 1, "text": "세무 하나"},
            "c2": {"id": "c2", "chunk_idx": 9, "text": "세무 둘"},
            "c3": {"id": "c3", "chunk_idx": 5, "text": "세무 셋"},
        },
        "cap": 2, "order": "chunk_idx", "top_score": 0.05,
    },
    {
        "name": "청크 점수 동점 — 입력 순서 유지",
        "page_doc_ids": ["d1"],
        "doc_score": {"d1": 0.02},
        "doc_chunk_scores": {"d1": {"z1": 0.02, "a2": 0.02, "m3": 0.02}},
        "chunks": {
            "z1": {"id": "z1", "chunk_idx": 3, "text": "가"},
            "a2": {"id": "a2", "chunk_idx": 2, "text": "나"},
            "m3": {"id": "m3", "chunk_idx": 1, "text": "다"},
        },
        "cap": 2, "order": "score", "top_score": 0.02,
    },
    {
        "name": "본문이 없는 청크는 조용히 빠진다",
        "page_doc_ids": ["d1"],
        "doc_score": {"d1": 0.03},
        "doc_chunk_scores": {"d1": {"c1": 0.03, "없음": 0.02}},
        "chunks": {"c1": {"id": "c1", "chunk_idx": 1, "text": "세무"}},
        "cap": 3, "order": "chunk_idx", "top_score": 0.03,
    },
    {
        "name": "relevance 정규화 — 1위 1.0, 나머지는 비율",
        "page_doc_ids": ["d1", "d2", "d3"],
        "doc_score": {"d1": 0.04, "d2": 0.01, "d3": 0.013},
        "doc_chunk_scores": {"d1": {"c1": 0.04}, "d2": {"c2": 0.01}, "d3": {"c3": 0.013}},
        "chunks": {
            "c1": {"id": "c1", "chunk_idx": 0, "text": "가"},
            "c2": {"id": "c2", "chunk_idx": 0, "text": "나"},
            "c3": {"id": "c3", "chunk_idx": 0, "text": "다"},
        },
        "cap": 3, "order": "chunk_idx", "top_score": 0.04,
    },
    {
        "name": "1위 점수가 0 이면 분모 1",
        "page_doc_ids": ["d1", "d2"],
        "doc_score": {"d1": 0.0, "d2": 0.0},
        "doc_chunk_scores": {"d1": {"c1": 0.0}, "d2": {"c2": 0.0}},
        "chunks": {
            "c1": {"id": "c1", "chunk_idx": 0, "text": "가"},
            "c2": {"id": "c2", "chunk_idx": 0, "text": "나"},
        },
        "cap": 3, "order": "chunk_idx", "top_score": 0.0,
    },
    {
        "name": "메타 필드 결측 · 빈 metadata 는 null",
        "page_doc_ids": ["d1"],
        "doc_score": {"d1": 0.02},
        "doc_chunk_scores": {"d1": {"c1": 0.02, "c2": 0.01}},
        "chunks": {
            "c1": {"id": "c1", "chunk_idx": 0, "text": "세무", "metadata": {}},
            "c2": {"id": "c2", "chunk_idx": 1, "text": "세무", "metadata": {"a": 1}},
        },
        "cap": 3, "order": "chunk_idx", "top_score": 0.02,
        "doc_meta": {"d1": {}},
    },
    {
        "name": "동의어 마커가 스니펫에 안 보인다",
        "page_doc_ids": ["d1"],
        "doc_score": {"d1": 0.02},
        "doc_chunk_scores": {"d1": {"c1": 0.02}},
        "chunks": {"c1": {"id": "c1", "chunk_idx": 0, "text": "세무 안내\n\n[검색어: 택스 tax]"}},
        "cap": 3, "order": "chunk_idx", "top_score": 0.02,
    },
]

RUNNER_TS = f"""
import {{ makeSnippetWithHighlights, stripSynonymMarker }} from "file://{SEARCH_DIR}/snippet.ts";
import {{ buildItems, pyRound, type ChunkRow }} from "file://{SEARCH_DIR}/assemble.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));

const snippets = input.snippets.map(([t, q]: [string, string]) => {{
  const r = makeSnippetWithHighlights(t, q);
  return [r.text, r.highlights];
}});

const markers = input.markers.map((t: string) => stripSynonymMarker(t));
const rounds = input.rounds.map((v: number) => pyRound(v, 4));

// deno-lint-ignore no-explicit-any
const assembled = input.assemble.map((c: any) => {{
  const docChunkScores = new Map<string, Map<string, number>>(
    Object.entries(c.doc_chunk_scores).map((
      [d, m]: [string, unknown],
    ) => [d, new Map(Object.entries(m as Record<string, number>))]),
  );
  const chunkRrf = new Map<string, number>();
  for (const m of docChunkScores.values()) for (const [k, v] of m) chunkRrf.set(k, v);
  return buildItems({{
    pageDocIds: c.page_doc_ids,
    docsMeta: new Map(Object.entries(c.doc_meta ?? {{}})),
    docScore: new Map(Object.entries(c.doc_score as Record<string, number>)),
    docChunkScores,
    chunksById: new Map(Object.entries(c.chunks as Record<string, ChunkRow>)),
    chunkRrf,
    cleanQ: c.clean_q ?? "세무",
    chunkCap: c.cap,
    order: c.order,
    topScore: c.top_score,
  }});
}});

console.log(JSON.stringify({{ snippets, markers, rounds, assembled }}));
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


def py_assemble(c: dict) -> list[dict]:
    """`search.py` 7) 단계의 복사본 (위에서 고정본과 대조했다)."""
    from app.routers.search import _make_snippet_with_highlights
    from app.services.synonym_inject import strip_synonym_marker

    doc_score = c["doc_score"]
    chunks_by_id = c["chunks"]
    chunk_cap = c["cap"]
    doc_meta_all = c.get("doc_meta") or {}
    clean_q = c.get("clean_q", "세무")
    top_score = c["top_score"] if c["top_score"] is not None else 1.0
    normalize = top_score if top_score > 0 else 1.0

    chunk_rrf: dict[str, float] = {}
    for m in c["doc_chunk_scores"].values():
        chunk_rrf.update(m)

    items = []
    for doc_id in c["page_doc_ids"]:
        meta = doc_meta_all.get(doc_id, {})
        all_matches = c["doc_chunk_scores"][doc_id]
        matched_count = len(all_matches)
        top_ids = [
            cid for cid, _ in sorted(
                all_matches.items(), key=lambda x: x[1], reverse=True
            )[:chunk_cap]
        ]
        if c["order"] == "score":
            top_chunks = [chunks_by_id[cid] for cid in top_ids if cid in chunks_by_id]
        else:
            top_chunks = sorted(
                (chunks_by_id[cid] for cid in top_ids if cid in chunks_by_id),
                key=lambda ch: ch["chunk_idx"],
            )

        matched_chunks = []
        for ch in top_chunks:
            snippet, highlights = _make_snippet_with_highlights(
                strip_synonym_marker(ch.get("text") or ""), clean_q
            )
            chunk_meta = ch.get("metadata") or None
            matched_chunks.append({
                "chunk_id": ch["id"],
                "chunk_idx": ch["chunk_idx"],
                "text": snippet,
                "page": ch.get("page"),
                "section_title": ch.get("section_title"),
                "highlight": highlights,
                "rrf_score": chunk_rrf.get(ch["id"]),
                "metadata": chunk_meta if chunk_meta else None,
            })

        items.append({
            "doc_id": doc_id,
            "doc_title": meta.get("title") or "",
            "doc_type": meta.get("doc_type") or "",
            "tags": meta.get("tags") or [],
            "summary": meta.get("summary"),
            "created_at": meta.get("created_at") or "",
            "relevance": round(min(1.0, doc_score[doc_id] / normalize), 4),
            "matched_chunk_count": matched_count,
            "matched_chunks": matched_chunks,
        })
    return items


def live_snippet_cases() -> list[tuple[str, str]]:
    """운영 청크 — astral 문자가 든 것 전량 + 나머지 표본."""
    import os as _os

    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(os.path.join(ROOT, ".env"))
    c = create_client(_os.environ["SUPABASE_URL"], _os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    astral: list[str] = []
    sample: list[str] = []
    page = 0
    while True:
        r = c.table("chunks").select("text").order("id").range(page * 1000, page * 1000 + 999)
        res = r.execute()
        if not res.data:
            break
        for row in res.data:
            t = row.get("text") or ""
            if any(ord(ch) > 0xFFFF for ch in t):
                astral.append(t)
            elif len(sample) < 400:
                sample.append(t)
        page += 1

    queries = ["세무", "목차", "매출", "the", "a", "계약", "이", "The", "SEMU", "𝑖"]
    out: list[tuple[str, str]] = []
    for t in astral:
        for q in queries:
            out.append((t, q))
    rng = random.Random(20260905)
    for t in sample:
        out.append((t, rng.choice(queries)))
    return out


def main() -> None:
    assert_pinned_unchanged()

    from app.routers.search import _make_snippet_with_highlights
    from app.services.synonym_inject import strip_synonym_marker

    snippet_cases = list(SNIPPET_CASES)
    live_n = 0
    if "--live" in sys.argv:
        live = live_snippet_cases()
        live_n = len(live)
        snippet_cases += live

    rounds = round_cases(20260905)
    ts = run_deno({
        "snippets": [list(x) for x in snippet_cases],
        "markers": MARKER_CASES,
        "rounds": rounds,
        "assemble": ASSEMBLE_CASES,
    })

    fails = 0

    print("=== 스니펫 + 하이라이트 ===")
    for (t, q), tv in zip(snippet_cases, ts["snippets"]):
        ps, ph = _make_snippet_with_highlights(t, q)
        if ps != tv[0] or ph != [list(x) for x in tv[1]]:
            fails += 1
            if fails <= 12:
                print(f"  MISMATCH text={t[:36]!r} q={q!r}")
                print(f"      py={ps[:70]!r} {ph}")
                print(f"      ts={tv[0][:70]!r} {tv[1]}")
    print(f"  {len(snippet_cases)}건 대조 (합성 {len(SNIPPET_CASES)} + live {live_n})")

    print()
    print("=== 동의어 마커 제거 ===")
    for t, tv in zip(MARKER_CASES, ts["markers"]):
        pv = strip_synonym_marker(t)
        if pv != tv:
            fails += 1
            print(f"  MISMATCH {t!r}: py={pv!r} ts={tv!r}")
    print(f"  {len(MARKER_CASES)}건 대조")

    print()
    print("=== round(x, 4) — 은행가 반올림 ===")
    tie_fail = 0
    for v, tv in zip(rounds, ts["rounds"]):
        pv = round(v, 4)
        if pv != tv:
            fails += 1
            if tie_fail < 10:
                print(f"  MISMATCH {v!r}: py={pv!r} ts={tv!r}")
            tie_fail += 1
    print(f"  {len(rounds)}건 대조 (정확히 절반인 값 16건 포함)")

    print()
    print("=== 응답 조립 ===")
    for c, tv in zip(ASSEMBLE_CASES, ts["assembled"]):
        pv = py_assemble(c)
        # 문자열이 아니라 **파싱된 값**으로 비교한다. JSON 바이트는 `1.0`(Python) 과
        # `1`(JS) 로 다르지만 어떤 파서로 읽어도 같은 수다. 아래 주석 참고.
        if pv != tv:
            fails += 1
            print(f"  MISMATCH {c['name']}")
            print(f"      py={json.dumps(pv, ensure_ascii=False)[:260]}")
            print(f"      ts={json.dumps(tv, ensure_ascii=False)[:260]}")
        else:
            print(f"  OK   {c['name']}")
    print(f"  {len(ASSEMBLE_CASES)}건 대조")

    total = len(snippet_cases) + len(MARKER_CASES) + len(rounds) + len(ASSEMBLE_CASES)
    print()
    print(f"케이스 {total}건 대조")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
