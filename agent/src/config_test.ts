import { assertEquals, assertThrows } from "@std/assert";
import { agentHome, configPath, loadConfig, saveConfig, validateConfig } from "./config.ts";

const TOKEN = "jrd_" + "a".repeat(43);

function tmpHome(): string {
  return Deno.makeTempDirSync({ prefix: "jetrag-cfg-" });
}

Deno.test("config — 정상값 검증 통과, api_base 뒤 슬래시 제거", () => {
  const r = validateConfig({
    api_base: "https://jetrag-api.woong-s.com/",
    device_token: TOKEN,
    watch_dirs: ["/Users/x/Downloads", "/Users/x/Downloads/"],
    backlog_days: 90,
  });
  assertEquals(r.ok, true);
  if (!r.ok) return;
  assertEquals(r.config.api_base, "https://jetrag-api.woong-s.com");
  // 같은 폴더가 슬래시 차이로 두 번 감시되면 watchFs 이벤트가 2배로 들어온다.
  assertEquals(r.config.watch_dirs, ["/Users/x/Downloads"]);
});

Deno.test("config — 잘못된 값은 항목별 사유를 전부 모아서 돌려준다", () => {
  const r = validateConfig({
    api_base: "ftp://x",
    device_token: "abc",
    watch_dirs: [],
    backlog_days: -1,
  });
  assertEquals(r.ok, false);
  if (r.ok) return;
  assertEquals(r.errors.length, 4);
  assertEquals(r.errors.some((e) => e.includes("api_base")), true);
  assertEquals(r.errors.some((e) => e.includes("device_token")), true);
  assertEquals(r.errors.some((e) => e.includes("watch_dirs")), true);
  assertEquals(r.errors.some((e) => e.includes("backlog_days")), true);
});

Deno.test("config — 토큰 형식은 jrd_ + 43자만", () => {
  const bad = ["jrd_", "jrd_" + "a".repeat(42), "jrd_" + "a".repeat(44), "jrx_" + "a".repeat(43)];
  for (const t of bad) {
    const r = validateConfig({
      api_base: "https://a.b",
      device_token: t,
      watch_dirs: ["/a"],
      backlog_days: 0,
    });
    assertEquals(r.ok, false, `통과하면 안 된다: ${t}`);
  }
});

Deno.test("config — localhost 는 http 허용, 그 외 도메인은 https 강제", () => {
  const mk = (base: string) =>
    validateConfig({ api_base: base, device_token: TOKEN, watch_dirs: ["/a"], backlog_days: 0 });
  assertEquals(mk("http://localhost:8000").ok, true);
  assertEquals(mk("http://127.0.0.1:8000").ok, true);
  // 기기 토큰이 평문으로 나간다. 운영 도메인에 http 를 허용하면 안 된다.
  assertEquals(mk("http://jetrag-api.woong-s.com").ok, false);
});

Deno.test("config — 저장·로드 왕복 + 0600", async () => {
  const home = tmpHome();
  const cfg = {
    api_base: "https://jetrag-api.woong-s.com",
    device_token: TOKEN,
    watch_dirs: ["/Users/x/Downloads"],
    backlog_days: 90,
  };
  await saveConfig(cfg, home);
  const loaded = await loadConfig(home);
  assertEquals(loaded, cfg);
  if (Deno.build.os !== "windows") {
    const st = await Deno.stat(configPath(home));
    assertEquals((st.mode ?? 0) & 0o777, 0o600);
  }
});

Deno.test("config — 파일이 없으면 안내가 담긴 오류", async () => {
  const home = tmpHome();
  let msg = "";
  try {
    await loadConfig(home);
  } catch (e) {
    msg = (e as Error).message;
  }
  assertEquals(msg.includes("jetrag-agent init"), true);
});

Deno.test("config — 깨진 JSON 은 파싱 오류를 그대로 숨기지 않는다", async () => {
  const home = tmpHome();
  await Deno.writeTextFile(configPath(home), "{ not json");
  let msg = "";
  try {
    await loadConfig(home);
  } catch (e) {
    msg = (e as Error).message;
  }
  assertEquals(msg.includes("config.json"), true);
});

Deno.test("config — JETRAG_AGENT_HOME 이 홈을 덮어쓴다", () => {
  const env = new Map([["JETRAG_AGENT_HOME", "/tmp/jr"], ["HOME", "/Users/x"]]);
  assertEquals(agentHome({ get: (k: string) => env.get(k) }), "/tmp/jr");
  // 덮어쓰기가 없으면 HOME 아래 .jetrag 로 떨어진다.
  assertEquals(
    agentHome({ get: (k: string) => (k === "HOME" ? "/Users/x" : undefined) }),
    "/Users/x/.jetrag",
  );
});

Deno.test("config — HOME·USERPROFILE 둘 다 없으면 던진다", () => {
  assertThrows(() => agentHome({ get: () => undefined }), Error, "홈 디렉터리");
});
