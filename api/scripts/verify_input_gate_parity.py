"""업로드 입력 게이트를 Python 원본과 대조.

## 이건 보안 게이트다
목적은 "exe 가 .docx 로 위장" 차단이다. 느슨하면 **위장 파일이 통과하고**, 빡빡하면
정상 업로드가 거절된다. 둘 다 조용히 나빠지므로 대조로 고정한다.

## 대조 방식
`validate_magic` 의 **통과/거절**을 비교한다. 원본이 반환하는 MIME 문자열까지 맞출
필요는 없다 — 허용 목록에 없는 MIME 은 어떤 값이든 거절이라 판정이 같기 때문이다.
(그래서 이식본은 exe·Mach-O 매처를 옮기지 않았다. 그 근거를 여기서 확인한다.)

## 케이스
실자산 + 위장·경계 합성. `filetype` 1.2.0 실측으로 만든 fixture 를 함께 쓴다.

사용:
    api/.venv/bin/python api/scripts/verify_input_gate_parity.py
"""

from __future__ import annotations

import glob
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

OLE2 = bytes([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1])
PAD = b"\x00" * 300


def ftyp(major: bytes, brands: bytes = b"", extra_len: int = 0) -> bytes:
    """ISO-BMFF ftyp 박스. box_len(4) + 'ftyp' + major(4) + minor(4) + brands."""
    body = b"ftyp" + major + b"\x00\x00\x00\x00" + brands
    box_len = 4 + len(body) + extra_len
    return box_len.to_bytes(4, "big") + body


# (설명, ext, head bytes)
CASES: list[tuple[str, str, bytes]] = [
    # --- 정상 ---
    ("PDF 정상", ".pdf", b"%PDF-1.7\n" + PAD),
    ("PNG 정상", ".png", bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + PAD),
    ("JPEG 정상", ".jpg", bytes([0xFF, 0xD8, 0xFF, 0xE0]) + PAD),
    ("JPEG as .jpeg", ".jpeg", bytes([0xFF, 0xD8, 0xFF, 0xDB]) + PAD),
    ("ZIP PK0304 as docx", ".docx", b"PK\x03\x04" + PAD),
    ("ZIP PK0506 as hwpx", ".hwpx", b"PK\x05\x06" + PAD),
    ("ZIP PK0708 as pptx", ".pptx", b"PK\x07\x08" + PAD),
    ("HWP OLE2", ".hwp", OLE2 + PAD),
    ("txt 평문", ".txt", "가나다 한글".encode()),
    ("md 평문", ".md", b"# heading\n"),
    ("txt 빈 내용", ".txt", b""),
    # --- 위장·거절 ---
    ("exe as pdf", ".pdf", b"MZ\x90\x00" + PAD),
    ("Mach-O as docx", ".docx", bytes([0xCF, 0xFA, 0xED, 0xFE]) + PAD),
    ("ELF as png", ".png", bytes([0x7F, 0x45, 0x4C, 0x46]) + PAD),
    ("PNG as pdf (교차)", ".pdf", bytes([0x89, 0x50, 0x4E, 0x47]) + PAD),
    ("PDF as png (교차)", ".png", b"%PDF-1.7\n" + PAD),
    ("ZIP as pdf (교차)", ".pdf", b"PK\x03\x04" + PAD),
    ("PDF as docx (교차)", ".docx", b"%PDF-1.7\n" + PAD),
    ("HWP 위장 평문", ".hwp", b"NOT-HWP" + PAD),
    ("빈 head as pdf", ".pdf", b""),
    ("3바이트 as png", ".png", bytes([0x89, 0x50, 0x4E])),
    ("2바이트 as jpg", ".jpg", bytes([0xFF, 0xD8])),
    ("PK 뒤 바이트 틀림", ".docx", b"PK\x03\x09" + PAD),
    # --- HWPML ---
    ("HWPML 정상", ".hwp", b'<?xml version="1.0"?>\n<HWPML Version="2.8">' + PAD),
    ("HWPML BOM", ".hwp", b"\xef\xbb\xbf" + b'<?xml version="1.0"?>\n<HWPML>' + PAD),
    ("HWPML 선행 공백", ".hwp", b"  \n" + b'<?xml version="1.0"?><HWPML>' + PAD),
    ("XML 이지만 다른 루트", ".hwp", b'<?xml version="1.0"?>\n<OTHER>' + PAD),
    ("HWPML 인데 xml 선언 없음", ".hwp", b"<HWPML Version=\"2.8\">" + PAD),
    # --- HEIC (ISO-BMFF) ---
    ("HEIC major=heic", ".heic", ftyp(b"heic") + PAD),
    ("HEIC major=mif1 + heic brand", ".heic", ftyp(b"mif1", b"heic") + PAD),
    ("HEIC major=mif1 brand 없음", ".heic", ftyp(b"mif1") + PAD),
    ("HEIC major=msf1 + heic", ".heic", ftyp(b"msf1", b"heic") + PAD),
    ("HEIC major=avif", ".heic", ftyp(b"avif") + PAD),
    ("ftyp 인데 box_len 이 버퍼보다 큼", ".heic", ftyp(b"heic", extra_len=9999)),
    ("ftyp 아님", ".heic", b"\x00\x00\x00\x18" + b"NOPE" + PAD),
    ("16바이트 미만", ".heic", b"\x00\x00\x00\x18ftyp"),
]

RUNNER_TS = f"""
import {{ InputGateError, validateMagic }} from "file://{SHARED}/documents/input_gate.ts";

const cases = JSON.parse(await Deno.readTextFile(Deno.args[0]));
console.log(JSON.stringify(cases.map(([, ext, hex]: [string, string, string]) => {{
  const head = new Uint8Array((hex.match(/../g) ?? []).map((h: string) => parseInt(h, 16)));
  try {{
    validateMagic(ext, head);
    return {{ ok: true }};
  }} catch (e) {{
    return {{ ok: false, status: e instanceof InputGateError ? e.status : 500 }};
  }}
}})));
"""


def main() -> None:
    from fastapi import HTTPException

    from app.routers._input_gate import validate_magic

    # 실자산도 섞는다 — 합성만 보면 실제 파일 구조를 놓친다.
    assets: list[tuple[str, str, bytes]] = []
    for pat in ["assets/**/*.pdf", "assets/**/*.hwp", "assets/**/*.hwpx",
                "assets/**/*.docx", "assets/**/*.pptx", "assets/**/*.png",
                "assets/**/*.jpg", "assets/**/*.md"]:
        for p in sorted(glob.glob(os.path.join(ROOT, pat), recursive=True))[:1]:
            with open(p, "rb") as f:
                assets.append((f"실자산 {os.path.basename(p)[:20]}",
                               os.path.splitext(p)[1].lower(), f.read(4096)))
    all_cases = CASES + assets

    py = []
    for _label, ext, head in all_cases:
        try:
            validate_magic(ext=ext, raw_head=head)
            py.append({"ok": True})
        except HTTPException as e:
            py.append({"ok": False, "status": e.status_code})

    payload = [[lb, ext, head.hex()] for lb, ext, head in all_cases]
    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=600,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:3000]}")
    ts = json.loads(proc.stdout)

    fails = 0
    bad = []
    for i, (label, ext, _h) in enumerate(all_cases):
        w, g = py[i], ts[i]
        if w["ok"] != g["ok"] or (not w["ok"] and w.get("status") != g.get("status")):
            bad.append((label, ext, w, g))
    if bad:
        fails += 1
        print(f"  **{len(bad)}건 불일치**")
        for label, ext, w, g in bad[:10]:
            print(f"    {label:<30} {ext:<7} py={json.dumps(w)}  ts={json.dumps(g)}")
    else:
        n_pass = sum(1 for v in py if v["ok"])
        print(f"  validateMagic             {len(all_cases)}건 OK  "
              f"(통과 {n_pass} / 거절 {len(py) - n_pass})")

    # 케이스 무효 검사 — 통과·거절이 모두 있어야 의미가 있다.
    n_pass = sum(1 for v in py if v["ok"])
    if n_pass == 0 or n_pass == len(py):
        fails += 1
        print("    **케이스 무효** — 통과/거절 한쪽만 나왔다")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
