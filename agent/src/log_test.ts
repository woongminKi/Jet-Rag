import { assertEquals } from "@std/assert";
import { FileLogger, tailLines } from "./log.ts";

Deno.test("log — 5MB 넘으면 회전, .3 은 버린다", async () => {
  const home = await Deno.makeTempDir({ prefix: "jetrag-log-" });
  const path = `${home}/agent.log`;
  const logger = new FileLogger(path, { maxBytes: 200, keep: 3 });
  for (let i = 0; i < 60; i++) await logger.write("info", `줄 ${i} ${"x".repeat(20)}`);
  await logger.close();
  const names = [...Deno.readDirSync(home)].map((e) => e.name).sort();
  assertEquals(names.includes("agent.log"), true);
  assertEquals(names.includes("agent.log.1"), true);
  assertEquals(names.includes("agent.log.4"), false);
});

Deno.test("log — tailLines 는 마지막 N 줄", async () => {
  const home = await Deno.makeTempDir({ prefix: "jetrag-log-" });
  const path = `${home}/agent.log`;
  await Deno.writeTextFile(path, ["a", "b", "c", "d"].join("\n") + "\n");
  assertEquals(await tailLines(path, 2), ["c", "d"]);
  assertEquals(await tailLines(`${home}/없는파일.log`, 2), []);
});
