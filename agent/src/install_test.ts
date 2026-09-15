import { assertEquals } from "@std/assert";
import {
  LAUNCHD_LABEL,
  launchdPlist,
  launchdPlistPath,
  SCHTASKS_NAME,
  schtasksCreateArgs,
  schtasksDeleteArgs,
  xmlEscape,
} from "./install.ts";

Deno.test("install — plist 에 필요한 키가 다 있다", () => {
  const p = launchdPlist("/Applications/jetrag-agent", "/Users/x/.jetrag");
  assertEquals(p.includes(`<string>${LAUNCHD_LABEL}</string>`), true);
  assertEquals(p.includes("<string>/Applications/jetrag-agent</string>"), true);
  assertEquals(p.includes("<string>run</string>"), true);
  assertEquals(p.includes("<key>RunAtLoad</key>\n  <true/>"), true);
  assertEquals(p.includes("<key>KeepAlive</key>\n  <true/>"), true);
  assertEquals(p.includes("/Users/x/.jetrag/launchd.err.log"), true);
  // plist 는 XML 이다. 경로에 & 가 들어가면 launchd 가 파일 자체를 못 읽는다.
  assertEquals(launchdPlist("/Users/a&b/jetrag-agent", "/h").includes("/Users/a&amp;b/jetrag-agent"), true);
  assertEquals(xmlEscape(`<a href="x">&</a>`), "&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;");
});

Deno.test("install — plist 경로", () => {
  assertEquals(launchdPlistPath("/Users/x"), "/Users/x/Library/LaunchAgents/com.jetrag.agent.plist");
});

Deno.test("install — schtasks 인자", () => {
  assertEquals(schtasksCreateArgs("C:\\Users\\x\\jetrag-agent.exe"), [
    "/Create",
    "/TN",
    SCHTASKS_NAME,
    "/TR",
    '"C:\\Users\\x\\jetrag-agent.exe" run',
    "/SC",
    "ONLOGON",
    "/RL",
    "LIMITED",
    "/F",
  ]);
  assertEquals(schtasksDeleteArgs(), ["/Delete", "/TN", SCHTASKS_NAME, "/F"]);
});
