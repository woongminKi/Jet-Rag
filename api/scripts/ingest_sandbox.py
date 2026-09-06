"""인제스트 대조용 **샌드박스 격리·정리 하네스** (Phase 3 전용).

## 왜 필요한가
Phase 3 은 인제스트를 *재작성*한다(BackgroundTasks → pgmq 큐 + ingest-worker).
완료 조건이 "결과가 현행과 동등" 인데, 인제스트는 **부수효과가 본질**이라 지금까지 쓴
"요청 모양만 대조" 로는 판정할 수 없다. 실제로 문서를 넣어 보고 결과를 비교해야 한다.

그런데 Supabase 프로젝트가 하나뿐이라(스테이징 없음) 대조가 곧 운영 DB 쓰기가 된다.
그래서 **전용 샌드박스 `user_id`** 로 격리한다. 아래는 그게 가능하다는 실측 근거다:

| 사실 | 확인 |
|---|---|
| dedup 이 `user_id` 단위 | `documents.py:474` `.eq("user_id", ...)` |
| Storage 가 `user/<uid>/` prefix | 마이그 020, `supabase_storage.py:11` |
| `documents.user_id` 에 `auth.users` FK 없음 | `001_init.sql:20` + insert 실측 → 실제 계정 불필요 |
| 모든 흔적에 정리 키가 있다 | documents.user_id → chunks.doc_id → ingest_jobs.doc_id → ingest_logs.job_id |

## 정리가 선택이 아니라 필수인 이유
`documents` 와 `chunks` 는 user 스코프라 `/stats` 를 오염시키지 않지만,
**`ingest_jobs` 와 `vision_usage_log` 는 사용자로 걸러지지 않는다**
(`stats/sources.ts:40` — "원본은 `ingest_jobs` 를 사용자로 안 거른다"). 즉 샌드박스
인제스트가 운영 지표에 섞인다. 그래서 이 하네스의 핵심은 **정리 완전성 검증**이다.

## 안전 장치
- 샌드박스 UUID 는 고정 네임스페이스에서 `uuid5` 로 만든다 — 매번 같고, 우연히 운영
  UUID 와 겹칠 수 없다.
- 삭제는 **샌드박스 UUID 로만** 한다. 운영 UUID(OWNER / DEFAULT)는 명시적으로 차단하고,
  차단이 걸리면 즉시 중단한다.
- 기본은 `--dry-run` 이다. 실제 삭제는 `--apply` 를 줘야 한다.
- 삭제 후 **남은 흔적을 다시 세어** 0 이 아니면 실패로 보고한다.

사용:
    api/.venv/bin/python api/scripts/ingest_sandbox.py status
    api/.venv/bin/python api/scripts/ingest_sandbox.py clean --apply
    api/.venv/bin/python api/scripts/ingest_sandbox.py selftest --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "api"))

# 샌드박스 user_id 네임스페이스. 이 값에서 uuid5 로 파생하므로 매번 같은 UUID 가 나오고,
# 운영에서 쓰는 UUID 와 겹칠 수 없다.
_NS = uuid.UUID("5a4d0000-0000-4000-8000-000000000000")

#: 기본 샌드박스 사용자. 필요하면 `--slot` 으로 여러 개를 쓴다(병렬 대조용).
def sandbox_user_id(slot: str = "default") -> str:
    return str(uuid.uuid5(_NS, f"ingest-sandbox/{slot}"))


def _protected_ids() -> set[str]:
    """**절대 건드리면 안 되는** user_id 들. 하나라도 대상에 들어오면 중단한다."""
    ids = {
        # D1 ship 의 OWNER 본인 — work-log·메모리에 기록된 값.
        "2af8fca5-03ab-421b-94b8-53d4fe9d8046",
        # 익명 fallback 기본 사용자 (`config.py` 의 DEFAULT_USER_ID).
        "00000000-0000-0000-0000-000000000001",
    }
    for key in ("OWNER_USER_ID", "DEFAULT_USER_ID"):
        v = os.environ.get(key)
        if v:
            ids.add(v.strip())
    return ids


def _client():
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
    from supabase import create_client

    return create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )


def _assert_sandbox(user_id: str) -> None:
    """샌드박스 UUID 가 아니면 즉시 중단. **모든 파괴적 경로가 이걸 먼저 통과한다.**"""
    if user_id in _protected_ids():
        raise SystemExit(f"거부: 보호된 user_id 다 — {user_id}")
    known = {sandbox_user_id(s) for s in ("default", "a", "b", "c", "selftest")}
    if user_id not in known:
        raise SystemExit(
            f"거부: 샌드박스에서 파생한 UUID 가 아니다 — {user_id}\n"
            f"       (허용: {sorted(known)})"
        )


# --------------------------------------------------------------- 흔적 수집

def collect(client, user_id: str) -> dict:
    """샌드박스가 남긴 흔적 전수. 정리 전후로 같은 함수를 쓴다."""
    docs = (
        client.table("documents").select("id").eq("user_id", user_id).execute().data
    ) or []
    doc_ids = [d["id"] for d in docs]

    def _by_doc(table: str, key: str = "id") -> list[dict]:
        if not doc_ids:
            return []
        out: list[dict] = []
        # `in_` 는 URL 길이 제한이 있어 나눠 던진다.
        for i in range(0, len(doc_ids), 50):
            out += (
                client.table(table).select(key)
                .in_("doc_id", doc_ids[i : i + 50]).execute().data
            ) or []
        return out

    chunks = _by_doc("chunks")
    jobs = (
        [] if not doc_ids else
        (client.table("ingest_jobs").select("id").in_("doc_id", doc_ids).execute().data or [])
    )
    job_ids = [j["id"] for j in jobs]
    logs: list[dict] = []
    for i in range(0, len(job_ids), 50):
        logs += (
            client.table("ingest_logs").select("id").in_("job_id", job_ids[i : i + 50])
            .execute().data
        ) or []
    # **`call_id` 로 받아야 한다.** `vision_usage_log.doc_id` 는 `ON DELETE SET NULL` 이라
    # documents 를 지우면 doc_id 가 NULL 이 되어 **doc_id 로는 영원히 못 찾는다**
    # (운영에 그렇게 고아가 된 행이 이미 1,929 개 있다). 그러면 정리 누락을 검증할 수 없다.
    vision = _by_doc("vision_usage_log", "call_id")
    # 마이그 027 — 단계 간 중간 산출물. `documents` CASCADE 로 자동 삭제되지만
    # **확인은 따로 해야 한다**(CASCADE 를 믿고 안 세면 누락을 못 잡는다).
    artifacts = _by_doc("ingest_artifacts", "id")

    return {
        "documents": doc_ids,
        "chunks": [c["id"] for c in chunks],
        "ingest_jobs": job_ids,
        "ingest_logs": [x["id"] for x in logs],
        "vision_usage_log": [v["call_id"] for v in vision],
        "ingest_artifacts": [a["id"] for a in artifacts],
    }


def snapshot_for_verify(client, user_id: str) -> dict:
    """정리 **전에** 찍어 두는 ID 스냅샷. 이걸로 나중에 고아 행까지 검증한다."""
    return collect(client, user_id)


def storage_objects(client, user_id: str) -> list[str]:
    """샌드박스 사용자의 Storage 파일. 경로가 `user/<uid>/` prefix 라 그걸로 찾는다."""
    from app.config import get_settings

    bucket = get_settings().supabase_storage_bucket
    try:
        entries = client.storage.from_(bucket).list(f"user/{user_id}")
    except Exception:
        return []
    return [f"user/{user_id}/{e['name']}" for e in (entries or []) if e.get("name")]


def verify_gone(client, trace: dict) -> dict:
    """**삭제 전에 받아 둔 ID 로 직접 재조회한다.**

    처음엔 정리 후 `collect()` 를 다시 불러 검증했는데, `collect()` 는 `documents` 에서
    출발해 자식을 찾는다 — 그래서 `documents` 만 지우고 자식을 남기면 `doc_ids` 가 비어
    **고아 행을 0 으로 세는** 결함이 있었다(음성 대조에서 `ingest_logs`·`ingest_jobs`
    삭제를 빼도 0 건이 나왔다). 검증은 수집과 **다른 경로**여야 한다.
    """
    left: dict[str, int] = {}

    def _count_in(table: str, key: str, ids: list) -> int:
        if not ids:
            return 0
        total = 0
        for i in range(0, len(ids), 50):
            total += (
                client.table(table).select(key, count="exact")
                .in_(key, ids[i : i + 50]).limit(1).execute().count or 0
            )
        return total

    left["documents"] = _count_in("documents", "id", trace["documents"])
    left["chunks"] = _count_in("chunks", "id", trace["chunks"])
    left["ingest_jobs"] = _count_in("ingest_jobs", "id", trace["ingest_jobs"])
    left["ingest_logs"] = _count_in("ingest_logs", "id", trace["ingest_logs"])
    # **doc_id 가 아니라 call_id 로 센다** — SET NULL 이라 doc_id 는 지워진 뒤 NULL 이 된다.
    left["vision_usage_log"] = _count_in("vision_usage_log", "call_id", trace["vision_usage_log"])
    left["ingest_artifacts"] = _count_in("ingest_artifacts", "id", trace["ingest_artifacts"])
    return left


def _counts(trace: dict) -> dict:
    return {
        k: (v if isinstance(v, int) else len(v))
        for k, v in trace.items()
    }


# ------------------------------------------------------------------- 정리

def clean(client, user_id: str, *, apply: bool) -> dict:
    """샌드박스 흔적을 **자식부터 역순으로** 지운다. dry-run 이 기본."""
    _assert_sandbox(user_id)
    trace = collect(client, user_id)
    files = storage_objects(client, user_id)
    plan = _counts(trace) | {"storage": len(files)}

    if not apply:
        return {"applied": False, "plan": plan}

    doc_ids, job_ids = trace["documents"], trace["ingest_jobs"]
    # 순서가 중요하다 — 부모(documents)를 먼저 지우면 자식을 찾을 키가 사라진다.
    for i in range(0, len(job_ids), 50):
        batch = job_ids[i : i + 50]
        if batch:
            client.table("ingest_logs").delete().in_("job_id", batch).execute()
    for i in range(0, len(doc_ids), 50):
        batch = doc_ids[i : i + 50]
        if not batch:
            continue
        # artifacts 를 먼저 — ingest_jobs 를 지우면 CASCADE 로 사라지지만 순서를 명시한다.
        client.table("ingest_artifacts").delete().in_("doc_id", batch).execute()
        client.table("ingest_jobs").delete().in_("doc_id", batch).execute()
        client.table("chunks").delete().in_("doc_id", batch).execute()
    # vision 은 **call_id** 로 지운다. doc_id 로 지우려 해도 documents 삭제가 먼저 일어나면
    # 이미 NULL 이라 대상을 못 찾는다.
    vision_ids = trace["vision_usage_log"]
    for i in range(0, len(vision_ids), 50):
        batch = vision_ids[i : i + 50]
        if batch:
            client.table("vision_usage_log").delete().in_("call_id", batch).execute()
    if files:
        from app.config import get_settings

        bucket = get_settings().supabase_storage_bucket
        try:
            client.storage.from_(bucket).remove(files)
        except Exception as exc:
            print(f"  경고: Storage 삭제 실패 — {exc}")
    # documents 는 **맨 마지막**. 여기가 모든 정리 키의 출발점이다.
    client.table("documents").delete().eq("user_id", user_id).execute()

    # **보관해 둔 ID 로 검증한다** — `collect()` 재호출은 고아 행을 못 본다(§verify_gone).
    left = verify_gone(client, trace) | {"storage": len(storage_objects(client, user_id))}
    return {"applied": True, "plan": plan, "left": left, "clean": all(v == 0 for v in left.values())}


# ---------------------------------------------------------- 운영 불변 확인

_PROD_TABLES = ("documents", "chunks", "ingest_jobs", "ingest_logs", "vision_usage_log",
                "ingest_artifacts")


def prod_snapshot(client) -> dict:
    """운영 전체 행 수. 샌드박스 작업 전후로 **변하지 않아야** 하는 값."""
    out = {}
    for t in _PROD_TABLES:
        col = "doc_id" if t == "vision_usage_log" else "id"
        out[t] = client.table(t).select(col, count="exact").limit(1).execute().count
    return out


# ------------------------------------------------------------------ 명령

def cmd_status(args) -> int:
    client = _client()
    uid = sandbox_user_id(args.slot)
    trace = collect(client, uid)
    files = storage_objects(client, uid)
    print(f"  샌드박스 user_id: {uid}  (slot={args.slot})")
    print(f"  흔적: {json.dumps(_counts(trace) | {'storage': len(files)}, ensure_ascii=False)}")
    print(f"  운영 전체: {json.dumps(prod_snapshot(client), ensure_ascii=False)}")
    return 0


def cmd_clean(args) -> int:
    client = _client()
    uid = sandbox_user_id(args.slot)
    before = prod_snapshot(client)
    r = clean(client, uid, apply=args.apply)
    print(f"  샌드박스 user_id: {uid}")
    print(f"  {'삭제함' if r['applied'] else 'dry-run — 삭제 대상'}: "
          f"{json.dumps(r['plan'], ensure_ascii=False)}")
    if not r["applied"]:
        print("  (실제로 지우려면 --apply)")
        return 0
    print(f"  남은 흔적: {json.dumps(r['left'], ensure_ascii=False)}")
    after = prod_snapshot(client)
    # 운영 총계는 샌드박스 행이 빠진 만큼만 줄어야 한다.
    expected = {t: before[t] - r["plan"].get(t, 0) for t in _PROD_TABLES}
    drift = {t: (expected[t], after[t]) for t in _PROD_TABLES if expected[t] != after[t]}
    print(f"  운영 총계 검증: {'OK' if not drift else f'**어긋남** {drift}'}")
    ok = r["clean"] and not drift
    print(f"\n{'정리 완료' if ok else '**정리 불완전**'}")
    return 0 if ok else 1


def cmd_selftest(args) -> int:
    """**하네스가 실제로 지우는지** 스스로 검사한다.

    합성 흔적을 심고 → 세어지는지 확인 → 정리 → 0 이 되는지 확인한다.
    심는 것도 지우는 것도 전부 샌드박스 UUID 안에서만 일어난다.
    """
    client = _client()
    uid = sandbox_user_id("selftest")
    _assert_sandbox(uid)
    print(f"  샌드박스 user_id: {uid}")

    # **잔여물을 먼저 지우고 나서** 기준선을 찍는다. 순서를 바꾸면 직전 중단 실행이 남긴
    # 행이 기준선에 섞여, 그걸 지운 것뿐인데 "운영 총계가 변했다"로 오판한다(실제로 겪었다).
    clean(client, uid, apply=True)
    before_prod = prod_snapshot(client)

    if not args.apply:
        print("  (심기·지우기 모두 --apply 가 필요하다. dry-run 종료)")
        return 0

    doc_ids = [str(uuid.uuid4()) for _ in range(2)]
    rows = [{
        "id": d, "user_id": uid, "title": f"__sandbox_selftest__{i}", "doc_type": "pdf",
        "size_bytes": 1, "content_type": "application/pdf",
        "storage_path": f"user/{uid}/{d}.pdf", "sha256": f"{i:064x}",
        # CHECK 제약이 있다 — 실제 데이터에 쓰이는 값이어야 한다(실측: drag-drop/api/email).
        "source_channel": "api",
    } for i, d in enumerate(doc_ids)]
    client.table("documents").insert(rows).execute()
    jobs = [{"doc_id": d, "status": "queued"} for d in doc_ids]
    inserted_jobs = client.table("ingest_jobs").insert(jobs).execute().data or []
    if inserted_jobs:
        client.table("ingest_logs").insert([
            {"job_id": j["id"], "stage": "extract", "status": "succeeded"}
            for j in inserted_jobs
        ]).execute()

    # **vision 분기를 실제로 태운다.** 안 심으면 정리 누락을 검증하지 못한다
    # (음성 대조에서 vision 삭제를 빼도 0 건이 나왔다 — 심은 게 없어서였다).
    client.table("vision_usage_log").insert([
        # call_id 는 bigint 자동 증가라 넣지 않는다(넣으면 22P02).
        {"doc_id": doc_ids[0], "success": True,
         "quota_exhausted": False, "model_used": "__sandbox__", "source_type": "pdf"}
    ]).execute()

    # artifacts 분기도 태운다 — 안 심으면 정리 누락을 검증하지 못한다.
    if inserted_jobs:
        client.table("ingest_artifacts").insert([
            {"job_id": inserted_jobs[0]["id"], "doc_id": doc_ids[0],
             "stage": "extract", "seq": 0, "payload": {"__sandbox__": True}}
        ]).execute()

    seeded = _counts(collect(client, uid))
    print(f"  심은 흔적: {json.dumps(seeded, ensure_ascii=False)}")
    fails = 0
    if seeded["documents"] != 2 or seeded["ingest_jobs"] != 2:
        fails += 1
        print("  **수집기 결함** — 심은 흔적을 다 세지 못한다")
    if seeded["ingest_logs"] != 2:
        fails += 1
        print("  **수집기 결함** — ingest_logs 를 job_id 로 못 따라간다")
    if seeded["vision_usage_log"] != 1:
        fails += 1
        print("  **수집기 결함** — vision_usage_log 를 못 센다")
    if seeded["ingest_artifacts"] != 1:
        fails += 1
        print("  **수집기 결함** — ingest_artifacts 를 못 센다")

    r = clean(client, uid, apply=True)
    print(f"  정리 후 남은 흔적: {json.dumps(r['left'], ensure_ascii=False)}")
    if not r["clean"]:
        fails += 1
        print("  **정리 불완전**")

    after_prod = prod_snapshot(client)
    if before_prod != after_prod:
        fails += 1
        print(f"  **운영 총계가 변했다** {before_prod} → {after_prod}")
    else:
        print(f"  운영 총계 불변 확인: {json.dumps(after_prod, ensure_ascii=False)}")

    print(f"\n{'FAIL 0' if fails == 0 else f'FAIL {fails}'}")
    return 0 if fails == 0 else 1


def cmd_guard(args) -> int:
    """보호 가드가 실제로 막는지 확인한다 — 막지 못하면 그게 가장 큰 위험이다."""
    caught = 0
    targets = sorted(_protected_ids()) + ["11111111-2222-3333-4444-555555555555"]
    for uid in targets:
        try:
            _assert_sandbox(uid)
            print(f"  **통과시켜 버렸다** {uid}")
        except SystemExit as e:
            caught += 1
            print(f"  차단 OK  {uid}  ({str(e).splitlines()[0][:60]})")
    ok_uid = sandbox_user_id("default")
    try:
        _assert_sandbox(ok_uid)
        print(f"  샌드박스는 통과 OK  {ok_uid}")
    except SystemExit:
        print(f"  **샌드박스를 막아 버렸다** {ok_uid}")
        return 1
    print(f"\n차단 {caught} / {len(targets)}")
    return 0 if caught == len(targets) else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="인제스트 샌드박스 격리·정리 하네스")
    ap.add_argument("--slot", default="default", help="샌드박스 슬롯 (default/a/b/c)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="샌드박스 흔적 + 운영 총계 표시")
    c = sub.add_parser("clean", help="샌드박스 흔적 삭제")
    c.add_argument("--apply", action="store_true", help="실제로 삭제 (없으면 dry-run)")
    s = sub.add_parser("selftest", help="심고 → 세고 → 지우고 → 0 인지 확인")
    s.add_argument("--apply", action="store_true")
    sub.add_parser("guard", help="보호 가드가 운영 UUID 를 막는지 확인")
    args = ap.parse_args()
    sys.exit({"status": cmd_status, "clean": cmd_clean,
              "selftest": cmd_selftest, "guard": cmd_guard}[args.cmd](args))


if __name__ == "__main__":
    main()
