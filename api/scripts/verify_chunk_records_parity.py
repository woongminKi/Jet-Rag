"""`chunk.py` 의 레코드 조립(2차 분할 → caption 합성 → NFC → 엔티티 → ChunkRecord)을 대조.

## 조각 c1 — synonym 은 제외
`JETRAG_SYNONYM_INJECTION_ENABLED` 는 운영 DB 기준 **지금 OFF** 다(주입 흔적 199행이
전부 2026-05-13 하루치, 이후 34,000행 이상 인제스트에는 0행 — 2026-09-07 실측).
OFF 경로에서 원본은 text/metadata 를 건드리지 않으므로 여기서 제외해도 정확하다.
ON 경로는 조각 c2.

## caption ENV 는 양쪽 다 돈다
`JETRAG_CAPTION_PREFIX_ENABLED` 도 운영에서 켜진 적이 있다(`[표 p.N: ` 101행). ON/OFF
두 번 대조한다.

## 케이스가 노리는 것
- vision 진입 조건 2가지(`vision_incremental` / `(vision)` prefix)와 미진입
- caption 200자 경계, `.strip()` 후 빈 caption, table/figure 우선순위
- `page=None` 분기
- NFC 정규화 (NFD 한글 — HWP 파서가 뱉는 형태)
- `char_range` 가 **코드포인트** 길이 (이모지)
- 2차 분할 진입 임계(`MAX_SIZE`)와 metadata/bbox 승계
- `table_caption` 이 `""`(falsy but not None) 일 때 키 주입 여부

사용:
    api/.venv/bin/python api/scripts/verify_chunk_records_parity.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

_KO = "한국어 문장입니다. "   # 11자
_NFD = "한국어"              # 아래에서 NFD 로 변환해 쓴다


def S(text, *, page=None, title=None, bbox=None, meta=None):
    return {"text": text, "page": page, "section_title": title,
            "bbox": bbox, "metadata": meta or {}}


import unicodedata  # noqa: E402

NFD_TEXT = unicodedata.normalize("NFD", "한국어 계약서 금액 50,000원")
assert NFD_TEXT != unicodedata.normalize("NFC", NFD_TEXT), "NFD 케이스가 무효"

CASES: list[list[dict]] = [
    [],
    # --- 일반 chunk (vision 미진입) — caption 이 있어도 무시돼야 한다 ---
    [S("평범한 본문입니다.", page=1, meta={"table_caption": "무시돼야 함"})],
    # --- vision 진입: `(vision)` prefix ---
    [S("표 본문", page=3, title="(vision) 표 1", meta={"table_caption": "월별 매출"})],
    [S("그림 본문", page=4, title="(vision) 그림", meta={"figure_caption": "구조도"})],
    [S("둘 다", page=5, title="(vision)",
       meta={"table_caption": "T", "figure_caption": "F"})],
    # --- vision 진입: `vision_incremental` (운영 0행이지만 코드 경로) ---
    [S("증분", page=6, meta={"vision_incremental": True, "table_caption": "IT"})],
    [S("증분 caption 없음", page=7, meta={"vision_incremental": True})],
    # --- caption 경계 ---
    [S("긴 caption", page=8, title="(vision)", meta={"table_caption": "가" * 199}),
     S("경계 200", page=8, title="(vision)", meta={"table_caption": "가" * 200}),
     S("초과 201", page=8, title="(vision)", meta={"table_caption": "가" * 201})],
    [S("공백 caption", page=9, title="(vision)", meta={"table_caption": "   "})],
    [S("빈 문자열", page=10, title="(vision)", meta={"table_caption": ""})],
    [S("page 없음", page=None, title="(vision)", meta={"table_caption": "P"})],
    [S("caption 이모지", page=11, title="(vision)", meta={"table_caption": "🙂" * 201})],
    # --- NFC 정규화 ---
    [S(NFD_TEXT, page=12)],
    [S("본문", page=13, title=unicodedata.normalize("NFD", "제목 한글"))],
    # --- char_range 코드포인트 ---
    [S("🙂" * 50, page=14)],
    # --- 2차 분할 진입 (MAX_SIZE 초과) + 승계 ---
    [S(_KO * 120, page=15, title="원제목", bbox=[1.0, 2.0, 3.0, 4.0],
       meta={"table_caption": "승계될까"})],
    [S(_KO * 120, page=16, title="(vision) 분할", bbox=[0.0, 0.0, 1.0, 1.0],
       meta={"table_caption": "vision 분할"})],
    # **`<= MAX_SIZE` 경계** — 정확히 1000자.
    # 단순히 1000자를 넣으면 부족하다. 문장 경계가 없으면 분할해도 1조각이라 임계를
    # `<` 로 바꿔도 결과가 같다. `"끝났다. "`(5자) 반복이라야 실제로 여러 조각이 된다.
    [S("끝났다. " * 200, page=20)],          # 정확히 1000 → 분할 안 함
    [S("끝났다. " * 200 + "가", page=21)],   # 1001 → 분할
    [S("끝났다. " * 199 + "끝났다.", page=22)],  # 999 → 분할 안 함
    # --- 병합까지 태우기 (짧은 섹션 연속) ---
    [S("짧다", page=17), S("이어짐", page=17), S("셋째", page=17)],
    # --- 엔티티가 잡히는/안 잡히는 ---
    [S("2024년 4월 30일 계약, 금액 50,000원, 이율 3.5%, 제12호", page=18)],
    [S("아무것도 없음", page=19)],
]

RUNNER_TS = f"""
import {{ runChunkStage }} from "file://{SHARED}/ingest/chunk_records.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out = input.cases.map((sections: unknown[]) =>
  runChunkStage({{
    docId: "DOC",
    sections: sections as never,
    env: {{
      captionPrefixEnabled: input.captionPrefix,
      synonymInjectionEnabled: false,
      synonymLlmEnabled: false,
    }},
  }})
);
console.log(JSON.stringify(out));
"""


def run_deno(cases, caption_prefix: bool, timeout: int = 300):
    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "cases.json"), os.path.join(tmp, "runner.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({"cases": cases, "captionPrefix": caption_prefix}, f)
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
    import app.ingest.stages.chunk as C
    from app.adapters.parser import ExtractedSection

    def to_py(d):
        return ExtractedSection(
            text=d["text"], page=d["page"], section_title=d["section_title"],
            bbox=tuple(d["bbox"]) if d["bbox"] else None,
            metadata=dict(d["metadata"]))

    def rec_to_dict(r):
        return {
            "doc_id": r.doc_id, "chunk_idx": r.chunk_idx, "text": r.text,
            "page": r.page, "section_title": r.section_title,
            "bbox": list(r.bbox) if r.bbox else None,
            "char_range": list(r.char_range) if r.char_range else None,
            "metadata": r.metadata,
        }

    fails = 0
    stats = {"vision": 0, "entities": 0, "split": 0, "truncated": 0}

    for caption_prefix in (False, True):
        env = {"JETRAG_CAPTION_PREFIX_ENABLED": "true" if caption_prefix else "false",
               "JETRAG_SYNONYM_INJECTION_ENABLED": "false",
               "JETRAG_SYNONYM_INJECTION_LLM": "false"}
        ts = run_deno(CASES, caption_prefix)
        label = "ON " if caption_prefix else "OFF"
        n_bad = 0
        for i, case in enumerate(CASES):
            with unittest.mock.patch.dict(os.environ, env, clear=False):
                split = C._split_long_sections([to_py(d) for d in case])
                merged = C._merge_short_sections(split)
                want = [rec_to_dict(r) for r in
                        C._to_chunk_records(doc_id="DOC", sections=merged)]
            got = ts[i]
            if len(split) > len(case):
                stats["split"] += 1
            for r in want:
                if "table_caption" in r["metadata"] or "figure_caption" in r["metadata"]:
                    stats["vision"] += 1
                if "entities" in r["metadata"]:
                    stats["entities"] += 1
                if "…" in r["text"]:
                    stats["truncated"] += 1
            if want != got:
                n_bad += 1
                fails += 1
                print(f"  **[caption {label}][{i}] 불일치** — py {len(want)}개 / ts {len(got)}개")
                for a, b in zip(want, got):
                    if a != b:
                        for k in a:
                            if a[k] != b.get(k):
                                print(f"      {k:<14} py={json.dumps(a[k], ensure_ascii=False)[:160]}")
                                print(f"      {'':<14} ts={json.dumps(b.get(k), ensure_ascii=False)[:160]}")
                        break
        if n_bad == 0:
            print(f"  caption prefix {label}         {len(CASES)}건 OK")

    print(f"    태워진 분기: vision caption {stats['vision']}  entities {stats['entities']}"
          f"  2차분할 {stats['split']}  caption 잘림 {stats['truncated']}")
    for k, v in stats.items():
        if v == 0:
            fails += 1
            print(f"    **케이스 무효** — {k} 분기가 한 번도 안 태워졌다")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
