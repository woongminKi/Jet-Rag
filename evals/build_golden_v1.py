"""W26 S1 D2 — `golden_v0.7_auto.csv` (자동) + `golden_v0.6_user.csv` (사용자) → `golden_v1.csv` 통합.

flow:
1. v0.7 auto load (12 컬럼) — 자동 생성된 ~115 row, id `G-A-***` / `G-N-***`
2. v0.6 user load (7 컬럼) — 사용자 작성 33 row, id 부재 → `G-U-***` 신규 부여
3. user CSV 의 7 컬럼을 12 컬럼으로 padding (id, doc_id, relevant_chunks, acceptable_chunks, source_chunk_text 빈 값)
4. 중복 query 검출 — 정규화 비교 (NFC + 공백 정리), user 우선 (must_include 등 채워짐)
5. 합산 → `evals/golden_v1.csv` (12 컬럼, utf-8-sig)

산출:
- `evals/golden_v1.csv` (148 row 예상, 12 컬럼)

stdout: 통합 결과 요약 (auto N건, user M건, 중복 K건, 최종 N+M-K건)

사용:
    cd evals && uv run python build_golden_v1.py
    # 또는
    uv run python build_golden_v1.py --auto golden_v0.7_auto.csv --user golden_v0.6_user.csv --output golden_v1.csv

CLAUDE.md 정합:
- 의존성 추가 0 (stdlib csv + unicodedata 만)
- 사용자 자료 노출 방지 — auto CSV 의 비식별화 정책 유지 (user CSV 는 사용자 직접 작성이라 raw 노출 X)
"""

from __future__ import annotations

import argparse
import csv
import sys
import unicodedata
from pathlib import Path

# auto_goldenset 의 schema 재사용 — 단일 출처.
sys.path.insert(0, str(Path(__file__).parent))
from auto_goldenset import _V07_FIELDNAMES  # noqa: E402

_DEFAULT_AUTO = Path(__file__).parent / "golden_v0.7_auto.csv"
_DEFAULT_USER = Path(__file__).parent / "golden_v0.6_user.csv"
_DEFAULT_OUTPUT = Path(__file__).parent / "golden_v1.csv"


def _normalize_query(q: str) -> str:
    """중복 검출용 정규화 — NFC + 공백 정리 + lower."""
    norm = unicodedata.normalize("NFC", q.strip())
    return " ".join(norm.split()).lower()


def _load_csv_rows(path: Path) -> list[dict]:
    """CSV → list[dict]. utf-8-sig (BOM) / utf-8 자동 처리."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _pad_user_row(user_row: dict, qid: int) -> dict:
    """v0.6 user (7 컬럼) → v1 (12 컬럼) padding.

    user 의 7 컬럼: query, query_type, expected_doc_title, expected_answer_summary,
                  must_include, source_hint, negative
    추가 5 컬럼: id (부여), doc_id (빈), relevant_chunks (빈), acceptable_chunks (빈),
               source_chunk_text (빈)
    """
    padded = {field: "" for field in _V07_FIELDNAMES}
    padded["id"] = f"G-U-{qid:03d}"
    for key in (
        "query", "query_type", "expected_doc_title", "expected_answer_summary",
        "must_include", "source_hint", "negative",
    ):
        if key in user_row:
            padded[key] = (user_row.get(key) or "").strip()
    # negative 미기재 시 default false
    if not padded["negative"]:
        padded["negative"] = "false"
    return padded


def _ensure_v07_schema(row: dict) -> dict:
    """auto CSV row 를 12 컬럼 schema 로 정규화. 누락 컬럼은 빈 값."""
    return {field: (row.get(field, "") or "").strip() for field in _V07_FIELDNAMES}


def merge_golden(
    auto_rows: list[dict], user_rows: list[dict]
) -> tuple[list[dict], dict]:
    """auto + user → 통합 list. 중복 query 는 user 우선.

    return: (merged_rows, stats_dict)
    """
    # 1) auto 정규화
    auto_normalized: list[dict] = [_ensure_v07_schema(r) for r in auto_rows]
    # 2) user padding
    user_padded: list[dict] = [_pad_user_row(r, i + 1) for i, r in enumerate(user_rows)]

    # 3) 중복 검출 — user query set 기준
    user_query_set: set[str] = {_normalize_query(r["query"]) for r in user_padded}
    auto_filtered: list[dict] = []
    duplicate_count = 0
    for row in auto_normalized:
        if _normalize_query(row["query"]) in user_query_set:
            duplicate_count += 1
            continue
        auto_filtered.append(row)

    # 4) 합산 — user 먼저 (id 가독성), auto 다음
    merged = user_padded + auto_filtered

    stats = {
        "auto_total": len(auto_rows),
        "user_total": len(user_rows),
        "duplicates_removed_from_auto": duplicate_count,
        "merged_total": len(merged),
    }
    return merged, stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="v0.7 auto + v0.6 user → golden_v1.csv 통합"
    )
    parser.add_argument("--auto", type=Path, default=_DEFAULT_AUTO,
                        help=f"auto CSV 경로 (default {_DEFAULT_AUTO.name})")
    parser.add_argument("--user", type=Path, default=_DEFAULT_USER,
                        help=f"user CSV 경로 (default {_DEFAULT_USER.name})")
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT,
                        help=f"출력 CSV 경로 (default {_DEFAULT_OUTPUT.name})")
    args = parser.parse_args()

    auto_rows = _load_csv_rows(args.auto)
    user_rows = _load_csv_rows(args.user)

    if not auto_rows and not user_rows:
        print("[ERROR] auto·user CSV 둘 다 비어있음", file=sys.stderr)
        return 1
    if not auto_rows:
        print(f"[WARN] {args.auto.name} 비어있음 — user only 통합", file=sys.stderr)
    if not user_rows:
        print(f"[WARN] {args.user.name} 비어있음 — auto only 통합", file=sys.stderr)

    merged, stats = merge_golden(auto_rows, user_rows)

    # 12 컬럼 schema, utf-8-sig (Excel 한글 호환)
    with args.output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(_V07_FIELDNAMES))
        writer.writeheader()
        writer.writerows(merged)

    print(
        f"[OK] auto={stats['auto_total']} + user={stats['user_total']} "
        f"- duplicates={stats['duplicates_removed_from_auto']} "
        f"= merged={stats['merged_total']} → {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
