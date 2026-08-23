"""Phase 0 / S1 스파이크 — 현행 파서의 기준 텍스트를 JSON 으로 덤프.

WASM 파서(@ohah/hwpjs, @rhwp/core, mammoth 등) 출력과 대조할 정답지를 만든다.
`scripts/monitor_search_slo.py` 의 얇은 wrapper 패턴(_API_ROOT sys.path insert) 계승.

사용:
    cd api && uv run python scripts/spike_hwp_baseline.py

출력:
    scripts/spike_baseline.json   { "<파일명>": {"source_type":..., "chars":..., "text":...} }
    stderr 에 파일별 요약.

주의: 이 스크립트는 Phase 0 이후에도 남긴다 — WASM 파서를 교체·업그레이드할 때마다
회귀 대조에 재사용한다(프로젝트 규칙: 검증 스크립트는 버리지 않는다).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

from app.adapters.impl.docx_parser import DocxParser  # noqa: E402
from app.adapters.impl.hwp_parser import Hwp5Parser  # noqa: E402
from app.adapters.impl.hwpml_parser import HwpmlParser  # noqa: E402
from app.adapters.impl.hwpx_parser import HwpxParser  # noqa: E402
from app.adapters.impl.pymupdf_parser import PyMuPDFParser  # noqa: E402

# 실제 골든 문서들. 합성 fixture 가 아닌 진짜 한국어 공문서라 WASM 파서의 실사용
# 적합성을 판정하는 데 적합하다.
#
# 확장자를 믿지 말 것 (2026-08-24 실측):
#   - `law sample2.hwp` 는 이름만 .hwp 이고 실제로는 UTF-8 BOM XML = HWPML 이다.
#     Hwp5Parser 에 넣으면 "Not an OLE2 Compound Binary File" 로 정상 거부된다.
#   - 진짜 HWP 5.x OLE2(매직 d0cf11e0)는 `assets/public/law_sample1.hwp` 다.
# S1 판정은 반드시 진짜 OLE2 파일로 해야 의미가 있다.
_SAMPLES: list[tuple[str, type]] = [
    ("assets/public/law_sample1.hwp", Hwp5Parser),
    ("law sample2.hwp", HwpmlParser),
    ("직제_규정(2024.4.30.개정).hwpx", HwpxParser),
    ("한마음생활체육관_운영_내규(2024.4.30.개정).hwpx", HwpxParser),
    ("law sample3.pdf", PyMuPDFParser),
    ("승인글 템플릿1.docx", DocxParser),
]

_OUT_PATH = _API_ROOT / "scripts" / "spike_baseline.json"


def main() -> int:
    repo_root = _API_ROOT.parent
    out: dict[str, dict[str, object]] = {}
    missing: list[str] = []
    failed: list[str] = []

    for name, parser_cls in _SAMPLES:
        path = repo_root / name
        if not path.exists():
            print(f"SKIP (파일 없음): {name}", file=sys.stderr)
            missing.append(name)
            continue

        parser = parser_cls()
        try:
            # DocumentParser Protocol: parse(data: bytes, *, file_name: str) -> ExtractionResult
            extraction = parser.parse(path.read_bytes(), file_name=name)
        except Exception as exc:  # noqa: BLE001 — 어떤 파서 실패도 스파이크 결과로 기록.
            print(f"FAIL {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed.append(name)
            continue

        text = extraction.raw_text
        out[name] = {
            "source_type": extraction.source_type,
            "chars": len(text),
            "sections": len(extraction.sections),
            "warnings": list(extraction.warnings),
            "text": text,
        }
        print(
            f"OK   {name}: source_type={extraction.source_type} "
            f"chars={len(text)} sections={len(extraction.sections)} "
            f"warnings={len(extraction.warnings)}",
            file=sys.stderr,
        )

    _OUT_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"\n기준선 저장: {_OUT_PATH} "
        f"(성공 {len(out)} / 없음 {len(missing)} / 실패 {len(failed)})",
        file=sys.stderr,
    )
    # 실패가 있어도 기준선 자체는 저장한다 — 부분 결과로도 대조를 시작할 수 있다.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
