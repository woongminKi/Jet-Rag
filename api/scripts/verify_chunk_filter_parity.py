"""`chunk_filter.ts` 를 `ingest/stages/chunk_filter.py` 와 대조.

## 노린 함정
- `re.compile(r"[\\d\\W_]", re.UNICODE)` — JS 로 그대로 옮기면 **ASCII 만** 잡는다.
  Python 은 `\\d`=유니코드 Nd, `\\w`=글자·숫자·밑줄이라 뜻이 "글자가 아닌 것" 이다.
  한글·한자·아랍숫자·전각숫자·이모지가 전부 갈리는 자리다.
- `str.isspace()` 로 세는 `non_ws_total` — JS `\\s` 는 U+0085 를 공백으로 안 본다.
- `len()` 은 코드포인트다 — 이모지가 든 청크에서 `.length` 로 재면 두 배가 된다.
- **판정 순서**: empty → extreme_short → header_footer → table_noise.
  `header_footer` 가 `table_noise` 보다 먼저다.
- 임계 경계: 20 / 50 / 100 자, 비율 0.90 / 0.70, 반복 3 회.

사용:
    api/.venv/bin/python api/scripts/verify_chunk_filter_parity.py
    api/.venv/bin/python api/scripts/verify_chunk_filter_parity.py --negative
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

# ── 표 노이즈 후보: 짧은 줄이 많고 숫자·기호 비율이 높은 텍스트
TABLE = "\n".join(["1,200", "3,400", "5,600", "7,800", "9,000", "1,100", "2,200"])
# ── 같은 모양인데 한글이 섞여 비율이 떨어지는 것
TABLE_KO = "\n".join(["항목 1,200", "항목 3,400", "항목 5,600", "항목 7,800", "항목 9,000"])
LONG_KO = "이것은 충분히 긴 한국어 문장입니다. " * 4

# 단일 청크 판정 케이스 (텍스트만) — header_footer 는 아래 문서 단위에서 본다.
TEXT_CASES = [
    "", "   ", "\n\n", "\t",
    "2", "2,800", "2,800원", "변제충당",
    "가" * 19, "가" * 20,
    "1" * 19, "1" * 20, "1" * 21,
    "١٢٣٤٥٦٧٨٩",              # 아랍-인도 숫자 — Python \d 에 걸린다
    "１２３４５６７８９",         # 전각 숫자
    "漢字漢字漢字",              # 한자 = 글자
    "😀😀😀",                   # 이모지 = 글자 아님
    "😀가나다라마바사아자차카타파하",  # 코드포인트 15, UTF-16 16
    "_" * 25,
    TABLE, TABLE_KO, LONG_KO,
    TABLE + "\n" + LONG_KO,
    # 50자 경계
    "가" * 49, "가" * 50, "가" * 51,
    # non_ws_total 에 U+0085 / U+00A0 가 섞인 경우
    "1,2\x853,4\x855,6\x857,8\x859,0\x851,1\x852,2\x853,3\x854,4\x855,5",
    "1,2\xa03,4\xa05,6\xa07,8\xa09,0\xa01,1\xa02,2\xa03,3\xa04,4\xa05,5",
    # 줄 하나가 정확히 30자
    "\n".join(["가" * 29, "가" * 30, "가" * 31]),
]

# 문서 단위 케이스 — (청크 텍스트 목록, 설명)
DOC_CASES = [
    ([], "빈 문서"),
    (["머리말"] * 2 + [LONG_KO], "반복 2회 — 임계 미만"),
    (["머리말"] * 3 + [LONG_KO], "반복 3회 — 마킹"),
    (["머리말 "] * 3 + [" 머리말"] + [LONG_KO], "strip 후 같은 텍스트"),
    ([TABLE] * 3 + [LONG_KO], "표가 3회 반복 — header_footer 가 table_noise 를 이긴다"),
    (["가" * 100] * 3 + [LONG_KO], "100자 = 임계 밖이라 header_footer 아님"),
    (["가" * 99] * 3 + [LONG_KO], "99자 = 임계 안"),
    ([LONG_KO] * 5, "전부 통과"),
    ([TABLE, TABLE_KO, "", "2,800", LONG_KO], "혼합"),
]

RUNNER_TS = """
import {
  classifyChunk, detectHeaderFooterTexts, hasMeaningfulLetter,
  lineMetrics, runChunkFilterStage,
} from "file://%(shared)s/ingest/chunk_filter.ts";
import type { ChunkRecord } from "file://%(shared)s/ingest/chunk_records.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const NEG = cfg.negative === true;

const rec = (text: string, i = 0): ChunkRecord => ({
  doc_id: "d1", chunk_idx: i, text, page: null, section_title: null,
  bbox: null, char_range: [0, text.length], metadata: {},
});

const out: Record<string, unknown> = {};
out.metrics = cfg.textCases.map((t: string) => lineMetrics(t));
out.meaningful = cfg.textCases.map((t: string) => hasMeaningfulLetter(t));
// 단일 청크 판정 — header_footer 집합은 비워 둔다.
out.classify = cfg.textCases.map((t: string) => classifyChunk(rec(t), new Set<string>()));

out.docs = cfg.docCases.map((texts: string[]) => {
  const chunks = texts.map((t, i) => rec(t, i));
  const hf = [...detectHeaderFooterTexts(chunks)].sort();
  const r = runChunkFilterStage(chunks);
  return {
    headerFooter: hf,
    reasons: r.chunks.map((c) =>
      ((c as { flags?: Record<string, unknown> }).flags?.["filtered_reason"] ?? null)),
    counts: r.counts,
    ratio: r.filterRatio,
  };
});

if (NEG) (out.classify as (string | null)[])[0] = "table_noise";
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}


def main() -> None:
    from app.adapters.vectorstore import ChunkRecord
    from app.ingest.stages import chunk_filter as CF

    negative = "--negative" in sys.argv

    def rec(text: str, i: int = 0) -> ChunkRecord:
        return ChunkRecord(doc_id="d1", chunk_idx=i, text=text)

    py_metrics = [list(CF._line_metrics(t)) for t in TEXT_CASES]
    py_meaningful = [CF._has_meaningful_letter(t) for t in TEXT_CASES]
    py_classify = [CF._classify_chunk(rec(t), set()) for t in TEXT_CASES]

    py_docs = []
    for texts, _desc in DOC_CASES:
        chunks = [rec(t, i) for i, t in enumerate(texts)]
        hf = sorted(CF._detect_header_footer_texts(chunks))
        # `run_chunk_filter_stage` 는 `stage()` 로 DB 를 건드린다 — 내부 로직만 그대로 재현.
        from collections import Counter
        counts: Counter = Counter()
        out_chunks = []
        import dataclasses
        for c in chunks:
            reason = CF._classify_chunk(c, set(hf))
            if reason is None:
                out_chunks.append(c)
                continue
            nf = dict(c.flags)
            nf["filtered_reason"] = reason
            out_chunks.append(dataclasses.replace(c, flags=nf))
            counts[reason] += 1
        total = len(chunks)
        py_docs.append({
            "headerFooter": hf,
            "reasons": [c.flags.get("filtered_reason") for c in out_chunks],
            "counts": {k: counts[k] for k in
                       ("table_noise", "header_footer", "empty", "extreme_short")},
            "ratio": (sum(counts.values()) / total) if total else 0.0,
        })

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "out.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({
                "textCases": TEXT_CASES,
                "docCases": [t for t, _ in DOC_CASES],
                "negative": negative,
            }, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf, of],
            capture_output=True, text=True, timeout=600,
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

    for t, a, b in zip(TEXT_CASES, py_metrics, ts["metrics"]):
        cmp(f"line_metrics({t[:16]!r}…)", a, b)
    for t, a, b in zip(TEXT_CASES, py_meaningful, ts["meaningful"]):
        cmp(f"has_meaningful_letter({t[:16]!r}…)", a, b)
    for t, a, b in zip(TEXT_CASES, py_classify, ts["classify"]):
        cmp(f"classify({t[:16]!r}… len={len(t)})", a, b)
    for (texts, desc), a, b in zip(DOC_CASES, py_docs, ts["docs"]):
        cmp(f"[{desc}] header_footer", a["headerFooter"], b["headerFooter"])
        cmp(f"[{desc}] reasons", a["reasons"], b["reasons"])
        cmp(f"[{desc}] counts", a["counts"], b["counts"])
        cmp(f"[{desc}] ratio", round(a["ratio"], 12), round(b["ratio"], 12))

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
