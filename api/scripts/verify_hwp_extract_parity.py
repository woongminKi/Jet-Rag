"""HWP `extract` 단계를 Python 원본과 대조.

## 왜 섹션 단위인가
기준선(`ingest_baseline.py`)은 **청크**를 고정한다. 그건 extract → chunk → chunk_filter
를 거친 결과라, extract 만 옮긴 지금은 그걸로 판정할 수 없다. extract 의 산출물은
`ExtractionResult.sections` 이므로 거기서 대조한다.

## 두 파서는 원리가 다르다
- Python: `hwp5txt` CLI → 실패 시 olefile+record 파싱
- Edge: `@rhwp/core` 의 `getTextFileText()` (Phase 0 에서 유사도 1.0000)

즉 **바이트가 같아서 같은 게 아니라, 결과가 같아야 같은 것**이다. 그래서 텍스트 자체를
비교한다. 다만 저장소에 남기는 출력에는 본문을 싣지 않는다 — 불일치일 때만 길이·앞부분을
짧게 보여 준다.

사용:
    api/.venv/bin/python api/scripts/verify_hwp_extract_parity.py
    api/.venv/bin/python api/scripts/verify_hwp_extract_parity.py --file <경로>
"""

from __future__ import annotations

import argparse
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

sys.path.insert(0, os.path.join(ROOT, "api"))

DEFAULT_FILES = ["assets/public/law_sample1.hwp"]

RUNNER_TS = f"""
import {{ extractHwp }} from "file://{SHARED}/ingest/hwp_extract.ts";
import {{
  buildHwpResult, decodeNumericEntities, splitParagraphs, unwrapJsonString,
}} from "file://{SHARED}/ingest/hwp_extract.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out: Record<string, unknown> = {{}};
for (const [name, path] of input.files) {{
  const bytes = await Deno.readFile(path);
  const t0 = performance.now();
  const r = await extractHwp(bytes);
  out[name] = {{ result: r, ms: performance.now() - t0 }};
}}
// 순수 함수 두 개도 따로 대조한다 — 실패했을 때 어느 단계인지 가른다.
out["__pure__"] = {{
  entities: input.entity_cases.map((s: string) => decodeNumericEntities(s)),
  paragraphs: input.para_cases.map((s: string) => splitParagraphs(s)),
  unwrapped: input.unwrap_cases.map((s: string) => unwrapJsonString(s)),
  built: input.build_cases.map((s: string) => buildHwpResult(s)),
}};
console.log(JSON.stringify(out));
"""

# 숫자 엔티티 디코딩 — Phase 0 이 확인한 건 숫자 엔티티뿐이다.
ENTITY_CASES = [
    "&#65378;안녕&#65379;",          # 실제로 나온 형태 (｢ ｣)
    "&#x41;&#x42;",                  # 16진
    "&amp; &lt;",                    # **명명 엔티티는 건드리지 않는다**
    "&#;", "&#99999999;", "&#x110000;",  # 깨진 값 → 원문 유지
    "엔티티 없음", "",
]

# 단락 분할 — `\n\n` 우선, 1개 이하면 `\n`
PARA_CASES = [
    "가\n\n나\n\n다",
    "가\n나\n다",            # `\n\n` 없음 → `\n` 로 재분할
    "한 줄뿐",               # 1개 → `\n` 재분할해도 1개
    "  앞뒤 공백  \n\n  다음  ",
    "\n\n\n\n",              # 전부 공백 → 빈 목록
    "가\n\n\n나",            # 연속 개행
    "",
]


# JSON 언랩 — `getTextFileText()` 가 따옴표로 감싼 문자열을 준다(실측).
UNWRAP_CASES = [
    '"가\\r\\n나"',       # 실제로 나온 형태
    '"따옴표 \\" 포함"',
    "평문 그대로",            # 따옴표로 시작 안 함 → 그대로
    '"깨진 JSON',            # 파싱 실패 → 그대로
    '"123"', '"true"', "", '  "앞 공백"',
]


# `buildHwpResult` 직접 대조 — 실제 파일 하나로는 안 태워지는 분기가 있다.
# (음성 대조에서 "빈 텍스트 분기 제거"·"pyStrip→trim" 이 0 건이었다.)
BUILD_CASES = [
    "",                          # 빈 문자열 → sections 0, warnings 1
    "   ",                       # 공백만 → 같은 분기
    "\u00a0\u00a0",              # NBSP 만 — Python strip 은 지우고 JS trim 도 지운다
    "가\u00a0\n\n\u00a0나",       # 단락 양끝 NBSP → strip 대상
    "\u001c가\u001c\n\n나",       # **U+001C** — Python `\s` 는 공백, JS `trim()` 은 아니다
    "가\ufeff\n\n나",            # **U+FEFF** — JS `trim()` 은 지우고 Python 은 안 지운다
    "한 줄", "가\n나\n다",
]


def run_deno(payload: dict, timeout: int = 300) -> dict:
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


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", action="append", help="대조할 HWP (여러 번 지정 가능)")
    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
    from app.adapters.impl.hwp_parser import Hwp5Parser

    files = args.file or DEFAULT_FILES
    pairs = []
    for f in files:
        p = f if os.path.isabs(f) else os.path.join(ROOT, f)
        if not os.path.exists(p):
            raise SystemExit(f"파일이 없다: {p}")
        pairs.append((os.path.basename(p), p))

    ts = run_deno({
        "files": [list(x) for x in pairs],
        "entity_cases": ENTITY_CASES,
        "para_cases": PARA_CASES,
        "unwrap_cases": UNWRAP_CASES,
        "build_cases": BUILD_CASES,
    })

    fails = 0

    print("=== 순수 함수 ===")
    import re as _re

    def py_decode(s: str) -> str:
        def rep(m):
            b = m.group(1)
            cp = int(b[1:], 16) if b[0] in "xX" else int(b)
            if cp < 0 or cp > 0x10FFFF:
                return m.group(0)
            try:
                return chr(cp)
            except ValueError:
                return m.group(0)
        return _re.sub(r"&#(x[0-9a-fA-F]+|\d+);", rep, s)

    got = ts["__pure__"]["entities"]
    want = [py_decode(s) for s in ENTITY_CASES]
    if got != want:
        fails += 1
        for i, (a, b) in enumerate(zip(want, got)):
            if a != b:
                print(f"  MISMATCH 엔티티[{i}] {ENTITY_CASES[i]!r}: py={a!r} ts={b!r}")
    else:
        print(f"  숫자 엔티티 디코딩 {len(ENTITY_CASES)}건        OK")

    def py_split(text: str) -> list[str]:
        parts = [p.strip() for p in text.split("\n\n") if p.strip()]
        if len(parts) <= 1:
            parts = [p.strip() for p in text.split("\n") if p.strip()]
        return parts

    got = ts["__pure__"]["paragraphs"]
    want = [py_split(s) for s in PARA_CASES]
    if got != want:
        fails += 1
        for i, (a, b) in enumerate(zip(want, got)):
            if a != b:
                print(f"  MISMATCH 단락[{i}] {PARA_CASES[i]!r}: py={a} ts={b}")
    else:
        print(f"  단락 분할 {len(PARA_CASES)}건                OK")

    def py_unwrap(s: str) -> str:
        if not s.lstrip().startswith('"'):
            return s
        try:
            v = json.loads(s)
            return v if isinstance(v, str) else s
        except Exception:
            return s

    got = ts["__pure__"]["unwrapped"]
    want = [py_unwrap(s) for s in UNWRAP_CASES]
    if got != want:
        fails += 1
        for i, (a, b) in enumerate(zip(want, got)):
            if a != b:
                print(f"  MISMATCH 언랩[{i}] {UNWRAP_CASES[i]!r}: py={a!r} ts={b!r}")
    else:
        print(f"  JSON 언랩 {len(UNWRAP_CASES)}건               OK")

    print()
    print("=== buildHwpResult — 텍스트 주입으로 분기 태우기 ===")
    # **Python 파서의 텍스트 추출만 갈아끼운다.** 그래야 "내 기대" 가 아니라 원본 로직과
    # 대조하게 된다.
    import app.adapters.impl.hwp_parser as HP

    empty_hits = 0
    for i, (txt, got) in enumerate(zip(BUILD_CASES, ts["__pure__"]["built"])):
        orig = HP._hwp_to_text_via_cli
        HP._hwp_to_text_via_cli = lambda _d, *, file_name, _t=txt: _t
        try:
            r = Hwp5Parser().parse(b"x", file_name="t.hwp")
        finally:
            HP._hwp_to_text_via_cli = orig
        want = {
            "source_type": r.source_type,
            "sections": [{"text": x.text, "page": x.page, "section_title": x.section_title,
                          "bbox": list(x.bbox) if x.bbox else None, "metadata": x.metadata}
                         for x in r.sections],
            "raw_text": r.raw_text,
            "warnings": r.warnings,
            "metadata": r.metadata,
        }
        if not r.sections:
            empty_hits += 1
        if want != got:
            fails += 1
            print(f"  MISMATCH [{i}] {txt!r}")
            print(f"      py sections={len(want['sections'])} raw={want['raw_text']!r} "
                  f"warn={len(want['warnings'])}")
            print(f"      ts sections={len(got['sections'])} raw={got['raw_text']!r} "
                  f"warn={len(got['warnings'])}")
    if fails == 0:
        print(f"  {len(BUILD_CASES)}건 대조                      OK")
    # 케이스가 분기를 실제로 태웠는지 검사기가 스스로 본다.
    if empty_hits == 0 or empty_hits == len(BUILD_CASES):
        fails += 1
        print(f"  **케이스 무효** — 빈 결과 분기가 한쪽만 태워졌다 ({empty_hits}건)")

    print()
    print("=== 실제 파일 — extract 결과 ===")
    for name, path in pairs:
        with open(path, "rb") as f:
            data = f.read()
        py = Hwp5Parser().parse(data, file_name=name)
        tv = ts[name]["result"]

        print(f"  {name}  ({len(data):,} bytes, Edge {ts[name]['ms']:.0f}ms)")
        # 1) 섹션 수
        if len(py.sections) != len(tv["sections"]):
            fails += 1
            print(f"    **섹션 수 다름** py={len(py.sections)} ts={len(tv['sections'])}")
        else:
            print(f"    섹션 수 {len(py.sections)}개                    OK")

        # 2) 섹션별 내용 — 본문은 해시로, 다를 때만 짧게 보여 준다
        bad = []
        for i, (a, b) in enumerate(zip(py.sections, tv["sections"])):
            if a.text != b["text"] or b["page"] is not None or b["section_title"] is not None \
                    or b["bbox"] is not None:
                bad.append(i)
        if bad:
            fails += 1
            print(f"    **다른 섹션 {len(bad)}개** 인덱스 {bad[:8]}")
            i = bad[0]
            a, b = py.sections[i].text, tv["sections"][i]["text"]
            print(f"      [{i}] py len={len(a)} sha={_sha(a)} :: {a[:60]!r}")
            print(f"      [{i}] ts len={len(b)} sha={_sha(b)} :: {b[:60]!r}")
        else:
            print(f"    섹션 내용·메타                     OK")

        # 3) raw_text — **공백을 제외한 내용은 완전일치를 요구**하고, 공백 자체는 허용한다.
        #
        #    두 파서(hwp5txt/olefile vs @rhwp/core)가 **빈 문단을 다르게 낸다**.
        #    실측: `\r\n` 이 py 35 vs ts 39 (Edge 가 빈 줄 4 개 더). 공백을 지우면 722 자
        #    완전일치다. 임의로 개행을 축약해 맞추면 다른 문서에서 오히려 갈리므로 안 한다.
        #
        #    영향 범위를 확인했다 — `raw_text` 소비처는 `tag_summarize`(LLM 입력) ·
        #    `doc_embed`(요약 NULL 일 때 fallback) · `chunk`(ENV OFF 면 미사용) 뿐이고,
        #    `extract.py:308` 의 스캔 판정은 PDF 전용이다. **결정적 산출물인 chunks 는
        #    sections 에서 나오므로 영향이 없다.**
        import re as _re2
        nw = lambda t: _re2.sub(r"\s+", "", t)
        if nw(py.raw_text) != nw(tv["raw_text"]):
            fails += 1
            print(f"    **raw_text 내용이 다름(공백 제외)** py {len(nw(py.raw_text))}자 / "
                  f"ts {len(nw(tv['raw_text']))}자")
        else:
            d = len(tv["raw_text"]) - len(py.raw_text)
            print(f"    raw_text 내용 일치(공백 제외 {len(nw(py.raw_text)):,}자)  OK"
                  f"{f'  — 공백만 {d:+d}자 차이(빈 문단 처리)' if d else ''}")
            # 공백 차이가 커지면 파서 동작이 바뀐 신호다. 임계를 둬서 조용히 벌어지지 않게 한다.
            if abs(d) > 50:
                fails += 1
                print(f"    **공백 차이가 임계(50)를 넘었다** — 파서 동작 변화를 의심할 것")

        # 4) source_type
        if py.source_type != tv["source_type"]:
            fails += 1
            print(f"    **source_type** py={py.source_type} ts={tv['source_type']}")

        # 5) warnings — 파서 경로가 달라 내용은 다를 수 있다. **개수만** 본다.
        if py.warnings:
            print(f"    (참고) Python warnings {len(py.warnings)}건: {py.warnings[0][:70]}")
        if tv["warnings"]:
            print(f"    (참고) Edge warnings {len(tv['warnings'])}건: {tv['warnings'][0][:70]}")

        # 케이스가 의미 있는지 — 빈 결과면 아무것도 대조하지 않은 것이다.
        if len(py.sections) == 0:
            fails += 1
            print("    **케이스 무효** — 섹션이 0개라 대조할 게 없다")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
