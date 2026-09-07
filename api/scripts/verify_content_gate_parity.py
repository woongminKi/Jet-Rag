"""`content_gate.ts` 를 `ingest/stages/content_gate.py` 와 대조.

## 노린 함정
- `\\d` / `\\s` / `\\b` 가 Python 은 유니코드, JS 는 ASCII 다. 아랍-인도 숫자로 쓴
  주민번호를 놓친다.
- `m.start()` / `m.end()` 가 Python 은 **코드포인트**, JS 정규식은 UTF-16 이다.
  앞에 이모지가 있으면 `pii_ranges` 가 밀린다 — 그 값으로 화면을 가리므로 엉뚱한
  자리를 가리게 된다.
- `int(yymmdd[2:4])` 는 아랍-인도 숫자도 읽는다. `parseInt` 는 못 읽는다.
- `re.IGNORECASE` 와 JS `i` 가 켈빈 기호(U+212A) 같은 데서 갈릴 수 있다 — 재 본다.
- 카드번호와 휴대폰 번호(11 자리)가 자릿수로만 갈린다.

사용:
    api/.venv/bin/python api/scripts/verify_content_gate_parity.py
    api/.venv/bin/python api/scripts/verify_content_gate_parity.py --negative
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

TEXT_CASES = [
    "",
    "평범한 문장입니다.",
    # --- 주민등록번호
    "900101-1234567",
    "900101 1234567",
    "9001011234567",
    "주민번호는 900101-1234567 입니다.",
    "901301-1234567",          # 13월 → 무효
    "900132-1234567",          # 32일 → 무효
    "900100-1234567",          # 0일 → 무효
    "900001-1234567",          # 0월 → 무효
    "901231-1234567",          # 유효 경계
    "900101-12345678",         # 8자리 → \\b 때문에 안 걸린다
    "a900101-1234567",         # 앞에 글자 → \\b 없음
    "٩٠٠١٠١-١٢٣٤٥٦٧",          # 아랍-인도 숫자 주민번호
    # --- 카드번호
    "4111-1111-1111-1111",
    "4111 1111 1111 1111",
    "4111111111111111",
    "4111111111111111111",     # 19자리
    "371449635398431",         # 15자리 Amex → 안 걸린다
    "010-1234-5678",           # 휴대폰 → 안 걸린다
    "카드 4111-1111-1111-1111 로 결제",
    # --- 겹침
    "900101-1234567 4111-1111-1111-1111",
    "9001011234567890",        # 주민+카드 후보가 겹치는 자리
    # --- 오프셋 (이모지)
    "😀 900101-1234567",
    "😀😀😀 4111-1111-1111-1111 끝",
    "𠮷 900101-1234567",
    # --- 워터마크
    "대외비 문서입니다",
    "이 자료는 내부자료 입니다",
    "보안 등급",
    "CONFIDENTIAL",
    "confidential",
    "Confidential",
    "INTERNAL USE ONLY",
    "internal",
    "KNTERNAL",           # 켈빈 기호 — IGNORECASE 가 갈릴 수 있다
    "대외비 CONFIDENTIAL 내부자료 internal 보안",
    "대외비대외비",
    # --- 섞임
    "대외비 900101-1234567 4111-1111-1111-1111",
]

VISION_TYPES = [None, "메신저대화", "표", "", "메신저 대화"]

RUNNER_TS = """
import { detectPii, detectWatermark, isValidYymmdd, runContentGateStage }
  from "file://%(shared)s/ingest/content_gate.ts";
import type { ChunkRecord } from "file://%(shared)s/ingest/chunk_records.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const NEG = cfg.negative === true;

const rec = (text: string, i = 0): ChunkRecord => ({
  doc_id: "d1", chunk_idx: i, text, page: null, section_title: null,
  bbox: null, char_range: [0, 0], metadata: {},
});

const out: Record<string, unknown> = {};
out.pii = cfg.textCases.map((t: string) => detectPii(t));
out.watermark = cfg.textCases.map((t: string) => detectWatermark(t));
out.yymmdd = cfg.yymmddCases.map((t: string) => isValidYymmdd(t));

// 문서 단위 — 전 케이스를 한 문서의 청크로 넣는다.
out.docs = cfg.visionTypes.map((vt: unknown) => {
  const r = runContentGateStage({
    chunks: cfg.textCases.map((t: string, i: number) => rec(t, i)),
    visionType: vt,
  });
  return {
    flags: r.flagsUpdate,
    withPii: r.chunksWithPii,
    withWatermark: r.chunksWithWatermark,
    metadata: r.chunks.map((c) => c.metadata),
  };
});

if (NEG) (out.pii as number[][][])[2] = [];
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}

YYMMDD_CASES = [
    "900101", "901231", "900001", "901301", "900100", "900132",
    "12345", "1234567", "abcdef", "90 101", "٩٠٠١٠١", "９００１０１",
]


def main() -> None:
    from app.adapters.vectorstore import ChunkRecord
    from app.ingest.stages import content_gate as CG

    negative = "--negative" in sys.argv

    py_pii = [CG._detect_pii(t) for t in TEXT_CASES]
    py_wm = [CG._detect_watermark(t) for t in TEXT_CASES]
    py_yy = [CG._is_valid_yymmdd(t) for t in YYMMDD_CASES]

    # `run_content_gate_stage` 는 `stage()` + DB 를 건드린다 — 내부 로직만 재현한다.
    import dataclasses
    py_docs = []
    for vt in VISION_TYPES:
        has_pii = False
        has_wm = False
        wm_doc: set[str] = set()
        updated = []
        for i, t in enumerate(TEXT_CASES):
            c = ChunkRecord(doc_id="d1", chunk_idx=i, text=t)
            pii = CG._detect_pii(c.text)
            wm = CG._detect_watermark(c.text)
            if pii:
                has_pii = True
            if wm:
                has_wm = True
                wm_doc.update(wm)
            md = dict(c.metadata)
            if pii:
                md["pii_ranges"] = pii
            if wm:
                md["watermark_hits"] = wm
            updated.append(dataclasses.replace(c, metadata=md))
        flags = {"has_pii": has_pii, "has_watermark": has_wm,
                 "third_party": vt == "메신저대화"}
        if wm_doc:
            flags["watermark_hits"] = sorted(wm_doc)
        py_docs.append({
            "flags": flags,
            "withPii": sum(1 for c in updated if "pii_ranges" in c.metadata),
            "withWatermark": sum(1 for c in updated if "watermark_hits" in c.metadata),
            "metadata": [c.metadata for c in updated],
        })

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "out.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({
                "textCases": TEXT_CASES, "yymmddCases": YYMMDD_CASES,
                "visionTypes": VISION_TYPES, "negative": negative,
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

    for t, a, b in zip(TEXT_CASES, py_pii, ts["pii"]):
        cmp(f"detect_pii({t[:24]!r})", a, b)
    for t, a, b in zip(TEXT_CASES, py_wm, ts["watermark"]):
        cmp(f"detect_watermark({t[:24]!r})", a, b)
    for t, a, b in zip(YYMMDD_CASES, py_yy, ts["yymmdd"]):
        cmp(f"is_valid_yymmdd({t!r})", a, b)
    for vt, a, b in zip(VISION_TYPES, py_docs, ts["docs"]):
        cmp(f"flags(vision_type={vt!r})", a["flags"], b["flags"])
        cmp(f"withPii({vt!r})", a["withPii"], b["withPii"])
        cmp(f"withWatermark({vt!r})", a["withWatermark"], b["withWatermark"])
        cmp(f"metadata({vt!r})", a["metadata"], b["metadata"])

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
