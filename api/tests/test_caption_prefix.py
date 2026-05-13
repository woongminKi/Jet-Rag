"""M2 W-3 (S4-A D6) — vision chunk caption prefix augmentation 단위 테스트.

검증 범위 (senior-planner 명세 §5.2 T1~T15):
- T1~T2  ENV OFF default → 기존 suffix 동작 보존 ({base}\\n\\n[표:...]\\n[그림:...])
- T3~T6  ENV ON → caption 이 base text **앞**에 prefix 로 부착, page 유무로 포맷 분기
- T7     ENV ON + caption whitespace-only → prefix 미부착 (base 그대로)
- T8     ENV ON + 둘 다 None → prefix 미부착 (vision-derived 아닌 chunk 와 동일)
- T9     ENV ON + caption 250자 → 199자 + `…` 잘림
- T10    ENV ON + table+figure 둘 다 set → table 우선 (figure 무시)
- T11    ENV ON + vision-derived 아닌 일반 chunk (둘 다 None) → 무영향
- T12    ENV ON + W-2 동의어 마커 동시 ON → `[표:...]` prefix 와 `[검색어:...]` suffix 공존
- T13    section_title 있는 일반 chunk → 무영향 (section_title 은 컬럼만, text 미포함)
- T14    NFC 정규화 통과 — caption 에 결합문자(NFD) 있어도 최종 text 는 NFC
- T15    chunk_filter table_noise 미오탐 — prefix 부착 chunk → `_classify_chunk` None

stdlib unittest + `patch.dict(os.environ, ...)` ENV 격리. 외부 호출 0 (DB·LLM·HF mock·no-op).
W-2 `test_synonym_inject.py` 스타일 답습.
"""

from __future__ import annotations

import os
import unicodedata
import unittest
from unittest.mock import patch

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from app.adapters.parser import ExtractedSection
from app.ingest.stages.chunk import (
    _compose_vision_text,
    _to_chunk_records,
)

_ENV_CAPTION = "JETRAG_CAPTION_PREFIX_ENABLED"
_ENV_SYNONYM = "JETRAG_SYNONYM_INJECTION_ENABLED"


def _vision_section(
    text: str,
    *,
    page: int | None = 1,
    table_caption: str | None = None,
    figure_caption: str | None = None,
    section_title: str = "(vision) p.1",
) -> ExtractedSection:
    """vision-derived chunk 분기 진입 조건 (section_title `(vision)` prefix) 충족 helper."""
    metadata: dict = {}
    if table_caption is not None:
        metadata["table_caption"] = table_caption
    if figure_caption is not None:
        metadata["figure_caption"] = figure_caption
    return ExtractedSection(
        text=text,
        page=page,
        section_title=section_title,
        bbox=None,
        metadata=metadata,
    )


class ComposeVisionTextTest(unittest.TestCase):
    """`_compose_vision_text` 직접 호출 — ENV 분기 + 포맷 검증."""

    # ---------- T1~T2: ENV OFF default — 기존 suffix 동작 보존 ----------

    def test_t1_env_off_table_caption_page_suffix_preserved(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_ENV_CAPTION, None)
            out = _compose_vision_text(
                "표 내용",
                table_caption="회원 자격",
                figure_caption=None,
                page=5,
            )
        self.assertEqual(out, "표 내용\n\n[표: 회원 자격]")

    def test_t2_env_off_figure_caption_page_suffix_preserved(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_ENV_CAPTION, None)
            out = _compose_vision_text(
                "그림 내용",
                table_caption=None,
                figure_caption="흐름도",
                page=10,
            )
        self.assertEqual(out, "그림 내용\n\n[그림: 흐름도]")

    # ---------- T3~T6: ENV ON — prefix 부착, page 분기 ----------

    def test_t3_env_on_table_caption_page(self) -> None:
        with patch.dict(os.environ, {_ENV_CAPTION: "true"}):
            out = _compose_vision_text(
                "표 내용",
                table_caption="회원 자격",
                figure_caption=None,
                page=5,
            )
        self.assertEqual(out, "[표 p.5: 회원 자격]\n\n표 내용")

    def test_t4_env_on_figure_caption_page(self) -> None:
        with patch.dict(os.environ, {_ENV_CAPTION: "true"}):
            out = _compose_vision_text(
                "그림 내용",
                table_caption=None,
                figure_caption="흐름도",
                page=10,
            )
        self.assertEqual(out, "[그림 p.10: 흐름도]\n\n그림 내용")

    def test_t5_env_on_table_caption_page_none_fallback(self) -> None:
        with patch.dict(os.environ, {_ENV_CAPTION: "true"}):
            out = _compose_vision_text(
                "본문",
                table_caption="요약",
                figure_caption=None,
                page=None,
            )
        self.assertEqual(out, "[표: 요약]\n\n본문")

    def test_t6_env_on_figure_caption_page_none_fallback(self) -> None:
        with patch.dict(os.environ, {_ENV_CAPTION: "true"}):
            out = _compose_vision_text(
                "본문",
                table_caption=None,
                figure_caption="다이어그램",
                page=None,
            )
        self.assertEqual(out, "[그림: 다이어그램]\n\n본문")

    # ---------- T7~T8: ENV ON — 빈/whitespace/None caption ----------

    def test_t7_env_on_whitespace_caption_no_prefix(self) -> None:
        with patch.dict(os.environ, {_ENV_CAPTION: "true"}):
            out = _compose_vision_text(
                "본문",
                table_caption="   ",
                figure_caption=None,
                page=3,
            )
        # whitespace-only → strip 후 빈 → prefix 미부착, base 그대로 (figure 도 None)
        self.assertEqual(out, "본문")

    def test_t8_env_on_both_none_no_prefix(self) -> None:
        with patch.dict(os.environ, {_ENV_CAPTION: "true"}):
            out = _compose_vision_text(
                "본문",
                table_caption=None,
                figure_caption=None,
                page=3,
            )
        self.assertEqual(out, "본문")

    # ---------- T9: 길이 cap ----------

    def test_t9_env_on_caption_over_200_truncated(self) -> None:
        long_caption = "가" * 250
        with patch.dict(os.environ, {_ENV_CAPTION: "true"}):
            out = _compose_vision_text(
                "본문",
                table_caption=long_caption,
                figure_caption=None,
                page=1,
            )
        # 199자 + `…` (200자 합)
        expected_caption = "가" * 199 + "…"
        self.assertEqual(out, f"[표 p.1: {expected_caption}]\n\n본문")
        # caption 부분만 잘렸는지 검증
        self.assertIn(expected_caption, out)
        self.assertNotIn("가" * 200, out)  # 200자 연속은 없음

    # ---------- T10: table 우선 ----------

    def test_t10_env_on_both_table_and_figure_table_wins(self) -> None:
        with patch.dict(os.environ, {_ENV_CAPTION: "true"}):
            out = _compose_vision_text(
                "본문",
                table_caption="표 caption",
                figure_caption="그림 caption",
                page=2,
            )
        self.assertEqual(out, "[표 p.2: 표 caption]\n\n본문")
        # 그림 caption 은 prefix 에 안 들어감
        self.assertNotIn("그림 caption", out)


class ToChunkRecordsCaptionPrefixTest(unittest.TestCase):
    """`_to_chunk_records` 통합 — vision-derived 분기 + 일반 chunk 무영향."""

    # ---------- T11: vision-derived 아닌 일반 chunk → 무영향 ----------

    def test_t11_env_on_non_vision_chunk_unchanged(self) -> None:
        with patch.dict(os.environ, {_ENV_CAPTION: "true"}):
            sec = ExtractedSection(
                text="일반 본문 내용입니다.",
                page=1,
                section_title="섹션 제목",  # `(vision)` prefix 없음 → vision-derived 아님
                bbox=None,
                metadata={},
            )
            records = _to_chunk_records(doc_id="d", sections=[sec])
        # 일반 chunk → caption prefix 적용 X, base 그대로 (NFC)
        self.assertEqual(records[0].text, "일반 본문 내용입니다.")
        self.assertNotIn("[표", records[0].text)
        self.assertNotIn("[그림", records[0].text)

    # ---------- T12: W-2 동의어 마커 공존 ----------

    def test_t12_env_on_with_synonym_marker_coexist(self) -> None:
        # W-3 ON + W-2 ON 동시 — table_caption 에 동의어 사전 키("쏘나타") 포함시켜
        # collect_synonym_candidates 가 후보를 뽑도록 유도.
        with patch.dict(
            os.environ,
            {_ENV_CAPTION: "true", _ENV_SYNONYM: "true"},
        ):
            sec = _vision_section(
                "본문",
                table_caption="쏘나타 회원",
                figure_caption=None,
                page=5,
                section_title="(vision) p.5",
            )
            records = _to_chunk_records(doc_id="d", sections=[sec])
        txt = records[0].text
        # caption prefix 가 가장 앞
        self.assertTrue(txt.startswith("[표 p.5: 쏘나타 회원]"))
        # 동의어 마커는 가장 뒤
        self.assertIn("[검색어:", txt)
        self.assertLess(txt.index("[표 p.5:"), txt.index("[검색어:"))
        # base 본문도 보존
        self.assertIn("본문", txt)

    # ---------- T13: section_title 있는 일반 chunk → 무영향 ----------

    def test_t13_section_title_only_chunk_unchanged(self) -> None:
        with patch.dict(os.environ, {_ENV_CAPTION: "true"}):
            sec = ExtractedSection(
                text="본문 내용",
                page=1,
                section_title="회원 관리 규정",  # vision prefix 없음
                bbox=None,
                metadata={},
            )
            records = _to_chunk_records(doc_id="d", sections=[sec])
        # section_title 은 컬럼만 — text 에 미포함, prefix 미부착
        self.assertEqual(records[0].text, "본문 내용")
        self.assertEqual(records[0].section_title, "회원 관리 규정")

    # ---------- T14: NFC 정규화 ----------

    def test_t14_caption_nfd_normalized_to_nfc(self) -> None:
        # caption 에 NFD 결합문자(자모 분리) 입력 → 최종 chunk text 는 NFC
        nfd_caption = unicodedata.normalize("NFD", "회원 자격")
        # NFD 결합문자가 실제로 들어갔는지 sanity check
        self.assertNotEqual(nfd_caption, "회원 자격")
        with patch.dict(os.environ, {_ENV_CAPTION: "true"}):
            sec = _vision_section(
                "본문",
                table_caption=nfd_caption,
                page=1,
            )
            records = _to_chunk_records(doc_id="d", sections=[sec])
        # _to_chunk_records 가 _compose 출력에 NFC 강제
        self.assertEqual(
            records[0].text,
            unicodedata.normalize("NFC", records[0].text),
        )
        # caption 도 NFC 로 보존됨
        self.assertIn("[표 p.1: 회원 자격]", records[0].text)

    # ---------- T15: chunk_filter table_noise 미오탐 ----------

    def test_t15_prefix_chunk_not_table_noise(self) -> None:
        from app.ingest.stages.chunk_filter import _classify_chunk

        with patch.dict(os.environ, {_ENV_CAPTION: "true"}):
            sec = _vision_section(
                "쏘나타 차량은 한국에서 생산되는 중형 세단으로, 다양한 트림과 옵션을 "
                "제공합니다. 전장과 전폭 정보는 제원표에 명시되어 있으며, 연비와 안전 "
                "사양도 함께 안내됩니다. 본 안내서는 구매 검토에 참고하시기 바랍니다.",
                table_caption="회원 자격 요약",
                page=3,
            )
            records = _to_chunk_records(doc_id="d", sections=[sec])
        rec = records[0]
        self.assertTrue(rec.text.startswith("[표 p.3: 회원 자격 요약]"))
        # prefix 가 부착된 chunk 가 table_noise 로 오분류되지 않음
        self.assertIsNone(_classify_chunk(rec, header_footer_texts=set()))


class CaptionPrefixEnvParsingTest(unittest.TestCase):
    """`_caption_prefix_enabled` truthy 평가 — `true/1/yes/on` 만 허용 (대소문자 무관)."""

    def _compose_with_env(self, env_value: str | None) -> str:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_ENV_CAPTION, None)
            if env_value is not None:
                os.environ[_ENV_CAPTION] = env_value
            return _compose_vision_text(
                "본문",
                table_caption="caption",
                figure_caption=None,
                page=1,
            )

    def test_env_unset_treated_as_off(self) -> None:
        self.assertEqual(self._compose_with_env(None), "본문\n\n[표: caption]")

    def test_env_false_treated_as_off(self) -> None:
        self.assertEqual(self._compose_with_env("false"), "본문\n\n[표: caption]")

    def test_env_true_treated_as_on(self) -> None:
        self.assertEqual(self._compose_with_env("true"), "[표 p.1: caption]\n\n본문")

    def test_env_uppercase_true_treated_as_on(self) -> None:
        self.assertEqual(self._compose_with_env("TRUE"), "[표 p.1: caption]\n\n본문")

    def test_env_one_treated_as_on(self) -> None:
        self.assertEqual(self._compose_with_env("1"), "[표 p.1: caption]\n\n본문")

    def test_env_on_treated_as_on(self) -> None:
        self.assertEqual(self._compose_with_env("on"), "[표 p.1: caption]\n\n본문")

    def test_env_random_treated_as_off(self) -> None:
        self.assertEqual(self._compose_with_env("yeah"), "본문\n\n[표: caption]")


if __name__ == "__main__":
    unittest.main()
