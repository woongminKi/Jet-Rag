/**
 * 틱 루프 통합 테스트 — `run.ts` 만 단위 테스트가 없었다.
 *
 * ## 전부 로컬이다
 * 서버는 `Deno.serve({ port: 0 })` 로 루프백에 띄운 목이고, 홈·감시 폴더는 임시 디렉터리,
 * 원장은 그 안의 SQLite 파일이다. 운영 API 를 부르지 않는다.
 *
 * ## 대부분 `once: true` 로 돈다
 * 한 틱만 돌면 시계를 기다릴 일이 없고 `watchFs` 도 뜨지 않아 결과가 결정적이다.
 * 2틱 이상이 필요한 것(전역 일시정지)만 루프를 돌리고, 재시도 재개는 `Retry-After: 0` 과
 * `runAgent` 두 번 호출로 **기다리지 않고** 확인한다.
 */

import { assertEquals, assertRejects } from "@std/assert";
import { ApiClient, QUOTA_WAIT_MS } from "./client.ts";
import type { AgentConfig } from "./config.ts";
import { sha256File } from "./hash.ts";
import { Ledger } from "./ledger.ts";
import { FileLogger } from "./log.ts";
import { FatalAuthError, runAgent } from "./run.ts";

const TOKEN = "jrd_" + "a".repeat(43);

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

interface MockOpts {
  /** 해시 → precheck 결과. 없으면 전부 `new`. */
  precheck?: (hashes: string[]) => Response;
  /** `n` 은 1부터 세는 업로드 호출 순번. 없으면 202 신규. */
  upload?: (n: number) => Response;
  batchStatus?: (ids: string[]) => Response;
}

function mockServer(opts: MockOpts = {}) {
  const uploads: string[] = [];
  const prechecks: string[][] = [];
  const statusCalls: string[][] = [];
  const server = Deno.serve({ port: 0, onListen: () => {} }, async (req) => {
    const url = new URL(req.url);
    if (url.pathname === "/documents/precheck") {
      const body = await req.json() as { hashes: string[] };
      prechecks.push(body.hashes);
      return opts.precheck?.(body.hashes) ??
        json(200, { results: Object.fromEntries(body.hashes.map((h) => [h, { state: "new" }])) });
    }
    if (url.pathname === "/documents/batch-status") {
      const ids = (url.searchParams.get("ids") ?? "").split(",").filter((s) => s !== "");
      statusCalls.push(ids);
      return opts.batchStatus?.(ids) ?? json(200, {
        items: ids.map((id) => ({
          doc_id: id,
          job: { status: "running", current_stage: "extract", error_msg: null },
        })),
      });
    }
    if (url.pathname === "/documents" && req.method === "POST") {
      const form = await req.formData();
      const f = form.get("file");
      uploads.push(f instanceof File ? f.name : "");
      return opts.upload?.(uploads.length) ?? json(202, { doc_id: "d1", job_id: "j1", duplicated: false });
    }
    return json(404, { detail: "목 서버에 없는 경로" });
  });
  const port = (server.addr as Deno.NetAddr).port;
  return {
    base: `http://127.0.0.1:${port}`,
    uploads,
    prechecks,
    statusCalls,
    close: () => server.shutdown(),
  };
}

interface Rig {
  home: string;
  watchDir: string;
  ledger: Ledger;
  logger: FileLogger;
  cfg: AgentConfig;
  client: ApiClient;
  close: () => void;
}

async function rig(base: string, backlogDays = 90): Promise<Rig> {
  const home = await Deno.makeTempDir({ prefix: "jetrag-run-home-" });
  const watchDir = await Deno.makeTempDir({ prefix: "jetrag-run-watch-" });
  const ledger = Ledger.open(`${home}/ledger.sqlite`);
  const logger = new FileLogger(`${home}/agent.log`);
  const cfg: AgentConfig = {
    api_base: base,
    device_token: TOKEN,
    watch_dirs: [watchDir],
    backlog_days: backlogDays,
  };
  const client = new ApiClient({
    apiBase: base,
    token: TOKEN,
    os: "darwin",
    requestTimeoutMs: 3000,
    uploadTimeoutMs: 3000,
  });
  return { home, watchDir, ledger, logger, cfg, client, close: () => ledger.close() };
}

/** 감시 폴더에 파일을 만들고 sha256 을 돌려준다. `ageMs` 로 mtime 을 밀어 claimUpload 순서를 고정한다. */
async function putFile(dir: string, name: string, body: string, ageMs = 0): Promise<string> {
  const path = `${dir}/${name}`;
  await Deno.writeTextFile(path, body);
  if (ageMs > 0) {
    const t = new Date(Date.now() - ageMs);
    await Deno.utime(path, t, t);
  }
  return await sha256File(path);
}

Deno.test("run — 신규 파일: 백로그 스캔 → precheck new → 202 → registered + doc_id", async () => {
  const s = mockServer({
    upload: () => json(202, { doc_id: "doc-새것", job_id: "job-1", duplicated: false }),
  });
  const r = await rig(s.base);
  try {
    const sha = await putFile(r.watchDir, "보고서.pdf", "%PDF-1.7\n본문");
    await runAgent({ ...r, once: true });

    const row = r.ledger.get(sha)!;
    assertEquals(row.state, "registered");
    assertEquals(row.doc_id, "doc-새것");
    assertEquals(row.job_id, "job-1");
    assertEquals(row.precheck_done, 1);
    assertEquals(row.attempts, 0);
    // 한 틱 안에서 precheck 1회 + 업로드 1회가 끝난다.
    assertEquals(s.uploads, ["보고서.pdf"]);
    assertEquals(s.prechecks.length, 1);
    // 폴링은 30초 뒤로 예약된다 — 이 틱에서는 batch-status 를 부르지 않는다.
    assertEquals(s.statusCalls.length, 0);
    assertEquals(row.next_attempt_at > Date.now(), true);
  } finally {
    r.close();
    await s.close();
  }
});

Deno.test("run — precheck existing: 업로드 요청을 아예 보내지 않고 done 으로 닫는다", async () => {
  const s = mockServer({
    precheck: (hashes) =>
      json(200, {
        results: Object.fromEntries(hashes.map((h) => [h, { state: "existing", doc_id: "doc-이미있음" }])),
      }),
  });
  const r = await rig(s.base);
  try {
    const sha = await putFile(r.watchDir, "중복.pdf", "%PDF-1.7\n같은내용");
    await runAgent({ ...r, once: true });

    const row = r.ledger.get(sha)!;
    assertEquals(row.state, "done");
    assertEquals(row.doc_id, "doc-이미있음");
    // 이게 이 기능의 전부다 — 바이트를 한 번도 보내지 않는다.
    assertEquals(s.uploads, []);
    assertEquals(r.ledger.counts().done, 1);
  } finally {
    r.close();
    await s.close();
  }
});

Deno.test("run — 429: 파일은 pending 으로 남고 Retry-After 만큼 뒤로 밀린다", async () => {
  const s = mockServer({
    upload: () => new Response("", { status: 429, headers: { "retry-after": "30" } }),
  });
  const r = await rig(s.base);
  try {
    const sha = await putFile(r.watchDir, "바쁨.pdf", "%PDF-1.7\n___");
    const t0 = Date.now();
    await runAgent({ ...r, once: true });

    const row = r.ledger.get(sha)!;
    assertEquals(row.state, "pending");
    assertEquals(row.attempts, 1);
    assertEquals(row.doc_id, null);
    // Retry-After: 30 → 30초 뒤. 지수 백오프(5초)를 쓰지 않는다.
    assertEquals(row.next_attempt_at >= t0 + 30_000, true);
    assertEquals(row.next_attempt_at <= Date.now() + 30_000, true);
    assertEquals(s.uploads.length, 1);
  } finally {
    r.close();
    await s.close();
  }
});

Deno.test("run — 429 는 전역 일시정지다: 준비된 다른 파일도 붙잡아 둔다", async () => {
  // 상한 키가 user_id 라 파일 하나만 미루면 다음 파일이 곧바로 또 429 를 맞는다(스펙 §10).
  const s = mockServer({
    upload: () => new Response("", { status: 429, headers: { "retry-after": "1" } }),
  });
  const r = await rig(s.base);
  const ac = new AbortController();
  try {
    // A 가 더 최신이라 claimUpload(mtime DESC) 가 먼저 집는다. B 는 같은 틱에 precheck 까지 끝난다.
    const shaA = await putFile(r.watchDir, "먼저.pdf", "%PDF-1.7\nA");
    const shaB = await putFile(r.watchDir, "나중.pdf", "%PDF-1.7\nB", 60_000);
    const loop = runAgent({ ...r, tickMs: 5, signal: ac.signal });
    await new Promise((res) => setTimeout(res, 250));
    ac.abort();
    await loop;

    // 250ms 동안 틱이 수십 번 돌았지만 업로드는 1건뿐이다 — 일시정지가 틱 전체를 막았다.
    assertEquals(s.uploads, ["먼저.pdf"]);
    // B 는 막혀서 못 간 것이지 준비가 안 된 게 아니다.
    const rowB = r.ledger.get(shaB)!;
    assertEquals(rowB.precheck_done, 1);
    assertEquals(rowB.state, "pending");
    assertEquals(rowB.next_attempt_at <= Date.now(), true);
    assertEquals(r.ledger.get(shaA)!.attempts, 1);
  } finally {
    ac.abort();
    r.close();
    await s.close();
  }
});

Deno.test("run — 일시정지가 풀리면 같은 파일을 다시 올린다 (429 → 202)", async () => {
  // Retry-After: 0 이라 기다릴 시간이 없다 — 틱을 두 번 돌리는 것으로 재개를 확인한다.
  const s = mockServer({
    upload: (n) =>
      n === 1
        ? new Response("", { status: 429, headers: { "retry-after": "0" } })
        : json(202, { doc_id: "doc-재시도", job_id: "job-2", duplicated: false }),
  });
  const r = await rig(s.base);
  try {
    const sha = await putFile(r.watchDir, "재시도.pdf", "%PDF-1.7\n___");
    await runAgent({ ...r, once: true });
    assertEquals(r.ledger.get(sha)!.state, "pending");
    assertEquals(r.ledger.get(sha)!.attempts, 1);

    await runAgent({ ...r, once: true });
    const row = r.ledger.get(sha)!;
    assertEquals(row.state, "registered");
    assertEquals(row.doc_id, "doc-재시도");
    assertEquals(s.uploads, ["재시도.pdf", "재시도.pdf"]);
    // 두 번째 틱은 precheck 를 다시 묻지 않는다 — precheck_done 이 살아 있다.
    assertEquals(s.prechecks.length, 1);
  } finally {
    r.close();
    await s.close();
  }
});

Deno.test("run — 401: 루프를 멈추고, 파일은 attempts 를 태우지 않은 채 pending 으로 되돌아온다", async () => {
  const s = mockServer({ upload: () => json(401, { detail: "기기 토큰이 폐기되었습니다." }) });
  const r = await rig(s.base);
  try {
    const sha = await putFile(r.watchDir, "토큰문제.pdf", "%PDF-1.7\n___");
    await assertRejects(() => runAgent({ ...r, once: true }), FatalAuthError);

    const row = r.ledger.get(sha)!;
    // 파일 잘못이 아니다. 토큰을 고친 뒤 재시도 예산이 남아 있어야 한다.
    assertEquals(row.state, "pending");
    assertEquals(row.attempts, 0);
    assertEquals(row.next_attempt_at, 0);
    assertEquals(r.ledger.counts().excluded, 0);
    assertEquals(r.ledger.counts().failed, 0);
  } finally {
    r.close();
    await s.close();
  }
});

Deno.test("run — 402 storage_limit: quota_wait + 6시간 뒤 재시도, 사유에 사용량이 남는다", async () => {
  const s = mockServer({
    upload: () => json(402, { detail: "저장 용량 한도 초과", code: "storage_limit", used: 900, limit: 1000 }),
  });
  const r = await rig(s.base);
  try {
    const sha = await putFile(r.watchDir, "용량초과.pdf", "%PDF-1.7\n___");
    const t0 = Date.now();
    await runAgent({ ...r, once: true });

    const row = r.ledger.get(sha)!;
    assertEquals(row.state, "quota_wait");
    assertEquals(row.next_attempt_at >= t0 + QUOTA_WAIT_MS, true);
    assertEquals((row.last_error ?? "").includes("900"), true);
    assertEquals((row.last_error ?? "").includes("1000"), true);
    assertEquals(r.ledger.counts().quota_wait, 1);
    // 영구 제외가 아니다 — 예약 시각이 되면 dueQuotaWait 로 돌아온다.
    assertEquals(r.ledger.dueQuotaWait(row.next_attempt_at).length, 1);
    assertEquals(r.ledger.dueQuotaWait(row.next_attempt_at - 1).length, 0);
  } finally {
    r.close();
    await s.close();
  }
});

Deno.test("run — 503 code channel: 제외가 아니라 백오프 재시도다 (마이그가 밀린 서버)", async () => {
  const s = mockServer({ upload: () => json(503, { detail: "채널 값을 DB 가 거절", code: "channel" }) });
  const r = await rig(s.base);
  try {
    const sha = await putFile(r.watchDir, "마이그밀림.pdf", "%PDF-1.7\n___");
    const t0 = Date.now();
    await runAgent({ ...r, once: true });

    const row = r.ledger.get(sha)!;
    assertEquals(row.state, "pending");
    assertEquals(row.attempts, 1);
    // 서버 배포만 따라오면 낫는 상태라 영구 제외로 닫으면 안 된다.
    assertEquals(r.ledger.counts().excluded, 0);
    assertEquals(r.ledger.counts().failed, 0);
    // 지수 백오프 1회차 5초 ± jitter 20%.
    assertEquals(row.next_attempt_at >= t0 + 4_000, true);
    assertEquals(row.next_attempt_at <= t0 + 6_000 + 1_000, true);
  } finally {
    r.close();
    await s.close();
  }
});

Deno.test("run — batch-status deferred_quota 는 종단이 아니다 (실패로 적지 않는다)", async () => {
  const s = mockServer({
    batchStatus: (ids) =>
      json(200, {
        items: ids.map((id) => ({
          doc_id: id,
          job: { status: "deferred_quota", current_stage: "vision", error_msg: null },
        })),
      }),
  });
  const r = await rig(s.base, 0);
  try {
    // 이미 등록돼 폴링만 남은 행을 만든다. 폴링 만기를 0 으로 둬 이번 틱에 바로 잡히게 한다.
    const sha = "f".repeat(64);
    const now = Date.now();
    r.ledger.discover(
      { sha256: sha, path: `${r.watchDir}/처리중.pdf`, size: 10, mtime: now, queue: "live" },
      now,
    );
    r.ledger.markPrechecked([sha], now);
    r.ledger.markRegistered(sha, "doc-대기", "job-대기", 0, now);

    await runAgent({ ...r, once: true, skipBacklog: true });

    const row = r.ledger.get(sha)!;
    assertEquals(row.state, "registered");
    assertEquals(r.ledger.counts().failed, 0);
    assertEquals((row.last_error ?? "").includes("한도"), true);
    // 6시간 뒤 다시 물어본다 — 월초 cron 이 재투입한다.
    assertEquals(row.next_attempt_at >= now + QUOTA_WAIT_MS, true);
    assertEquals(s.statusCalls, [["doc-대기"]]);
    assertEquals(s.uploads, []);
  } finally {
    r.close();
    await s.close();
  }
});

Deno.test("run — 폴링을 반복해도 doc_id 를 잃지 않고 completed 까지 간다", async () => {
  // 회귀: apply() 가 폴링 전이에서 doc_id 를 덮어써 "" 로 만들면 batch-status?ids= 가 되고
  // 그 문서는 영원히 registered 에 남는다. 운영에서 모든 문서가 완료되지 않는 버그였다.
  let poll = 0;
  const s = mockServer({
    upload: () => json(202, { doc_id: "doc-추적", job_id: "job-추적", duplicated: false }),
    batchStatus: (ids) => {
      poll++;
      const status = poll === 1 ? "running" : "completed";
      return json(200, {
        items: ids.map((id) => ({
          doc_id: id,
          job: { status, current_stage: "extract", error_msg: null },
        })),
      });
    },
  });
  const r = await rig(s.base);
  try {
    const sha = await putFile(r.watchDir, "추적.pdf", "%PDF-1.7\n___");
    await runAgent({ ...r, once: true });
    assertEquals(r.ledger.get(sha)!.doc_id, "doc-추적");

    // 폴링 만기를 앞당겨 다음 틱에 바로 잡히게 한다(시계를 기다리지 않는다).
    r.ledger.markRegistered(sha, null, null, 0, Date.now());
    await runAgent({ ...r, once: true, skipBacklog: true });
    assertEquals(r.ledger.get(sha)!.state, "registered");
    assertEquals(r.ledger.get(sha)!.doc_id, "doc-추적");

    r.ledger.markRegistered(sha, null, null, 0, Date.now());
    await runAgent({ ...r, once: true, skipBacklog: true });
    assertEquals(r.ledger.get(sha)!.state, "done");
    assertEquals(r.ledger.get(sha)!.doc_id, "doc-추적");

    // 두 번의 폴링 모두 진짜 doc_id 로 물었다. 빈 문자열이면 여기서 드러난다.
    assertEquals(s.statusCalls, [["doc-추적"], ["doc-추적"]]);
    assertEquals(s.uploads, ["추적.pdf"]);
  } finally {
    r.close();
    await s.close();
  }
});
