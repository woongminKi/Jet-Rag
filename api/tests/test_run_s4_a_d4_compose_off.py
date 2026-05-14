"""S4-A D4 — `evals/run_s4_a_d4_compose_off.py` 단위 테스트.

검증 범위
- ``golden_v2.csv`` 14 컬럼 schema + NFC + doc_type 5종 + caption_dependent bool
- qtype 9종 화이트리스트 groupby
- caption_dependent (true/false) subset 분리
- prompt_version hook — vision_page_cache mock 주입 후 v1/v2 라벨링

의존성 주입 — cache_rows mock 으로 Supabase 호출 0, search() 호출 0.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


def _make_cell(
    *,
    golden_id: str,
    query_type: str = "exact_fact",
    doc_id: str = "doc-A",
    caption_dependent: bool = False,
    recall_at_10: float | None = 0.8,
    recall_at_5: float | None = 0.7,
    mrr: float | None = 0.5,
    note: str = "",
):
    """CellResult 생성 헬퍼."""
    from run_s4_a_d4_compose_off import CellResult

    return CellResult(
        golden_id=golden_id,
        query_type=query_type,
        doc_id=doc_id,
        caption_dependent=caption_dependent,
        doc_type="pdf",
        recall_at_10=recall_at_10,
        recall_at_5=recall_at_5,
        mrr=mrr,
        note=note,
    )


def _make_golden(
    *,
    qid: str,
    doc_id: str = "doc-A",
    query_type: str = "exact_fact",
    caption_dependent: bool = False,
):
    """GoldenRow 생성 헬퍼."""
    from run_s4_a_d4_compose_off import GoldenRow

    return GoldenRow(
        id=qid,
        query="test query",
        query_type=query_type,
        doc_id=doc_id,
        expected_doc_title="test doc",
        relevant_chunks=(1, 2, 3),
        acceptable_chunks=(),
        doc_type="pdf",
        caption_dependent=caption_dependent,
    )


class GoldenV2SchemaTests(unittest.TestCase):
    """golden_v2.csv 의 schema 검증 — 의뢰서 §1 요구 사항."""

    @classmethod
    def setUpClass(cls):
        cls.golden_v2_path = (
            Path(__file__).resolve().parents[2] / "evals" / "golden_v2.csv"
        )

    def test_validate_golden_v2_schema_returns_expected_columns(self):
        from run_s4_a_d4_compose_off import validate_golden_v2_schema

        meta = validate_golden_v2_schema(self.golden_v2_path)
        # Phase 3 (cross_doc n=4→8) + origin/main 누적 보강 + safety branch G-A-124~128 append → 183 row
        # → M0-b (2026-05-13) golden 라벨 재검수: G-U-027 broken row 제거 → 182 row
        # → P4 (2026-05-14): hard-deleted 4 doc (승인글 템플릿1/3·sonata·포트폴리오) 참조 row 47건 제거 → 135 row
        self.assertEqual(meta["n_rows"], 135, "golden_v2 는 135 row (P4 에서 47 row 제거)")
        expected = {
            "id",
            "query",
            "query_type",
            "doc_id",
            "expected_doc_title",
            "relevant_chunks",
            "acceptable_chunks",
            "source_chunk_text",
            "expected_answer_summary",
            "must_include",
            "source_hint",
            "negative",
            "doc_type",
            "caption_dependent",
        }
        self.assertEqual(set(meta["columns"]), expected)

    def test_doc_type_5_categories(self):
        """doc_type 5종 — pdf / hwpx / hwp / pptx / docx (+ empty 일부)."""
        from run_s4_a_d4_compose_off import validate_golden_v2_schema

        meta = validate_golden_v2_schema(self.golden_v2_path)
        dt = meta["doc_type_counts"]
        self.assertIn("pdf", dt)
        self.assertIn("hwpx", dt)
        self.assertIn("hwp", dt)
        self.assertIn("pptx", dt)
        self.assertIn("docx", dt)
        # 5 known + (empty) <=6 keys
        known = {"pdf", "hwpx", "hwp", "pptx", "docx", "(empty)"}
        self.assertTrue(set(dt).issubset(known), f"예상 외 doc_type: {set(dt) - known}")

    def test_caption_dependent_25_true_110_false(self):
        """caption_dependent — true 25 / false 110 (P4 에서 hard-deleted doc 참조 row 47건 제거: caption true 31→25, false 151→110)."""
        from run_s4_a_d4_compose_off import validate_golden_v2_schema

        meta = validate_golden_v2_schema(self.golden_v2_path)
        self.assertEqual(meta["caption_counts"].get("true", 0), 25)
        self.assertEqual(meta["caption_counts"].get("false", 0), 110)

    def test_qtype_9_categories_present(self):
        """query_type 9종 모두 존재 — exact_fact / cross_doc / vision_diagram /
        synonym_mismatch / fuzzy_memory / summary / numeric_lookup / table_lookup /
        out_of_scope."""
        from run_s4_a_d4_compose_off import (
            _QTYPE_ORDER,
            validate_golden_v2_schema,
        )

        meta = validate_golden_v2_schema(self.golden_v2_path)
        for qt in _QTYPE_ORDER:
            self.assertIn(qt, meta["qtype_counts"], f"missing qtype: {qt}")

    def test_load_golden_rows_parses_caption_bool(self):
        """load_golden_rows 가 caption_dependent 컬럼을 Python bool 로 파싱."""
        from run_s4_a_d4_compose_off import load_golden_rows

        rows = load_golden_rows(self.golden_v2_path)
        # Phase 3 + origin/main 누적 + safety cross_doc 5 append → 183 row
        # → M0-b (2026-05-13): G-U-027 broken row 제거 → 182 row (caption false 152→151)
        # → P4 (2026-05-14): hard-deleted 4 doc 참조 row 47건 제거 → 135 row (caption true 31→25, false 151→110)
        self.assertEqual(len(rows), 135)
        n_caption_true = sum(1 for r in rows if r.caption_dependent)
        n_caption_false = sum(1 for r in rows if not r.caption_dependent)
        self.assertEqual(n_caption_true, 25)
        self.assertEqual(n_caption_false, 110)

    def test_load_golden_rows_preserves_nfc(self):
        """한국어 query 가 NFC 형식 유지 (BOM 제거 utf-8-sig)."""
        import unicodedata

        from run_s4_a_d4_compose_off import load_golden_rows

        rows = load_golden_rows(self.golden_v2_path)
        for r in rows[:5]:
            self.assertEqual(
                r.query, unicodedata.normalize("NFC", r.query), f"NFC 불일치: {r.id}"
            )


class QtypeBreakdownTests(unittest.TestCase):
    """qtype 9종 groupby 로직 — 의뢰서 §1 축 (1)."""

    def test_group_by_qtype_returns_all_9_keys(self):
        from run_s4_a_d4_compose_off import _QTYPE_ORDER, group_by_qtype

        cells = [
            _make_cell(golden_id="g1", query_type="exact_fact"),
            _make_cell(golden_id="g2", query_type="cross_doc"),
            _make_cell(golden_id="g3", query_type="vision_diagram"),
        ]
        groups = group_by_qtype(cells)
        # 9 키 모두 (빈 그룹 포함) — 출력 일관성
        self.assertEqual(set(groups), set(_QTYPE_ORDER))
        self.assertEqual(len(groups["exact_fact"]), 1)
        self.assertEqual(len(groups["cross_doc"]), 1)
        self.assertEqual(len(groups["vision_diagram"]), 1)
        self.assertEqual(len(groups["summary"]), 0)

    def test_group_by_qtype_skips_unknown_qtype(self):
        """화이트리스트 외 query_type (legacy v1 값 등) 는 9 키 어디에도 들어가지 않는다."""
        from run_s4_a_d4_compose_off import group_by_qtype

        cells = [
            _make_cell(golden_id="g1", query_type="exact_fact"),
            _make_cell(golden_id="g_legacy", query_type="legacy_unknown"),
        ]
        groups = group_by_qtype(cells)
        total = sum(len(v) for v in groups.values())
        self.assertEqual(total, 1, "legacy 값은 그룹화 대상 외")


class CaptionDependentSubsetTests(unittest.TestCase):
    """caption_dependent (true/false) subset 분리 — 의뢰서 §1 축 (2)."""

    def test_group_by_caption_true_false_split(self):
        from run_s4_a_d4_compose_off import group_by_caption

        cells = [
            _make_cell(golden_id="g1", caption_dependent=True),
            _make_cell(golden_id="g2", caption_dependent=True),
            _make_cell(golden_id="g3", caption_dependent=False),
            _make_cell(golden_id="g4", caption_dependent=False),
            _make_cell(golden_id="g5", caption_dependent=False),
        ]
        groups = group_by_caption(cells)
        self.assertEqual(len(groups["true"]), 2)
        self.assertEqual(len(groups["false"]), 3)

    def test_aggregate_caption_delta_correctness(self):
        """true 평균 R@10 - false 평균 R@10 delta 계산 정확."""
        from run_s4_a_d4_compose_off import _aggregate_group, group_by_caption

        cells = [
            _make_cell(golden_id="g1", caption_dependent=True, recall_at_10=0.9),
            _make_cell(golden_id="g2", caption_dependent=True, recall_at_10=0.7),
            _make_cell(golden_id="g3", caption_dependent=False, recall_at_10=0.5),
            _make_cell(golden_id="g4", caption_dependent=False, recall_at_10=0.5),
        ]
        groups = group_by_caption(cells)
        s_true = _aggregate_group("true", groups["true"])
        s_false = _aggregate_group("false", groups["false"])
        self.assertAlmostEqual(s_true.avg_recall_at_10, 0.8, places=4)
        self.assertAlmostEqual(s_false.avg_recall_at_10, 0.5, places=4)


class PromptVersionHookTests(unittest.TestCase):
    """prompt_version (v1/v2) hook — 의뢰서 §1 축 (3).

    vision_page_cache mock 주입으로 외부 DB 호출 0.
    """

    def test_label_prompt_version_v1_majority(self):
        """doc-A 가 v1 majority 면 cell.prompt_version='v1' 라벨."""
        from run_s4_a_d4_compose_off import label_prompt_version

        cells = [_make_cell(golden_id="g1", doc_id="doc-A")]
        rows = [_make_golden(qid="g1", doc_id="doc-A")]
        cache_rows = [
            {"doc_id": "doc-A", "prompt_version": "v1"},
            {"doc_id": "doc-A", "prompt_version": "v1"},
            {"doc_id": "doc-A", "prompt_version": "v1"},
        ]
        counts = label_prompt_version(cells, rows, cache_rows=cache_rows)
        self.assertEqual(cells[0].prompt_version, "v1")
        self.assertEqual(counts["v1"], 1)
        self.assertEqual(counts["v2"], 0)
        self.assertEqual(counts["unlabeled"], 0)

    def test_label_prompt_version_v2_when_only_v2_rows(self):
        """vision_page_cache 가 v2 만 있는 경우 v2 라벨."""
        from run_s4_a_d4_compose_off import label_prompt_version

        cells = [_make_cell(golden_id="g1", doc_id="doc-B")]
        rows = [_make_golden(qid="g1", doc_id="doc-B")]
        cache_rows = [
            {"doc_id": "doc-B", "prompt_version": "v2"},
            {"doc_id": "doc-B", "prompt_version": "v2"},
        ]
        counts = label_prompt_version(cells, rows, cache_rows=cache_rows)
        self.assertEqual(cells[0].prompt_version, "v2")
        self.assertEqual(counts["v2"], 1)

    def test_label_prompt_version_unlabeled_when_no_cache(self):
        """vision_page_cache 가 비어있으면 unlabeled (D4 시점 reingest 전 상태)."""
        from run_s4_a_d4_compose_off import label_prompt_version

        cells = [_make_cell(golden_id="g1", doc_id="doc-A")]
        rows = [_make_golden(qid="g1", doc_id="doc-A")]
        counts = label_prompt_version(cells, rows, cache_rows=[])
        self.assertIsNone(cells[0].prompt_version)
        self.assertEqual(counts["unlabeled"], 1)
        self.assertEqual(counts["v1"], 0)
        self.assertEqual(counts["v2"], 0)

    def test_label_prompt_version_skips_row_without_doc_id(self):
        """doc_id 없는 row (U-row) 는 unlabeled."""
        from run_s4_a_d4_compose_off import label_prompt_version

        cells = [_make_cell(golden_id="g_u", doc_id="")]
        rows = [_make_golden(qid="g_u", doc_id="")]
        cache_rows = [{"doc_id": "doc-A", "prompt_version": "v1"}]
        counts = label_prompt_version(cells, rows, cache_rows=cache_rows)
        self.assertIsNone(cells[0].prompt_version)
        self.assertEqual(counts["unlabeled"], 1)

    def test_group_by_prompt_version_distributes_keys(self):
        """group_by_prompt_version 이 v1/v2/unlabeled 3 키로 정확히 분배."""
        from run_s4_a_d4_compose_off import group_by_prompt_version

        c1 = _make_cell(golden_id="g1")
        c1.prompt_version = "v1"
        c2 = _make_cell(golden_id="g2")
        c2.prompt_version = "v2"
        c3 = _make_cell(golden_id="g3")
        c3.prompt_version = None  # unlabeled
        groups = group_by_prompt_version([c1, c2, c3])
        self.assertEqual(len(groups["v1"]), 1)
        self.assertEqual(len(groups["v2"]), 1)
        self.assertEqual(len(groups["unlabeled"]), 1)


class CaptionComposeStripTests(unittest.TestCase):
    """Phase 1 option C — caption 합성 suffix regex 제거 헬퍼.

    검증 대상: ``_strip_caption_compose(text) -> base_text``.
    합성 포맷 (api/app/ingest/stages/chunk.py::_compose_vision_text):
        base + "\\n\\n" + ("[표: X]\\n[그림: Y]" 또는 그 sub).
    """

    def test_strip_both_table_and_figure_compose(self) -> None:
        """`[표: X]\\n[그림: Y]` 둘 다 합성된 경우 base 만 남는다."""
        from run_s4_a_d4_compose_off import _strip_caption_compose

        text = "본문 내용입니다.\n\n[표: 가격표 트림별]\n[그림: 차량 외관 도식]"
        base = _strip_caption_compose(text)
        self.assertEqual(base, "본문 내용입니다.")

    def test_strip_table_only_compose(self) -> None:
        """`[표: X]` 만 합성된 경우 base 만 남는다."""
        from run_s4_a_d4_compose_off import _strip_caption_compose

        text = "본문 내용입니다.\n\n[표: 트림별 사양표]"
        base = _strip_caption_compose(text)
        self.assertEqual(base, "본문 내용입니다.")

    def test_strip_figure_only_compose(self) -> None:
        """`[그림: Y]` 만 합성된 경우 base 만 남는다 (table 없이 figure)."""
        from run_s4_a_d4_compose_off import _strip_caption_compose

        text = "본문 내용입니다.\n\n[그림: 조직도]"
        base = _strip_caption_compose(text)
        self.assertEqual(base, "본문 내용입니다.")

    def test_strip_no_compose_returns_text_unchanged(self) -> None:
        """합성 없는 chunk 는 원문 그대로 — idempotent."""
        from run_s4_a_d4_compose_off import _strip_caption_compose

        text = "합성 없는 일반 chunk text. 빈줄도 있고\n중간 줄바꿈도 있다."
        self.assertEqual(_strip_caption_compose(text), text)

    def test_strip_handles_empty_string(self) -> None:
        """빈 문자열 → 빈 문자열."""
        from run_s4_a_d4_compose_off import _strip_caption_compose

        self.assertEqual(_strip_caption_compose(""), "")


class RerankWithBaseTextTests(unittest.TestCase):
    """Phase 1 option C — base_text 재임베딩 + cosine 재정렬 헬퍼."""

    def test_rerank_caption_chunk_climbs_when_base_text_closer(self) -> None:
        """합성 chunk 의 base_text 가 query 와 더 가까우면 ranking 상승.

        시나리오 — query_vec=[1,0,0]:
        - chunk A (caption, rrf=0.3): base_text "A" → vec [1,0,0] (cosine=1.0)
        - chunk B (no caption, rrf=0.6): rrf_score 0.6 그대로
        - chunk C (caption, rrf=0.2): base_text "C" → vec [0,1,0] (cosine=0.0)

        compose ON 기준 ranking: B(0.6) > A(0.3) > C(0.2).
        compose OFF (option C) 기준: A(cosine=1.0) > B(rrf=0.6) > C(cosine=0.0).
        """
        from run_s4_a_d4_compose_off import _rerank_with_base_text

        matched = [
            {
                "chunk_idx": 1,
                "text": "A base\n\n[표: 표 A]",
                "metadata": {"table_caption": "표 A"},
                "rrf_score": 0.3,
            },
            {
                "chunk_idx": 2,
                "text": "B no caption",
                "metadata": {},
                "rrf_score": 0.6,
            },
            {
                "chunk_idx": 3,
                "text": "C base\n\n[그림: 그림 C]",
                "metadata": {"figure_caption": "그림 C"},
                "rrf_score": 0.2,
            },
        ]
        # base_text → vec mock
        base_to_vec = {
            "A base": [1.0, 0.0, 0.0],
            "C base": [0.0, 1.0, 0.0],
        }

        def embed_fn(text: str) -> list[float]:
            return base_to_vec[text]

        ranked = _rerank_with_base_text(
            matched_chunks=matched,
            query_vec=[1.0, 0.0, 0.0],
            embed_fn=embed_fn,
        )
        # A (cosine=1.0) > B (rrf=0.6) > C (cosine=0.0)
        self.assertEqual(ranked, [1, 2, 3])

    def test_rerank_keeps_order_when_no_caption_chunks(self) -> None:
        """caption 합성 chunk 가 없으면 rrf_score 그대로 ranking 유지."""
        from run_s4_a_d4_compose_off import _rerank_with_base_text

        matched = [
            {"chunk_idx": 10, "text": "X", "metadata": {}, "rrf_score": 0.5},
            {"chunk_idx": 20, "text": "Y", "metadata": {}, "rrf_score": 0.9},
            {"chunk_idx": 30, "text": "Z", "metadata": {}, "rrf_score": 0.7},
        ]

        def embed_fn(_text: str) -> list[float]:
            raise AssertionError("caption 없는 chunk 면 embed 호출되면 안 됨")

        ranked = _rerank_with_base_text(
            matched_chunks=matched,
            query_vec=[1.0, 0.0, 0.0],
            embed_fn=embed_fn,
        )
        self.assertEqual(ranked, [20, 30, 10])

    def test_rerank_handles_empty_matched_chunks(self) -> None:
        """빈 matched_chunks → 빈 리스트 반환."""
        from run_s4_a_d4_compose_off import _rerank_with_base_text

        ranked = _rerank_with_base_text(
            matched_chunks=[],
            query_vec=[1.0, 0.0, 0.0],
            embed_fn=lambda _t: [1.0, 0.0, 0.0],
        )
        self.assertEqual(ranked, [])


class AggregationTests(unittest.TestCase):
    """_aggregate_group fail 카운트 / n_eval 분리 검증."""

    def test_aggregate_separates_eval_from_fail(self):
        """recall_at_10=None row 는 n_eval 제외, note='doc 매칭 fail' 은 fail_count."""
        from run_s4_a_d4_compose_off import _aggregate_group

        cells = [
            _make_cell(golden_id="g1", recall_at_10=0.9),
            _make_cell(golden_id="g2", recall_at_10=0.3),
            _make_cell(
                golden_id="g3",
                recall_at_10=None,
                recall_at_5=None,
                mrr=None,
                note="doc 매칭 fail",
            ),
            _make_cell(
                golden_id="g4",
                recall_at_10=None,
                recall_at_5=None,
                mrr=None,
                note="ERROR: HTTPException",
            ),
        ]
        s = _aggregate_group("test", cells)
        self.assertEqual(s.n_total, 4)
        self.assertEqual(s.n_eval, 2)
        self.assertEqual(s.fail_count, 2)
        self.assertAlmostEqual(s.avg_recall_at_10, 0.6, places=4)

    def test_aggregate_empty_group_returns_zeros(self):
        """빈 group 은 모든 metric 0.0."""
        from run_s4_a_d4_compose_off import _aggregate_group

        s = _aggregate_group("empty", [])
        self.assertEqual(s.n_total, 0)
        self.assertEqual(s.n_eval, 0)
        self.assertEqual(s.avg_recall_at_10, 0.0)
        self.assertEqual(s.fail_count, 0)


if __name__ == "__main__":
    unittest.main()
