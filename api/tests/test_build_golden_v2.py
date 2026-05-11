"""S4-A D3 Phase 1 — `evals/build_golden_v2.py` 단위 테스트.

검증 범위
- doc 매칭 자동 fix (NFC 정규화, 25자 prefix 매칭)
- relevant_chunks 자동 추출 (BGE-M3 cosine top-k + must_include 우선)
- acceptable_chunks 자동 추출 (cosine ≥ threshold)
- cross_doc sub-doc 별 top-1 합산
- caption_dependent 마킹 (query_type 룰 + chunk 패턴)

의존성 주입 — `fetch_chunks_fn` / `embed_query_fn` mock 으로 외부 호출 0,
DB 의존 0. CLAUDE.md 준수.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# evals/ 의 build_golden_v2 import — api/tests/ 에서 evals 모듈 경로 보정
_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


def _make_chunk(idx: int, text: str, dense_vec: list[float] | None = None,
                page: int | None = None):
    """ChunkRecord 생성 헬퍼 — caption_marker 자동 검출."""
    from build_golden_v2 import ChunkRecord, detect_caption_marker
    return ChunkRecord(
        chunk_idx=idx,
        text=text,
        page=page,
        dense_vec=dense_vec,
        has_caption_marker=detect_caption_marker(text),
    )


def _make_doc(doc_id: str, title: str, doc_type: str = "pdf"):
    """DocRecord 생성 헬퍼."""
    from build_golden_v2 import DocRecord, normalize_title
    return DocRecord(
        doc_id=doc_id,
        title=title,
        title_norm=normalize_title(title),
        doc_type=doc_type,
    )


class DocMatchAutoFixTest(unittest.TestCase):
    """doc 매칭 자동 fix — NFC 정규화 + prefix 매칭."""

    def test_nfc_normalize_and_underscore_match(self) -> None:
        """공백·밑줄 차이를 흡수해 doc_id 매칭. v1 row 의 빈 doc_id 가 채워진다."""
        from build_golden_v2 import build_v2_row, BuildStats

        # docs 인덱스 — 인제스트 시 title 에 밑줄 포함된 형태
        docs_index = {
            _make_doc("doc-A", "보건의료_빅데이터_플랫폼_시범사업_추진계획").title_norm: _make_doc(
                "doc-A", "보건의료_빅데이터_플랫폼_시범사업_추진계획"
            ),
        }

        # v1 row — title 에 공백 사용 (사용자 작성 표기)
        v1_row = {
            "id": "G-U-099",
            "query": "보건의료 빅데이터 시범사업 데이터 종류는?",
            "query_type": "exact_fact",
            "doc_id": "",  # 빈 → 매칭 fix 대상
            "expected_doc_title": "보건의료 빅데이터 플랫폼 시범사업 추진계획",
            "relevant_chunks": "1",  # 이미 채워짐 — 자동 추출 skip
            "acceptable_chunks": "",
            "must_include": "데이터;종류",
            "negative": "false",
        }

        stats = BuildStats()
        v2_row = build_v2_row(
            v1_row,
            docs_index=docs_index,
            fetch_chunks_fn=lambda _doc_id: [],
            embed_query_fn=lambda _q: [],
            threshold=0.7,
            stats=stats,
        )

        self.assertEqual(v2_row["doc_id"], "doc-A")
        self.assertEqual(v2_row["doc_type"], "pdf")
        self.assertEqual(stats.doc_match_fixed, 1)
        self.assertEqual(stats.doc_match_failed, [])

    def test_decoration_strip_substring_match(self) -> None:
        """docs.title 이 `(붙임2)` prefix·`(1)` suffix 데코를 가져도 v1 표기와 매칭."""
        from build_golden_v2 import build_v2_row, BuildStats

        docs_index = {
            _make_doc(
                "doc-DC",
                "(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서 (1)",
                "pdf",
            ).title_norm: _make_doc(
                "doc-DC",
                "(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서 (1)",
                "pdf",
            ),
        }
        v1_row = {
            "id": "G-U-006",
            "query": "데이터센터 사업 신청 자격",
            "query_type": "exact_fact",
            "doc_id": "",
            "expected_doc_title": "2025년 데이터센터 산업 활성화 지원 사업 통합_안내서",
            "relevant_chunks": "1",
            "acceptable_chunks": "",
            "must_include": "",
            "negative": "false",
        }
        stats = BuildStats()
        v2_row = build_v2_row(
            v1_row,
            docs_index=docs_index,
            fetch_chunks_fn=lambda _doc_id: [],
            embed_query_fn=lambda _q: [],
            threshold=0.7,
            stats=stats,
        )
        self.assertEqual(v2_row["doc_id"], "doc-DC")
        self.assertEqual(stats.doc_match_failed, [])


class RelevantChunkAutoExtractTest(unittest.TestCase):
    """relevant_chunks 자동 추출 — must_include hit 우선."""

    def test_must_include_hit_wins_over_top1(self) -> None:
        """must_include 토큰 hit chunk 가 cosine top-1 보다 우선 채택된다."""
        from build_golden_v2 import build_v2_row, BuildStats

        docs_index = {
            _make_doc("doc-X", "sample-report").title_norm: _make_doc(
                "doc-X", "sample-report"
            ),
        }

        # query_vec 과 가까운 순서 — chunk 1 (top1, must_include miss),
        # chunk 2 (top2, must_include hit), chunk 3 (top3, miss)
        chunks = [
            _make_chunk(1, "관련 없는 일반 텍스트입니다.", dense_vec=[1.0, 0.0, 0.0]),
            _make_chunk(2, "GDP 수치는 12% 증가했다.", dense_vec=[0.9, 0.4, 0.0]),
            _make_chunk(3, "다른 무관 chunk.", dense_vec=[0.7, 0.0, 0.5]),
        ]

        v1_row = {
            "id": "G-A-200",
            "query": "12% 증가",
            "query_type": "numeric_lookup",
            "doc_id": "doc-X",
            "expected_doc_title": "sample-report",
            "relevant_chunks": "",  # 자동 추출 대상
            "acceptable_chunks": "",
            "must_include": "12%;증가",
            "negative": "false",
        }

        stats = BuildStats()
        v2_row = build_v2_row(
            v1_row,
            docs_index=docs_index,
            fetch_chunks_fn=lambda _doc_id: chunks,
            embed_query_fn=lambda _q: [1.0, 0.0, 0.0],
            threshold=0.99,  # acceptable 거의 0
            stats=stats,
        )

        # must_include hit 인 chunk 2 가 채택돼야 함 (top-1 인 chunk 1 은 miss).
        # top-3 중 hit 만 채택되면 chunk 2 만 들어감.
        self.assertIn("2", v2_row["relevant_chunks"].split(","))
        self.assertEqual(stats.relevant_filled, 1)


class AcceptableChunkExtractTest(unittest.TestCase):
    """acceptable_chunks — cosine ≥ threshold (relevant 제외)."""

    def test_cosine_threshold_filter(self) -> None:
        from build_golden_v2 import build_v2_row, BuildStats

        docs_index = {
            _make_doc("doc-Y", "law sample2").title_norm: _make_doc(
                "doc-Y", "law sample2", "hwp"
            ),
        }

        # query_vec=[1,0,0] 기준 cosine — chunk1=1.0, chunk2=0.85, chunk3=0.5
        chunks = [
            _make_chunk(1, "정답 chunk", dense_vec=[1.0, 0.0, 0.0]),
            _make_chunk(2, "유사 chunk", dense_vec=[0.85, 0.5267, 0.0]),
            _make_chunk(3, "거리 먼 chunk", dense_vec=[0.5, 0.866, 0.0]),
        ]

        v1_row = {
            "id": "G-U-200",
            "query": "임의 query",
            "query_type": "exact_fact",
            "doc_id": "doc-Y",
            "expected_doc_title": "law sample2",
            "relevant_chunks": "",
            "acceptable_chunks": "",
            "must_include": "",
            "negative": "false",
        }

        stats = BuildStats()
        v2_row = build_v2_row(
            v1_row,
            docs_index=docs_index,
            fetch_chunks_fn=lambda _doc_id: chunks,
            embed_query_fn=lambda _q: [1.0, 0.0, 0.0],
            threshold=0.7,
            stats=stats,
        )

        # relevant top-1 = chunk1 (cosine 1.0)
        self.assertIn("1", v2_row["relevant_chunks"].split(","))
        # acceptable — cosine ≥ 0.7 인 chunk 중 relevant 제외 → chunk 2
        accept_idxs = v2_row["acceptable_chunks"].split(",") if v2_row["acceptable_chunks"] else []
        self.assertIn("2", accept_idxs)
        # chunk 3 (cosine 0.5) 은 미포함
        self.assertNotIn("3", accept_idxs)
        # doc_type 정상 채움
        self.assertEqual(v2_row["doc_type"], "hwp")


class CrossDocSubDocTopOneTest(unittest.TestCase):
    """cross_doc — sub_doc 별 top-1 합산."""

    def test_pipe_separator_aggregates_top1_per_subdoc(self) -> None:
        from build_golden_v2 import build_v2_row, BuildStats

        # 두 sub_doc — `|` 분리. 각 doc 마다 top-1 chunk 채택.
        doc_a = _make_doc("doc-A", "law sample2", "hwp")
        doc_b = _make_doc("doc-B", "law sample3", "pdf")
        docs_index = {
            doc_a.title_norm: doc_a,
            doc_b.title_norm: doc_b,
        }

        chunks_by_doc = {
            "doc-A": [
                _make_chunk(10, "doc A 정답 chunk", dense_vec=[1.0, 0.0, 0.0]),
                _make_chunk(11, "doc A 다른 chunk", dense_vec=[0.3, 0.9, 0.0]),
            ],
            "doc-B": [
                _make_chunk(20, "doc B 다른 chunk", dense_vec=[0.2, 0.9, 0.3]),
                _make_chunk(21, "doc B 정답 chunk", dense_vec=[0.95, 0.3, 0.05]),
            ],
        }

        v1_row = {
            "id": "G-U-300",
            "query": "법률 자료 비교",
            "query_type": "cross_doc",
            "doc_id": "",
            "expected_doc_title": "law sample2|law sample3",  # cross_doc
            "relevant_chunks": "",
            "acceptable_chunks": "",
            "must_include": "",
            "negative": "false",
        }

        stats = BuildStats()
        v2_row = build_v2_row(
            v1_row,
            docs_index=docs_index,
            fetch_chunks_fn=lambda doc_id: chunks_by_doc.get(doc_id, []),
            embed_query_fn=lambda _q: [1.0, 0.0, 0.0],
            threshold=0.99,
            stats=stats,
        )

        rel_idxs = set(v2_row["relevant_chunks"].split(","))
        # 각 sub_doc 의 top-1 — doc-A: chunk 10 (cosine 1.0), doc-B: chunk 21 (cosine 0.95)
        self.assertIn("10", rel_idxs)
        self.assertIn("21", rel_idxs)
        # cross_doc 카운트 +1
        self.assertEqual(stats.cross_doc_processed, 1)


class CaptionDependentMarkingTest(unittest.TestCase):
    """caption_dependent — query_type 또는 chunk 패턴 OR."""

    def test_query_type_vision_diagram_marks_true(self) -> None:
        """query_type=vision_diagram → caption_dependent=true (chunk 텍스트 무관)."""
        from build_golden_v2 import build_v2_row, BuildStats

        docs_index = {
            _make_doc("doc-V", "직제_규정").title_norm: _make_doc(
                "doc-V", "직제_규정", "hwpx"
            ),
        }
        chunks = [_make_chunk(1, "조직도 텍스트", dense_vec=[1.0])]

        v1_row = {
            "id": "G-U-400",
            "query": "직제규정 부서 구조 그림 어디",
            "query_type": "vision_diagram",
            "doc_id": "doc-V",
            "expected_doc_title": "직제_규정",
            "relevant_chunks": "1",
            "acceptable_chunks": "",
            "must_include": "",
            "negative": "false",
        }
        stats = BuildStats()
        v2_row = build_v2_row(
            v1_row,
            docs_index=docs_index,
            fetch_chunks_fn=lambda _doc_id: chunks,
            embed_query_fn=lambda _q: [1.0],
            threshold=0.7,
            stats=stats,
        )
        self.assertEqual(v2_row["caption_dependent"], "true")

    def test_chunk_text_caption_marker_marks_true(self) -> None:
        """exact_fact query 라도 정답 chunk 에 `[표`/`<표` 등 패턴 있으면 true."""
        from build_golden_v2 import build_v2_row, BuildStats

        docs_index = {
            _make_doc("doc-T", "sonata-the-edge_catalog").title_norm: _make_doc(
                "doc-T", "sonata-the-edge_catalog"
            ),
        }
        # chunk text 에 caption marker `[표` 포함
        chunks = [_make_chunk(1, "[표 1] 트림별 가격표", dense_vec=[1.0])]

        v1_row = {
            "id": "G-U-401",
            "query": "쏘나타 가격",
            "query_type": "exact_fact",  # vision/table 아님
            "doc_id": "doc-T",
            "expected_doc_title": "sonata-the-edge_catalog",
            "relevant_chunks": "1",
            "acceptable_chunks": "",
            "must_include": "",
            "negative": "false",
        }
        stats = BuildStats()
        v2_row = build_v2_row(
            v1_row,
            docs_index=docs_index,
            fetch_chunks_fn=lambda _doc_id: chunks,
            embed_query_fn=lambda _q: [1.0],
            threshold=0.7,
            stats=stats,
        )
        # query_type 이 exact_fact 여도 chunk text 의 `[표` 마커로 true
        self.assertEqual(v2_row["caption_dependent"], "true")


class StaleDocIdHookTest(unittest.TestCase):
    """Phase 2 — stale doc_id 재발 방지 hook.

    CSV 에 doc_id 가 있는데 docs_index 에 미존재 (= 이전 인스턴스의 stale id) 인
    경우의 동작:
    1. 경고 + ``stale_doc_id_count`` 카운트
    2. title fallback 으로 auto-fix 시도 → 성공 시 row.doc_id 교체 +
       ``stale_doc_id_fixed`` 카운트
    3. 실패 시 stale id 보존 + ``stale_doc_id_kept`` 에 row id append
    """

    def test_stale_doc_id_auto_fix_via_title_fallback(self) -> None:
        """stale id + 유효한 title → title 매칭으로 auto-fix + row.doc_id 정정."""
        from build_golden_v2 import build_v2_row, BuildStats

        docs_index = {
            _make_doc("doc-NEW", "보건의료_빅데이터_플랫폼").title_norm: _make_doc(
                "doc-NEW", "보건의료_빅데이터_플랫폼"
            ),
        }
        v1_row = {
            "id": "G-A-104",
            "query": "보건의료 빅데이터 데이터 종류",
            "query_type": "exact_fact",
            # stale id — 이전 인스턴스에서 부여된 사라진 id
            "doc_id": "629332ab-673d-49b7-a956-4857e3e9a5ee",
            "expected_doc_title": "보건의료_빅데이터_플랫폼",
            "relevant_chunks": "1",
            "acceptable_chunks": "",
            "must_include": "",
            "negative": "false",
        }
        stats = BuildStats()
        v2_row = build_v2_row(
            v1_row,
            docs_index=docs_index,
            fetch_chunks_fn=lambda _doc_id: [],
            embed_query_fn=lambda _q: [],
            threshold=0.7,
            stats=stats,
        )
        # auto-fix 결과 row.doc_id 가 새 id 로 정정되어야 함
        self.assertEqual(v2_row["doc_id"], "doc-NEW")
        self.assertEqual(stats.stale_doc_id_count, 1)
        self.assertEqual(stats.stale_doc_id_fixed, 1)
        self.assertEqual(stats.stale_doc_id_kept, [])

    def test_stale_doc_id_kept_when_title_fallback_fails(self) -> None:
        """stale id + 매칭 안 되는 title → stale id 보존 + kept 카운트."""
        from build_golden_v2 import build_v2_row, BuildStats

        docs_index = {
            _make_doc("doc-A", "전혀 다른 자료").title_norm: _make_doc(
                "doc-A", "전혀 다른 자료"
            ),
        }
        v1_row = {
            "id": "G-A-NONE",
            "query": "임의 query",
            "query_type": "exact_fact",
            "doc_id": "deadbeef-0000-0000-0000-000000000000",
            # title 도 docs_index 에 없음 → fallback 실패
            "expected_doc_title": "존재하지 않는 자료",
            "relevant_chunks": "1",
            "acceptable_chunks": "",
            "must_include": "",
            "negative": "false",
        }
        stats = BuildStats()
        v2_row = build_v2_row(
            v1_row,
            docs_index=docs_index,
            fetch_chunks_fn=lambda _doc_id: [],
            embed_query_fn=lambda _q: [],
            threshold=0.7,
            stats=stats,
        )
        # stale id 보존
        self.assertEqual(v2_row["doc_id"], "deadbeef-0000-0000-0000-000000000000")
        self.assertEqual(stats.stale_doc_id_count, 1)
        self.assertEqual(stats.stale_doc_id_fixed, 0)
        self.assertEqual(stats.stale_doc_id_kept, ["G-A-NONE"])

    def test_valid_doc_id_does_not_trigger_hook(self) -> None:
        """docs_index 에 존재하는 doc_id 는 hook 미발동 (회귀 보호)."""
        from build_golden_v2 import build_v2_row, BuildStats

        docs_index = {
            _make_doc("doc-VALID", "유효 자료").title_norm: _make_doc(
                "doc-VALID", "유효 자료"
            ),
        }
        v1_row = {
            "id": "G-A-VALID",
            "query": "유효 query",
            "query_type": "exact_fact",
            "doc_id": "doc-VALID",  # docs_index 에 존재
            "expected_doc_title": "유효 자료",
            "relevant_chunks": "1",
            "acceptable_chunks": "",
            "must_include": "",
            "negative": "false",
        }
        stats = BuildStats()
        v2_row = build_v2_row(
            v1_row,
            docs_index=docs_index,
            fetch_chunks_fn=lambda _doc_id: [],
            embed_query_fn=lambda _q: [],
            threshold=0.7,
            stats=stats,
        )
        # 회귀 보호 — 정상 row 는 stale 카운터 0
        self.assertEqual(v2_row["doc_id"], "doc-VALID")
        self.assertEqual(stats.stale_doc_id_count, 0)
        self.assertEqual(stats.stale_doc_id_fixed, 0)
        self.assertEqual(stats.stale_doc_id_kept, [])


if __name__ == "__main__":
    unittest.main()
