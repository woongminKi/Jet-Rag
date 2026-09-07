"""`email_route.ts` · `email_ingest.ts` 를 원본과 대조.

## 거절 정책이 핵심이다
잘못된 주소·모르는 토큰·발신자 불일치·Pro 아님·첨부 없음은 **전부 200 `ignored`** 다.
4xx 를 내면 Cloudflare Worker 가 재시도하거나 발신자에게 반송 메일이 간다.
**secret 불일치만 401**, **secret 미설정은 503** 이다.

## 노린 함정
- `_EMAIL_RE` 의 `\\s` 는 유니코드 공백 — `Name <a@b.c>` 와 `a@b.c` 둘 다 받는다.
- `token.isalnum()` 은 빈 문자열에 False 다.
- `b64decode(validate=True)` 는 알파벳 밖 문자를 **거부**한다. JS `atob` 은 관대하다.
- 첨부별 결과는 예외 없이 dict 로 모인다 — 하나가 실패해도 나머지는 처리된다.

사용:
    api/.venv/bin/python api/scripts/verify_email_ingest_parity.py
    api/.venv/bin/python api/scripts/verify_email_ingest_parity.py --negative
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

ADDR_CASES = [
    "u-abcd1234@in.woong-s.com",
    "Jet <u-abcd1234@in.woong-s.com>",
    "  u-abcd1234@in.woong-s.com  ",
    "U-ABCD1234@IN.WOONG-S.COM",
    "u-abcd123@in.woong-s.com",       # 7자
    "u-abcd12345@in.woong-s.com",     # 9자
    "u-abcd_123@in.woong-s.com",      # isalnum 아님
    "u-@in.woong-s.com",              # 빈 토큰
    "abcd1234@in.woong-s.com",        # u- 없음
    "그냥문자열",
    "",
    "이름 <u-abcd1234@in.woong-s.com>\n",
    "<u-abcd1234@in.woong-s.com>",
    "u-abcd1234@in.woong-s.com, other@x.com",
]

SENDER_CASES = [
    ("a@b.com", "a@b.com"),
    ("A@B.COM", "a@b.com"),
    ("Name <a@b.com>", "a@b.com"),
    ("a@b.com", "  A@B.COM  "),
    ("a@b.com", None),
    ("a@b.com", ""),
    ("x@y.com", "a@b.com"),
    ("이상한값", "a@b.com"),
]

B64_CASES = [
    base64.b64encode(b"hello").decode(),
    "aGVsbG8=",
    "aGVsbG8",          # 패딩 없음 — atob 은 받는다
    "aGVsbG8==",        # 패딩 과다 (길이 9)
    "aGVsbG8!",         # 알파벳 밖
    "aGVs bG8=",        # 공백
    "aGVs\nbG8=",       # 개행
    "",
    "====",
    "a===",
    "aG==",
    "=",
    "a",
    "aGVs",
    "-_-_",             # base64url — 표준 알파벳 아님
    "aGVsbG8=x",
]

RUNNER_TS = """
import {
  EMAIL_ALLOWED_EXTENSIONS, extractEmail, ingestEmailAttachment, parseToken, senderAllowed,
} from "file://%(shared)s/ingest/email_ingest.ts";
import { decodeBase64Strict, handleEmailWebhook }
  from "file://%(shared)s/ingest/email_route.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const NEG = cfg.negative === true;

const out: Record<string, unknown> = {};
out.tokens = cfg.addrCases.map((a: string) => parseToken(a));
out.emails = cfg.addrCases.map((a: string) => extractEmail(a));
out.senders = cfg.senderCases.map(([f, o]: [string, string | null]) => senderAllowed(f, o));
out.b64 = cfg.b64Cases.map((s: string) => {
  const r = decodeBase64Strict(s);
  return r === null ? null : [...r];
});
out.exts = EMAIL_ALLOWED_EXTENSIONS;

// ---- 라우트: DB 를 스텁으로 갈고 실제 코드를 돌린다 ----
function makeClient(state: Record<string, unknown>) {
  // deno-lint-ignore no-explicit-any
  const q = (rows: unknown): any => {
    // deno-lint-ignore no-explicit-any
    const o: any = {
      eq: () => o, is: () => o, limit: () => o, single: () => o, select: () => o,
      then: (res: (v: unknown) => void) => res({ data: rows, error: null }),
    };
    return o;
  };
  // deno-lint-ignore no-explicit-any
  return {
    rpc: () => Promise.resolve({ data: null, error: null }),
    storage: { from: () => ({ upload: () => Promise.resolve({ error: null }) }) },
    from(table: string) {
      if (table === "email_ingest_addresses") {
        return { select: () => q(state.addr ? [state.addr] : []) };
      }
      if (table === "documents") {
        return { select: () => q(state.dup ? [state.dup] : []), insert: () => q({ id: "newdoc" }) };
      }
      if (table === "ingest_jobs") return { insert: () => q({ id: "newjob" }) };
      // 플랜 판정도 진짜 `getEffectivePlan` 을 태운다 — 테이블만 스텁이다.
      if (table === "subscriptions") {
        return { select: () => q(state.plan ? [{ plan_code: state.plan, status: "active" }] : []) };
      }
      if (table === "plans") {
        return {
          select: () =>
            q(state.plan
              ? [{ code: state.plan, max_documents: 100, answers_per_day: 100 }]
              : []),
        };
      }
      return { select: () => q([]) };
    },
    // deno-lint-ignore no-explicit-any
  } as any;
}

const routes: unknown[] = [];
for (const c of cfg.routeCases) {
  const req = new Request("https://x/ingest/email", {
    method: "POST",
    headers: c.secretHeader === null ? {} : { "x-jetrag-webhook-secret": c.secretHeader },
    body: c.rawBody !== undefined ? c.rawBody : JSON.stringify(c.body),
  });
  const r = await handleEmailWebhook(
    {
      client: makeClient(c.state ?? {}),
      bucket: "documents",
      settings: { emailWebhookSecret: c.secret },
      nowMs: () => 0,
    },
    req,
  );
  // 422 본문은 pydantic 이 만드는 필드별 오류라 글자까지 맞추지 않는다.
  routes.push({ name: c.name, status: r.status, body: r.status === 422 ? null : r.body });
}
out.routes = routes;

// 50MB 경계 — 버퍼를 여기서 직접 만든다(JSON 으로 실어 나르면 67MB 다).
const sizes: unknown[] = [];
for (const s of cfg.sizeCases) {
  const raw = new Uint8Array(s.size);
  raw.set(cfg.pdfMagic); // "%%PDF-1.4\\n" — 여기 literal 로 쓰면 %%-포매팅과 충돌한다
  const r = await ingestEmailAttachment(
    { client: makeClient({}), bucket: "documents" },
    { userId: "u1", filename: s.filename, contentType: "application/pdf", raw },
  );
  sizes.push({ name: s.name, result: r });
}
out.sizes = sizes;

// 음성 대조는 **세 다리를 각각** 흔든다 — 하나만 흔들면 나머지 다리가 실제로
// 비교되고 있는지 알 수 없다(이번 세션에 조용히 죽은 대조를 3번 만났다).
if (NEG) {
  (out.tokens as (string | null)[])[0] = "변조";
  (routes[5] as { status: number }).status = 999;
  (sizes[1] as { result: Record<string, unknown> }).result = { status: "변조" };
}
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}

PDF_MAGIC = b"%PDF-1.4\n"
PDF_BYTES = PDF_MAGIC + b"x" * 40
SECRET = "s3cr3t"
TOK = "abcd1234"
ADDR = {"user_id": "u1", "token": TOK, "owner_email": "a@b.com"}


def route_cases() -> list[dict]:
    att = {
        "filename": "a.pdf", "content_type": "application/pdf",
        "content_base64": base64.b64encode(PDF_BYTES).decode(),
    }
    good = {"to": f"u-{TOK}@in.x", "from": "a@b.com", "attachments": [att]}
    return [
        {"name": "secret 미설정", "secret": "", "secretHeader": SECRET, "body": good},
        {"name": "secret 불일치", "secret": SECRET, "secretHeader": "nope", "body": good},
        {"name": "secret 헤더 없음", "secret": SECRET, "secretHeader": None, "body": good},
        {"name": "본문 JSON 아님", "secret": SECRET, "secretHeader": SECRET,
         "rawBody": "not json"},
        {"name": "to 없음", "secret": SECRET, "secretHeader": SECRET,
         "body": {"from": "a@b.com"}},
        {"name": "잘못된 to", "secret": SECRET, "secretHeader": SECRET,
         "body": {**good, "to": "someone@in.x"}},
        {"name": "모르는 토큰", "secret": SECRET, "secretHeader": SECRET, "body": good,
         "state": {"addr": None}},
        {"name": "발신자 불일치", "secret": SECRET, "secretHeader": SECRET,
         "body": {**good, "from": "x@y.com"}, "state": {"addr": ADDR, "plan": "pro"}},
        {"name": "Pro 아님", "secret": SECRET, "secretHeader": SECRET, "body": good,
         "state": {"addr": ADDR, "plan": "free"}},
        {"name": "플랜 조회 실패", "secret": SECRET, "secretHeader": SECRET, "body": good,
         "state": {"addr": ADDR, "plan": None}},
        {"name": "첨부 없음", "secret": SECRET, "secretHeader": SECRET,
         "body": {**good, "attachments": []}, "state": {"addr": ADDR, "plan": "pro"}},
        {"name": "base64 오류", "secret": SECRET, "secretHeader": SECRET,
         "body": {**good, "attachments": [{**att, "content_base64": "aGVsbG8!"}]},
         "state": {"addr": ADDR, "plan": "pro"}},
        {"name": "비허용 확장자", "secret": SECRET, "secretHeader": SECRET,
         "body": {**good, "attachments": [{**att, "filename": "a.txt"}]},
         "state": {"addr": ADDR, "plan": "pro"}},
        {"name": "빈 첨부", "secret": SECRET, "secretHeader": SECRET,
         "body": {**good, "attachments": [{**att, "content_base64": ""}]},
         "state": {"addr": ADDR, "plan": "pro"}},
        {"name": "매직 불일치", "secret": SECRET, "secretHeader": SECRET,
         "body": {**good, "attachments": [
             {**att, "content_base64": base64.b64encode(b"NOTPDF" * 10).decode()}]},
         "state": {"addr": ADDR, "plan": "pro"}},
        {"name": "정상", "secret": SECRET, "secretHeader": SECRET, "body": good,
         "state": {"addr": ADDR, "plan": "pro"}},
        {"name": "중복", "secret": SECRET, "secretHeader": SECRET, "body": good,
         "state": {"addr": ADDR, "plan": "pro", "dup": {"id": "olddoc", "flags": {}}}},
        {"name": "첨부 여러 건", "secret": SECRET, "secretHeader": SECRET,
         "body": {**good, "attachments": [
             {**att, "filename": "a.txt"}, att, {**att, "content_base64": "!!!!"}]},
         "state": {"addr": ADDR, "plan": "pro"}},
        # pydantic 은 **처리 전에** 본문 전체를 검증한다 — 아래 셋은 전부 422 다.
        {"name": "content_base64 없음", "secret": SECRET, "secretHeader": SECRET,
         "body": {**good, "attachments": [{"filename": "a.pdf"}]},
         "state": {"addr": ADDR, "plan": "pro"}},
        {"name": "content_base64 숫자", "secret": SECRET, "secretHeader": SECRET,
         "body": {**good, "attachments": [{**att, "content_base64": 123}]},
         "state": {"addr": ADDR, "plan": "pro"}},
        {"name": "attachments 배열 아님", "secret": SECRET, "secretHeader": SECRET,
         "body": {**good, "attachments": "nope"},
         "state": {"addr": ADDR, "plan": "pro"}},
        {"name": "둘째 첨부만 불량", "secret": SECRET, "secretHeader": SECRET,
         "body": {**good, "attachments": [att, {"filename": "b.pdf"}]},
         "state": {"addr": ADDR, "plan": "pro"}},
        {"name": "attachments 키 없음", "secret": SECRET, "secretHeader": SECRET,
         "body": {"to": good["to"], "from": good["from"]},
         "state": {"addr": ADDR, "plan": "pro"}},
    ]


# 50MB 경계는 base64 로 실어 보내면 67MB JSON 이 된다 — 양쪽에서 **직접** 만들어 잰다.
MAX_SIZE = 50 * 1024 * 1024
SIZE_CASES = [
    {"name": "정확히 50MB", "size": MAX_SIZE, "filename": "a.pdf"},
    {"name": "50MB + 1", "size": MAX_SIZE + 1, "filename": "a.pdf"},
    {"name": "50MB 초과 + 비허용 확장자", "size": MAX_SIZE + 1, "filename": "a.txt"},
]


def main() -> None:
    negative = "--negative" in sys.argv
    from fastapi import HTTPException
    from pydantic import ValidationError

    import app.routers.email_ingest as R
    from app.services import email_ingest as SVC
    from app.services import quota as QUOTA

    py_tokens = [SVC.parse_token(a) for a in ADDR_CASES]
    py_emails = [SVC._extract_email(a) for a in ADDR_CASES]
    py_senders = [SVC.sender_allowed(f, o) for f, o in SENDER_CASES]
    py_b64 = []
    for s in B64_CASES:
        try:
            py_b64.append(list(base64.b64decode(s, validate=True)))
        except Exception:  # noqa: BLE001
            py_b64.append(None)

    class BG:
        def add_task(self, *a, **k):
            pass

    cases = route_cases()
    py_routes = []
    for c in cases:
        state = c.get("state", {})

        # 서비스 함수는 **진짜**를 돌린다 — DB 만 스텁이다.
        # (재구현끼리 비교하면 대조가 아무것도 증명하지 않는다.)
        class Table:
            def __init__(self, rows, ins):
                self._rows, self._ins, self._mode = rows, ins, "sel"

            def select(self, *a, **k):
                self._mode = "sel"
                return self

            def insert(self, *a, **k):
                self._mode = "ins"
                return self

            def eq(self, *a, **k):
                return self

            def is_(self, *a, **k):
                return self

            def limit(self, *a, **k):
                return self

            def execute(self):
                data = self._rows if self._mode == "sel" else self._ins
                return type("R", (), {"data": data})()

        class Client:
            def __init__(self, st):
                self.st = st

            def table(self, name):
                if name == "email_ingest_addresses":
                    return Table([self.st["addr"]] if self.st.get("addr") else [], [])
                if name == "documents":
                    return Table(
                        [self.st["dup"]] if self.st.get("dup") else [],
                        [{"id": "newdoc"}],
                    )
                if name == "subscriptions":
                    p = self.st.get("plan")
                    return Table([{"plan_code": p, "status": "active"}] if p else [], [])
                if name == "plans":
                    p = self.st.get("plan")
                    return Table(
                        [{"code": p, "max_documents": 100, "answers_per_day": 100}]
                        if p else [],
                        [],
                    )
                return Table([], [])

            def rpc(self, *a, **k):
                return Table([], [])

        client = Client(state)
        SVC.get_supabase_client = lambda _c=client: _c
        SVC.create_job = lambda doc_id: type("J", (), {"id": "newjob"})()
        # 업로드 경로 값이라 반환 dict 에 안 들어간다 — 설정 로딩만 피한다.
        SVC.resolve_page_cap = lambda _m, _s: None
        SVC.get_settings = lambda: type("S", (), {})()
        R.get_supabase_client = lambda _c=client: _c
        # 플랜 판정도 **진짜**를 돌린다 — subscriptions·plans 테이블만 스텁이다.
        QUOTA.get_supabase_client = lambda _c=client: _c

        settings = type("S", (), {"email_webhook_secret": c["secret"]})()
        body = c.get("body")
        try:
            if "rawBody" in c:
                raise ValueError("json")  # pydantic 이 먼저 422 를 낸다
            payload = R.EmailWebhookPayload(**{
                "to": body.get("to"), "from": body.get("from"),
                "attachments": body.get("attachments", []),
            }) if body and "to" in body and "from" in body else None
            if payload is None:
                status, out = 422, {"detail": "`to` 와 `from` 이 필요합니다."}
            else:
                resp = R.email_webhook(
                    payload, BG(),
                    x_jetrag_webhook_secret=(c["secretHeader"] or ""),
                    settings=settings,
                )
                status, out = 200, {"status": resp.status, "results": resp.results}
        except HTTPException as e:
            status, out = e.status_code, {"detail": e.detail}
        except ValidationError:
            # pydantic 은 처리 **전에** 본문 전체를 본다 — 첨부 하나만 어긋나도 422 다.
            status, out = 422, {"detail": "본문 검증 실패"}
        except ValueError:
            status, out = 422, {"detail": "JSON 본문이 필요합니다."}
        # 상태코드만 비교한다 — 422 본문은 pydantic 이 만드는 필드별 오류 배열이라
        # 원본과 글자까지 맞출 수 없고, Worker 도 읽지 않는다.
        py_routes.append({
            "name": c["name"], "status": status,
            "body": None if status == 422 else out,
        })

    # 50MB 경계 — 버퍼를 여기서 직접 만든다.
    py_sizes = []
    for s in SIZE_CASES:
        raw = bytearray(s["size"])
        raw[0:9] = PDF_MAGIC
        py_sizes.append({
            "name": s["name"],
            "result": SVC.ingest_email_attachment(
                user_id="u1", filename=s["filename"],
                content_type="application/pdf", raw=bytes(raw), background_tasks=BG(),
            ),
        })

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "out.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({
                "addrCases": ADDR_CASES, "senderCases": [list(x) for x in SENDER_CASES],
                "b64Cases": B64_CASES,
                "routeCases": [{
                    **c,
                    "state": {
                        "addr": c.get("state", {}).get("addr"),
                        "dup": c.get("state", {}).get("dup"),
                        "plan": c.get("state", {}).get("plan"),
                    },
                } for c in cases],
                "sizeCases": SIZE_CASES,
                "pdfMagic": list(PDF_MAGIC),
                "negative": negative,
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
            ts = json.load(f)

    fails: list[str] = []
    checks = 0

    def cmp(label, a, b):
        nonlocal checks
        checks += 1
        if a != b:
            fails.append(f"{label}\n      py={a!r}\n      ts={b!r}")

    for a, x, y in zip(ADDR_CASES, py_tokens, ts["tokens"]):
        cmp(f"parse_token({a!r})", x, y)
    for a, x, y in zip(ADDR_CASES, py_emails, ts["emails"]):
        cmp(f"extract_email({a!r})", x, y)
    for c, x, y in zip(SENDER_CASES, py_senders, ts["senders"]):
        cmp(f"sender_allowed{c}", x, y)
    for s, x, y in zip(B64_CASES, py_b64, ts["b64"]):
        cmp(f"b64({s!r})", x, y)
    cmp("허용 확장자", SVC._EMAIL_ALLOWED_EXTENSIONS, ts["exts"])

    print("  --- 라우트 ---")
    for a, b in zip(py_routes, ts["routes"]):
        ok = a["status"] == b["status"] and a["body"] == b["body"]
        cmp(f"[{a['name']}] status", a["status"], b["status"])
        cmp(f"[{a['name']}] body", a["body"], b["body"])
        print(f"    {a['name']:<22} {a['status']} {'일치' if ok else '**불일치**'}")

    print("  --- 50MB 경계 ---")
    for a, b in zip(py_sizes, ts["sizes"]):
        cmp(f"[{a['name']}]", a["result"], b["result"])
        ok = a["result"] == b["result"]
        print(f"    {a['name']:<22} {a['result'].get('status')} "
              f"{'일치' if ok else '**불일치**'}")

    for f in fails[:10]:
        print(f"  **{f}**")
    print(f"  비교 {checks}건, 불일치 {len(fails)}건")
    if negative:
        print("음성 대조: " + ("검사기 정상 (변조 검출)" if fails else "**검사기가 못 잡는다**"))
        sys.exit(0 if fails else 1)
    print("FAIL 0" if not fails else f"FAIL {len(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "api"))
    main()
