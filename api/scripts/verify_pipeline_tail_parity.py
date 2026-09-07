"""`tag_summarize` · `doc_embed` · `dedup` 의 순수 로직을 원본과 대조.

LLM·임베딩 호출은 빼고 **그 앞뒤의 판단**을 본다 — 무엇을 잘라 보내고, 응답을 어떻게
읽고, 무엇을 문서에 쓰고, 어느 tier 로 판정하는지.

## 노린 함정
- `raw_text[:3000]` / `[:12000]` 은 **코드포인트** 슬라이스다. 이모지에서 갈린다.
- `_parse_json` 의 코드블록 벗기기 — `split("```", 2)[1]` 은 **가운데** 조각이다.
- `list(x)` 는 문자열이면 **글자 단위**로 쪼갠다. LLM 이 `topic_tags: "보고서"` 를 주면
  원본은 `['보','고','서']` 를 저장한다.
- `dict.fromkeys` 는 순서를 지키며 중복을 없앤다. JS `Set` 은 객체도 받지만 Python 은
  unhashable 로 죽는다.
- `summary_3line` 은 `is not None` 검사라 **빈 문자열도 저장**된다(`or` 가 아니다).
- `round(sim, 4)` 는 은행가 반올림이다.
- `SequenceMatcher` 는 `pydifflib.ts` 가 따로 대조한다 — 여기서는 tier 판정만 본다.

사용:
    api/.venv/bin/python api/scripts/verify_pipeline_tail_parity.py
    api/.venv/bin/python api/scripts/verify_pipeline_tail_parity.py --negative
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
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

# ── _parse_json 케이스
JSON_CASES = [
    '{"a": 1}',
    '  {"a": 1}  ',
    '```json\n{"a": 1}\n```',
    '```\n{"a": 1}\n```',
    '```json{"a": 1}```',
    '```JSON\n{"a": 1}\n```',          # 대문자 — `json` 접두사 제거 안 됨
    '```json\n{"a": [1, 2], "b": null}\n```',
    '```\n\n{"a": 1}\n\n```\n',
    '{"한글": "값", "emoji": "😀"}',
    '[1, 2, 3]',                        # 객체가 아님
    '"문자열"',
    '123',
]

# ── 슬라이스 케이스 (코드포인트)
SLICE_CASES = [
    "", "짧은 글", "😀" * 10, "가" * 5000, "😀" * 2000 + "끝",
    "a" * 3000 + "b" * 100,
]

# ── buildPatch 케이스 (tags, summary)
PATCH_CASES = [
    (None, None),
    ({"topic_tags": ["가", "나"], "entity_tags": ["다"], "document_type": "보고서",
      "time_reference": "2026-01"}, None),
    ({"topic_tags": ["가", "나", "가"], "entity_tags": ["나", "라"]}, None),
    ({"topic_tags": [], "entity_tags": []}, None),
    ({"topic_tags": None, "entity_tags": None, "document_type": None,
      "time_reference": None}, None),
    ({"topic_tags": "보고서"}, None),                    # 문자열 → 글자 분해
    ({"topic_tags": ["가"], "document_type": ""}, None),  # 빈 문자열은 falsy
    ({}, None),
    (None, {"summary_3line": "1줄\n2줄\n3줄", "implications": "의미"}),
    (None, {"summary_3line": "", "implications": ""}),    # 빈 문자열도 저장
    (None, {"summary_3line": None, "implications": None}),
    (None, {}),
    ({"topic_tags": ["x"], "document_type": "메모"},
     {"summary_3line": "요약", "implications": "함의"}),
]
EXISTING_FLAGS = {"scan": True, "document_type": "이전값"}

# ── pick_source 케이스
PICK_CASES = [
    (None, None, "본문입니다"),
    ("요약", None, "본문"),
    ("요약", "함의", "본문"),
    ("  ", "함의", "본문"),          # 공백뿐 → raw 로 떨어진다
    ("요약", "  ", "본문"),          # implications 공백 → 요약만
    (None, None, "  "),
    (None, None, ""),
    (None, None, "가" * 4000),
    (None, None, "😀" * 4000),
    ("", "", "본문"),
    ("요약\n", "\n함의\n", "본문"),
]

# ── dedup 판정 케이스 (top_sim, fname_sim)
TIER_CASES = [
    (0.999, 0.9), (0.95, 0.0), (0.9499999, 0.9), (0.85, 0.6), (0.85, 0.5999),
    (0.8499999, 0.9), (0.0, 1.0), (0.123456789, 0.987654321),
    (0.95005, 0.65), (0.855555, 0.605555),
]

RUNNER_TS = """
import { buildPatch, parseJson } from "file://%(shared)s/ingest/tag_summarize.ts";
import { pickSource } from "file://%(shared)s/ingest/doc_embed.ts";
import { cosine, filenameSimilarity, parseVec } from "file://%(shared)s/ingest/dedup.ts";
import { pyRound } from "file://%(shared)s/pynum.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const NEG = cfg.negative === true;

function cpSlice(s: string, end: number): string {
  let out = ""; let n = 0;
  for (const ch of s) { if (n >= end) break; out += ch; n++; }
  return out;
}

const safe = <T>(fn: () => T): T | string => {
  try { return fn(); } catch (e) { return `ERR:${(e as Error).constructor.name}`; }
};

const out: Record<string, unknown> = {};
out.parsed = cfg.jsonCases.map((t: string) => safe(() => parseJson(t)));
out.slices = cfg.sliceCases.flatMap((t: string) => [cpSlice(t, 3000), cpSlice(t, 12000)]);
out.patches = cfg.patchCases.map(([tags, summary]: [unknown, unknown]) =>
  safe(() => buildPatch(tags, summary, cfg.existingFlags)));
out.sources = cfg.pickCases.map(([s, i, r]: [string | null, string | null, string]) =>
  pickSource({ summary: s, implications: i, rawText: r }));
out.cosines = cfg.vecPairs.map(([a, b]: [number[], number[]]) => cosine(a, b));
out.vecs = cfg.vecRaws.map((r: unknown) => safe(() => parseVec(r)));
out.fnames = cfg.namePairs.map(([a, b]: [string, string]) => filenameSimilarity(a, b));
out.tiers = cfg.tierCases.map(([sim, fn]: [number, number]) => {
  if (sim >= 0.95) {
    return { duplicate_tier: 2, duplicate_of: "other", duplicate_similarity: pyRound(sim, 4) };
  }
  if (sim >= 0.85 && fn >= 0.6) {
    return {
      duplicate_tier: 3, previous_version_of: "other",
      duplicate_similarity: pyRound(sim, 4), filename_similarity: pyRound(fn, 4),
    };
  }
  return null;
});

if (NEG) (out.sources as (string | null)[])[1] = "변조";
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}


def main() -> None:
    negative = "--negative" in sys.argv
    from app.ingest.stages import dedup as DD
    from app.ingest.stages import doc_embed as DE
    from app.ingest.stages import tag_summarize as TS

    rnd = random.Random(20260907)
    vec_pairs = []
    for _ in range(30):
        n = rnd.choice([0, 1, 8, 1024])
        a = [rnd.uniform(-1, 1) for _ in range(n)]
        b = [rnd.uniform(-1, 1) for _ in range(n)]
        vec_pairs.append((a, b))
    vec_pairs.append(([1.0, 0.0], [1.0, 0.0]))
    vec_pairs.append(([0.0, 0.0], [1.0, 1.0]))
    vec_pairs.append(([1.0, 2.0], [1.0, 2.0, 3.0]))   # 길이 다름
    vec_pairs.append(([], []))

    vec_raws = [[1.0, 2.0], "[1.0, 2.0]", "[1, 2]", 3, None, {"a": 1}, '"x"']
    name_pairs = [
        ("report_v1.pdf", "report_v2.pdf"),
        ("보고서.pdf", "보고서(최종).pdf"),
        ("", "x"), ("x", ""), ("", ""),
        ("A" * 300, "a" * 300),
        ("😀 회의.pdf", "😀 회의 2.pdf"),
    ]

    py_parsed = []
    for t in JSON_CASES:
        try:
            py_parsed.append(TS._parse_json(t))
        except Exception as e:  # noqa: BLE001
            py_parsed.append(f"ERR:{type(e).__name__}")

    py_slices = []
    for t in SLICE_CASES:
        py_slices.append(t[:3000])
        py_slices.append(t[:12000])

    # `_persist` 의 patch 조립만 재현 (DB 호출 제외).
    def build_patch(tags, summary, existing):
        patch = {}
        if tags is not None:
            topic = list(tags.get("topic_tags") or [])
            entity = list(tags.get("entity_tags") or [])
            patch["tags"] = list(dict.fromkeys([*topic, *entity]))
            flags_patch = {}
            if tags.get("document_type"):
                flags_patch["document_type"] = tags["document_type"]
            if tags.get("time_reference"):
                flags_patch["time_reference"] = tags["time_reference"]
            if flags_patch:
                merged = dict(existing)
                merged.update(flags_patch)
                patch["flags"] = merged
        if summary is not None:
            if summary.get("summary_3line") is not None:
                patch["summary"] = summary["summary_3line"]
            if summary.get("implications") is not None:
                patch["implications"] = summary["implications"]
        return patch

    py_patches = []
    for tags, summary in PATCH_CASES:
        try:
            py_patches.append(build_patch(tags, summary, EXISTING_FLAGS))
        except Exception as e:  # noqa: BLE001
            py_patches.append(f"ERR:{type(e).__name__}")

    py_sources = [
        DE._pick_source(summary=s, implications=i, raw_text=r)
        for s, i, r in PICK_CASES
    ]
    py_cosines = [DD._cosine(a, b) for a, b in vec_pairs]
    py_vecs = []
    for r in vec_raws:
        try:
            py_vecs.append(DD._parse_vec(r))
        except Exception as e:  # noqa: BLE001
            py_vecs.append(f"ERR:{type(e).__name__}")
    py_fnames = [DD._filename_similarity(a, b) for a, b in name_pairs]

    py_tiers = []
    for sim, fn in TIER_CASES:
        if sim >= DD._TIER2_THRESHOLD:
            py_tiers.append({"duplicate_tier": 2, "duplicate_of": "other",
                             "duplicate_similarity": round(sim, 4)})
        elif sim >= DD._TIER3_SIM_THRESHOLD and fn >= DD._TIER3_FILENAME_THRESHOLD:
            py_tiers.append({"duplicate_tier": 3, "previous_version_of": "other",
                             "duplicate_similarity": round(sim, 4),
                             "filename_similarity": round(fn, 4)})
        else:
            py_tiers.append(None)

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "out.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({
                "jsonCases": JSON_CASES, "sliceCases": SLICE_CASES,
                "patchCases": [list(p) for p in PATCH_CASES],
                "existingFlags": EXISTING_FLAGS,
                "pickCases": [list(p) for p in PICK_CASES],
                "vecPairs": [list(p) for p in vec_pairs],
                "vecRaws": vec_raws, "namePairs": [list(p) for p in name_pairs],
                "tierCases": [list(p) for p in TIER_CASES],
                "negative": negative,
            }, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf, of],
            capture_output=True, text=True, timeout=900,
        )
        if proc.returncode != 0:
            raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
        with open(of, encoding="utf-8") as f:
            ts = json.load(f)

    fails: list[str] = []
    checks = 0

    def cmp(label, a, b):
        nonlocal checks
        checks += 1
        if a != b:
            fails.append(f"{label}\n      py={a!r}\n      ts={b!r}")

    # JSON 파싱 실패의 **예외 이름**은 언어마다 다르다(JSONDecodeError vs SyntaxError).
    # 둘 다 던진다는 사실이 계약이고, 호출자는 예외 종류를 안 가린다.
    json_err = {"ERR:SyntaxError": "ERR:JSONDecodeError"}
    for t, a, b in zip(JSON_CASES, py_parsed, ts["parsed"]):
        cmp(f"parse_json({t[:20]!r})", a, json_err.get(b, b) if isinstance(b, str) else b)
    for i, (a, b) in enumerate(zip(py_slices, ts["slices"])):
        cmp(f"slice[{i}] len", len(a), len(b))
        cmp(f"slice[{i}]", a, b)
    for (tg, sm), a, b in zip(PATCH_CASES, py_patches, ts["patches"]):
        cmp(f"patch({tg}, {sm})", a, b)
    for c, a, b in zip(PICK_CASES, py_sources, ts["sources"]):
        cmp(f"pick_source({c[0]!r},{c[1]!r},{c[2][:12]!r})", a, b)
    for i, (a, b) in enumerate(zip(py_cosines, ts["cosines"])):
        cmp(f"cosine[{i}]", a, b)
    # JS 에는 ValueError 가 없다 — `RangeError` 를 그 자리로 쓴다(값이 변환 불가).
    err_map = {"ERR:RangeError": "ERR:ValueError"}
    for r, a, b in zip(vec_raws, py_vecs, ts["vecs"]):
        cmp(f"parse_vec({r!r})", a, err_map.get(b, b) if isinstance(b, str) else b)
    for (x, y), a, b in zip(name_pairs, py_fnames, ts["fnames"]):
        cmp(f"filename_similarity({x[:16]!r},{y[:16]!r})", a, b)
    for c, a, b in zip(TIER_CASES, py_tiers, ts["tiers"]):
        cmp(f"tier{c}", a, b)

    for f in fails[:12]:
        print(f"  **{f}**")
    print(f"  비교 {checks}건, 불일치 {len(fails)}건")
    if negative:
        print("음성 대조: " + ("검사기 정상 (변조 검출)" if fails else "**검사기가 못 잡는다**"))
        sys.exit(0 if fails else 1)
    print("FAIL 0" if not fails else f"FAIL {len(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "api"))
    main()
