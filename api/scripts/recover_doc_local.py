"""문서 1건을 레거시 Python 파이프라인으로 **로컬에서** 재생성한다 — Edge 가 처리 못 하는 대형 문서 복구용.

    cd api && JETRAG_EMBED_PROVIDER=deepinfra PYTHONPATH=. uv run python scripts/recover_doc_local.py <doc_id> [--embed-only <job_id>]

배경(2026-09-15): Edge `chunk` 단계는 extract 산출물을 한 태스크에서 처리해 대형 문서(SK 사업보고서,
추출물 20MB·청크 25,831)에서 런타임 kill 로 실패했다. 재인제스트가 청크를 먼저 지우므로 문서가 청크 0 이
됐고, 5월 원본 처리기인 이 Python 파이프라인으로 복구했다(173s, 25,806 청크).

주의
- **운영 DB 를 직접 만진다.** `.env` 의 SUPABASE_URL/SERVICE_ROLE_KEY 를 쓴다.
- `JETRAG_EMBED_PROVIDER=deepinfra` 를 꼭 준다. 기본값 hf 는 HF 토큰이 없으면 401 이고,
  파이프라인 실패 정리가 **청크를 다시 지운다**(2026-09-15 1차 시도에서 겪음).
- `run_embed_stage` 는 PostgREST 기본 1,000행 상한으로 한 번에 1,000개만 채운다.
  `--embed-only <job_id>` 는 NULL 이 0 이 될 때까지 반복한다(약 460건/분).
- 여기서 "NULL" 은 **`flags->>filtered_reason` 이 없는** 청크의 dense_vec NULL 이다.
  필터된 청크는 `run_embed_stage` 가 건너뛰므로 계속 NULL 로 남는 게 정상이다.
"""
from __future__ import annotations

import logging
import os
import sys
import time

os.environ.setdefault("HF_API_TOKEN", "dummy")
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

from app.db import get_supabase_client  # noqa: E402
from app.ingest.jobs import create_job  # noqa: E402
from app.ingest.pipeline import run_pipeline  # noqa: E402
from app.ingest.stages.embed import run_embed_stage  # noqa: E402


def _count(sb, doc_id: str, *, null_only: bool) -> int:
    """null_only=True 는 **run_embed_stage 가 실제로 채울 행**만 센다.

    `run_embed_stage` 가 `flags->>filtered_reason IS NULL` 도 걸고 뽑기 때문에,
    여기서 그 조건을 빼면 필터된 청크가 영원히 NULL 로 남아 `embed_rest` 의
    루프가 max_rounds 까지 헛돈다(종료 조건이 조회 조건과 같아야 한다).
    """
    q = sb.table("chunks").select("id", count="exact").eq("doc_id", doc_id)
    if null_only:
        q = q.is_("dense_vec", "null").is_("flags->>filtered_reason", "null")
    return q.limit(1).execute().count or 0


def embed_rest(sb, doc_id: str, job_id: str, max_rounds: int = 60) -> int:
    t0 = time.time()
    for i in range(1, max_rounds + 1):
        n = _count(sb, doc_id, null_only=True)
        if n == 0:
            break
        done = run_embed_stage(job_id, doc_id=doc_id)
        print(f"round {i}: remaining {n} → embedded {done} (+{time.time() - t0:.0f}s)", flush=True)
    final = _count(sb, doc_id, null_only=True)
    print(f"final dense_vec NULL: {final} ({time.time() - t0:.0f}s)", flush=True)
    return final


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    doc_id = argv[1]
    if os.environ.get("JETRAG_EMBED_PROVIDER") != "deepinfra":
        print("JETRAG_EMBED_PROVIDER=deepinfra 를 지정하라 (hf 기본값은 401 → 청크 롤백).", file=sys.stderr)
        return 2
    sb = get_supabase_client()
    if "--embed-only" in argv:
        job_id = argv[argv.index("--embed-only") + 1]
        return 0 if embed_rest(sb, doc_id, job_id) == 0 else 1

    print("before chunks:", _count(sb, doc_id, null_only=False), flush=True)
    job = create_job(doc_id=doc_id)
    print("job:", job.id, flush=True)
    t0 = time.time()
    run_pipeline(job.id, doc_id)
    st = sb.table("ingest_jobs").select("status,current_stage,error_msg").eq("id", job.id).single().execute().data
    print(f"after chunks: {_count(sb, doc_id, null_only=False)}, job: {st}, {time.time() - t0:.0f}s", flush=True)
    if st["status"] != "completed":
        return 1
    return 0 if embed_rest(sb, doc_id, job.id) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
