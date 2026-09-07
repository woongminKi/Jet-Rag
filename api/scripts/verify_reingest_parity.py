"""`reingest.ts` 2 종을 FastAPI 라우트 원본과 대조.

## DB 를 스텁으로 갈아끼우고 라우트 함수를 그대로 부른다
양쪽에 **같은 상태**(문서·최근 잡·청크)를 주입하고 실제 라우트 코드를 실행한다.
비교 대상:

- 상태 코드와 응답 본문 (404/409/400/202)
- **한국어 오류 메시지 전문** — 사용자에게 그대로 보인다
- 부작용: `documents.update` 로 쓴 flags, 삭제한 chunks 수, 큐에 넣은 페이로드

## 노린 함정
- 검사 **순서**. `reingest-missing` 은 PDF 검사가 409 보다 먼저다. 순서가 바뀌면
  같은 요청이 다른 코드로 나간다.
- 409 메시지가 두 라우트에서 다르다(전체만 " 완료 후 다시 시도하세요." 가 붙는다).
- 400 메시지의 `{raw!r}` — Python repr 이라 따옴표가 붙는다.
- 전체 reingest 는 flags 를 **빈 dict 에서** 다시 만들고(모드만 남김), 증분은
  기존 flags 를 보존한다.

사용:
    api/.venv/bin/python api/scripts/verify_reingest_parity.py
    api/.venv/bin/python api/scripts/verify_reingest_parity.py --negative
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

OWNER = "11111111-1111-5111-8111-111111111111"
OTHER = "22222222-2222-5222-8222-222222222222"

def doc(**kw):
    base = {
        "id": "d1", "user_id": OWNER, "doc_type": "pdf",
        "storage_path": "user/x/abc.pdf", "flags": {},
    }
    base.update(kw)
    return base

CHUNKS_MIXED = [
    {"id": "c0", "chunk_idx": 0, "page": 1, "section_title": "본문"},
    {"id": "c1", "chunk_idx": 1, "page": 1, "section_title": "(vision) p.1 OCR 텍스트"},
    {"id": "c2", "chunk_idx": 2, "page": 3, "section_title": "(vision) p.3 이미지 분류: 표"},
    {"id": "c3", "chunk_idx": 7, "page": None, "section_title": "(vision) p.9 액션 아이템"},
    {"id": "c4", "chunk_idx": 4, "page": 0, "section_title": "(vision) p.0 뭔가"},
    {"id": "c5", "chunk_idx": 5, "page": 2, "section_title": "(vision)"},
]

# (이름, 라우트, 상태, mode 쿼리, 호출자)
CASES = [
    ("없는 문서", "full", {"doc": None}, None, OWNER),
    ("남의 문서", "full", {"doc": doc(user_id=OTHER)}, None, OWNER),
    ("진행 중 running", "full", {"doc": doc(), "job": {"id": "j0", "status": "running"}}, None, OWNER),
    ("진행 중 queued", "full", {"doc": doc(), "job": {"id": "j0", "status": "queued"}}, None, OWNER),
    ("끝난 잡", "full", {"doc": doc(), "job": {"id": "j0", "status": "completed"}}, None, OWNER),
    ("mode 무효", "full", {"doc": doc()}, "turbo", OWNER),
    ("mode 무효 따옴표", "full", {"doc": doc()}, "it's", OWNER),
    ("mode 빈문자열", "full", {"doc": doc()}, "", OWNER),
    ("mode fast", "full", {"doc": doc()}, "fast", OWNER),
    ("mode precise", "full", {"doc": doc()}, "precise", OWNER),
    ("mode 미지정 + 기존 fast", "full", {"doc": doc(flags={"ingest_mode": "fast", "scan": True})}, None, OWNER),
    ("mode 미지정 + 기존 쓰레기", "full", {"doc": doc(flags={"ingest_mode": "nope"})}, None, OWNER),
    ("청크 있음", "full", {"doc": doc(flags={"scan": True, "has_pii": True}), "chunks": CHUNKS_MIXED}, None, OWNER),

    ("없는 문서", "missing", {"doc": None}, None, OWNER),
    ("남의 문서", "missing", {"doc": doc(user_id=OTHER)}, None, OWNER),
    # PDF 검사가 409 보다 먼저다 — 잡이 돌고 있어도 400 이 나와야 한다.
    ("hwp + 진행중", "missing", {"doc": doc(doc_type="hwp"), "job": {"id": "j0", "status": "running"}}, None, OWNER),
    ("진행 중", "missing", {"doc": doc(), "job": {"id": "j0", "status": "queued"}}, None, OWNER),
    ("mode 무효", "missing", {"doc": doc()}, "turbo", OWNER),
    ("누락 계산", "missing", {"doc": doc(), "chunks": CHUNKS_MIXED, "total_pages": 5}, None, OWNER),
    ("누락 0", "missing", {"doc": doc(), "chunks": [
        {"id": "a", "chunk_idx": 0, "page": 1, "section_title": "(vision) p.1 x"},
        {"id": "b", "chunk_idx": 1, "page": 2, "section_title": "(vision) p.2 x"},
    ], "total_pages": 2}, None, OWNER),
    ("mode 변경 → flags 보존", "missing", {"doc": doc(flags={"ingest_mode": "default", "scan": True}), "total_pages": 2}, "precise", OWNER),
    ("mode 동일 → 갱신 없음", "missing", {"doc": doc(flags={"ingest_mode": "fast"}), "total_pages": 2}, "fast", OWNER),
]

RUNNER_TS = """
import { reingestDocument, reingestMissingVision }
  from "file://%(shared)s/documents/reingest.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const NEG = cfg.negative === true;

function makeClient(state: Record<string, unknown>, ops: unknown[]) {
  const chunks = (state.chunks ?? []) as Record<string, unknown>[];
  // deno-lint-ignore no-explicit-any
  const q = (rows: unknown, count: number | null = null): any => {
    // deno-lint-ignore no-explicit-any
    const o: any = {
      eq: () => o, is: () => o, lt: () => o, in: () => o,
      order: () => o, limit: () => o, single: () => o, select: () => o,
      then: (res: (v: unknown) => void) => res({ data: rows, error: null, count }),
    };
    return o;
  };
  // deno-lint-ignore no-explicit-any
  return {
    rpc(_name: string, args: Record<string, unknown>) {
      ops.push({ op: "enqueue", payload: args.payload });
      return Promise.resolve({ data: 1, error: null });
    },
    from(table: string) {
      return {
        select(_cols?: string, opts?: { count?: string; head?: boolean }) {
          if (table === "documents") return q(state.doc === null ? [] : [state.doc]);
          if (table === "ingest_jobs") return q(state.job ? [state.job] : []);
          if (table === "chunks") {
            return opts?.count === "exact" && opts?.head ? q([], chunks.length) : q(chunks);
          }
          return q([]);
        },
        insert(row: Record<string, unknown>) {
          ops.push({ op: "insert", table, row });
          return q({ id: cfg.newJobId });
        },
        update(row: Record<string, unknown>) {
          ops.push({ op: "update", table, row });
          return q([]);
        },
        delete() {
          ops.push({ op: "delete", table });
          return q([]);
        },
      };
    },
    // deno-lint-ignore no-explicit-any
  } as any;
}

const out = [];
for (const c of cfg.cases) {
  const ops: unknown[] = [];
  const client = makeClient(c.state, ops);
  const deps = {
    client, bucket: "documents",
    // 케이스별로 실제 PDF 바이트를 준다 — `countPdfPages` 가 진짜로 연다.
    download: () => Promise.resolve(new Uint8Array(c.pdf ?? [])),
  };
  const params = new URLSearchParams();
  if (c.mode !== null) params.set("mode", c.mode);
  let r;
  try {
    r = c.route === "full"
      ? await reingestDocument(deps, c.caller, "d1", params)
      : await reingestMissingVision(deps, c.caller, "d1", params);
  } catch (e) {
    r = { status: 500, body: { error: String(e) } };
  }
  out.push({ name: c.name, route: c.route, status: r.status, body: r.body, ops });
}
if (NEG) out[0].body = { detail: "문서를 찾을 수 없습니다!" };
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}


class FakeResp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class FakeQuery:
    def __init__(self, rows, count=None):
        self._rows = rows
        self._count = count

    def eq(self, *a, **k):
        return self

    def is_(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def execute(self):
        return FakeResp(self._rows, self._count)


class FakeTable:
    def __init__(self, name, state, ops, new_job_id):
        self.name = name
        self.state = state
        self.ops = ops
        self.new_job_id = new_job_id

    def select(self, *cols, count=None, **k):
        if self.name == "documents":
            d = self.state.get("doc")
            return FakeQuery([] if d is None else [d])
        if self.name == "ingest_jobs":
            j = self.state.get("job")
            return FakeQuery([j] if j else [])
        if self.name == "chunks":
            rows = self.state.get("chunks") or []
            return FakeQuery(rows, len(rows) if count == "exact" else None)
        return FakeQuery([])

    def insert(self, row, **k):
        self.ops.append({"op": "insert", "table": self.name, "row": row})
        return FakeQuery([{"id": self.new_job_id, "doc_id": row.get("doc_id"),
                           "status": "queued", "queued_at": None, "started_at": None,
                           "finished_at": None, "current_stage": None, "attempts": 0,
                           "error_msg": None}])

    def update(self, row, **k):
        self.ops.append({"op": "update", "table": self.name, "row": row})
        return FakeQuery([])

    def delete(self, **k):
        self.ops.append({"op": "delete", "table": self.name})
        return FakeQuery([])


class FakeClient:
    def __init__(self, state, ops, new_job_id):
        self.state = state
        self.ops = ops
        self.new_job_id = new_job_id

    def table(self, name):
        return FakeTable(name, self.state, self.ops, self.new_job_id)


def main() -> None:
    from fastapi import HTTPException

    import app.routers.documents as R
    from app.ingest.jobs import IngestJob

    negative = "--negative" in sys.argv
    new_job_id = "99999999-9999-5999-8999-999999999999"

    # 최소 PDF (페이지 수만 쓰인다) — 케이스마다 total_pages 로 갈아끼운다.
    import fitz

    py_out = []
    for name, route, state, mode, caller in CASES:
        ops: list = []
        client = FakeClient(state, ops, new_job_id)
        total_pages = state.get("total_pages", 1)

        # 의존성 스텁
        R.get_supabase_client = lambda _c=client: _c
        R.get_latest_job_for_doc = lambda _d, _s=state: (
            IngestJob(
                id=_s["job"]["id"], doc_id="d1", status=_s["job"]["status"],
                queued_at=None, started_at=None, finished_at=None,
                current_stage=None, attempts=0, error_msg=None,
            ) if _s.get("job") else None
        )
        def _create_job(doc_id, _j=new_job_id, _ops=ops):
            # TS 는 client.insert 를 거쳐 기록되므로 여기서도 같은 자리에 남긴다.
            _ops.append({"op": "insert", "table": "ingest_jobs",
                         "row": {"doc_id": doc_id, "status": "queued"}})
            return IngestJob(
                id=_j, doc_id=doc_id, status="queued", queued_at=None, started_at=None,
                finished_at=None, current_stage=None, attempts=0, error_msg=None,
            )

        R.create_job = _create_job

        class BG:
            def add_task(self, fn, *a, **k):
                ops.append({"op": "bg", "fn": getattr(fn, "__name__", str(fn)),
                            "args": [str(x) for x in a], "kwargs": {
                                kk: vv for kk, vv in k.items()}})

        class User:
            user_id = caller

        try:
            if route == "full":
                resp = R.reingest_document(  # type: ignore[arg-type]
                    "d1", BG(), mode=mode, current_user=User(),
                )
                body = {"doc_id": resp.doc_id, "job_id": resp.job_id,
                        "chunks_deleted": resp.chunks_deleted}
                status = 202
            else:
                # 페이지 수는 fitz 로 세므로 그 부분만 스텁한다.
                pdf = fitz.open()
                for _ in range(total_pages):
                    pdf.new_page()
                data = pdf.tobytes()
                pdf.close()

                class Storage:
                    def __init__(self, bucket=None):
                        pass

                    def get(self, _p, _d=data):
                        return _d

                import app.adapters.impl.supabase_storage as SS
                SS.SupabaseBlobStorage = Storage
                resp = R.reingest_missing_vision(  # type: ignore[arg-type]
                    "d1", BG(), mode=mode, current_user=User(),
                )
                body = {"doc_id": resp.doc_id, "job_id": resp.job_id,
                        "total_pages": resp.total_pages,
                        "missing_pages_before": resp.missing_pages_before,
                        "note": resp.note}
                status = 202
        except HTTPException as e:
            status, body = e.status_code, {"detail": e.detail}
        py_out.append({"name": name, "route": route, "status": status,
                       "body": body, "ops": ops})

    # ---- TS ----
    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "out.json")
        # 페이지 수만 맞는 실제 PDF 를 만들어 넘긴다.
        pdf_cache: dict[int, list[int]] = {}

        def pdf_for(n: int) -> list[int]:
            if n not in pdf_cache:
                d = fitz.open()
                for _ in range(n):
                    d.new_page()
                pdf_cache[n] = list(d.tobytes())
                d.close()
            return pdf_cache[n]

        with open(cf, "w", encoding="utf-8") as f:
            json.dump({
                "cases": [{
                    "name": n, "route": r, "state": st, "mode": m, "caller": c,
                    "pdf": pdf_for(st.get("total_pages", 1)) if r == "missing" else [],
                } for n, r, st, m, c in CASES],
                "newJobId": new_job_id, "negative": negative,
            }, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf, of],
            capture_output=True, text=True, timeout=900,
        )
        if proc.returncode != 0:
            raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
        with open(of, encoding="utf-8") as f:
            ts_out = json.load(f)

    fails: list[str] = []
    checks = 0

    def cmp(label, a, b):
        nonlocal checks
        checks += 1
        if a != b:
            fails.append(f"{label}\n      py={a!r}\n      ts={b!r}")

    def side_effects(ops):
        """양쪽에서 의미가 같은 부작용만 뽑는다.

        Python 은 `BackgroundTasks.add_task`, TS 는 큐 enqueue 라 실행 방식이 달라
        직접 비교가 안 된다. **관찰 가능한 DB 변경**만 본다 — flags 로 무엇을 썼는지,
        chunks 를 지웠는지, 잡을 만들었는지.
        """
        out = []
        for o in ops:
            if o.get("op") == "update" and o.get("table") == "documents":
                out.append(("flags", o["row"]))
            elif o.get("op") == "delete" and o.get("table") == "chunks":
                out.append(("chunks_deleted",))
            elif o.get("op") == "insert" and o.get("table") == "ingest_jobs":
                out.append(("job_created",))
        return out

    for py, ts in zip(py_out, ts_out):
        tag = f"{ts['route']}/{ts['name']}"
        cmp(f"{tag} status", py["status"], ts["status"])
        cmp(f"{tag} body", py["body"], ts["body"])
        pe, te = side_effects(py["ops"]), side_effects(ts["ops"])
        cmp(f"{tag} 부작용", pe, te)
        # 원본은 BackgroundTasks 로 파이프라인을 직접 돌리고 포팅은 큐에 넣는다.
        # 실행 방식은 달라도 **무엇을 시작하는가**는 같아야 한다.
        BG_TO_STAGE = {
            "run_pipeline": "extract",
            "run_incremental_vision_pipeline": "vision_missing",
        }
        py_next = [BG_TO_STAGE.get(o["fn"], o["fn"])
                   for o in py["ops"] if o.get("op") == "bg"]
        ts_next = [(o["payload"] or {}).get("stage")
                   for o in ts["ops"] if o.get("op") == "enqueue"]
        cmp(f"{tag} 다음 작업", py_next, ts_next)
        ok = (py["status"] == ts["status"] and py["body"] == ts["body"] and pe == te)
        print(f"  {tag:<38} {py['status']} {'일치' if ok else '**불일치**'}"
              f"  부작용 {len(pe)}건")

    for f in fails[:12]:
        print(f"  **{f}**")
    print()
    print(f"  비교 {checks}건, 불일치 {len(fails)}건")
    if negative:
        print("음성 대조: " + ("검사기 정상 (변조 검출)" if fails else "**검사기가 못 잡는다**"))
        sys.exit(0 if fails else 1)
    print("FAIL 0" if not fails else f"FAIL {len(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "api"))
    main()
