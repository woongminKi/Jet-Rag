"""S2 D5 phase 1 §6.3 도구 보강 — `_measure_baseline_retrieval` ↔
GoldenRow 로딩의 acceptable_chunks 전달 통합 테스트 1건 (planner 권고).

검증 범위 (한 케이스에 통합):
- `_load_golden_targets` 가 골든셋 CSV 의 acceptable_chunks 컬럼을 GoldenRow 에
  파싱한다 (relevant_chunks 와 동일 로직 — 콤마 분리 + isdigit 필터).
- `_measure_baseline_retrieval` 이 GoldenRow.acceptable_chunks 를 recall_at_k
  에 정확히 전달하여 graded recall 을 산출한다.
- 결과 dict 에 `acceptable_used` 플래그가 들어간다.

DB / HTTP 호출 0 — search() / recall_at_k 는 monkeypatch.
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

# evals/ 를 import path 에 추가 — D4-pre regression 스크립트 모듈 import
_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVALS_PATH = _REPO_ROOT / "evals"
if str(_EVALS_PATH) not in sys.path:
    sys.path.insert(0, str(_EVALS_PATH))

import run_s2_d4_pre_regression as d4_pre  # noqa: E402
from run_s2_d4_pre_regression import (  # noqa: E402
    GoldenRow,
    _load_golden_targets,
    _measure_baseline_retrieval,
)


_FIELDNAMES = (
    "id,query,query_type,doc_id,expected_doc_title,relevant_chunks,"
    "acceptable_chunks,source_chunk_text,expected_answer_summary,"
    "must_include,source_hint,negative"
).split(",")


def _write_golden_csv(path: Path, rows: list[dict]) -> None:
    """테스트용 골든셋 CSV 작성 — 실 골든셋 v1 과 동일 schema."""
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({**{k: "" for k in _FIELDNAMES}, **row})


class LoadGoldenForwardsAcceptableTest(unittest.TestCase):
    """호출 경로 통합 — golden CSV 에서 읽은 acceptable_chunks 가
    `_measure_baseline_retrieval` → recall_at_k 까지 정확히 전달되는지 한 케이스로 검증.
    """

    def setUp(self) -> None:
        self._captured: dict[str, Any] = {}

        def fake_search(**kwargs: Any) -> Any:
            # G-A-021 케이스 모사 — top10 에 acceptable hit (904, 908) 포함
            top_chunks = [
                {"chunk_idx": 904, "rrf_score": 0.99},
                {"chunk_idx": 963, "rrf_score": 0.95},
                {"chunk_idx": 810, "rrf_score": 0.92},
                {"chunk_idx": 908, "rrf_score": 0.90},
            ]
            return SimpleNamespace(
                model_dump=lambda: {
                    "items": [
                        {
                            "doc_id": kwargs.get("doc_id"),
                            "matched_chunks": top_chunks,
                        }
                    ]
                }
            )

        def fake_recall(
            chunks: list[int],
            relv_set: set[int],
            *,
            k: int = 10,
            acceptable_chunks: set[int] | None = None,
        ) -> float:
            # 호출 인자 캡처 — 통합 검증 핵심
            self._captured["chunks"] = list(chunks)
            self._captured["relv_set"] = set(relv_set)
            self._captured["acceptable"] = (
                set(acceptable_chunks) if acceptable_chunks else None
            )
            self._captured["k"] = k
            return 0.7

        import app.routers.search as search_mod
        import app.services.retrieval_metrics as metrics_mod

        self._orig_search = search_mod.search
        self._orig_recall = metrics_mod.recall_at_k
        search_mod.search = fake_search  # type: ignore[assignment]
        metrics_mod.recall_at_k = fake_recall  # type: ignore[assignment]

    def tearDown(self) -> None:
        import app.routers.search as search_mod
        import app.services.retrieval_metrics as metrics_mod

        search_mod.search = self._orig_search  # type: ignore[assignment]
        metrics_mod.recall_at_k = self._orig_recall  # type: ignore[assignment]

    def test_csv_to_recall_at_k_acceptable_forwarding(self) -> None:
        # 1) 골든셋 CSV → GoldenRow 로딩
        with tempfile.TemporaryDirectory() as td:
            csv_path = Path(td) / "g.csv"
            _write_golden_csv(
                csv_path,
                [
                    {
                        "id": "G-A-021",
                        "query": "샘플 표 조회",
                        "query_type": "table_lookup",
                        "doc_id": "d1259dfe-c402-4cd0-bb04-3e67d88a2773",
                        "expected_doc_title": "sample-report",
                        "relevant_chunks": "868",
                        "acceptable_chunks": "904, 908, 909",
                        "source_hint": "p.91",
                    }
                ],
            )
            golden = _load_golden_targets(csv_path)

        self.assertEqual(len(golden), 1)
        self.assertEqual(golden[0].relevant_chunks, (868,))
        self.assertEqual(golden[0].acceptable_chunks, (904, 908, 909))

        # 2) baseline retrieval 측정 — recall_at_k 호출 인자 캡처
        results = _measure_baseline_retrieval(golden, k=10)

        self.assertEqual(self._captured["relv_set"], {868})
        self.assertEqual(self._captured["acceptable"], {904, 908, 909})
        self.assertEqual(self._captured["k"], 10)

        # 3) 결과 dict 의 acceptable_used 플래그 + recall 보존
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["acceptable_used"])
        self.assertAlmostEqual(results[0]["recall_at_10"], 0.7, places=4)


if __name__ == "__main__":
    unittest.main()
