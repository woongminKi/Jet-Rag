"""KPI #11 인제스트 SLO 달성률 스크립트 단위 테스트.

검증 포인트
- `_resolve_slo_ms`: doc_type + page bucket → SLO ms 매칭 (6 항목 + 보수 default)
- `_build_metrics`: jobs / docs / durations / max_pages 합성 → JobMetric 정확
- `_summarize`: rate / avg / P95 산출
- `_render_markdown`: 헤더 + 게이트 status 정확 표시

stdlib unittest only. supabase 의존성 0 (data fetch 는 별도 — mock 안 함).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


class ResolveSloMsTests(unittest.TestCase):
    def test_pdf_text_page_le_20(self) -> None:
        from ingest_slo_report import _resolve_slo_ms
        slo_ms, _ = _resolve_slo_ms("pdf", page_count=10)
        self.assertEqual(slo_ms, 60_000)

    def test_pdf_text_page_eq_20_boundary(self) -> None:
        from ingest_slo_report import _resolve_slo_ms
        slo_ms, _ = _resolve_slo_ms("pdf", page_count=20)
        self.assertEqual(slo_ms, 60_000)

    def test_pdf_image_page_gt_20(self) -> None:
        from ingest_slo_report import _resolve_slo_ms
        slo_ms, _ = _resolve_slo_ms("pdf", page_count=50)
        self.assertEqual(slo_ms, 180_000)

    def test_hwp(self) -> None:
        from ingest_slo_report import _resolve_slo_ms
        slo_ms, _ = _resolve_slo_ms("hwp", page_count=50)
        self.assertEqual(slo_ms, 90_000)

    def test_hwpx(self) -> None:
        from ingest_slo_report import _resolve_slo_ms
        slo_ms, _ = _resolve_slo_ms("hwpx", page_count=50)
        self.assertEqual(slo_ms, 90_000)

    def test_image(self) -> None:
        from ingest_slo_report import _resolve_slo_ms
        slo_ms, _ = _resolve_slo_ms("image", page_count=1)
        self.assertEqual(slo_ms, 15_000)

    def test_url(self) -> None:
        from ingest_slo_report import _resolve_slo_ms
        slo_ms, _ = _resolve_slo_ms("url", page_count=1)
        self.assertEqual(slo_ms, 30_000)

    def test_unknown_doc_type_defaults_to_60s(self) -> None:
        """docx/pptx 등 기획서 §10.11 비명시 — 60s 보수 default."""
        from ingest_slo_report import _resolve_slo_ms
        slo_ms, _ = _resolve_slo_ms("docx", page_count=5)
        self.assertEqual(slo_ms, 60_000)
        slo_ms, _ = _resolve_slo_ms("pptx", page_count=5)
        self.assertEqual(slo_ms, 60_000)


class BuildMetricsTests(unittest.TestCase):
    def test_satisfied_pdf_under_slo(self) -> None:
        from ingest_slo_report import _build_metrics

        jobs = [{"id": "j1", "doc_id": "d1"}]
        docs = {"d1": {"id": "d1", "doc_type": "pdf"}}
        durations = {"j1": 30_000}  # 30s < 60s (≤20p PDF SLO)
        max_pages = {"d1": 5}

        metrics = _build_metrics(jobs, docs, durations, max_pages)
        self.assertEqual(len(metrics), 1)
        self.assertTrue(metrics[0].satisfied)
        self.assertEqual(metrics[0].page_count, 5)

    def test_violated_pdf_over_slo(self) -> None:
        from ingest_slo_report import _build_metrics

        jobs = [{"id": "j1", "doc_id": "d1"}]
        docs = {"d1": {"id": "d1", "doc_type": "pdf"}}
        durations = {"j1": 100_000}  # 100s > 60s (텍스트 PDF SLO)
        max_pages = {"d1": 5}

        metrics = _build_metrics(jobs, docs, durations, max_pages)
        self.assertFalse(metrics[0].satisfied)

    def test_missing_doc_skipped(self) -> None:
        from ingest_slo_report import _build_metrics

        jobs = [{"id": "j1", "doc_id": "d_unknown"}]
        docs = {}  # documents 누락
        metrics = _build_metrics(jobs, docs, {}, {})
        self.assertEqual(metrics, [])

    def test_zero_duration_not_satisfied(self) -> None:
        """duration_ms == 0 (logs 누락) → satisfied=False (보수)."""
        from ingest_slo_report import _build_metrics

        jobs = [{"id": "j1", "doc_id": "d1"}]
        docs = {"d1": {"id": "d1", "doc_type": "url"}}
        metrics = _build_metrics(jobs, docs, {"j1": 0}, {})
        self.assertFalse(metrics[0].satisfied)

    def test_url_page_count_defaults_to_1(self) -> None:
        """url 처럼 chunks page NULL 일 때 page_count=1 default."""
        from ingest_slo_report import _build_metrics

        jobs = [{"id": "j1", "doc_id": "d1"}]
        docs = {"d1": {"id": "d1", "doc_type": "url"}}
        durations = {"j1": 10_000}  # 10s < 30s URL SLO
        metrics = _build_metrics(jobs, docs, durations, {})
        self.assertEqual(metrics[0].page_count, 1)
        self.assertTrue(metrics[0].satisfied)


class SummarizeTests(unittest.TestCase):
    def test_empty(self) -> None:
        from ingest_slo_report import _summarize
        s = _summarize("overall", [])
        self.assertEqual(s.n_total, 0)
        self.assertEqual(s.n_satisfied, 0)
        self.assertEqual(s.rate, 0.0)

    def test_rate_calculation(self) -> None:
        from ingest_slo_report import JobMetric, _summarize

        def _m(satisfied: bool, dur: int) -> JobMetric:
            return JobMetric(
                job_id="x", doc_id="d", doc_type="pdf", page_count=5,
                duration_ms=dur, slo_ms=60_000, slo_label="pdf",
                satisfied=satisfied,
            )

        ms = [_m(True, 10_000), _m(True, 20_000), _m(False, 100_000), _m(True, 30_000)]
        s = _summarize("pdf", ms)
        self.assertEqual(s.n_total, 4)
        self.assertEqual(s.n_satisfied, 3)
        self.assertAlmostEqual(s.rate, 0.75)
        self.assertAlmostEqual(s.avg_duration_ms, 40_000.0)


class RenderMarkdownTests(unittest.TestCase):
    def test_pass_gate_when_rate_ge_90(self) -> None:
        from ingest_slo_report import GroupSummary, _render_markdown

        overall = GroupSummary(label="overall", n_total=10, n_satisfied=9)
        md = _render_markdown(
            metrics=[], overall=overall, by_doc_type=[],
            generated_at="2026-05-14T00:00:00Z",
        )
        self.assertIn("✅ 통과", md)
        self.assertIn("9/10", md)
        self.assertIn("90.0%", md)

    def test_fail_gate_when_rate_lt_90(self) -> None:
        from ingest_slo_report import GroupSummary, _render_markdown

        overall = GroupSummary(label="overall", n_total=10, n_satisfied=5)
        md = _render_markdown(
            metrics=[], overall=overall, by_doc_type=[],
            generated_at="2026-05-14T00:00:00Z",
        )
        self.assertIn("❌ 미달", md)


if __name__ == "__main__":
    unittest.main()
