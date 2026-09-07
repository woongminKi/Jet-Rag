/**
 * Railway 종료 리허설 — **끄기 전에** 무엇이 깨지는지 센다.
 *
 * `LEGACY_ORIGIN` 을 비우면 프록시가 미이관 경로를 Railway 로 넘기지 않고 404 를 낸다
 * (`index.js` 의 "Phase 6 에서 이 값을 비우면 Railway 의존이 끝난다"). 그 상태를
 * **라이브가 아니라 여기서** 재현한다 — 진짜로 비우면 그 순간 트래픽이 죽는다.
 *
 * ## 워커를 흉내내지 않고 진짜로 돌린다
 * `resolveTarget` 만 부르면 라우팅 표만 보는 것이고, `LEGACY_ORIGIN` 분기·자기참조
 * 검사·헤더 주입은 안 거친다. 여기서는 워커의 `fetch` 핸들러를 그대로 부르고
 * **네트워크만 가로챈다.** 그래야 "종료 후 실제 응답"을 재현했다고 말할 수 있다.
 *
 * ## 라우트 표만으로는 부족하다
 * HTTP 라우트 밖에도 Railway 의존이 있다. 그건 자동으로 못 재므로 목록으로 띄운다
 * (§비-HTTP 의존).
 *
 * ## 이 스크립트는 기본적으로 **저장소**를 잰다
 * `routes.js` 를 import 해서 도는 것이라, 프록시를 아직 **배포하지 않았으면 프로덕션과
 * 다르다.** 실제로 지금 이메일·결제 규칙은 커밋만 돼 있고 배포 전이다 — 저장소 기준으로는
 * "Edge 로 간다" 지만 프로덕션에서는 Railway 로 간다.
 *
 * `--live` 를 주면 실제 프록시를 찔러 **배포된 워커가 어디로 보내는지**까지 확인하고,
 * 저장소와 어긋나면 드리프트로 보고한다. 이게 없으면 리허설이 안전하다고 말하는데
 * 프로덕션은 아닌 상태가 된다.
 *
 * 사용:
 *   deno run --allow-read api/scripts/rehearse_railway_shutdown.ts
 *   deno run --allow-read --allow-net api/scripts/rehearse_railway_shutdown.ts --live
 */

import worker from "../../workers/api-proxy/src/index.js";

const ROOT = new URL("../../", import.meta.url);
const PROXY_HOST = "https://jetrag-api.woong-s.com";

const ENV_NOW = {
  SUPABASE_FUNCTIONS_BASE: "https://mpmtydudhojpukuuadrd.supabase.co/functions/v1",
  LEGACY_ORIGIN: "https://jet-rag-production.up.railway.app",
  SUPABASE_FUNCTION_REGION: "ap-northeast-2",
};
const ENV_AFTER = { ...ENV_NOW, LEGACY_ORIGIN: "" };

interface RouteRow {
  path: string;
  methods: string[];
}

/** 실제 요청은 보내지 않는다 — 어디로 갔을지만 기록한다. */
async function run(
  path: string,
  method: string,
  env: Record<string, string>,
): Promise<{ status: number; where: string }> {
  const original = globalThis.fetch;
  let sentTo: string | null = null;
  globalThis.fetch = ((input: Request | string | URL) => {
    sentTo = input instanceof Request ? input.url : String(input);
    // 실제 백엔드 대신 200 을 준다 — 우리가 보려는 건 "어디로 가는가" 다.
    return Promise.resolve(new Response("{}", { status: 200 }));
  }) as typeof fetch;
  try {
    const res = await worker.fetch(
      new Request(`${PROXY_HOST}${path}`, { method }),
      // deno-lint-ignore no-explicit-any
      env as any,
    );
    if (sentTo === null) {
      return { status: res.status, where: "프록시가 직접 응답" };
    }
    return {
      status: res.status,
      where: sentTo.includes("railway.app") ? "Railway" : "Edge",
    };
  } finally {
    globalThis.fetch = original;
  }
}

const verbose = Deno.args.includes("--verbose");
const routes: RouteRow[] = JSON.parse(
  await Deno.readTextFile(new URL("api/scripts/fixtures/fastapi_routes.json", ROOT)),
);

// `{doc_id}` 같은 자리표시자는 실제 값으로 바꿔야 라우팅이 제대로 걸린다.
const SAMPLE_ID = "11111111-1111-4111-8111-111111111111";
const concrete = (p: string) => p.replace(/\{[^}]+\}/g, SAMPLE_ID);

interface Finding {
  method: string;
  path: string;
  before: string;
  after: string;
  afterStatus: number;
}

const broken: Finding[] = [];
const unchanged: Finding[] = [];

for (const { path, methods } of routes) {
  for (const method of methods) {
    const p = concrete(path);
    const b = await run(p, method, ENV_NOW);
    const a = await run(p, method, ENV_AFTER);
    const row: Finding = {
      method,
      path,
      before: b.where,
      after: a.where,
      afterStatus: a.status,
    };
    if (b.where !== a.where || b.status !== a.status) broken.push(row);
    else unchanged.push(row);
  }
}

console.log("\n══ Railway 종료 리허설 ══");
console.log(`  라우트 ${routes.length}개 · 메서드 단위 ${broken.length + unchanged.length}건\n`);

console.log(`── 그대로 도는 것 (${unchanged.length}건) ──`);
if (verbose) {
  for (const r of unchanged) console.log(`  ${r.method.padEnd(6)}${r.path.padEnd(40)}→ ${r.after}`);
} else {
  console.log(`  전부 Edge 로 간다. 목록은 --verbose`);
}

console.log(`\n── 끄면 달라지는 것 (${broken.length}건) ──`);
for (const r of broken) {
  console.log(
    `  ${r.method.padEnd(6)}${r.path.padEnd(30)} ${r.before} → ${r.after} (${r.afterStatus})`,
  );
}

// 라우트 표에 없는 것들 — 클라이언트가 실제로 보낼 법한 경로.
console.log("\n── 라우트 표 밖 (클라이언트가 보낼 법한 것) ──");
const EXTRA: [string, string][] = [
  ["GET", "/search/"], // FastAPI 는 307 로 리다이렉트했다 — Edge 는 200 을 준다
  ["GET", "/documents/"],
  ["GET", "/me"], // 슬래시 없음 — 원본에 라우트가 없다
  ["GET", "/favicon.ico"],
  ["GET", "/없는경로"],
  ["POST", "/documents/url"], // 2026-09-07 폐기
];
for (const [method, path] of EXTRA) {
  const b = await run(path, method, ENV_NOW);
  const a = await run(path, method, ENV_AFTER);
  const mark = b.where === a.where ? " " : "*";
  console.log(
    `  ${mark}${method.padEnd(6)}${path.padEnd(30)} ${b.where} → ${a.where} (${a.status})`,
  );
}

console.log(`
── 비-HTTP 의존 (자동으로 못 잰다 — 사람이 확인해야 한다) ──
  1. Railway cron — api/scripts/billing_charge.py
     끄면 **월 자동결제가 멈춘다.** 대체: pg_cron 이 POST /billing/run 호출 (마이그 029 미작성)
  2. .github/workflows/monitor-search-slo.yml — **2026-09-07 해소.**
     API base 를 프록시 도메인 기본값으로 박았다(공개 URL 이라 secret 이 아니다).
     secret 은 더 이상 읽지 않는다. 남은 확인: repo **variable** JET_RAG_API_BASE 가
     Railway 주소로 설정돼 있으면 그쪽이 이긴다 — Settings → Variables 에서 한 번 볼 것
  3. api/scripts/verify_documents_read_parity.py
     원본과 Edge 를 비교하는 대조 스크립트다. Railway 가 사라지면 **비교 대상이 없어져**
     더 못 돈다 — 이관이 끝나면 역할도 끝나므로 정상이다
  4. FastAPI 자체 문서(/docs · /redoc · /openapi.json · /)
     Railway 와 함께 사라진다. 대체물을 둘지는 결정 사항
  5. 프런트(Vercel) — **의존 아님**. NEXT_PUBLIC_API_BASE_URL 이
     https://jetrag-api.woong-s.com (프록시 도메인)로 설정돼 있다
     (2026-05-19 도메인 부착 기록). NEXT_PUBLIC_* 은 빌드 시 inlining 되므로
     값이 바뀌었다면 재배포가 필요했을 것 — 대시보드에서 한 번 재확인하면 확실하다

── 끄기 전 순서 ──
  1. secret 4개 설정 → deno run --allow-net api/scripts/verify_cutover.ts
  2. 프록시 배포 (이메일·결제가 Edge 로)
  3. billing cron 대체 (마이그 029)
  4. monitor-search-slo 의 JET_RAG_API_BASE 를 프록시 도메인으로
  5. 그 다음에 LEGACY_ORIGIN 비우기
`);

// ─────────────────────────────────────────────────────────────
// --live: 배포된 워커가 실제로 어디로 보내는지. 저장소와 어긋나면 드리프트다.
// ─────────────────────────────────────────────────────────────
let driftCount = 0;
if (Deno.args.includes("--live")) {
  console.log("── 배포된 프록시 실측 (저장소와 대조) ──");
  // 부작용 없는 요청만 고른다. 인증·secret 이 없어 401/503 로 끊기는 경로들이다.
  const PROBES: [string, string][] = [
    ["POST", "/ingest/email"],
    ["POST", "/billing/run"],
    ["POST", "/payments/subscribe/ready"],
    ["GET", "/health"],
    ["GET", "/documents/active"],
  ];
  for (const [method, path] of PROBES) {
    const repo = (await run(path, method, ENV_NOW)).where;
    let live = "확인 불가";
    try {
      const res = await fetch(`${PROXY_HOST}${path}`, {
        method,
        headers: { "content-type": "application/json" },
        body: method === "POST" ? "{}" : undefined,
      });
      await res.body?.cancel();
      // 본문이 아니라 **헤더**로 가른다 — 오류 본문은 양쪽이 비슷할 수 있다.
      live = res.headers.get("x-served-by") === "supabase-edge-runtime"
        ? "Edge"
        : (res.headers.has("x-railway-request-id") || res.headers.has("x-railway-edge"))
        ? "Railway"
        : "확인 불가";
    } catch (e) {
      live = `요청 실패(${e})`;
    }
    const drift = repo !== live;
    if (drift) driftCount++;
    console.log(
      `  ${method.padEnd(6)}${path.padEnd(30)} 저장소 ${repo.padEnd(8)} 배포 ${live.padEnd(8)}` +
        `${drift ? "  ← **드리프트**" : ""}`,
    );
  }
  if (driftCount > 0) {
    console.log(
      `\n  **저장소와 배포가 ${driftCount}건 어긋난다.** 프록시를 배포하지 않은 상태다 —\n` +
        `  위 "그대로 도는 것" 은 저장소 기준이라 프로덕션에는 아직 해당하지 않는다.`,
    );
  } else {
    console.log("\n  저장소와 배포가 일치한다.");
  }
  console.log("");
}

// 결제·이메일이 아직 Railway 로 가면 종료 준비가 안 된 것이다.
const blocking = broken.filter((r) =>
  r.path.startsWith("/payments") || r.path.startsWith("/billing") ||
  r.path.startsWith("/ingest/email")
);
if (blocking.length > 0) {
  console.log(`**아직 끄면 안 된다** — 기능 라우트 ${blocking.length}건이 Railway 로 간다:`);
  for (const r of blocking) console.log(`    ${r.method} ${r.path}`);
  Deno.exit(1);
}
const appRoutes = broken.filter((r) =>
  !["/", "/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"].includes(r.path)
);
console.log(
  appRoutes.length === 0
    ? "저장소 기준: 끄면 사라지는 건 FastAPI 자체 문서뿐이다 — 앱 라우트는 전부 Edge 다."
    : `**앱 라우트 ${appRoutes.length}건이 아직 Railway 다.**`,
);
if (driftCount > 0) {
  console.log("**단, 배포가 저장소를 못 따라왔다 — 위 드리프트를 먼저 해소해야 한다.**");
}
Deno.exit(driftCount > 0 ? 1 : 0);
