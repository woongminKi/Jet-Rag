"""evals/run_acceptable_chunks_judge.py 단위 테스트.

검증 범위 (LLM/DB/HF 모두 mock, stdlib unittest only)
- row 선별: empty acceptable + relevant 있음 + not negative + doc_id 있음 만 통과
- 정렬: caption_dependent=true 우선
- CSV 멱등 쓰기: 선별 row acceptable 만 갱신, 나머지 bit-identical, utf-8-sig BOM, 14 컬럼
- cost guard: cap 도달 시 partial ship
- candidate 구성: BGE-M3 cosine top-K + relevant 강제 포함
- dry-run: judge 호출 0, CSV 변경 0
- vision_metrics._VALID_SOURCE_TYPES 에 "acceptable_judge" 포함
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-key")
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVALS_DIR = _REPO_ROOT / "evals"
if str(_EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(_EVALS_DIR))

import run_acceptable_chunks_judge as runner  # noqa: E402
from _acceptable_judge import _USAGE_SOURCE_TYPE  # noqa: E402
from _cost_guard import CostGuard  # noqa: E402
from build_golden_v2 import ChunkRecord, _V2_FIELDNAMES  # noqa: E402


def _make_row(**overrides) -> dict:
    base = {k: "" for k in _V2_FIELDNAMES}
    base.update({
        "id": "G-X-001",
        "query": "테스트 질의",
        "query_type": "exact_fact",
        "doc_id": "doc-1",
        "expected_doc_title": "테스트 문서",
        "relevant_chunks": "10,20",
        "acceptable_chunks": "",
        "negative": "false",
        "doc_type": "pdf",
        "caption_dependent": "false",
    })
    base.update(overrides)
    return base


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(_V2_FIELDNAMES))
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in _V2_FIELDNAMES})


class SelectRowsTest(unittest.TestCase):
    def test_selection_conditions(self) -> None:
        rows = [
            _make_row(id="ok", relevant_chunks="10,20", acceptable_chunks="", negative="false", doc_id="d1"),
            _make_row(id="no-relevant", relevant_chunks="", acceptable_chunks=""),
            _make_row(id="negative", negative="true"),
            _make_row(id="no-doc", doc_id=""),
            _make_row(id="already-filled", acceptable_chunks="5,6"),
        ]
        selected, skipped = runner.select_rows(rows)
        self.assertEqual([r["id"] for r in selected], ["ok"])
        self.assertEqual(skipped["no_relevant"], 1)
        self.assertEqual(skipped["negative"], 1)
        self.assertEqual(skipped["no_doc_id"], 1)
        self.assertEqual(skipped["already_filled"], 1)

    def test_sort_caption_dependent_first(self) -> None:
        rows = [
            _make_row(id="b-plain", caption_dependent="false"),
            _make_row(id="a-caption", caption_dependent="true"),
            _make_row(id="z-caption", caption_dependent="true"),
        ]
        selected, _ = runner.select_rows(rows)
        # caption=true 두 row 먼저 (id asc), 그 다음 plain
        self.assertEqual([r["id"] for r in selected], ["a-caption", "z-caption", "b-plain"])


class BuildCandidatesTest(unittest.TestCase):
    def test_top_k_plus_relevant_forced(self) -> None:
        # query_vec = [1,0]; cosine 높은 순: idx2(=[1,0]) > idx3(=[0.7,0.7]) > idx4(=[0,1])
        chunks = [
            ChunkRecord(chunk_idx=10, text="rel10", page=1, dense_vec=[0.0, 1.0], has_caption_marker=False),
            ChunkRecord(chunk_idx=20, text="rel20", page=1, dense_vec=[0.0, 1.0], has_caption_marker=False),
            ChunkRecord(chunk_idx=2, text="c2", page=1, dense_vec=[1.0, 0.0], has_caption_marker=False),
            ChunkRecord(chunk_idx=3, text="c3", page=1, dense_vec=[0.7, 0.7], has_caption_marker=False),
        ]
        cand = runner.build_candidates(
            chunks=chunks, relevant_idxs=[10, 20], query_vec=[1.0, 0.0], top_k=2,
        )
        idxs = [c[0] for c in cand]
        # relevant 먼저 (10, 20), 그다음 top-2 cosine (2, 3) — 중복 없음
        self.assertEqual(idxs[:2], [10, 20])
        self.assertIn(2, idxs)
        self.assertIn(3, idxs)
        self.assertEqual(len(idxs), 4)

    def test_stale_relevant_excluded(self) -> None:
        chunks = [
            ChunkRecord(chunk_idx=2, text="c2", page=1, dense_vec=[1.0, 0.0], has_caption_marker=False),
        ]
        # relevant 99 는 chunks 에 없음 → candidate 에서 빠짐
        cand = runner.build_candidates(
            chunks=chunks, relevant_idxs=[99], query_vec=[1.0, 0.0], top_k=5,
        )
        self.assertEqual([c[0] for c in cand], [2])


class _FakeChunksDB:
    """fetch_chunks_fn mock — doc_id → ChunkRecord list."""

    def __init__(self, mapping: dict[str, list[ChunkRecord]]) -> None:
        self._mapping = mapping

    def __call__(self, doc_id: str) -> list[ChunkRecord]:
        return self._mapping.get(doc_id, [])


class RunTest(unittest.TestCase):
    def _chunks(self):
        return [
            ChunkRecord(chunk_idx=10, text="rel10", page=1, dense_vec=[0.0, 1.0], has_caption_marker=False),
            ChunkRecord(chunk_idx=20, text="rel20", page=1, dense_vec=[0.0, 1.0], has_caption_marker=False),
            ChunkRecord(chunk_idx=2, text="c2", page=1, dense_vec=[1.0, 0.0], has_caption_marker=False),
        ]

    def test_fills_acceptable_and_excludes_relevant(self) -> None:
        rows = [_make_row(id="r1", doc_id="d1", relevant_chunks="10,20")]
        # judge: 10(=rel, exclude) high, 2 high, 20(=rel, exclude) high
        import json
        judge_raw = json.dumps([
            {"chunk_idx": 10, "score": 0.9},
            {"chunk_idx": 20, "score": 0.9},
            {"chunk_idx": 2, "score": 0.8},
        ])
        summary = runner.run(
            rows=rows,
            judge_call_fn=lambda s, u: judge_raw,
            fetch_chunks_fn=_FakeChunksDB({"d1": self._chunks()}),
            embed_query_fn=lambda q: [1.0, 0.0],
            threshold=0.5,
            max_count=8,
            candidate_top_k=5,
            cost_guard=CostGuard(cap_usd=1.0, est_per_unit=0.005),
            max_rows=None,
        )
        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["filled"], 1)
        self.assertEqual(rows[0]["acceptable_chunks"], "2")  # relevant 10,20 제외

    def test_stale_doc_skipped(self) -> None:
        rows = [_make_row(id="r1", doc_id="missing", relevant_chunks="10")]
        summary = runner.run(
            rows=rows,
            judge_call_fn=lambda s, u: "[]",
            fetch_chunks_fn=_FakeChunksDB({}),  # missing → []
            embed_query_fn=lambda q: [1.0, 0.0],
            threshold=0.5, max_count=8, candidate_top_k=5,
            cost_guard=CostGuard(cap_usd=1.0, est_per_unit=0.005),
            max_rows=None,
        )
        self.assertEqual(summary["processed"], 0)
        self.assertEqual(summary["skipped"].get("stale_doc"), 1)
        self.assertEqual(rows[0]["acceptable_chunks"], "")

    def test_cost_guard_partial_ship(self) -> None:
        rows = [_make_row(id=f"r{i}", doc_id="d1", relevant_chunks="10") for i in range(3)]
        import json
        judge_raw = json.dumps([{"chunk_idx": 2, "score": 0.9}])
        # cap 0.01, per-row 0.005 → 1번째 row OK (누적 0.005), 2번째 before_unit: 0.005+0.005=0.01 OK?
        # projected > cap+eps → 0.010 > 0.010+1e-9? no. so 2nd proceeds → 누적 0.010.
        # 3rd before_unit: 0.010 + 0.005 = 0.015 > 0.010 → BREAK. → 2 processed.
        summary = runner.run(
            rows=rows,
            judge_call_fn=lambda s, u: judge_raw,
            fetch_chunks_fn=_FakeChunksDB({"d1": self._chunks()}),
            embed_query_fn=lambda q: [1.0, 0.0],
            threshold=0.5, max_count=8, candidate_top_k=5,
            cost_guard=CostGuard(cap_usd=0.01, est_per_unit=0.005),
            max_rows=None,
        )
        self.assertTrue(summary["partial"])
        self.assertEqual(summary["processed"], 2)

    def test_consecutive_llm_failure_early_stop(self) -> None:
        rows = [_make_row(id=f"r{i}", doc_id="d1", relevant_chunks="10") for i in range(5)]

        def always_fail(s, u):
            raise RuntimeError("RESOURCE_EXHAUSTED")

        summary = runner.run(
            rows=rows,
            judge_call_fn=always_fail,
            fetch_chunks_fn=_FakeChunksDB({"d1": self._chunks()}),
            embed_query_fn=lambda q: [1.0, 0.0],
            threshold=0.5, max_count=8, candidate_top_k=5,
            cost_guard=CostGuard(cap_usd=1.0, est_per_unit=0.005),
            max_rows=None,
            consecutive_failure_limit=3,
        )
        self.assertTrue(summary["consecutive_fail_stop"])
        self.assertEqual(summary["processed"], 3)  # 3회 시도 후 중단
        self.assertEqual(summary["filled"], 0)


class CsvIdempotentWriteTest(unittest.TestCase):
    def test_only_acceptable_updated_rest_bit_identical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "golden.csv"
            rows_in = [
                _make_row(id="r1", doc_id="d1", relevant_chunks="10", acceptable_chunks=""),
                _make_row(id="r2", doc_id="", relevant_chunks="", acceptable_chunks="",
                          query="cross doc q", query_type="cross_doc"),
                _make_row(id="r3", doc_id="d2", relevant_chunks="5", acceptable_chunks="7,8"),  # already filled
            ]
            _write_csv(path, rows_in)
            original_bytes = path.read_bytes()

            # read → 선별 row(r1)만 acceptable 갱신 → atomic write
            rows = runner._read_csv(path)
            for r in rows:
                if r["id"] == "r1":
                    r["acceptable_chunks"] = "3,4"
            runner._write_csv_atomic(path, rows)

            new_bytes = path.read_bytes()
            # BOM 유지
            self.assertTrue(new_bytes.startswith(b"\xef\xbb\xbf"))
            # 헤더 14 컬럼 순서 유지
            with path.open(encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                self.assertEqual(reader.fieldnames, list(_V2_FIELDNAMES))
                out_rows = list(reader)
            by_id = {r["id"]: r for r in out_rows}
            self.assertEqual(by_id["r1"]["acceptable_chunks"], "3,4")
            self.assertEqual(by_id["r2"]["acceptable_chunks"], "")
            self.assertEqual(by_id["r3"]["acceptable_chunks"], "7,8")
            # r2/r3 다른 컬럼도 그대로
            self.assertEqual(by_id["r2"]["query"], "cross doc q")
            self.assertEqual(by_id["r3"]["relevant_chunks"], "5")

            # 재실행 (변경 없이) → bit-identical
            rows2 = runner._read_csv(path)
            runner._write_csv_atomic(path, rows2)
            self.assertEqual(path.read_bytes(), new_bytes)


class DryRunTest(unittest.TestCase):
    def test_dry_run_no_judge_no_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "golden.csv"
            rows_in = [
                _make_row(id="r1", doc_id="d1", relevant_chunks="10", acceptable_chunks=""),
                _make_row(id="r2", negative="true"),
            ]
            _write_csv(path, rows_in)
            before = path.read_bytes()
            rc = runner.main(["--input", str(path), "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertEqual(path.read_bytes(), before)  # CSV 변경 0

    def test_default_is_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "golden.csv"
            _write_csv(path, [_make_row(id="r1", doc_id="d1", relevant_chunks="10")])
            before = path.read_bytes()
            rc = runner.main(["--input", str(path)])  # flag 없음 → dry-run
            self.assertEqual(rc, 0)
            self.assertEqual(path.read_bytes(), before)


class SourceTypeRegisteredTest(unittest.TestCase):
    def test_acceptable_judge_in_valid_source_types(self) -> None:
        from app.services import vision_metrics

        self.assertIn(_USAGE_SOURCE_TYPE, vision_metrics._VALID_SOURCE_TYPES)
        self.assertEqual(_USAGE_SOURCE_TYPE, "acceptable_judge")


if __name__ == "__main__":
    unittest.main()
