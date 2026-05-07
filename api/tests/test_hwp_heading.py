"""E2 3차 ship — Hwp5Parser 실 자산 회귀 단위 테스트.

검증 범위 (HWP 5.x 의 OLE2 컨테이너)
- `can_parse` 확장자 매칭 (`.hwp`)
- 정상 OLE2 자산에 대한 텍스트 추출 — `raw_text` 비어 있지 않음
- `sections` 분할 결과 ≥ 1건
- 첫 단락 의미 텍스트 한 글자 이상 (CLI/olefile 둘 중 한 경로 성공)

> NOTE: `Hwp5Parser` 는 현재 heading 추출 미구현 — `section_title=None` 으로 두므로
> KPI §13.1 "section_title 채움 비율 ≥ 30%" 는 본 자산에 대해 적용 불가. 후속 구현 시
> `test_pymupdf_heading.py` 와 동일 패턴으로 KPI 케이스 추가 가능.

자산 디렉토리 우선순위 (5단계, `test_pymupdf_heading.py` 와 동일 패턴)
- 1순위: 공개 fixture `<repo>/assets/public/` — 모든 컴퓨터·CI 자동 회귀
- 2순위: `<repo>/assets/` 직속 (사용자 PC raw 자료, `.gitignore` `/assets/*` 로 다른 컴퓨터엔 부재) — 자동 진입
- 3순위: `<repo>/` 루트 직속 (다른 컴퓨터에서 자료가 repo 루트에 있을 때, `.gitignore` `/*.hwp`) — 자동 진입
- 4순위: `JETRAG_TEST_HWP_DIR` ENV 폴백 — 외장 디스크·별 위치 보강용 옵션
- 5단계: 자산 부재 시 자동 skip (CI 호환)

> E2 3차 ship — 한국 저작권법 §7 (대법원 결정문) 비보호 자료 1건 (`law_sample1.hwp`) 을
> `assets/public/` 으로 이동, 모든 컴퓨터·CI 자동 회귀 진입.
> `cosmetic_law_sample.hwp` / `law sample2.hwp` 는 OLE2 컨테이너 아님 — Hwp5Parser 가
> 인제스트 단계에서 거부하므로 fixture 부적합 (private 유지).
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

# 모듈 import 단계에서 환경 변수 요구 회피
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")


# repo root 자동 인식: api/tests/test_*.py → parents[2] = repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PUBLIC_HWP_DIR = _REPO_ROOT / "assets" / "public"

# 공개 fixture (assets/public 안, 모든 컴퓨터·CI 자동 회귀)
_PUBLIC_HWP_FILES = [
    "law_sample1.hwp",  # 대법원 두-2025-34754 결정 (저작권법 §7 비보호)
]

# 비공개 자료 — OLE2 아닌 자료 (`cosmetic_law_sample.hwp`, `law sample2.hwp`) 는
# Hwp5Parser 가 인제스트 단계에서 거부 → 정상 추출 fixture 부적합 (positive set 에서 제외).
# 단, **거부 동작 자체는 회귀 보호 대상** — `Hwp5ParserRejectNonOle2Test` 가 negative path 검증.
_PRIVATE_HWP_FILES: list[str] = []

# 회귀 가능 자산 = 공개 + 비공개 (호출부에서 부재 시 skip)
_HWP_FILES = _PUBLIC_HWP_FILES + _PRIVATE_HWP_FILES

# negative fixture (OLE2 컨테이너 아님) — `Hwp5ParserRejectNonOle2Test` 전용.
# 거부 동작 + 사용자 안내 메시지 회귀 보호. `.gitignore` `/assets/*` 로 다른 컴퓨터에는
# 부재 — 자료 부재 시 자동 skipTest (CI 호환). 5단계 우선순위 그대로 활용.
_NEGATIVE_HWP_FILES = [
    "cosmetic_law_sample.hwp",
    "law sample2.hwp",
]


def _hwp_path(name: str) -> Path:
    """5단계 우선순위로 HWP fixture 경로 해석. 부재 시 부재 path 반환 (호출부 skipTest).

    1) `<repo>/assets/public/<name>` — 공개 fixture, 모든 컴퓨터·CI 자동
    2) `<repo>/assets/<name>` — 사용자 PC raw 자료 (`.gitignore` `/assets/*`)
    3) `<repo>/<name>` — 다른 컴퓨터에서 자료가 repo 루트 직속에 있을 때
       (`.gitignore` `/*.hwp` 로 추적 X — 사용자 자료 노출 방지)
    4) `$JETRAG_TEST_HWP_DIR/<name>` — 외장 디스크·별 위치 보강용 ENV 폴백
    5) 부재 → public path 반환 (exists() False, 호출부 skipTest)
    """
    public = _PUBLIC_HWP_DIR / name
    if public.exists():
        return public

    # assets/ 직속 (사용자 PC raw 자료, .gitignore /assets/* 로 다른 컴퓨터엔 없음)
    assets_direct = _REPO_ROOT / "assets" / name
    if assets_direct.exists():
        return assets_direct

    # repo 루트 직속 (다른 컴퓨터 패턴, .gitignore /*.hwp 로 추적 X)
    repo_root_direct = _REPO_ROOT / name
    if repo_root_direct.exists():
        return repo_root_direct

    # ENV 폴백 — 외장 디스크 등 위 3순위 외 위치
    env_base = os.environ.get("JETRAG_TEST_HWP_DIR")
    if env_base:
        env_path = Path(env_base) / name
        if env_path.exists():
            return env_path

    return public  # exists() False — 호출부에서 skipTest


class Hwp5ParserCanParseTest(unittest.TestCase):
    """`can_parse` 확장자 매칭."""

    def test_hwp_extension_accepted(self) -> None:
        from app.adapters.impl.hwp_parser import Hwp5Parser

        self.assertTrue(Hwp5Parser().can_parse("doc.hwp", None))
        self.assertTrue(Hwp5Parser().can_parse("DOC.HWP", "application/x-hwp"))

    def test_other_extension_rejected(self) -> None:
        from app.adapters.impl.hwp_parser import Hwp5Parser

        self.assertFalse(Hwp5Parser().can_parse("doc.hwpx", None))
        self.assertFalse(Hwp5Parser().can_parse("doc.pdf", None))
        self.assertFalse(Hwp5Parser().can_parse("doc.txt", None))


class Hwp5ParserBadInputTest(unittest.TestCase):
    """오류 입력 처리 — 1차 CLI / 2차 olefile 둘 다 실패하면 RuntimeError."""

    def test_bad_input_raises_runtime_error(self) -> None:
        from app.adapters.impl.hwp_parser import Hwp5Parser

        with self.assertRaises(RuntimeError) as ctx:
            Hwp5Parser().parse(b"not an OLE2 container", file_name="bad.hwp")
        # CLI fail / olefile fallback fail 둘 다 메시지에 포함
        self.assertIn("HWP 추출 실패", str(ctx.exception))


class Hwp5ParserRealAssetTest(unittest.TestCase):
    """실 HWP 자산 텍스트 추출 가능성 회귀. 자산 부재 시 skip."""

    def _parse(self, file_name: str):
        from app.adapters.impl.hwp_parser import Hwp5Parser

        path = _hwp_path(file_name)
        if not path.exists():
            self.skipTest(f"HWP fixture not found: {path}")
        data = path.read_bytes()
        return Hwp5Parser().parse(data, file_name=file_name)

    def test_law_sample1_text_extracted(self) -> None:
        """대법원 결정문 1건이 정상 추출 — raw_text 비어 있지 않음, sections ≥ 1건."""
        result = self._parse("law_sample1.hwp")
        self.assertGreater(len(result.raw_text), 0, "raw_text empty")
        self.assertGreaterEqual(
            len(result.sections), 1, f"sections={len(result.sections)}"
        )
        # 첫 단락은 의미 있는 텍스트 한 글자 이상
        self.assertGreater(
            len(result.sections[0].text.strip()),
            0,
            f"first section empty: {result.sections[0]!r}",
        )


class Hwp5ParserRejectNonOle2Test(unittest.TestCase):
    """OLE2 형식이 아닌 HWP 자산 — `Hwp5Parser` 가 정상적으로 거부 + 한국어 안내 메시지 반환.

    페르소나 A 정합 (사용자 점검, 2026-05-07 E2 5차 ship)
    - 사용자가 잘못된 형식 (OLE2 아닌 HWP) 업로드 시 PDF 또는 HWPX 로 변환 안내를 명확히 받기.
    - backend 의 안내 메시지는 `ingest_jobs.error_msg` 에 그대로 보존 → frontend
      `upload-item.tsx` 의 failed 상태 카드 (`text-destructive` 영역) 에 노출.

    검증 범위
    - `RuntimeError` raise (`Hwp5ParserBadInputTest` 와 동일 예외 클래스)
    - 메시지에 변환 안내 키워드 포함 — `OLE2` 또는 (`PDF` AND `HWPX`)
    - 자료 부재 시 자동 skipTest (private fixture, `.gitignore` `/assets/*`)
    """

    def test_reject_with_korean_conversion_hint(self) -> None:
        from app.adapters.impl.hwp_parser import Hwp5Parser

        parser = Hwp5Parser()
        for name in _NEGATIVE_HWP_FILES:
            with self.subTest(name=name):
                path = _hwp_path(name)
                if not path.exists():
                    self.skipTest(f"비정형 HWP fixture 부재 (private): {name}")
                data = path.read_bytes()

                with self.assertRaises(RuntimeError) as ctx:
                    parser.parse(data, file_name=name)
                msg = str(ctx.exception)
                # 변환 안내 키워드 — OLE2 (원인) 또는 PDF+HWPX (해결책) 둘 중 하나는 노출
                self.assertTrue(
                    ("OLE2" in msg) or ("PDF" in msg and "HWPX" in msg),
                    f"안내 메시지에 변환 가이드 키워드 부재: {msg}",
                )


if __name__ == "__main__":
    unittest.main()
