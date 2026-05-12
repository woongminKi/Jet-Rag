"""sample-report (doc d1259dfe…) dense_vec 결손 복구 — embed-only 재실행 도구.

배경 (senior-qa 감사 + DB 조회 결과, 2026-05-12)
    sample-report 의 1000 chunk 가 전부 `dense_vec IS NULL`. 2026-05-11 D5 회복
    (`_phase4_d5_recovery_3docs.py`) 직후 embed 스테이지가 HF BGE-M3 cold-start 로
    예외 없이 멈춤 → `ingest_jobs` 에 `status=running, current_stage=embed` stale row
    (`cd912e70-…`, started_at 2026-05-11 05:53:58, finished_at NULL) 잔존.
    chunks.text 는 채워져 있고 vision-derived chunk (102 page, `section_title` 가
    `(vision) p.N` 으로 시작) 도 포함 — 즉 콘텐츠는 정상, dense 임베딩만 누락.

왜 embed-only 인가 (재인제스트 대신)
    - sparse(키워드) 검색은 PGroonga FTS on `chunks.text` → 이미 동작 중. `sparse_json`
      이 `'{}'` 인 것은 설계상 정상 (인제스트 파이프라인이 sparse_json 을 안 채움).
      따라서 "완전 dark" 가 아니라 "dense path 만 dark".
    - 1000 chunk 가 이미 vision 정보를 포함하므로 재인제스트의 이점이 없음.
    - `POST /reingest` (full pipeline) 재실행은 embed 스테이지에서 또 cold-start 로
      예외 → `_cleanup_failed_doc` 가 1000 chunk 전부 삭제하는 위험이 있음.
      본 도구는 chunks 를 절대 삭제하지 않음 + cold-start 내성 (warmup ping + 작은
      batch + Retry-After 존중하는 어댑터 retry + batch 단위 재시도) + resumable
      (`dense_vec IS NULL` 필터 → 부분 실행도 진전, 재실행 시 이어서).

동작
    1. (--apply) 시작 시 BGE-M3 warmup ping 1회 → HF scale-to-zero cold-start 흡수.
    2. `chunks` 에서 doc_id 의 `dense_vec IS NULL` row 를 chunk_idx 순으로 조회.
    3. _BATCH_SIZE (default 16) 씩 `embed_batch` → 각 row 를 id 기준 단건 UPDATE
       (embed.py 와 동일 패턴 — supabase upsert 의 NULL 컬럼 문제 회피).
    4. batch 실패 시 _BATCH_MAX_RETRIES 회까지 지수 백오프 후 재시도. 그래도 실패하면
       해당 batch 만 skip 하고 다음 batch 진행 (재실행 시 자연 resumable).
    5. batch 사이 _INTER_BATCH_SLEEP_S 짧은 sleep — HF rate-limit 완화.
    6. (--apply, --fix-stale-job) stale `running` job 을 status=failed + error_msg 마킹.
    7. 끝에 검증 쿼리 — 남은 `dense_vec IS NULL` 건수 출력.

기본 dry-run. `--apply` 로만 실제 쓰기. 운영 코드 변경 0 (embed.py / pipeline / adapter
모두 그대로 — 본 스크립트가 embed.py 의 로직을 cold-start 내성 있게 재구성).

사용
    # dry-run (DB 변경 0 — 대상 건수 + sample 출력)
    cd api && uv run python ../evals/_repair_sample_report_dense_vec.py

    # 실제 복구 (HF 호출 ~63 batch — cold-start 시 첫 batch 가 수십 초 소요 가능)
    cd api && uv run python ../evals/_repair_sample_report_dense_vec.py --apply

    # stale job 도 같이 정리
    cd api && uv run python ../evals/_repair_sample_report_dense_vec.py --apply --fix-stale-job

    # 다른 doc / batch 크기 조정
    cd api && uv run python ../evals/_repair_sample_report_dense_vec.py --apply --doc-id <UUID> --batch-size 8

비용 / SLO
    - HF BGE-M3 Inference API = 무료 티어. embed 호출은 무료 (rate-limit 만 주의).
    - vision 호출 0, Gemini 호출 0, 추가 paid 비용 0.
    - 1000 chunk / batch 16 = 63 batch. cold-start 없으면 batch 당 0.5~2s ≈ 1~2분.
      cold-start 1회 (~95s worst, 어댑터 3-retry) 포함해도 ~3~4분. SLO PDF50p<60s 는
      인제스트 신규 경로 기준이라 본 복구 도구에는 해당 없음.

롤백
    본 도구는 chunks 의 `dense_vec` 만 NULL → 벡터로 채움. 되돌리려면 Supabase SQL
    Editor 에서:
        UPDATE chunks SET dense_vec = NULL WHERE doc_id = '<doc_id>';
    (단 그러면 다시 dense dark 상태 — 권장하지 않음.)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "api"))

# noqa: E402 — sys.path 확정 후 import
from app.db.client import get_supabase_client  # noqa: E402

# 대상 doc — sample-report (senior-qa 감사 대상).
_DEFAULT_DOC_ID = "d1259dfe-c402-4cd0-bb04-3e67d88a2773"

# embed.py 와 동일 batch 크기. cold-start 잦으면 --batch-size 8 등으로 작게.
_DEFAULT_BATCH_SIZE = 16
# batch 1건이 transient 로 실패하면 (어댑터 내부 3-retry 도 소진 후) 본 도구가 재시도.
_BATCH_MAX_RETRIES = 3
_BATCH_RETRY_BASE_S = 5.0
# batch 사이 짧은 sleep — HF rate-limit 완화 (무료 티어 RPM 보호).
_INTER_BATCH_SLEEP_S = 0.5
# dry-run sample 출력 개수.
_SAMPLE_N = 5
# stale job 마킹 시 error_msg.
_STALE_JOB_ERROR_MSG = (
    "embed 스테이지 중 프로세스 종료로 stale running 상태 — "
    "_repair_sample_report_dense_vec.py 로 dense_vec 복구 + job 정리"
)


def _fetch_null_dense_chunks(client, doc_id: str) -> list[dict]:
    """doc_id 의 dense_vec NULL chunk 를 chunk_idx 순으로 (id, text) 만 조회."""
    resp = (
        client.table("chunks")
        .select("id, chunk_idx, text")
        .eq("doc_id", doc_id)
        .is_("dense_vec", "null")
        .order("chunk_idx")
        .execute()
    )
    return resp.data or []


def _count_null_dense(client, doc_id: str) -> int:
    """검증용 — 남은 dense_vec NULL 건수."""
    resp = (
        client.table("chunks")
        .select("id", count="exact")
        .eq("doc_id", doc_id)
        .is_("dense_vec", "null")
        .execute()
    )
    return resp.count or 0


def _count_total_chunks(client, doc_id: str) -> int:
    resp = (
        client.table("chunks")
        .select("id", count="exact")
        .eq("doc_id", doc_id)
        .execute()
    )
    return resp.count or 0


def _count_vision_derived(client, doc_id: str) -> int:
    """vision-derived chunk 수 (`section_title` 가 `(vision) p.` 로 시작)."""
    resp = (
        client.table("chunks")
        .select("id", count="exact")
        .eq("doc_id", doc_id)
        .like("section_title", "(vision) p.%")
        .execute()
    )
    return resp.count or 0


def _find_stale_embed_jobs(client, doc_id: str) -> list[dict]:
    """doc_id 의 running 상태 + current_stage=embed + finished_at NULL job."""
    resp = (
        client.table("ingest_jobs")
        .select("id, status, current_stage, attempts, started_at, finished_at, error_msg")
        .eq("doc_id", doc_id)
        .eq("status", "running")
        .is_("finished_at", "null")
        .execute()
    )
    return [r for r in (resp.data or []) if (r.get("current_stage") or "") == "embed"]


def _mark_stale_job_failed(client, job_id: str) -> None:
    from datetime import datetime, timezone

    (
        client.table("ingest_jobs")
        .update(
            {
                "status": "failed",
                "error_msg": _STALE_JOB_ERROR_MSG,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        .eq("id", job_id)
        .execute()
    )


def _embed_batch_with_retry(provider, texts: list[str], *, batch_no: int):
    """1 batch 임베딩 — 어댑터 내부 3-retry 도 소진된 transient 실패를 본 도구가 추가 재시도.

    반환: list[EmbeddingResult] | None (최종 실패 시 None — caller 가 batch skip).
    """
    last_exc: Exception | None = None
    for attempt in range(1, _BATCH_MAX_RETRIES + 1):
        try:
            return provider.embed_batch(texts)
        except Exception as exc:  # noqa: BLE001 — batch 단위 graceful
            last_exc = exc
            if attempt == _BATCH_MAX_RETRIES:
                break
            delay = _BATCH_RETRY_BASE_S * (2 ** (attempt - 1))
            print(
                f"  [batch {batch_no}] 실패 (attempt {attempt}/{_BATCH_MAX_RETRIES}, "
                f"{delay:.0f}s 후 재시도): {exc}"
            )
            time.sleep(delay)
    print(
        f"  [batch {batch_no}] 최종 실패 (skip — 재실행 시 이어서 처리됨): {last_exc}"
    )
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="sample-report dense_vec 결손 복구 (embed-only). 기본 dry-run."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 DB 쓰기 (없으면 dry-run — 대상 건수만 출력).",
    )
    parser.add_argument(
        "--doc-id",
        default=_DEFAULT_DOC_ID,
        help=f"대상 doc_id (default: {_DEFAULT_DOC_ID} = sample-report).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=_DEFAULT_BATCH_SIZE,
        help=f"HF embed_batch 당 chunk 수 (default {_DEFAULT_BATCH_SIZE}). "
        "cold-start 잦으면 8 등으로 작게.",
    )
    parser.add_argument(
        "--fix-stale-job",
        action="store_true",
        help="doc 의 stale running embed job 을 status=failed 로 마킹 (--apply 필요).",
    )
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="시작 warmup ping skip (디버깅용 — 운영 복구 시 권장 안 함).",
    )
    args = parser.parse_args()

    doc_id = args.doc_id
    batch_size = max(1, args.batch_size)
    mode = "APPLY" if args.apply else "DRY-RUN"

    client = get_supabase_client()

    # --- 사전 상태 스냅샷 ---
    total = _count_total_chunks(client, doc_id)
    null_before = _count_null_dense(client, doc_id)
    vision_derived = _count_vision_derived(client, doc_id)
    stale_jobs = _find_stale_embed_jobs(client, doc_id)

    print("=" * 72)
    print(f"[{mode}] sample-report dense_vec 복구 — doc_id={doc_id}")
    print("=" * 72)
    print(f"  chunks 총 {total}건 / dense_vec NULL {null_before}건 "
          f"({(null_before / total * 100) if total else 0:.1f}%)")
    print(f"  vision-derived chunk (section_title '(vision) p.%'): {vision_derived}건")
    if stale_jobs:
        for j in stale_jobs:
            print(
                f"  stale job: id={j['id']} status={j['status']} stage={j['current_stage']} "
                f"attempts={j.get('attempts')} started_at={j.get('started_at')} "
                f"finished_at={j.get('finished_at')}"
            )
    else:
        print("  stale running embed job: 없음")

    rows = _fetch_null_dense_chunks(client, doc_id)
    if not rows:
        print("\n  dense_vec NULL chunk 없음 — 복구할 것 없음.")
        if args.fix_stale_job and stale_jobs and args.apply:
            for j in stale_jobs:
                _mark_stale_job_failed(client, j["id"])
                print(f"  [stale job] {j['id']} → status=failed 마킹")
        return 0

    n_batches = (len(rows) + batch_size - 1) // batch_size
    print(f"\n  처리 대상 {len(rows)}건 → batch_size={batch_size} × {n_batches} batch")
    print("  sample (chunk_idx, text 앞 60자):")
    for r in rows[:_SAMPLE_N]:
        snippet = (r.get("text") or "").replace("\n", " ")[:60]
        print(f"    idx={r['chunk_idx']:>4}  {snippet}…")

    if not args.apply:
        print("\n  [DRY-RUN] --apply 없이는 DB 쓰기 안 함. 위 대상에 대해 embed_batch 호출 예정.")
        if stale_jobs:
            print(f"  [DRY-RUN] --fix-stale-job 지정 시 stale job {len(stale_jobs)}건 → failed 마킹 예정.")
        print("\n  실제 실행:")
        print(f"    cd api && uv run python ../evals/_repair_sample_report_dense_vec.py --apply"
              + (" --fix-stale-job" if stale_jobs else ""))
        return 0

    # --- APPLY ---
    # provider 는 HF 토큰 필요 — 여기서 처음 import (dry-run 은 토큰 없이도 동작).
    from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider

    provider = get_bgem3_provider()

    if not args.no_warmup:
        print("\n  [warmup] BGE-M3 cold-start ping … (수 초~수십 초 소요 가능)")
        t_w = time.time()
        try:
            provider.embed_query("warmup")
            print(f"  [warmup] OK ({time.time() - t_w:.1f}s)")
        except Exception as exc:  # noqa: BLE001 — warmup best-effort
            print(f"  [warmup] 실패 (무시, 첫 batch 에서 재시도됨): {exc}")

    print(f"\n  [embed] {len(rows)}건 / {n_batches} batch 시작")
    t_start = time.time()
    updated = 0
    skipped_batches = 0
    for i in range(0, len(rows), batch_size):
        batch_no = i // batch_size + 1
        batch = rows[i : i + batch_size]
        texts = [r["text"] for r in batch]
        embeddings = _embed_batch_with_retry(provider, texts, batch_no=batch_no)
        if embeddings is None:
            skipped_batches += 1
            continue
        for r, emb in zip(batch, embeddings):
            (
                client.table("chunks")
                .update({"dense_vec": emb.dense})
                .eq("id", r["id"])
                .execute()
            )
        updated += len(batch)
        if batch_no % 5 == 0 or batch_no == n_batches:
            print(
                f"  [embed] batch {batch_no}/{n_batches}  누적 {updated}/{len(rows)} "
                f"({time.time() - t_start:.1f}s)"
            )
        time.sleep(_INTER_BATCH_SLEEP_S)

    elapsed = time.time() - t_start
    print(f"\n  [embed] 완료 — {updated}건 채움, batch skip {skipped_batches}건 ({elapsed:.1f}s)")

    # --- stale job 정리 ---
    if args.fix_stale_job:
        if stale_jobs:
            for j in stale_jobs:
                _mark_stale_job_failed(client, j["id"])
                print(f"  [stale job] {j['id']} → status=failed 마킹")
        else:
            print("  [stale job] 정리 대상 없음")
    elif stale_jobs:
        print(f"  [stale job] {len(stale_jobs)}건 잔존 — --fix-stale-job 로 정리 권장")

    # --- 검증 ---
    null_after = _count_null_dense(client, doc_id)
    print("\n" + "=" * 72)
    print("[검증]")
    print(f"  dense_vec NULL: {null_before} → {null_after}")
    print(f"  chunks 총: {total}")
    if null_after == 0:
        print("  ✅ dense_vec 결손 0 — dense + sparse(PGroonga) 양쪽 검색 정상.")
        rc = 0
    else:
        print(f"  ⚠ 아직 {null_after}건 NULL — batch skip 되었거나 transient 실패. 본 도구 재실행 권장.")
        rc = 1
    print("=" * 72)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
