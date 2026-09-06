"""현행 인제스트의 **기준선**을 샌드박스에서 뜬다 (Phase 3 대조의 기준).

Phase 3 은 인제스트를 재작성한다. "결과가 현행과 동등"을 판정하려면 **먼저 현행이 무엇을
만드는지** 고정해 둬야 한다. 이 스크립트가 그 기준선을 만든다.

## 원본 핸들러를 그대로 태운다
`run_full_ingest` 를 직접 부르지 않고 **`upload_document` 핸들러**를 부른다. dedup·검증·
`documents`/`ingest_jobs` 행 생성까지 원본 경로를 그대로 지나가야 기준선이 의미가 있다.
FastAPI 가 응답 후 실행하는 `BackgroundTasks` 는 여기서 **수동으로** 돌린다.

## 무엇을 기준선에 담는가
인제스트에는 **비결정적 단계가 섞여 있다** — 임베딩(DeepInfra), 태그·요약(LLM),
vision 캡션(Gemini). 그대로 저장하면 재현이 안 되므로 성격을 나눈다:

| 성격 | 단계 | 기준선에 담는 것 |
|---|---|---|
| 결정적 | extract · chunk · chunk_filter · dedup | 청크별 **sha256 + 길이 + 위치**, 개수 |
| 비결정적 | embed · doc_embed · tag_summarize · vision | **존재·개수·차원**만 |

## 본문을 저장하지 않는다
`assets/private/` 와 루트의 `*.pdf` 류는 gitignore 대상이다. 기준선이 저장소에 남는 파일
이므로 **텍스트 원문을 넣지 않고 해시로만** 적는다. `assets/public/` 은 추적 중인 공개
fixture 지만 같은 규칙을 적용한다 — 나중에 private 자산으로 넓혀도 안전해야 한다.

사용:
    api/.venv/bin/python api/scripts/ingest_baseline.py --file "assets/public/law_sample1.hwp"
    api/.venv/bin/python api/scripts/ingest_baseline.py --file ... --keep   # 정리 안 함
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "api"))
sys.path.insert(0, HERE)

BASELINE_DIR = os.path.join(HERE, "fixtures", "ingest_baselines")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def snapshot(client, user_id: str) -> dict:
    """샌드박스 사용자의 인제스트 산출물을 성격별로 정리한다."""
    docs = (
        client.table("documents").select("*").eq("user_id", user_id)
        .order("created_at").execute().data
    ) or []
    out_docs = []
    for d in docs:
        doc_id = d["id"]
        # `dense_vec` 은 1024 차원이라 통째로 받으면 무겁다 — **개수만 count 질의로** 센다.
        chunks = (
            client.table("chunks")
            .select("chunk_idx,page,section_title,text,flags,metadata,bbox,char_range")
            .eq("doc_id", doc_id).order("chunk_idx").execute().data
        ) or []
        embedded_n = (
            client.table("chunks").select("id", count="exact")
            .eq("doc_id", doc_id).not_.is_("dense_vec", "null").limit(1).execute().count
        ) or 0
        sparse_n = (
            client.table("chunks").select("id", count="exact")
            .eq("doc_id", doc_id).not_.is_("sparse_json", "null").limit(1).execute().count
        ) or 0
        # 차원은 한 행만 꺼내 확인한다.
        one = (
            client.table("chunks").select("dense_vec").eq("doc_id", doc_id)
            .not_.is_("dense_vec", "null").limit(1).execute().data
        ) or []
        dim = None
        if one:
            v = one[0]["dense_vec"]
            if isinstance(v, str):
                dim = v.count(",") + 1 if v.strip() else 0
            elif isinstance(v, list):
                dim = len(v)
        jobs = (
            client.table("ingest_jobs").select("*").eq("doc_id", doc_id).execute().data
        ) or []
        job_ids = [j["id"] for j in jobs]
        logs = []
        for jid in job_ids:
            logs += (
                client.table("ingest_logs").select("stage,status")
                .eq("job_id", jid).order("id").execute().data
            ) or []
        vision = (
            client.table("vision_usage_log").select("call_id,success,page")
            .eq("doc_id", doc_id).execute().data
        ) or []

        # --- 결정적: 청크 본문은 **해시로만** 남긴다 (§본문을 저장하지 않는다) ---
        chunk_rows = [{
            "chunk_idx": c["chunk_idx"],
            "page": c.get("page"),
            "has_section_title": bool(c.get("section_title")),
            "text_sha16": _sha(c.get("text") or ""),
            "text_len": len(c.get("text") or ""),
            "flag_keys": sorted((c.get("flags") or {}).keys()),
            "meta_keys": sorted((c.get("metadata") or {}).keys()),
            "has_bbox": c.get("bbox") is not None,
            "has_char_range": c.get("char_range") is not None,
        } for c in chunks]

        flags = d.get("flags") or {}
        out_docs.append({
            "deterministic": {
                "doc_type": d.get("doc_type"),
                "content_type": d.get("content_type"),
                "size_bytes": d.get("size_bytes"),
                "sha256": d.get("sha256"),
                "source_channel": d.get("source_channel"),
                "storage_path_shape": (
                    "user/<uid>/<sha256><ext>"
                    if str(d.get("storage_path", "")).startswith(f"user/{user_id}/")
                    else str(d.get("storage_path"))
                ),
                "chunk_count": len(chunks),
                "chunks": chunk_rows,
                # 청크 전체를 이어붙인 해시 — 하나만 달라도 바뀐다.
                "chunks_digest": _sha("|".join(c["text_sha16"] for c in chunk_rows)),
                "flag_keys": sorted(flags.keys()),
                "job_count": len(jobs),
                "stages": [f'{x["stage"]}:{x["status"]}' for x in logs],
            },
            "nondeterministic": {
                "embedded_chunks": embedded_n,
                "sparse_chunks": sparse_n,
                "embedding_dim": dim,
                "has_title": bool(d.get("title")),
                "has_doc_embedding": d.get("doc_embedding") is not None,
                "has_implications": bool(d.get("implications")),
                "has_summary": bool(d.get("summary")),
                "tag_count": len(d.get("tags") or []),
                "vision_calls": len(vision),
                "vision_success": sum(1 for v in vision if v.get("success")),
                "job_status": sorted({j.get("status") for j in jobs}),
            },
        })
    return {"documents": out_docs}


async def ingest_one(path: str, user_id: str) -> dict:
    """`upload_document` 를 원본 그대로 호출하고 BG 태스크까지 수동 실행한다."""
    from fastapi import BackgroundTasks
    from starlette.datastructures import Headers, UploadFile

    from app.auth.dependencies import CurrentUser
    import app.routers.documents as D

    with open(path, "rb") as f:
        raw = f.read()
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    ctype = {
        ".pdf": "application/pdf",
        ".hwp": "application/x-hwp",
        ".hwpx": "application/hwp+zip",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }.get(ext, "application/octet-stream")

    upload = UploadFile(
        file=io.BytesIO(raw),
        filename=name,
        headers=Headers({"content-type": ctype}),
    )
    bg = BackgroundTasks()
    user = CurrentUser(user_id=user_id, email=None, is_authenticated=True)

    t0 = time.time()
    resp = await D.upload_document(
        background_tasks=bg, file=upload, title=None, mode=None,
        source_channel="api", current_user=user,
    )
    accepted_ms = int((time.time() - t0) * 1000)
    body = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)

    # FastAPI 가 응답 뒤에 돌리는 것 — 여기서는 우리가 돌린다.
    t1 = time.time()
    for task in bg.tasks:
        r = task.func(*task.args, **task.kwargs)
        if asyncio.iscoroutine(r):
            await r
    bg_ms = int((time.time() - t1) * 1000)

    return {
        "response": {k: v for k, v in body.items() if k != "doc_id"},
        "accepted_ms": accepted_ms,
        "background_ms": bg_ms,
        "bg_task_count": len(bg.tasks),
    }


def _compare(baseline_path: str, got: dict) -> int:
    """기준선과 이번 실행을 비교한다.

    **결정적 부분은 완전 일치를 요구하고**, 비결정적 부분은 개수·차원만 본다
    (임베딩·태그·요약은 값이 매번 달라 값 비교가 성립하지 않는다).
    """
    with open(baseline_path, encoding="utf-8") as f:
        want = json.load(f)
    fails = 0
    if want.get("source_sha256") != got.get("source_sha256"):
        print("  **다른 파일이다** — 기준선의 source_sha256 과 다르다")
        return 1
    wd, gd = want["documents"], got["documents"]
    if len(wd) != len(gd):
        print(f"  **문서 수가 다르다** {len(wd)} → {len(gd)}")
        return 1
    for i, (w, g) in enumerate(zip(wd, gd)):
        for k in w["deterministic"]:
            a, b = w["deterministic"][k], g["deterministic"].get(k)
            if a != b:
                fails += 1
                if k == "chunks":
                    # **길이를 먼저 본다.** `zip` 은 짧은 쪽에 맞춰 도는 탓에, 한쪽이 비면
                    # "다른 청크 0 개" 라는 말이 안 되는 보고가 나온다(실제로 그랬다).
                    b = b or []
                    if len(a) != len(b):
                        print(f"  MISMATCH [{i}] chunks — 개수가 다르다 {len(a)} → {len(b)}")
                    bad = [j for j, (x, y) in enumerate(zip(a, b)) if x != y]
                    if bad:
                        print(f"  MISMATCH [{i}] chunks — 내용이 다른 청크 {len(bad)}개, "
                              f"인덱스 {bad[:8]}")
                else:
                    print(f"  MISMATCH [{i}] {k}")
                    print(f"      기준선: {json.dumps(a, ensure_ascii=False)[:300]}")
                    print(f"      이번  : {json.dumps(b, ensure_ascii=False)[:300]}")
            else:
                print(f"  [{i}] {k:<20} OK")
        # 비결정적: 개수·차원만
        for k in ("embedded_chunks", "sparse_chunks", "embedding_dim", "vision_calls"):
            a, b = w["nondeterministic"].get(k), g["nondeterministic"].get(k)
            if a != b:
                fails += 1
                print(f"  MISMATCH [{i}] (비결정적 개수) {k}: {a} → {b}")
    print(f"\n{'대조 FAIL 0' if fails == 0 else f'대조 FAIL {fails}'}")
    return 0 if fails == 0 else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="인제스트할 파일 경로")
    ap.add_argument("--slot", default="default")
    ap.add_argument("--keep", action="store_true", help="끝나고 정리하지 않는다")
    ap.add_argument("--out", help="기준선 저장 경로 (기본: fixtures/ingest_baselines/<파일명>.json)")
    ap.add_argument("--compare", metavar="BASELINE",
                    help="저장하지 않고 기존 기준선과 비교한다 (Phase 3 신규 구현 대조용)")
    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))

    from ingest_sandbox import _client, clean, prod_snapshot, sandbox_user_id

    client = _client()
    uid = sandbox_user_id(args.slot)
    path = args.file if os.path.isabs(args.file) else os.path.join(ROOT, args.file)
    if not os.path.exists(path):
        raise SystemExit(f"파일이 없다: {path}")

    print(f"  샌드박스 user_id: {uid}")
    print(f"  파일: {os.path.basename(path)} ({os.path.getsize(path):,} bytes)")

    # 잔여물을 먼저 지우고 기준선을 찍는다 — 순서를 바꾸면 남은 게 섞인다.
    clean(client, uid, apply=True)
    before = prod_snapshot(client)

    print("\n  인제스트 실행 중... (임베딩·vision 호출이 있어 수 분 걸릴 수 있다)")
    run = asyncio.run(ingest_one(path, uid))
    print(f"  응답 {run['accepted_ms']}ms · 백그라운드 {run['background_ms']}ms "
          f"· BG 태스크 {run['bg_task_count']}개")
    print(f"  응답 본문: {json.dumps(run['response'], ensure_ascii=False)[:200]}")

    snap = snapshot(client, uid)

    # **인제스트가 실제로 끝났는지 먼저 본다.** 한 번 중간에 죽은 적이 있는데(2026-09-07,
    # 재현 안 됨) `stages` 불일치로만 드러나 원인을 바로 알기 어려웠다. 잡 상태를 직접
    # 확인해 실패를 실패라고 말하게 한다.
    bad_jobs = [
        d for d in snap["documents"]
        if d["nondeterministic"]["job_status"] != ["completed"]
    ]
    if bad_jobs:
        print(f"\n  **인제스트가 완료되지 않았다** — job_status "
              f"{[d['nondeterministic']['job_status'] for d in bad_jobs]}")
        print("     기준선으로 쓰면 안 된다. 로그를 확인하고 다시 돌릴 것.")
    if not snap["documents"]:
        print("\n  **문서가 만들어지지 않았다** — dedup 에 걸렸거나 업로드가 실패했다.")
    baseline = {
        "source_file": os.path.relpath(path, ROOT),
        "source_bytes": os.path.getsize(path),
        "source_sha256": hashlib.sha256(open(path, "rb").read()).hexdigest(),
        "run": {k: v for k, v in run.items() if k != "accepted_ms" and k != "background_ms"},
        **snap,
    }

    if args.compare:
        rc = _compare(args.compare, baseline)
    else:
        out = args.out or os.path.join(
            BASELINE_DIR, os.path.basename(path).replace(" ", "_") + ".json"
        )
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(baseline, f, ensure_ascii=False, indent=2)
        rc = 0

    for d in snap["documents"]:
        det, nd = d["deterministic"], d["nondeterministic"]
        print(f"\n  === 산출물 ===")
        print(f"  결정적   : 청크 {det['chunk_count']}개 · digest {det['chunks_digest']} "
              f"· 잡 {det['job_count']}개")
        print(f"             stages {det['stages']}")
        print(f"             flags {det['flag_keys']}")
        print(f"  비결정적 : 임베딩 {nd['embedded_chunks']}개 dim={nd['embedding_dim']} "
              f"· sparse {nd['sparse_chunks']}개 "
              f"· 태그 {nd['tag_count']} · 요약 {nd['has_summary']} "
              f"· vision {nd['vision_success']}/{nd['vision_calls']}")
        print(f"             job_status {nd['job_status']}")
    if not args.compare:
        print(f"\n  기준선 저장: {os.path.relpath(out, ROOT)}")

    if args.keep:
        print("  --keep — 정리하지 않았다. 나중에 `ingest_sandbox.py clean --apply` 로 지울 것")
        return
    r = clean(client, uid, apply=True)
    after = prod_snapshot(client)
    ok = r["clean"] and before == after and rc == 0
    print(f"  정리: {'완료' if r['clean'] else '**불완전** ' + json.dumps(r['left'])}")
    print(f"  운영 총계 불변: {'예' if before == after else f'**아니오** {before} → {after}'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
