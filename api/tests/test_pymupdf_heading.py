"""W4 Day 2 — PyMuPDFParser heading 추출 강화 단위 테스트 (W4-Q-17).

검증 범위
- `_page_median_size` / `_block_max_size` / `_block_text` 헬퍼
- `_is_heading_block` 의 (A) font size 비율 / (B) 텍스트 패턴 / 길이 cap
- 실 자산에 대한 KPI §13.1 "PDF 평균 section_title 채움 비율 ≥ 30%" 충족
- sticky propagate — page 경계 넘어 직전 heading 상속
- `get_text("dict")` 실패 시 `get_text("blocks")` fallback graceful degrade

자산 디렉토리 우선순위 (5단계)
- 1순위: 공개 fixture `<repo>/assets/public/` — 모든 컴퓨터·CI 자동 회귀
- 2순위: `<repo>/assets/` 직속 (사용자 PC raw 자료, `.gitignore` `/assets/*` 로 다른 컴퓨터엔 부재) — 자동 진입
- 3순위: `<repo>/` 루트 직속 (다른 컴퓨터에서 자료가 repo 루트에 있을 때, `.gitignore` `/*.pdf` 로 추적 X) — 자동 진입
- 4순위: `JETRAG_TEST_PDF_DIR` ENV 폴백 — 외장 디스크·별 위치 보강용 옵션
- 5단계: 자산 부재 시 자동 skip (CI 호환)
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

# 모듈 import 단계에서 환경 변수 요구 회피 (다른 테스트 파일과 동일 패턴)
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")


# repo root 자동 인식: api/tests/test_*.py → parents[2] = repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PUBLIC_PDF_DIR = _REPO_ROOT / "assets" / "public"

# 공개 fixture (assets/public 안, 모든 컴퓨터·CI 자동 회귀)
_PUBLIC_PDF_FILES = [
    "(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서.pdf",
    "보건의료_빅데이터_플랫폼_시범사업_추진계획(안).pdf",
    # E2 3차 ship — 한국 저작권법 §7 (대법원 판결문 등) 비보호 자료
    "law sample3.pdf",
    "law_sample2.pdf",
    # E2 4차 ship — 사용자 명시 공공데이터 (`assets/public/` 추적 중이나 등록 누락이었음)
    "sample-report.pdf",
]

# 비공개 자료 (사용자 PC `assets/` 직속 또는 repo 루트 직속, `.gitignore` 로 다른 컴퓨터엔 부재)
# 사용자 PC 에서는 자동 회귀 진입 / 다른 컴퓨터·CI 에서는 부재 → 자동 skip
_PRIVATE_PDF_FILES = [
    "sonata-the-edge_catalog.pdf",
    # law sample3 / law_sample2 는 E2 3차 ship 으로 public 이동
]


def _pdf_path(name: str) -> Path:
    """5단계 우선순위로 PDF fixture 경로 해석. 부재 시 부재 path 반환 (호출부 skipTest).

    1) `<repo>/assets/public/<name>` — 공개 fixture, 모든 컴퓨터·CI 자동
    2) `<repo>/assets/<name>` — 사용자 PC raw 자료 (`.gitignore` `/assets/*`)
    3) `<repo>/<name>` — 다른 컴퓨터에서 자료가 repo 루트 직속에 있을 때
       (`.gitignore` `/*.pdf` 로 추적 X — 사용자 자료 노출 방지)
    4) `$JETRAG_TEST_PDF_DIR/<name>` — 외장 디스크·별 위치 보강용 ENV 폴백
    5) 부재 → public path 반환 (exists() False, 호출부 skipTest)
    """
    public = _PUBLIC_PDF_DIR / name
    if public.exists():
        return public

    # assets/ 직속 (사용자 PC raw 자료, .gitignore /assets/* 로 다른 컴퓨터엔 없음)
    assets_direct = _REPO_ROOT / "assets" / name
    if assets_direct.exists():
        return assets_direct

    # repo 루트 직속 (다른 컴퓨터 패턴, .gitignore /*.pdf 로 추적 X)
    repo_root_direct = _REPO_ROOT / name
    if repo_root_direct.exists():
        return repo_root_direct

    # ENV 폴백 — 외장 디스크 등 위 3순위 외 위치
    env_base = os.environ.get("JETRAG_TEST_PDF_DIR")
    if env_base:
        env_path = Path(env_base) / name
        if env_path.exists():
            return env_path

    return public  # exists() False — 호출부에서 skipTest


# 회귀 가능 자산 = 공개 + (ENV 가 있을 때) 비공개 (KPI 평균 산출용)
_PDF_FILES = _PUBLIC_PDF_FILES + _PRIVATE_PDF_FILES


def _make_span(text: str, size: float) -> dict:
    return {"text": text, "size": size, "flags": 0}


def _make_line(spans: list[dict]) -> dict:
    return {"spans": spans}


def _make_block(lines: list[dict], block_type: int = 0) -> dict:
    return {"type": block_type, "lines": lines, "bbox": (0.0, 0.0, 100.0, 20.0)}


class PageMedianSizeTest(unittest.TestCase):
    """`_page_median_size` 본문 폰트 중앙값 계산."""

    def test_basic_returns_median(self) -> None:
        from app.adapters.impl.pymupdf_parser import _page_median_size

        # 본문 10pt 5건 + heading 12pt 1건 → median 10.0
        page_dict = {
            "blocks": [
                _make_block([_make_line([_make_span("body", 10.0)])]),
                _make_block([_make_line([_make_span("body", 10.0)])]),
                _make_block([_make_line([_make_span("body", 10.0)])]),
                _make_block([_make_line([_make_span("body", 10.0)])]),
                _make_block([_make_line([_make_span("body", 10.0)])]),
                _make_block([_make_line([_make_span("HEAD", 12.0)])]),
            ]
        }
        self.assertEqual(_page_median_size(page_dict), 10.0)

    def test_empty_returns_zero(self) -> None:
        from app.adapters.impl.pymupdf_parser import _page_median_size

        self.assertEqual(_page_median_size({"blocks": []}), 0.0)
        self.assertEqual(_page_median_size({}), 0.0)

    def test_outlier_robust(self) -> None:
        """대형 표지 폰트 한 글자 (60pt) 가 평균을 끌어올려도 median 은 본문 유지."""
        from app.adapters.impl.pymupdf_parser import _page_median_size

        page_dict = {
            "blocks": [
                _make_block([_make_line([_make_span("body", 9.0)])]),
                _make_block([_make_line([_make_span("body", 9.0)])]),
                _make_block([_make_line([_make_span("body", 9.0)])]),
                _make_block([_make_line([_make_span("COVER", 60.0)])]),
            ]
        }
        # median([9, 9, 9, 60]) = 9.0 (정확히는 (9+9)/2)
        self.assertEqual(_page_median_size(page_dict), 9.0)

    def test_image_blocks_excluded(self) -> None:
        from app.adapters.impl.pymupdf_parser import _page_median_size

        page_dict = {
            "blocks": [
                _make_block([_make_line([_make_span("body", 10.0)])]),
                _make_block([], block_type=1),  # image block — 무시
            ]
        }
        self.assertEqual(_page_median_size(page_dict), 10.0)


class BlockMaxSizeTest(unittest.TestCase):
    """`_block_max_size` 블록 내 max span size."""

    def test_basic_returns_max(self) -> None:
        from app.adapters.impl.pymupdf_parser import _block_max_size

        block = _make_block(
            [_make_line([_make_span("a", 9.0), _make_span("b", 11.0)])]
        )
        self.assertEqual(_block_max_size(block), 11.0)

    def test_empty_block_returns_zero(self) -> None:
        from app.adapters.impl.pymupdf_parser import _block_max_size

        self.assertEqual(_block_max_size(_make_block([])), 0.0)

    def test_multi_line_max(self) -> None:
        from app.adapters.impl.pymupdf_parser import _block_max_size

        block = _make_block(
            [
                _make_line([_make_span("line1", 10.0)]),
                _make_line([_make_span("line2", 21.0)]),
            ]
        )
        self.assertEqual(_block_max_size(block), 21.0)


class BlockTextTest(unittest.TestCase):
    """`_block_text` line break 보존."""

    def test_joins_lines_with_newline(self) -> None:
        from app.adapters.impl.pymupdf_parser import _block_text

        block = _make_block(
            [
                _make_line([_make_span("Hello ", 10.0), _make_span("World", 10.0)]),
                _make_line([_make_span("Line2", 10.0)]),
            ]
        )
        self.assertEqual(_block_text(block), "Hello World\nLine2")

    def test_empty_block_returns_empty(self) -> None:
        from app.adapters.impl.pymupdf_parser import _block_text

        self.assertEqual(_block_text(_make_block([])), "")


class IsHeadingBlockTest(unittest.TestCase):
    """`_is_heading_block` (A) font size + (B) 텍스트 패턴."""

    def test_size_ratio_hit(self) -> None:
        from app.adapters.impl.pymupdf_parser import _is_heading_block

        # 12 / 10 = 1.20 ≥ 1.15 → True
        self.assertTrue(_is_heading_block(12.0, 10.0, "본문 텍스트"))

    def test_size_ratio_miss(self) -> None:
        from app.adapters.impl.pymupdf_parser import _is_heading_block

        # 10.5 / 10 = 1.05 < 1.15 → False (텍스트 패턴 매칭도 실패)
        self.assertFalse(_is_heading_block(10.5, 10.0, "일반 본문"))

    def test_text_pattern_hit_jo(self) -> None:
        from app.adapters.impl.pymupdf_parser import _is_heading_block

        # size 휴리스틱 fail (1.0 ratio) + 텍스트 패턴 hit
        self.assertTrue(_is_heading_block(10.0, 10.0, "제3조(목적)"))

    def test_text_pattern_hit_korean_brackets(self) -> None:
        """한국 법률 PDF 의 `【판시사항】` 패턴."""
        from app.adapters.impl.pymupdf_parser import _is_heading_block

        self.assertTrue(_is_heading_block(10.0, 10.0, "【판시사항】"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "【판결요지】"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "[참조조문]"))

    def test_text_pattern_too_long_blocked(self) -> None:
        """긴 본문이 prefix outline 패턴이면 false positive 차단."""
        from app.adapters.impl.pymupdf_parser import _is_heading_block

        long_text = "제1조(목적) " + "x" * 200
        # size 휴리스틱도 fail + 텍스트는 길이 cap 초과
        self.assertFalse(_is_heading_block(10.0, 10.0, long_text))

    def test_zero_page_median_skips_size_heuristic(self) -> None:
        """page_median=0 (본문 추출 실패) 이면 size 휴리스틱 skip, 텍스트 패턴만."""
        from app.adapters.impl.pymupdf_parser import _is_heading_block

        # size 휴리스틱 skip → 텍스트 패턴 match → True
        self.assertTrue(_is_heading_block(20.0, 0.0, "제1조"))
        # size skip + 텍스트 패턴 miss → False
        self.assertFalse(_is_heading_block(20.0, 0.0, "일반 본문 텍스트"))

    def test_neither_size_nor_text(self) -> None:
        from app.adapters.impl.pymupdf_parser import _is_heading_block

        self.assertFalse(_is_heading_block(10.0, 10.0, "본문 한 줄"))
        self.assertFalse(_is_heading_block(0.0, 0.0, ""))

    # 2026-05-15 권고 5 — 영어 학술 PDF heading 보강

    def test_text_pattern_hit_english_numbered(self) -> None:
        """영어 학술 numbered section heading — `1. Introduction`, `2.1 Method`, `3.4.1 …`."""
        from app.adapters.impl.pymupdf_parser import _is_heading_block

        # size 휴리스틱 fail (1.0 ratio) + 텍스트 패턴 hit
        self.assertTrue(_is_heading_block(10.0, 10.0, "1. Introduction"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "2.1 Related Work"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "3.4.1 Methodology"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "10. Conclusions"))
        # 점 없는 변형 — `1 Introduction` (학술 일부 저널)
        self.assertTrue(_is_heading_block(10.0, 10.0, "1 Introduction"))

    def test_text_pattern_hit_english_standalone(self) -> None:
        """영어 학술 표준 단독 단어 — `Abstract`, `References`, `Related Work`, `Appendix A`."""
        from app.adapters.impl.pymupdf_parser import _is_heading_block

        self.assertTrue(_is_heading_block(10.0, 10.0, "Abstract"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "Introduction"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "References"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "Related Work"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "Appendix A"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "Acknowledgments"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "Discussion"))

    def test_arxiv_header_blocked_even_with_large_font(self) -> None:
        """arXiv-style page header 는 font ratio 가 커도 heading 에서 차단.

        실제 자산 (`bc7b4591` doc, 749 chunk 중 94.5% 가 이 패턴으로 잘못 잡힘)
        를 정규식만으로 회복 — 본 변경의 핵심 검증.
        """
        from app.adapters.impl.pymupdf_parser import _is_heading_block

        # font ratio 2.0 (= heading 후보) + 블랙리스트 hit → False 우선
        self.assertFalse(
            _is_heading_block(20.0, 10.0, "arXiv:2601.00442v1 [hep-th] 1 Jan 2026")
        )
        self.assertFalse(
            _is_heading_block(20.0, 10.0, "arXiv:2401.12345v2 [cs.CL] 5 Feb 2024")
        )
        # 짧은 변형 — 카테고리·날짜 없음
        self.assertFalse(_is_heading_block(20.0, 10.0, "arXiv:2601.00442v1"))
        # 대소문자 변형 (IGNORECASE 동작 확인)
        self.assertFalse(
            _is_heading_block(20.0, 10.0, "ARXIV:2601.00442V1 [HEP-TH] 1 JAN 2026")
        )

    def test_page_number_only_blocked(self) -> None:
        """순수 페이지 번호는 font ratio 가 커도 heading 에서 차단."""
        from app.adapters.impl.pymupdf_parser import _is_heading_block

        # font ratio 2.0 hit + 블랙리스트 hit → False
        self.assertFalse(_is_heading_block(20.0, 10.0, "1"))
        self.assertFalse(_is_heading_block(20.0, 10.0, "12"))
        self.assertFalse(_is_heading_block(20.0, 10.0, "Page 3"))
        self.assertFalse(_is_heading_block(20.0, 10.0, "- 4 -"))

    def test_korean_patterns_no_regression(self) -> None:
        """기존 한국어 + 영문 키워드 패턴은 회귀 없이 그대로 동작."""
        from app.adapters.impl.pymupdf_parser import _is_heading_block

        # 한국어 조문 (size 휴리스틱 fail 시에도 텍스트 패턴으로 hit)
        self.assertTrue(_is_heading_block(10.0, 10.0, "제3조(목적)"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "제 12 조 (정의)"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "부칙"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "별표 2"))
        # 한국 법률 PDF 의 corner bracket
        self.assertTrue(_is_heading_block(10.0, 10.0, "【판시사항】"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "[참조조문]"))
        # 영문 Chapter/Section (기존)
        self.assertTrue(_is_heading_block(10.0, 10.0, "Chapter 1"))
        self.assertTrue(_is_heading_block(10.0, 10.0, "Section 2.1"))


class PageHeaderBlacklistTest(unittest.TestCase):
    """2026-05-15 권고 5 — `_PAGE_HEADER_BLACKLIST` 패턴 단독 검증.

    arXiv 영어 학술 PDF 의 page header 가 heading 으로 잡혀 section_title 94.5%
    를 오염시키던 케이스 (`bc7b4591` doc) 회복. font size 휴리스틱과 독립적으로
    텍스트 자체만으로 차단 가능해야 함.
    """

    def test_arxiv_variations_match(self) -> None:
        from app.adapters.impl.pymupdf_parser import _PAGE_HEADER_BLACKLIST

        cases = [
            "arXiv:2601.00442v1 [hep-th] 1 Jan 2026",
            "arXiv:2401.12345v2 [cs.CL] 5 Feb 2024",
            "arXiv:2601.00442v1",
            "arXiv: 2601.00442v1 [math.PR]",
        ]
        for s in cases:
            self.assertIsNotNone(
                _PAGE_HEADER_BLACKLIST.match(s),
                f"arXiv 패턴 unmatched: {s!r}",
            )

    def test_page_number_variations_match(self) -> None:
        from app.adapters.impl.pymupdf_parser import _PAGE_HEADER_BLACKLIST

        for s in ("1", "12", "1234", "Page 3", "page 12", "- 4 -", "-  9  -"):
            self.assertIsNotNone(
                _PAGE_HEADER_BLACKLIST.match(s),
                f"페이지 번호 패턴 unmatched: {s!r}",
            )

    def test_normal_body_text_does_not_match(self) -> None:
        """본문/한국어/영문 heading 후보는 블랙리스트에 걸리지 않음."""
        from app.adapters.impl.pymupdf_parser import _PAGE_HEADER_BLACKLIST

        cases = [
            "1. Introduction",  # numbered heading — 차단되면 안 됨
            "2.1 Related Work",
            "Abstract",
            "제3조(목적)",
            "【판시사항】",
            "본문 한 줄입니다",
            "12345678901",  # 5자리 초과 — 페이지 번호로 보기엔 너무 김
        ]
        for s in cases:
            self.assertIsNone(
                _PAGE_HEADER_BLACKLIST.match(s),
                f"정상 텍스트가 잘못 블랙리스트 매치: {s!r}",
            )


class PyMuPDFParserBadInputTest(unittest.TestCase):
    """오류 입력 처리."""

    def test_bad_pdf_raises_runtime_error(self) -> None:
        from app.adapters.impl.pymupdf_parser import PyMuPDFParser

        with self.assertRaises(RuntimeError) as ctx:
            PyMuPDFParser().parse(b"not a pdf", file_name="bad.pdf")
        self.assertIn("PDF 열기 실패", str(ctx.exception))


class PyMuPDFParserDictFallbackTest(unittest.TestCase):
    """`get_text("dict")` 실패 시 `get_text("blocks")` fallback."""

    def test_dict_failure_falls_back_to_blocks(self) -> None:
        from app.adapters.impl.pymupdf_parser import PyMuPDFParser

        # 실 자산 1건으로 fallback 동작 확인 — dict 호출만 실패시키고 blocks 는 정상
        path = _pdf_path(_PDF_FILES[0])
        if not path.exists():
            self.skipTest(f"PDF fixture not found: {path}")
        data = path.read_bytes()

        with patch(
            "app.adapters.impl.pymupdf_parser._get_page_dict",
            side_effect=RuntimeError("forced dict failure"),
        ):
            result = PyMuPDFParser().parse(data, file_name=_PDF_FILES[0])

        # fallback 경로로도 sections 가 추출되어야 함
        self.assertGreater(len(result.sections), 0)
        # 모든 페이지에 대해 fallback warning 누적
        self.assertTrue(
            any("blocks fallback" in w for w in result.warnings),
            f"expected fallback warning, got: {result.warnings[:3]}",
        )


class PyMuPDFParserRealAssetKpiTest(unittest.TestCase):
    """실 PDF 자산의 평균 section_title 채움 비율 ≥ 30% (KPI §13.1).

    공개 fixture 2건은 항상 검사. 비공개 2건은 사용자 PC `assets/` 직속에 보유 시 자동 진입,
    부재 시 (다른 컴퓨터·CI) 자동 skip.
    """

    def _parse_one(self, file_name: str):
        from app.adapters.impl.pymupdf_parser import PyMuPDFParser

        path = _pdf_path(file_name)
        if not path.exists():
            return None
        data = path.read_bytes()
        return PyMuPDFParser().parse(data, file_name=file_name)

    def test_average_fill_ratio_meets_kpi(self) -> None:
        """존재하는 자산의 평균 채움 비율 ≥ 30%."""
        ratios: list[tuple[str, float, int, int]] = []
        for name in _PDF_FILES:
            result = self._parse_one(name)
            if result is None:
                continue
            total = len(result.sections)
            if total == 0:
                continue
            filled = sum(1 for s in result.sections if s.section_title)
            ratios.append((name, filled / total, filled, total))

        if not ratios:
            self.skipTest("no PDF fixtures available")

        avg = sum(r[1] for r in ratios) / len(ratios)
        # 진단용 출력 (실패 시 어느 자산이 약한지 즉시 식별)
        report = "\n".join(
            f"  {name}: {ratio:.1%} ({filled}/{total})"
            for name, ratio, filled, total in ratios
        )
        self.assertGreaterEqual(
            avg,
            0.30,
            f"평균 section_title 채움 비율 {avg:.1%} < 30%\n{report}",
        )


class PyMuPDFParserStickyPropagateTest(unittest.TestCase):
    """heading sticky propagate — page 경계 넘어 직전 title 상속."""

    def test_sticky_after_heading_within_doc(self) -> None:
        """heading 단락 직후 본문 단락이 그 heading text 를 section_title 로 상속."""
        from app.adapters.impl.pymupdf_parser import (
            PyMuPDFParser,
            _HEADING_TEXT_PATTERN,
        )

        # 텍스트 패턴 heading 이 분명히 잡히는 자산 — law sample3 의 `【판시사항】`
        path = _pdf_path("law sample3.pdf")
        if not path.exists():
            self.skipTest(f"PDF fixture not found: {path}")
        data = path.read_bytes()
        result = PyMuPDFParser().parse(data, file_name="law sample3.pdf")

        # heading 직후의 다음 단락이 section_title 을 갖는지 한 건 검증
        for i, sec in enumerate(result.sections[:-1]):
            if (
                len(sec.text) <= 80
                and _HEADING_TEXT_PATTERN.match(sec.text)
                and sec.section_title == sec.text
            ):
                nxt = result.sections[i + 1]
                self.assertIsNotNone(
                    nxt.section_title,
                    f"sticky propagate 실패: idx={i + 1} title=None",
                )
                return
        self.fail("text-pattern heading 단락을 찾지 못함 — 자산 가정과 다름")


if __name__ == "__main__":
    unittest.main()
