/**
 * 기기 토큰 + precheck **라이브 스모크**. 운영(`JETRAG_API_BASE`, 기본 운영 도메인)을 실제로 친다.
 *
 *   deno run --allow-net --allow-env --allow-read \
 *     api/scripts/e2e_device_precheck.ts <session_access_token> <이미_올라간_파일경로>
 *
 * 1) 세션으로 기기 발급 → 2) 기기 토큰으로 precheck(existing/new) → 3) 500 개 precheck(청크 경로)
 * 4) 기기 토큰으로 업로드(duplicated) → 5) 기기 토큰으로 GET /documents → 403
 * 6) 폐기 → 7) 폐기된 토큰으로 precheck → 401
 *
 * ## 판정은 이 스크립트가 한다
 * 각 단계의 관측값을 기대값과 대조하고, **하나라도 어긋나면 비 0 으로 종료**한다.
 * 전부 맞으면 마지막 줄이 `E2E PASS` 다. 눈으로 로그를 읽어 판단하지 않는다.
 *
 * ## 부작용 0
 * **이미 올라가 있는 파일**을 인자로 준다. 업로드는 dedup 에 걸려 `duplicated` 로 끝나므로
 * 새 문서·잡·Storage 객체가 생기지 않는다. 새 파일을 주면 진짜 인제스트가 돈다.
 * 발급한 기기는 6 단계에서 폐기한다 — 실패로 중단되면 기기가 남을 수 있으니 설정에서 확인할 것.
 */

import { basename } from "jsr:@std/path@^1";

/** `_shared/documents/precheck.ts` 의 `PRECHECK_MAX` 와 같아야 한다 — 상한 경계를 친다. */
const PRECHECK_MAX = 500;

const [session, filePath] = Deno.args;
if (!session || !filePath) {
  console.error("사용: e2e_device_precheck.ts <session_access_token> <이미_올라간_파일경로>");
  Deno.exit(2);
}
const API = Deno.env.get("JETRAG_API_BASE") ?? "https://jetrag-api.woong-s.com";
const UA = "JetRag-Agent/0.0-e2e";
const ZERO = "0".repeat(64);

const failures: string[] = [];
/** 관측값을 기대값과 대조한다. 어긋나면 기록만 하고 계속 — 뒷단계 정보도 같이 얻는다. */
function check(label: string, actual: unknown, expected: unknown): void {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  console.log(`${ok ? "OK  " : "FAIL"} ${label}: ${JSON.stringify(actual)}`);
  if (!ok) failures.push(`${label} — 기대 ${JSON.stringify(expected)}, 실제 ${JSON.stringify(actual)}`);
}
function die(msg: string, detail: unknown): never {
  console.error(msg, detail);
  Deno.exit(1);
}
function randomHex64(): string {
  const b = new Uint8Array(32);
  crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}
async function safeJson(r: Response): Promise<unknown> {
  const text = await r.text();
  try {
    return JSON.parse(text);
  } catch {
    return { _raw: text.slice(0, 400) };
  }
}

/* ---------------------------------------------------------------- 1 기기 발급 */
const devRes = await fetch(`${API}/me/devices`, {
  method: "POST",
  headers: {
    Authorization: `Bearer ${session}`,
    "Content-Type": "application/json",
    "User-Agent": UA,
  },
  body: JSON.stringify({ name: "e2e-test" }),
});
// deno-lint-ignore no-explicit-any
const dev = await safeJson(devRes) as any;
if (!devRes.ok || !dev?.token || !dev?.id) die("1 발급 실패:", { status: devRes.status, dev });
console.log("1 발급:", dev.id, dev.token_prefix);
const dh = { Authorization: `Bearer ${dev.token}`, "User-Agent": UA };

const bytes = await Deno.readFile(filePath);
const digest = await crypto.subtle.digest("SHA-256", bytes);
const sha = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");

const precheck = (hashes: string[]) =>
  fetch(`${API}/documents/precheck`, {
    method: "POST",
    headers: { ...dh, "Content-Type": "application/json" },
    body: JSON.stringify({ hashes }),
  });

/* --------------------------------------------------------------- 2 precheck 2개 */
const preRes = await precheck([sha, ZERO]);
// deno-lint-ignore no-explicit-any
const pre = await safeJson(preRes) as any;
if (!preRes.ok || !pre?.results) die("2 precheck 실패:", { status: preRes.status, pre });
check("2 precheck 상태", preRes.status, 200);
check("2 precheck 기존 파일", pre.results[sha]?.state, "existing");
check("2 precheck 없는 해시", pre.results[ZERO]?.state, "new");

/* ------------------------------------------------- 3 precheck 상한(청크 경로 실측) */
// 서버가 100 개씩 끊어 조회한다. 500 을 한 번에 보내면 게이트웨이 8KB 버퍼에서 414 였다 —
// 그 회귀를 라이브에서 잡는 단계다.
const randoms = Array.from({ length: PRECHECK_MAX - 1 }, randomHex64);
const bigRes = await precheck([sha, ...randoms]);
// deno-lint-ignore no-explicit-any
const big = await safeJson(bigRes) as any;
check(`3 precheck ${PRECHECK_MAX}개 상태`, bigRes.status, 200);
if (bigRes.ok && big?.results) {
  check("3 기존 파일", big.results[sha]?.state, "existing");
  const notNew = randoms.filter((h) => big.results[h]?.state !== "new");
  check("3 랜덤 해시가 전부 new", notNew.length, 0);
  check("3 결과 개수", Object.keys(big.results).length, PRECHECK_MAX);
} else {
  failures.push(`3 precheck ${PRECHECK_MAX}개 — 본문: ${JSON.stringify(big).slice(0, 300)}`);
}

/* -------------------------------------------------------------------- 4 업로드 */
const fd = new FormData();
fd.append("file", new Blob([bytes]), basename(filePath));
fd.append("source_channel", "pc-agent");
const up = await fetch(`${API}/documents`, { method: "POST", headers: dh, body: fd });
// deno-lint-ignore no-explicit-any
const upBody = await safeJson(up) as any;
check("4 upload 상태", up.status, 202);
check("4 upload duplicated", upBody?.duplicated, true);

/* ------------------------------------------------ 5 기기 토큰은 읽기를 못 한다 */
const list = await fetch(`${API}/documents`, { headers: dh });
check("5 GET /documents", list.status, 403);

/* ---------------------------------------------------------------------- 6 폐기 */
const rv = await fetch(`${API}/me/devices/${dev.id}`, {
  method: "DELETE",
  headers: { Authorization: `Bearer ${session}`, "User-Agent": UA },
});
check("6 폐기", rv.status, 200);

/* ------------------------------------------------------- 7 폐기 후에는 401 이다 */
const again = await precheck([sha]);
await again.body?.cancel();
check("7 폐기 후 precheck", again.status, 401);

if (failures.length > 0) {
  console.error(`\nE2E FAIL — ${failures.length}건`);
  for (const f of failures) console.error(" -", f);
  Deno.exit(1);
}
console.log("\nE2E PASS");
