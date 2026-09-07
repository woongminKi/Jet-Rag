"""`image_parser.ts` 결과 합성 + `vision_cache.ts` 직렬화를 원본과 대조.

## 여기서 갈리면 검색 결과가 갈린다
`_compose_result` 가 만든 섹션이 그대로 청크가 되고 임베딩된다. 섹션 개수·제목·본문·
metadata 중 하나만 어긋나도 사용자가 보는 검색 결과가 달라진다.

## 노린 함정
- `raw.get("ocr_text") or ""` — Python 은 빈 배열·빈 dict 가 falsy 인데 JS `||` 는
  truthy 로 본다.
- `str(v).strip() for v in item.values() if v` — 컨테이너의 `str()` 표기가 JS
  `String()` 과 다르다 (`[1, 2]` vs `1,2`).
- `.strip()` 은 유니코드 공백 집합이 JS `trim()` 과 다르다.

사용:
    api/.venv/bin/python api/scripts/verify_vision_compose_parity.py
    api/.venv/bin/python api/scripts/verify_vision_compose_parity.py --negative
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

# (type, ocr_text, caption, structured, table_caption, figure_caption)
CAPTION_CASES: list[tuple] = [
    ("표", "OCR 본문", "표 요약", None, None, None),
    ("표", "", "표 요약", None, "표 캡션", "그림 캡션"),
    ("그림", "   ", "  캡션 앞뒤 공백  ", None, None, "그림만"),
    ("문서", "본문", "", None, None, None),
    ("문서", "본문", "   ", None, None, None),
    # 유니코드 공백 — Python .strip() 과 JS trim() 이 갈리는 자리
    ("문서", " 본문 ", " 캡션 ", None, None, None),
    ("문서", "\x1c본문\x1f", "\x85캡션\x85", None, None, None),
    # action_items — str / dict / 혼합 / 잘못된 타입
    ("화이트보드", "메모", "회의", {"action_items": ["A 하기", " B 하기 ", ""]}, None, None),
    ("화이트보드", "", "회의", {"action_items": [
        {"task": "배포", "owner": "김", "due_date": "내일"},
    ]}, None, None),
    ("화이트보드", "", "회의", {"action_items": [
        {"task": "배포", "owner": "", "due_date": None, "n": 0, "arr": [], "d": {}},
    ]}, None, None),
    # 컨테이너 값 — str() 표기가 JS String() 과 다르다
    ("화이트보드", "", "회의", {"action_items": [
        {"tags": ["a", "b"], "meta": {"k": 1}, "flag": True, "num": 1.5},
    ]}, None, None),
    ("화이트보드", "", "회의", {"action_items": ["A", 5, None, ["x"], {"y": 1}]}, None, None),
    ("화이트보드", "", "회의", {"action_items": []}, None, None),
    ("화이트보드", "", "회의", {"action_items": "리스트 아님"}, None, None),
    ("화이트보드", "", "회의", {"other": ["A"]}, None, None),
    ("화이트보드", "", "회의", {}, None, None),
    # table/figure caption 이 공백뿐인 경우
    ("표", "본문", "요약", None, "   ", None),
    ("표", "본문", "요약", None, "", ""),
]

# vision_cache._deserialize 케이스 — 깨진 row 포함
DESERIALIZE_CASES: list = [
    None, "문자열", 42, [], [1, 2],
    {},
    {"type": "표", "ocr_text": "본문", "caption": "요약", "structured": {"a": 1},
     "table_caption": "T", "figure_caption": "F"},
    {"type": 42, "ocr_text": "x", "caption": "y"},
    {"type": "표"},
    {"type": "표", "ocr_text": None, "caption": None},
    {"type": "표", "ocr_text": [], "caption": {}},        # Python falsy → ""
    {"type": "표", "ocr_text": [1], "caption": {"k": 1}},  # Python truthy → 그대로
    {"type": "표", "ocr_text": 0, "caption": 0},
    {"type": "표", "structured": [1, 2]},                  # dict 아님 → None
    {"type": "표", "structured": "x"},
    {"type": "표", "table_caption": "  ", "figure_caption": 5},
    {"type": "표", "table_caption": " T ", "figure_caption": "F"},
]

RUNNER_TS = """
import { composeResult, extractActionItems }
  from "file://%(shared)s/ingest/image_parser.ts";
import { deserialize, serialize } from "file://%(shared)s/ingest/vision_cache.ts";
import type { VisionCaption } from "file://%(shared)s/ingest/vision_caption.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const NEG = cfg.negative === true;

const out: Record<string, unknown> = {};
out.composed = cfg.captionCases.map((c: unknown[]) => {
  const cap: VisionCaption = {
    // deno-lint-ignore no-explicit-any
    type: c[0] as any, ocr_text: c[1] as string, caption: c[2] as string,
    structured: c[3] as Record<string, unknown> | null, usage: null,
    table_caption: c[4] as string | null, figure_caption: c[5] as string | null,
  };
  return {
    result: composeResult(cap, { warnings: ["w1"] }),
    actionItems: extractActionItems(cap.structured),
    serialized: serialize(cap),
  };
});
out.deserialized = cfg.deserializeCases.map((raw: unknown) => deserialize(raw));

if (NEG) {
  // deno-lint-ignore no-explicit-any
  const first = (out.composed as any[])[0];
  first.result.sections[0].section_title += "!";
}
// stdout 으로 내보내면 안 된다 — 데이터에 U+0085 같은 문자가 있으면
// Python `splitlines()` 가 그걸 줄바꿈으로 쪼갠다(실제로 여기서 깨졌다).
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}


def main() -> None:
    from app.adapters.impl.image_parser import ImageParser, _extract_action_items
    from app.adapters.vision import VisionCaption
    from app.services import vision_cache

    negative = "--negative" in sys.argv
    parser = ImageParser(captioner=object())  # type: ignore[arg-type]

    def sec_json(s) -> dict:
        return {
            "text": s.text, "page": s.page, "section_title": s.section_title,
            "bbox": list(s.bbox) if s.bbox else None, "metadata": dict(s.metadata),
        }

    def res_json(r) -> dict:
        return {
            "source_type": r.source_type,
            "sections": [sec_json(s) for s in r.sections],
            "raw_text": r.raw_text, "warnings": list(r.warnings),
            "metadata": dict(r.metadata),
        }

    py_composed = []
    for case in CAPTION_CASES:
        cap = VisionCaption(
            type=case[0], ocr_text=case[1], caption=case[2], structured=case[3],
            usage=None, table_caption=case[4], figure_caption=case[5],
        )
        py_composed.append({
            "result": res_json(parser._compose_result(cap, warnings=["w1"])),
            "actionItems": _extract_action_items(cap.structured),
            "serialized": vision_cache._serialize(cap),
        })

    def cap_json(c) -> dict | None:
        if c is None:
            return None
        d = dataclasses.asdict(c)
        return {
            "type": d["type"], "ocr_text": d["ocr_text"], "caption": d["caption"],
            "structured": d["structured"], "usage": d["usage"],
            "table_caption": d["table_caption"], "figure_caption": d["figure_caption"],
        }

    py_deser = [cap_json(vision_cache._deserialize(raw)) for raw in DESERIALIZE_CASES]

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "out.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({"captionCases": CAPTION_CASES,
                       "deserializeCases": DESERIALIZE_CASES,
                       "negative": negative}, f)
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

    def cmp(label: str, a, b) -> None:
        nonlocal checks
        checks += 1
        if a != b:
            fails.append(f"{label}\n      py={a!r}\n      ts={b!r}")

    for i, (case, a, b) in enumerate(zip(CAPTION_CASES, py_composed, ts["composed"])):
        tag = f"compose[{i}] type={case[0]}"
        cmp(f"{tag}.actionItems", a["actionItems"], b["actionItems"])
        cmp(f"{tag}.serialized", a["serialized"], b["serialized"])
        cmp(f"{tag}.source_type", a["result"]["source_type"], b["result"]["source_type"])
        cmp(f"{tag}.raw_text", a["result"]["raw_text"], b["result"]["raw_text"])
        cmp(f"{tag}.warnings", a["result"]["warnings"], b["result"]["warnings"])
        cmp(f"{tag}.metadata", a["result"]["metadata"], b["result"]["metadata"])
        cmp(f"{tag}.섹션수", len(a["result"]["sections"]), len(b["result"]["sections"]))
        for j, (sa, sb) in enumerate(zip(a["result"]["sections"], b["result"]["sections"])):
            cmp(f"{tag}.sections[{j}]", sa, sb)

    for i, (raw, a, b) in enumerate(zip(DESERIALIZE_CASES, py_deser, ts["deserialized"])):
        cmp(f"deserialize[{i}] {raw!r}", a, b)

    for f in fails[:20]:
        print(f"  **{f}**")
    print()
    print(f"  비교 {checks}건, 불일치 {len(fails)}건")
    if negative:
        print("음성 대조: " + ("검사기 정상 (변조 검출)" if fails else "**검사기가 못 잡는다**"))
        sys.exit(0 if fails else 1)
    print("FAIL 0" if not fails else f"FAIL {len(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "api"))
    main()
