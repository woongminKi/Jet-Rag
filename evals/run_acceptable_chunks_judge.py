"""acceptable_chunks LLM-judge 자동 보완 runner (2026-05-11, 1순위 sprint).

골든셋 `evals/golden_v2.csv` 의 `acceptable_chunks` 가 empty 인 row 에 대해
query + 후보 chunk 텍스트를 Gemini 2.5 Flash judge (JSON mode, temperature 0.0)
로 0.0~1.0 평가 → threshold(0.5) 이상 chunk_idx 를 `acceptable_chunks` 컬럼에
자동 채운다. `relevant_chunks` 는 절대 건드리지 않는다 (read-only).

설계 원칙
- **dry-run 기본** — `--with-acceptable-judge` (또는 env `JETRAG_ACCEPTABLE_JUDGE=1`)
  없으면 선별 row 목록 + 예상 cost 만 출력하고 종료 (cost 0).
- **cost cap fail-close** — `CostGuard(cap_usd=0.30, est_per_unit=0.005)`. 초과 시
  partial ship (caption_dependent 우선 정렬이라 중요 row 먼저 채워짐). 80% ALERT.
- **멱등** — 재실행 시 이미 채워진 row 는 선별에서 자동 제외 → judge 호출 0 →
  CSV 변경 0.
- **CSV 무결성** — `utf-8-sig` BOM 유지, `_V2_FIELDNAMES` 14 컬럼 순서 유지,
  선별 row 의 `acceptable_chunks` 만 갱신 (나머지 컬럼·row bit-identical),
  임시 파일 + `os.replace` atomic write. `--backup` (default ON) 으로 실행 전
  `.bak.YYYYMMDD` 백업.
- **candidate 풀** — 같은 doc 의 BGE-M3 query-cosine top-K (default 15) +
  relevant_chunks 강제 포함 (중복 제거). relevant idx 는 candidate 에 포함해
  judge 에 보내되, judge 결과에서 relevant 와 겹치는 idx 는 acceptable 에서 제외
  (`exclude=relevant`). stale chunk_idx (chunks DB 에 없음) 는 candidate 자연 제외.
- **graceful** — stale doc_id (fetch_chunks 빈 list) / LLM 실패 / parse 실패 →
  해당 row skip, 나머지 진행. 연속 K(=3)회 LLM 실패 시 early stop + partial ship
  (quota exhausted 대비).

CLI / env — §아래 _build_arg_parser 참조. 기본 (flag 없음) = dry-run.
GEMINI_API_KEY 미설정 + judge 모드면 즉시 에러 종료.

CLAUDE.md 정합
- 의존성 추가 0 — stdlib + 기존 helper 재사용.
- paid LLM 호출 (`--with-acceptable-judge`) 은 사용자 명시 승인 시에만.
- 검증은 `evals/eval_retrieval_metrics.py --goldenset golden_v2.csv` 로 R@10 ±0.02.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import tempfile
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

# api/ 를 import path 에 추가 (Gemini client / vision_metrics import 용)
_REPO_ROOT = Path(__file__).resolve().parents[1]
_API_PATH = _REPO_ROOT / "api"
if (_API_PATH / "app").exists() and str(_API_PATH) not in sys.path:
    sys.path.insert(0, str(_API_PATH))

# evals/ 를 import path 에 추가 (sibling 모듈 import)
_EVALS_DIR = Path(__file__).parent
if str(_EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(_EVALS_DIR))

from _acceptable_judge import (  # noqa: E402
    _DEFAULT_JUDGE_MODEL,
    _USAGE_SOURCE_TYPE,
    evaluate_acceptable,
    make_acceptable_judge_caller,
)
from _cost_guard import CostGuard, GuardAction  # noqa: E402
from auto_goldenset import _cosine  # noqa: E402
from build_golden_v2 import ChunkRecord, _V2_FIELDNAMES, fetch_chunks  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

_DEFAULT_INPUT = _EVALS_DIR / "golden_v2.csv"

# CLI / env defaults
_ENV_JUDGE_FLAG = "JETRAG_ACCEPTABLE_JUDGE"
_ENV_JUDGE_MODEL = "JETRAG_ACCEPTABLE_JUDGE_MODEL"
_DEFAULT_THRESHOLD = 0.5
_DEFAULT_MAX_COUNT = 8
_DEFAULT_CANDIDATE_TOP_K = 15
_DEFAULT_COST_CAP_USD = 0.30
_DEFAULT_COST_PER_ROW_USD = 0.005

# 연속 LLM 실패 N회 시 early stop (quota exhausted 대비)
_CONSECUTIVE_FAILURE_LIMIT = 3

_CSV_ENCODING = "utf-8-sig"


# ---------------------------------------------------------------------------
# row 선별 / 정렬
# ---------------------------------------------------------------------------


def _parse_int_csv(value: str | None) -> list[int]:
    """콤마 구분 정수 문자열 → list[int]. 비숫자 토큰은 skip (build_golden_v2 패턴)."""
    if not value:
        return []
    out: list[int] = []
    for tok in str(value).split(","):
        tok = tok.strip()
        if tok and (tok.lstrip("-").isdigit()):
            out.append(int(tok))
    return out


def _is_negative(value: str | None) -> bool:
    return str(value or "").strip().lower() == "true"


def _is_caption_dependent(value: str | None) -> bool:
    return str(value or "").strip().lower() == "true"


def select_rows(rows: list[dict]) -> tuple[list[dict], Counter]:
    """판정 대상 row 선별 + skip 사유별 count.

    선별 조건 (모두 만족): acceptable_chunks empty(strip) AND relevant_chunks 에
    정수 ≥1개 AND negative != "true" AND doc_id non-empty.

    return: (선별 row list — caption_dependent desc → id asc 정렬, skip_reason Counter)
    """
    selected: list[dict] = []
    skipped: Counter = Counter()
    for r in rows:
        if str(r.get("acceptable_chunks", "")).strip():
            skipped["already_filled"] += 1
            continue
        if _is_negative(r.get("negative")):
            skipped["negative"] += 1
            continue
        if not str(r.get("doc_id", "")).strip():
            skipped["no_doc_id"] += 1
            continue
        if not _parse_int_csv(r.get("relevant_chunks")):
            skipped["no_relevant"] += 1
            continue
        selected.append(r)
    # caption_dependent=true 우선 → 같은 그룹 내 id asc.
    selected.sort(key=lambda r: (not _is_caption_dependent(r.get("caption_dependent")), str(r.get("id", ""))))
    return selected, skipped


# ---------------------------------------------------------------------------
# candidate 구성
# ---------------------------------------------------------------------------


def build_candidates(
    *,
    chunks: list[ChunkRecord],
    relevant_idxs: Iterable[int],
    query_vec: list[float],
    top_k: int,
) -> list[tuple[int, str]]:
    """같은 doc chunk 들 → BGE-M3 query-cosine top-K + relevant 강제 포함.

    - top-K: dense_vec 가 있는 chunk 중 cosine 내림차순 K개.
    - relevant idx 중 chunks 에 존재하는 것은 무조건 포함 (중복 제거).
    - stale relevant idx (chunks DB 에 없음) 는 candidate 에 안 들어감 (text 없음).
    - 반환 순서: relevant 우선 (id asc) → 나머지 cosine 순. 총 ≤ top_k + |relevant|.
    """
    by_idx: dict[int, ChunkRecord] = {c.chunk_idx: c for c in chunks}
    # cosine 계산 — dense_vec 있는 chunk 만.
    scored: list[tuple[float, int]] = []
    for c in chunks:
        if c.dense_vec is None or not query_vec:
            continue
        scored.append((_cosine(query_vec, c.dense_vec), c.chunk_idx))
    scored.sort(key=lambda t: -t[0])
    top_idxs = [idx for _, idx in scored[:max(top_k, 0)]]

    ordered: list[int] = []
    seen: set[int] = set()
    # relevant 우선 (chunks 에 존재하는 것만).
    for idx in sorted(set(relevant_idxs)):
        if idx in by_idx and idx not in seen:
            ordered.append(idx)
            seen.add(idx)
    for idx in top_idxs:
        if idx not in seen:
            ordered.append(idx)
            seen.add(idx)
    return [(idx, by_idx[idx].text) for idx in ordered if idx in by_idx]


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding=_CSV_ENCODING, newline="") as f:
        return list(csv.DictReader(f))


def _write_csv_atomic(path: Path, rows: list[dict]) -> None:
    """임시 파일에 전체 rows 쓰고 os.replace — atomic. utf-8-sig BOM + 14 컬럼 순서.

    DictWriter fieldnames 를 `_V2_FIELDNAMES` 로 고정 → 컬럼 순서 보존.
    선별 row 가 아닌 row 는 호출부에서 변경 없이 그대로 전달되므로 bit-identical.
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=_CSV_ENCODING, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(_V2_FIELDNAMES))
            writer.writeheader()
            for r in rows:
                # _V2_FIELDNAMES 에 없는 키는 무시. None (DictReader short row) → "".
                writer.writerow({k: (r.get(k) if r.get(k) is not None else "") for k in _V2_FIELDNAMES})
        os.replace(tmp_name, path)
    except BaseException:
        # 실패 시 임시 파일 정리.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _backup_csv(path: Path) -> Path:
    """실행 전 백업 — `<path>.bak.YYYYMMDD`. 같은 날 재실행 시 덮어씀."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    backup_path = path.with_name(path.name + f".bak.{stamp}")
    backup_path.write_bytes(path.read_bytes())
    return backup_path


# ---------------------------------------------------------------------------
# DB / embed 의존성 (실행 모드에서 lazy import)
# ---------------------------------------------------------------------------


def _build_real_deps() -> tuple[Callable[[str], list[ChunkRecord]], Callable[[str], list[float]]]:
    """실 Supabase client + BGE-M3 embedder 주입 — (fetch_chunks_fn, embed_query_fn).

    lazy import — dry-run 경로에서는 호출되지 않으므로 외부 의존 비발동.
    """
    from app.adapters.impl.bgem3_hf_embedding import BGEM3HFEmbeddingProvider
    from app.db import get_supabase_client

    client = get_supabase_client()
    embedder = BGEM3HFEmbeddingProvider()

    def fetch_chunks_fn(doc_id: str) -> list[ChunkRecord]:
        return fetch_chunks(client, doc_id)

    def embed_query_fn(query: str) -> list[float]:
        return embedder.embed_query(query)

    return fetch_chunks_fn, embed_query_fn


# ---------------------------------------------------------------------------
# 실측 cost 합산 (종료 후 stderr 출력)
# ---------------------------------------------------------------------------


def _sum_actual_cost(*, since: datetime) -> float | None:
    """vision_usage_log 에서 source_type='acceptable_judge' 인 row 의 estimated_cost 합산.

    `since` 이후 row 만 — 같은 테이블의 과거 multimodal_judge / 이전 run 영향 배제.
    DB 부재 / 마이그레이션 미적용 시 None 반환 (graceful).
    """
    try:
        from app.db import get_supabase_client

        client = get_supabase_client()
        rows = (
            client.table("vision_usage_log")
            .select("estimated_cost")
            .eq("source_type", _USAGE_SOURCE_TYPE)
            .gte("called_at", since.isoformat())
            .execute()
            .data
            or []
        )
        return sum(float(r.get("estimated_cost") or 0.0) for r in rows)
    except Exception as exc:  # noqa: BLE001 — DB 부재 graceful
        logger.debug("실측 cost 합산 실패 (graceful): %s", exc)
        return None


# ---------------------------------------------------------------------------
# 핵심 로직
# ---------------------------------------------------------------------------


class _FailureTrackingCaller:
    """`judge_call_fn` wrapper — raw LLM 호출 실패를 카운트.

    `evaluate_acceptable` 은 LLM 실패와 "모두 < threshold" 를 모두 [] 로 반환해
    구분이 불가하다. 따라서 runner 는 judge_call_fn 자체를 wrap 해 호출 단계의
    실패 (RuntimeError / quota exhausted 등) 만 명시 카운트한다. parse 실패는 LLM
    응답은 받은 것이므로 (호출 성공) 별도로 본다.
    """

    def __init__(self, inner: Callable[[str, str], str]) -> None:
        self._inner = inner
        self.consecutive_failures = 0
        self.total_failures = 0

    def __call__(self, system_prompt: str, user_prompt: str) -> str:
        try:
            raw = self._inner(system_prompt, user_prompt)
        except Exception:
            self.consecutive_failures += 1
            self.total_failures += 1
            raise
        self.consecutive_failures = 0
        return raw


def run(
    *,
    rows: list[dict],
    judge_call_fn: Callable[[str, str], str],
    fetch_chunks_fn: Callable[[str], list[ChunkRecord]],
    embed_query_fn: Callable[[str], list[float]],
    threshold: float,
    max_count: int,
    candidate_top_k: int,
    cost_guard: CostGuard,
    max_rows: int | None,
    consecutive_failure_limit: int = _CONSECUTIVE_FAILURE_LIMIT,
) -> dict:
    """선별 row 를 순회하며 judge 호출 → acceptable_chunks 갱신 (in-place on dict).

    return: 요약 dict — total_selected / processed / filled / partial /
            consecutive_fail_stop / llm_failures / skipped.
    """
    selected, skipped = select_rows(rows)
    if max_rows is not None:
        selected = selected[:max_rows]

    total = len(selected)
    processed = 0
    filled = 0
    partial = False
    consecutive_fail_stop = False
    tracking_caller = _FailureTrackingCaller(judge_call_fn)

    for i, r in enumerate(selected, start=1):
        action = cost_guard.before_unit(unit_n=1)
        if action == GuardAction.BREAK:
            print(
                f"[cost-cap] {cost_guard.summary()} → break, partial ship ({i - 1}/{total})",
                file=sys.stderr,
            )
            partial = True
            break
        if action == GuardAction.ALERT:
            print(f"[cost-cap] ⚠ {cost_guard.summary()}", file=sys.stderr)

        qid = str(r.get("id", "")) or f"row#{i}"
        doc_id = str(r.get("doc_id", "")).strip()
        query = unicodedata.normalize("NFC", str(r.get("query", "")))
        relevant_idxs = _parse_int_csv(r.get("relevant_chunks"))

        try:
            chunks = fetch_chunks_fn(doc_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[stale-doc] {qid} — fetch_chunks 실패: {exc}", file=sys.stderr)
            skipped["fetch_failed"] += 1
            continue
        if not chunks:
            print(f"[stale-doc] {qid} — chunks 0건 (doc_id={doc_id[:8]})", file=sys.stderr)
            skipped["stale_doc"] += 1
            continue

        try:
            query_vec = embed_query_fn(query)
        except Exception as exc:  # noqa: BLE001
            print(f"[embed-fail] {qid} — query embed 실패: {exc}", file=sys.stderr)
            skipped["embed_failed"] += 1
            continue

        candidates = build_candidates(
            chunks=chunks,
            relevant_idxs=relevant_idxs,
            query_vec=query_vec,
            top_k=candidate_top_k,
        )
        if not candidates:
            print(f"[no-candidate] {qid} — candidate 0건", file=sys.stderr)
            skipped["no_candidate"] += 1
            continue

        acc = evaluate_acceptable(
            query=query,
            candidates=candidates,
            judge_call_fn=tracking_caller,
            threshold=threshold,
            max_count=max_count,
            exclude=relevant_idxs,
        )
        # est cost 로 guard 갱신 — 실측은 종료 후 vision_usage_log 합산.
        cost_guard.add_actual(cost_guard.est_per_unit)
        processed += 1

        if acc:
            r["acceptable_chunks"] = ",".join(str(x) for x in acc)
            filled += 1
            print(f"[filled] {qid} — acceptable={r['acceptable_chunks']}", file=sys.stderr)
        else:
            # LLM 호출은 성공했으나 모두 < threshold → 정상 빈 결과. row 변경 0.
            # LLM 호출 실패였다면 tracking_caller.consecutive_failures 가 올라감.
            print(f"[empty] {qid} — acceptable 채울 chunk 없음 (변경 0)", file=sys.stderr)

        if tracking_caller.consecutive_failures >= consecutive_failure_limit:
            print(
                f"[early-stop] 연속 {tracking_caller.consecutive_failures}회 LLM 호출 실패 — "
                f"partial ship ({i}/{total}). quota exhausted 가능성.",
                file=sys.stderr,
            )
            consecutive_fail_stop = True
            partial = True
            break

    return {
        "total_selected": total,
        "processed": processed,
        "filled": filled,
        "partial": partial,
        "consecutive_fail_stop": consecutive_fail_stop,
        "llm_failures": tracking_caller.total_failures,
        "skipped": dict(skipped),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_acceptable_chunks_judge",
        description="acceptable_chunks LLM-judge 자동 보완 (기본 dry-run)",
    )
    p.add_argument(
        "--with-acceptable-judge",
        action="store_true",
        default=(os.environ.get(_ENV_JUDGE_FLAG, "") == "1"),
        help=f"judge 실행 (paid). 없으면 dry-run. env {_ENV_JUDGE_FLAG}=1 와 동일.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="judge flag 있어도 강제 dry-run (선별 row + 예상 cost 만 출력).",
    )
    p.add_argument(
        "--acceptable-judge-model",
        default=os.environ.get(_ENV_JUDGE_MODEL, _DEFAULT_JUDGE_MODEL),
        help=f"judge model (default {_DEFAULT_JUDGE_MODEL}, env {_ENV_JUDGE_MODEL}).",
    )
    p.add_argument("--acceptable-threshold", type=float, default=_DEFAULT_THRESHOLD,
                   help=f"acceptable score 임계 (default {_DEFAULT_THRESHOLD}).")
    p.add_argument("--acceptable-max-count", type=int, default=_DEFAULT_MAX_COUNT,
                   help=f"row 당 acceptable_chunks 최대 개수 (default {_DEFAULT_MAX_COUNT}).")
    p.add_argument("--candidate-top-k", type=int, default=_DEFAULT_CANDIDATE_TOP_K,
                   help=f"BGE-M3 cosine top-K candidate (default {_DEFAULT_CANDIDATE_TOP_K}).")
    p.add_argument("--cost-cap-usd", type=float, default=_DEFAULT_COST_CAP_USD,
                   help=f"누적 cost cap USD (default {_DEFAULT_COST_CAP_USD}).")
    p.add_argument("--cost-per-row-usd", type=float, default=_DEFAULT_COST_PER_ROW_USD,
                   help=f"row 당 추정 cost USD (default {_DEFAULT_COST_PER_ROW_USD}).")
    p.add_argument("--max-rows", type=int, default=None,
                   help="처리할 최대 row 수 (default 무제한).")
    p.add_argument("--input", type=Path, default=_DEFAULT_INPUT,
                   help=f"입력 CSV (default {_DEFAULT_INPUT}).")
    p.add_argument("--output", type=Path, default=None,
                   help="출력 CSV (default = --input, in-place).")
    p.add_argument("--backup", action=argparse.BooleanOptionalAction, default=True,
                   help="실행 전 `.bak.YYYYMMDD` 백업 (default ON, --no-backup 으로 끄기).")
    p.add_argument("--verbose", action="store_true", help="DEBUG 로그.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    input_path: Path = args.input
    output_path: Path = args.output or input_path
    if not input_path.exists():
        print(f"[error] 입력 CSV 없음: {input_path}", file=sys.stderr)
        return 2

    rows = _read_csv(input_path)
    selected, skipped = select_rows(rows)

    judge_mode = args.with_acceptable_judge and not args.dry_run
    est_cost = len(selected) * args.cost_per_row_usd

    # --- dry-run ---
    if not judge_mode:
        print(f"[dry-run] 입력: {input_path}", file=sys.stderr)
        print(f"[dry-run] 선별 row: {len(selected)}건", file=sys.stderr)
        for r in selected:
            cap = "caption" if _is_caption_dependent(r.get("caption_dependent")) else "-"
            print(f"  - {r.get('id')} [{r.get('query_type')}] [{cap}] doc={str(r.get('doc_id',''))[:8]} "
                  f"relevant={r.get('relevant_chunks')}", file=sys.stderr)
        print(f"[dry-run] skip 사유별: {dict(skipped)}", file=sys.stderr)
        print(f"[dry-run] 예상 cost: ~${est_cost:.4f} (cap ${args.cost_cap_usd:.2f}, "
              f"row당 ${args.cost_per_row_usd:.4f}). 실행하려면 --with-acceptable-judge.", file=sys.stderr)
        return 0

    # --- 실행 모드 ---
    if not os.environ.get("GEMINI_API_KEY"):
        print("[error] GEMINI_API_KEY 미설정 — judge 실행 불가. .env 확인.", file=sys.stderr)
        return 2

    if not selected:
        print("[done] 선별 row 0건 — 모두 이미 채워졌거나 조건 미충족. CSV 변경 없음.", file=sys.stderr)
        print(f"[done] skip 사유별: {dict(skipped)}", file=sys.stderr)
        return 0

    started_at = datetime.now(timezone.utc)

    if args.backup:
        backup_path = _backup_csv(input_path)
        print(f"[backup] {backup_path}", file=sys.stderr)

    print(f"[run] 선별 {len(selected)}건 (max_rows={args.max_rows}), "
          f"예상 cost ~${est_cost:.4f} / cap ${args.cost_cap_usd:.2f}, model={args.acceptable_judge_model}",
          file=sys.stderr)

    cost_guard = CostGuard(cap_usd=args.cost_cap_usd, est_per_unit=args.cost_per_row_usd)
    judge_call_fn = make_acceptable_judge_caller(model=args.acceptable_judge_model)
    fetch_chunks_fn, embed_query_fn = _build_real_deps()

    summary = run(
        rows=rows,
        judge_call_fn=judge_call_fn,
        fetch_chunks_fn=fetch_chunks_fn,
        embed_query_fn=embed_query_fn,
        threshold=args.acceptable_threshold,
        max_count=args.acceptable_max_count,
        candidate_top_k=args.candidate_top_k,
        cost_guard=cost_guard,
        max_rows=args.max_rows,
    )

    # CSV write — 선별 row 의 acceptable_chunks 만 갱신된 상태. filled=0 이어도
    # 멱등성 검증 위해 항상 atomic rewrite (내용 동일하면 bit-identical).
    _write_csv_atomic(output_path, rows)

    actual_cost = _sum_actual_cost(since=started_at)
    print("=" * 60, file=sys.stderr)
    print(f"[summary] 선별 {summary['total_selected']}건 / 처리 {summary['processed']}건 / "
          f"채움 {summary['filled']}건", file=sys.stderr)
    print(f"[summary] skip 사유별: {summary['skipped']}", file=sys.stderr)
    print(f"[summary] partial={summary['partial']} consecutive_fail_stop={summary['consecutive_fail_stop']}",
          file=sys.stderr)
    print(f"[summary] 추정 cost: ~${cost_guard.actual_total:.4f} / cap ${args.cost_cap_usd:.2f}", file=sys.stderr)
    if actual_cost is not None:
        print(f"[summary] 실측 cost (vision_usage_log, source_type={_USAGE_SOURCE_TYPE}): ${actual_cost:.4f}",
              file=sys.stderr)
    else:
        print("[summary] 실측 cost: 조회 불가 (DB 부재 / 마이그레이션 미적용)", file=sys.stderr)
    print(f"[summary] 출력: {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
