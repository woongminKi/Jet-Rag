"""W26 S1 D2 — `golden_v0.7_auto.csv` (자동) + `golden_v0.6_user.csv` (사용자) → `golden_v1.csv` 통합.

flow:
1. v0.7 auto load (12 컬럼) — 자동 생성된 ~115 row, id `G-A-***` / `G-N-***`
2. v0.6 user load (7 컬럼) — 사용자 작성 33 row, id 부재 → `G-U-***` 신규 부여
3. user CSV 의 7 컬럼을 12 컬럼으로 padding (id, doc_id, relevant_chunks, acceptable_chunks, source_chunk_text 빈 값)
4. 중복 query 검출 — 정규화 비교 (NFC + 공백 정리), user 우선 (must_include 등 채워짐)
5. 합산 → `evals/golden_v1.csv` (12 컬럼, utf-8-sig)
6. (옵션) ``--validate-doc-ids`` 또는 ``JETRAG_GOLDEN_VALIDATE_DOC_IDS=1`` 일 때
   merged rows 의 모든 doc_id 가 Supabase ``documents`` 테이블에 존재하는지 검증.
   미존재 doc_id 발견 시 ``exit 1`` 로 종료 (CI 가드).

산출:
- `evals/golden_v1.csv` (148 row 예상, 12 컬럼)

stdout: 통합 결과 요약 (auto N건, user M건, 중복 K건, 최종 N+M-K건)

사용:
    cd evals && uv run python build_golden_v1.py
    # 또는
    uv run python build_golden_v1.py --auto golden_v0.7_auto.csv --user golden_v0.6_user.csv --output golden_v1.csv

    # Phase 2 — doc_id 무결성 검증 (default OFF)
    uv run python build_golden_v1.py --validate-doc-ids
    # 또는 환경변수
    JETRAG_GOLDEN_VALIDATE_DOC_IDS=1 uv run python build_golden_v1.py

CLAUDE.md 정합:
- 의존성 추가 0 (stdlib csv + unicodedata 만)
- 사용자 자료 노출 방지 — auto CSV 의 비식별화 정책 유지 (user CSV 는 사용자 직접 작성이라 raw 노출 X)
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import unicodedata
from pathlib import Path

# auto_goldenset 의 schema 재사용 — 단일 출처.
sys.path.insert(0, str(Path(__file__).parent))
from auto_goldenset import _V07_FIELDNAMES  # noqa: E402

_DEFAULT_AUTO = Path(__file__).parent / "golden_v0.7_auto.csv"
_DEFAULT_USER = Path(__file__).parent / "golden_v0.6_user.csv"
_DEFAULT_OUTPUT = Path(__file__).parent / "golden_v1.csv"

# Phase 2 — doc_id 무결성 검증 trigger 환경 변수.
_VALIDATE_ENV_VAR = "JETRAG_GOLDEN_VALIDATE_DOC_IDS"


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


def validate_doc_ids(
    rows: list[dict],
    *,
    fetch_valid_ids_fn,
) -> tuple[list[str], list[str]]:
    """Phase 2 — merged rows 의 doc_id 가 Supabase ``documents`` 에 존재하는지 검증.

    cross_doc row (doc_id 빈 칸) 와 negative row 는 검증 대상 외 — 빈 doc_id 통과.

    Args:
        rows: merge_golden 결과 list[dict].
        fetch_valid_ids_fn: 의존성 주입 — () -> set[str] (Supabase
            documents.id 전체 셋). 테스트 mock 가능. 실제 호출부에서는
            Supabase client 로 한 번에 fetch.

    Returns:
        (checked_ids, missing_ids) — 검사된 unique id 들, 미존재 id 들.
    """
    target_ids: set[str] = set()
    for r in rows:
        did = (r.get("doc_id") or "").strip()
        if did:
            target_ids.add(did)
    if not target_ids:
        return [], []
    valid_ids = fetch_valid_ids_fn()
    missing = sorted(target_ids - valid_ids)
    return sorted(target_ids), missing


def _fetch_valid_doc_ids_from_supabase() -> set[str]:
    """Supabase ``documents`` 테이블에서 모든 id fetch.

    실패 시 빈 set — 호출 측에서 미존재 판정 → exit 1 발생.
    """
    try:
        # api/ 를 import path 에 추가
        api_path = Path(__file__).resolve().parents[1] / "api"
        if (api_path / "app").exists() and str(api_path) not in sys.path:
            sys.path.insert(0, str(api_path))
        from app.config import get_settings  # noqa: E402
        from app.db import get_supabase_client  # noqa: E402
    except Exception as exc:  # noqa: BLE001
        print(
            f"[WARN] Supabase client import 실패: {exc} — 검증 skip",
            file=sys.stderr,
        )
        return set()
    try:
        client = get_supabase_client()
        settings = get_settings()
        # user_id 필터 + soft-delete 제외 — 골든셋 build 시점과 동일 조건
        resp = (
            client.table("documents")
            .select("id")
            .eq("user_id", settings.default_user_id)
            .is_("deleted_at", "null")
            .limit(10000)
            .execute()
        )
        rows = resp.data or []
        return {r["id"] for r in rows if r.get("id")}
    except Exception as exc:  # noqa: BLE001
        print(
            f"[WARN] Supabase documents fetch 실패: {exc} — 검증 skip",
            file=sys.stderr,
        )
        return set()


def _should_validate(cli_flag: bool) -> bool:
    """CLI 플래그 OR 환경 변수 — 둘 중 하나라도 truthy 면 검증 ON."""
    if cli_flag:
        return True
    env_val = (os.environ.get(_VALIDATE_ENV_VAR) or "").strip().lower()
    return env_val in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="v0.7 auto + v0.6 user → golden_v1.csv 통합"
    )
    parser.add_argument("--auto", type=Path, default=_DEFAULT_AUTO,
                        help=f"auto CSV 경로 (default {_DEFAULT_AUTO.name})")
    parser.add_argument("--user", type=Path, default=_DEFAULT_USER,
                        help=f"user CSV 경로 (default {_DEFAULT_USER.name})")
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT,
                        help=f"출력 CSV 경로 (default {_DEFAULT_OUTPUT.name})")
    parser.add_argument(
        "--validate-doc-ids", action="store_true",
        help=(
            "Phase 2 — merged 결과의 모든 doc_id 가 Supabase documents 테이블에 "
            "존재하는지 검증. 미존재 발견 시 exit 1. 환경변수 "
            f"{_VALIDATE_ENV_VAR}=1 로도 활성 가능."
        ),
    )
    args = parser.parse_args(argv)

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

    # Phase 2 — doc_id 무결성 검증 (default OFF, CLI flag 또는 env var 로 ON)
    if _should_validate(args.validate_doc_ids):
        checked, missing = validate_doc_ids(
            merged, fetch_valid_ids_fn=_fetch_valid_doc_ids_from_supabase
        )
        print(
            f"[VALIDATE] doc_id 검사: 검사 {len(checked)}건 / "
            f"미존재 {len(missing)}건",
            file=sys.stderr,
        )
        if missing:
            preview = missing[:10]
            print(
                f"[ERROR] Supabase documents 에 미존재하는 doc_id "
                f"{len(missing)}건 검출:",
                file=sys.stderr,
            )
            for did in preview:
                print(f"  - {did}", file=sys.stderr)
            if len(missing) > len(preview):
                print(
                    f"  ... 추가 {len(missing) - len(preview)}건",
                    file=sys.stderr,
                )
            print(
                "  → 골든셋 rebuild 또는 stale doc_id 수동 정정 필요",
                file=sys.stderr,
            )
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
