/**
 * Phase 0 / S1 프로브 v3 — **WASM 경로만** 강제로 태워 Edge 이식 가능성을 판정한다.
 *
 * v1/v2 가 남긴 숙제: `@ohah/hwpjs` 를 그냥 import 하면 Deno 는 `node` export 조건을 골라
 * `dist/index.js`(NAPI 로더) → `hwpjs.darwin-arm64.node` 네이티브 바이너리를 쓴다.
 * Supabase Edge Functions 는 Linux + 네이티브 애드온 불가이므로 그 경로는 애초에 무효다.
 *
 * 패키지 구조 실측 (0.1.0-rc.10):
 *   exports["."].node    → dist/index.js        (NAPI 네이티브 로더)   ← Deno 기본 선택
 *   exports["."].browser → dist/browser.js      → `export * from '@ohah/hwpjs-wasm32-wasi'`
 *   optionalDependencies 에 @ohah/hwpjs-wasm32-wasi 존재 (순수 WASM 폴백)
 *
 * 즉 **탈출구는 있다.** 이 프로브는 그 탈출구를 직접 import 해서 세 가지를 분리 측정한다:
 *   (a) import 자체가 되는가            — emnapi 부트스트랩(SharedArrayBuffer / fetch(file:) / Worker)
 *   (b) toJson/toHtml 이 실제로 도는가  — 네이티브 없이 파싱되는가
 *   (c) 출력이 네이티브 경로와 같은가   — 품질 회귀 없는지 (chars 비교)
 *
 * 로컬 Deno 에서 (a)가 실패하면 Edge 에서도 100% 실패다 (Edge 는 더 제한적). 배포가 필요 없다.
 * (a)가 통과해도 Edge 통과는 보장되지 않는다 — Edge 는 Worker 생성·SAB 정책이 다르므로
 * 최종 판정은 여전히 배포 후 `?kind=hwp` 다.
 *
 * 사용:
 *   deno run -A scripts/spike_wasm_probe3.ts [hwp파일경로]
 */

const SAMPLE = Deno.args[0] ??
  new URL("../assets/public/law_sample1.hwp", import.meta.url).pathname;

function errText(e: unknown): string {
  return e instanceof Error ? `${e.name}: ${e.message}` : String(e);
}

const bytes = await Deno.readFile(SAMPLE);
console.error(`샘플: ${SAMPLE} (${bytes.byteLength} bytes)\n`);

// 부트스트랩 환경 사실 확인 — 실패 시 원인 특정에 쓴다.
const env = {
  denoVersion: Deno.version.deno,
  hasSharedArrayBuffer: typeof SharedArrayBuffer !== "undefined",
  hasWorker: typeof Worker !== "undefined",
  canSharedWasmMemory: (() => {
    try {
      new WebAssembly.Memory({ initial: 1, maximum: 2, shared: true });
      return true;
    } catch (e) {
      return errText(e);
    }
  })(),
};
console.error("환경:", JSON.stringify(env));

interface Out {
  env: typeof env;
  importOk: boolean;
  importMs: number | null;
  importError: string | null;
  exports: string[];
  calls: { name: string; ok: boolean; chars: number | null; ms: number | null; error: string | null }[];
}

const out: Out = {
  env,
  importOk: false,
  importMs: null,
  importError: null,
  exports: [],
  calls: [],
};

try {
  const t0 = performance.now();
  // 네이티브 우회 — WASM 서브패키지를 직접 지목한다(버전은 scripts/deno.json 에 고정).
  const wasm = await import("@ohah/hwpjs-wasm32-wasi") as Record<string, unknown>;
  out.importMs = performance.now() - t0;
  out.importOk = true;
  out.exports = Object.keys(wasm);
  console.error(`\nimport OK (${out.importMs.toFixed(1)}ms) exports: ${out.exports.join(", ")}`);

  for (const name of ["toJson", "toHtml", "toMarkdown"]) {
    const fn = wasm[name];
    if (typeof fn !== "function") {
      out.calls.push({ name, ok: false, chars: null, ms: null, error: "export 없음" });
      continue;
    }
    try {
      const t1 = performance.now();
      const r = (fn as (b: Uint8Array) => unknown)(bytes);
      const ms = performance.now() - t1;
      const text = typeof r === "string" ? r : JSON.stringify(r);
      out.calls.push({ name, ok: true, chars: text.length, ms, error: null });
      console.error(`OK   ${name.padEnd(12)} chars=${text.length} ms=${ms.toFixed(1)}`);
    } catch (e) {
      out.calls.push({ name, ok: false, chars: null, ms: null, error: errText(e) });
      console.error(`FAIL ${name.padEnd(12)} ${errText(e)}`);
    }
  }
} catch (e) {
  out.importError = errText(e);
  console.error(`\nimport FAIL: ${out.importError}`);
}

console.log(JSON.stringify(out, null, 2));
