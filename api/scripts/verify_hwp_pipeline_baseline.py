"""HWP 인제스트 파이프라인(extract → chunk)을 **운영 기준선과 직접 대조**.

## 이게 이번 이식의 최종 관문이다
지금까지는 함수 단위로 Python↔TS 를 맞췄다. 그건 "내가 고른 입력에서 같다" 는 증거일
뿐이다. 여기서는 **실제 HWP 파일 하나를 통째로** 두 구현에 넣고, Python 이 운영 DB 에
실제로 만들어 넣은 청크 11 개의 `text_sha16` 과 대조한다.

기준선: `fixtures/ingest_baselines/law_sample1.hwp.json`
(`ingest_baseline.py` 가 샌드박스 인제스트로 떴다. `_sha` = sha256 앞 16 자.)

## 대조 범위
`chunk` 단계까지다. `flag_keys` 는 `chunk_filter` 산물이라 제외한다(아직 미이식).
`meta_keys`·`text_len`·`page`·`has_section_title`·`has_char_range` 는 전부 대조한다.

사용:
    api/.venv/bin/python api/scripts/verify_hwp_pipeline_baseline.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")
BASELINE = os.path.join(HERE, "fixtures", "ingest_baselines", "law_sample1.hwp.json")
ASSET = os.path.join(ROOT, "assets", "public", "law_sample1.hwp")

RUNNER_TS = f"""
import {{ extractHwp }} from "file://{SHARED}/ingest/hwp_extract.ts";
import {{ runChunkStage }} from "file://{SHARED}/ingest/chunk_records.ts";

const bytes = await Deno.readFile(Deno.args[0]);
const extraction = await extractHwp(bytes);
const records = runChunkStage({{
  docId: "DOC",
  sections: extraction.sections,
  // 기준선을 뜬 로컬 환경에는 두 ENV 가 없었다 → 코드 기본값 false.
  env: {{ captionPrefixEnabled: false, synonymInjectionEnabled: false, synonymLlmEnabled: false }},
}});

const enc = new TextEncoder();
const rows = [];
for (const r of records) {{
  const h = await crypto.subtle.digest("SHA-256", enc.encode(r.text));
  const hex = [...new Uint8Array(h)].map((b) => b.toString(16).padStart(2, "0")).join("");
  rows.push({{
    chunk_idx: r.chunk_idx,
    page: r.page,
    has_section_title: Boolean(r.section_title),
    text_sha16: hex.slice(0, 16),
    text_len: [...r.text].length,          // Python len() = 코드포인트
    meta_keys: Object.keys(r.metadata).sort(),
    has_bbox: r.bbox !== null,
    has_char_range: r.char_range !== null && r.char_range !== undefined,
  }});
}}
console.log(JSON.stringify({{
  section_count: extraction.sections.length,
  raw_text: extraction.raw_text,
  rows,
}}));
"""


def sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def run_deno(timeout: int = 600) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        rf = os.path.join(tmp, "runner.ts")
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, ASSET],
            capture_output=True, text=True, timeout=timeout,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
    return json.loads(proc.stdout)


def main() -> None:
    if not os.path.exists(ASSET):
        raise SystemExit(f"자산 없음: {ASSET}")
    with open(BASELINE, encoding="utf-8") as f:
        base = json.load(f)["documents"][0]["deterministic"]
    want_rows = base["chunks"]

    # 참고용 — Python 을 지금 다시 돌려 기준선이 아직 유효한지도 본다.
    sys.path.insert(0, os.path.join(ROOT, "api"))
    import app.ingest.stages.chunk as C
    from app.adapters.impl.hwp_parser import Hwp5Parser
    from app.adapters.impl.hwpml_parser import is_hwpml_bytes

    with open(ASSET, "rb") as f:
        raw = f.read()
    # `extract.py` 의 HWP 변형 분기 그대로 — doc_type='hwp' 가 OLE2 일 수도 HWPML XML
    # 일 수도 있다. 이식한 `@rhwp/core` 경로는 OLE2 전용이라 여기서 확인하고 간다.
    if is_hwpml_bytes(raw[:4096]):
        raise SystemExit("이 자산은 HWPML(XML) 이다 — @rhwp/core 경로 대상이 아니다.")
    py_extraction = Hwp5Parser().parse(raw, file_name="law_sample1.hwp")
    py_records = C._to_chunk_records(
        doc_id="DOC",
        sections=C._merge_short_sections(C._split_long_sections(py_extraction.sections)),
    )
    py_rows = [{
        "chunk_idx": r.chunk_idx, "page": r.page,
        "has_section_title": bool(r.section_title),
        "text_sha16": sha16(r.text), "text_len": len(r.text),
        "meta_keys": sorted(r.metadata.keys()),
        "has_bbox": r.bbox is not None,
        "has_char_range": r.char_range is not None,
    } for r in py_records]

    ts = run_deno()
    ts_rows = ts["rows"]

    fails = 0
    print(f"  섹션 수    py {len(py_extraction.sections):>3}  ts {ts['section_count']:>3}")
    print(f"  raw_text   py {len(py_extraction.raw_text):>3}자  ts {[*ts['raw_text']].__len__():>3}자")
    print(f"  청크 수    기준선 {base['chunk_count']}  py {len(py_rows)}  ts {len(ts_rows)}")
    print()

    keys = ["chunk_idx", "page", "has_section_title", "text_sha16", "text_len",
            "meta_keys", "has_bbox", "has_char_range"]

    def cmp(label: str, a_rows, b_rows) -> int:
        bad = 0
        if len(a_rows) != len(b_rows):
            print(f"  **{label} — 청크 수 불일치** {len(a_rows)} vs {len(b_rows)}")
            return 1
        for a, b in zip(a_rows, b_rows):
            diff = {k: (a.get(k), b.get(k)) for k in keys if a.get(k) != b.get(k)}
            if diff:
                bad += 1
                print(f"  **{label} [{a['chunk_idx']}] 불일치** "
                      + "  ".join(f"{k}: {v[0]!r} vs {v[1]!r}" for k, v in diff.items()))
        if bad == 0:
            print(f"  {label:<34} {len(a_rows)}청크 전부 일치")
        return bad

    # `flag_keys` 는 chunk_filter 산물 — 기준선에서 떼어내고 비교한다.
    want_cmp = [{k: r[k] for k in keys} for r in want_rows]

    # --- raw_text 는 **일치하지 않는다.** 알고 넘어가는 차이라 여기서 고정한다 ---
    # Python 은 `hwp5txt` CLI 가 이 파일에서 죽어(msoleprops.py `KeyError: 2` —
    # 환경이 아니라 파일 고유 문제라 Railway 에서도 같다) olefile fallback 을 탄다.
    # TS 는 `@rhwp/core` 다. 추출기가 다르니 raw_text 는 근본적으로 못 맞춘다.
    #
    # 실측: 993 vs 985 자, 차이는 **빈 줄 3 곳뿐**이고 공백을 모두 제거하면 동일하다.
    # 섹션 36 개와 청크 11 개는 완전히 같다. raw_text 는 `tag_summarize` LLM 입력과
    # `doc_embed` 의 summary-없음 fallback 에만 쓰이는데 둘 다 비결정 단계라
    # 기준선도 `nondeterministic` 으로 분류한다 → 결정적 산출물 영향 0.
    #
    # 문자 내용이 갈리기 시작하면 그건 다른 문제이므로 여기서 잡는다.
    py_raw, ts_raw = py_extraction.raw_text, ts["raw_text"]
    same_nows = "".join(py_raw.split()) == "".join(ts_raw.split())
    print(f"  raw_text  공백 제외 동일? {same_nows}   "
          f"(길이 차 {len(ts_raw) - len(py_raw):+d}자 — 추출기가 달라 빈 줄이 다르다)")
    if not same_nows:
        fails += 1
        print("  **raw_text 의 문자 내용이 갈렸다 — 공백 차이가 아니다**")
    print()

    fails += cmp("기준선 ↔ Python (기준선 유효성)", want_cmp, py_rows)
    fails += cmp("기준선 ↔ TypeScript (본 대조)", want_cmp, ts_rows)
    fails += cmp("Python ↔ TypeScript", py_rows, ts_rows)

    print()
    digest_want = base["chunks_digest"]
    digest_ts = sha16("|".join(r["text_sha16"] for r in ts_rows))
    digest_py = sha16("|".join(r["text_sha16"] for r in py_rows))
    print(f"  chunks_digest  기준선 {digest_want}")
    print(f"                 python {digest_py}  {'일치' if digest_py == digest_want else '**불일치**'}")
    print(f"                 deno   {digest_ts}  {'일치' if digest_ts == digest_want else '**불일치**'}")
    if digest_ts != digest_want:
        fails += 1

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
