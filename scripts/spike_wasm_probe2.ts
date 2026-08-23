/**
 * Phase 0 / S1 프로브 v2 — 실제 export 이름으로 HWP 파싱을 시도한다.
 *
 * v1 에서 확인된 사실 (2026-08-24 실측):
 *   @ohah/hwpjs 0.1.0-rc.10 → exports: fileHeader, toJson, toHtml, toMarkdown
 *   @rhwp/core  0.8.4       → exports: HwpDocument, HwpViewer, DocumentExport,
 *                                      extractThumbnail, initSync, init_panic_hook
 *
 * v1 이 남긴 경고: @ohah/hwpjs 설치 시 `@ohah/hwpjs-darwin-arm64` 가 함께 초기화됐다.
 * 플랫폼별 **네이티브 바이너리**라면 Supabase Edge Functions(Linux + Deno, 네이티브
 * 애드온 불가)에서 동작하지 않는다. 그래서 이 프로브는 두 가지를 분리해서 본다:
 *   (a) 파싱이 되는가                  → 텍스트 품질 판정의 전제
 *   (b) 순수 WASM 경로가 있는가        → Edge 이식 가능성의 전제
 * (a)만 통과하고 (b)가 실패하면 그 후보는 S1 기준 FAIL 이다.
 *
 * 사용:
 *   deno run -A scripts/spike_wasm_probe2.ts <hwp파일경로>
 */

const SAMPLE = Deno.args[0] ??
  `${new URL("..", import.meta.url).pathname}assets/public/law_sample1.hwp`;

function errText(e: unknown): string {
  return e instanceof Error ? `${e.name}: ${e.message}` : String(e);
}

/** 반환값이 무엇이든 텍스트 길이를 재기 위한 정규화. */
function toText(out: unknown): string {
  if (typeof out === "string") return out;
  if (out && typeof out === "object") {
    const o = out as Record<string, unknown>;
    for (const k of ["markdown", "text", "html", "content"]) {
      if (typeof o[k] === "string") return o[k] as string;
    }
    return JSON.stringify(out);
  }
  return String(out ?? "");
}

interface Attempt {
  entry: string;
  ok: boolean;
  chars: number | null;
  ms: number | null;
  head: string | null;
  error: string | null;
}

async function attempt(
  entry: string,
  fn: () => unknown | Promise<unknown>,
): Promise<Attempt> {
  try {
    const t0 = performance.now();
    const out = await fn();
    const ms = performance.now() - t0;
    const text = toText(out);
    return {
      entry,
      ok: true,
      chars: text.length,
      ms,
      head: text.slice(0, 200).replace(/\s+/g, " "),
      error: null,
    };
  } catch (e) {
    return { entry, ok: false, chars: null, ms: null, head: null, error: errText(e) };
  }
}

const bytes = await Deno.readFile(SAMPLE);
console.error(`샘플: ${SAMPLE} (${bytes.byteLength} bytes)\n`);

const attempts: Attempt[] = [];

// ---------------- @ohah/hwpjs ----------------
try {
  const hwpjs = await import("@ohah/hwpjs") as Record<string, unknown>;
  for (const name of ["toMarkdown", "toJson", "toHtml"]) {
    const fn = hwpjs[name];
    if (typeof fn !== "function") continue;
    attempts.push(
      await attempt(`@ohah/hwpjs.${name}`, () => (fn as (b: Uint8Array) => unknown)(bytes)),
    );
  }
} catch (e) {
  attempts.push({
    entry: "@ohah/hwpjs (import)", ok: false, chars: null, ms: null, head: null,
    error: errText(e),
  });
}

// ---------------- @rhwp/core ----------------
try {
  const rhwp = await import("@rhwp/core") as Record<string, unknown>;

  // wasm-bindgen 초기화 — default export 가 init 함수인 경우가 많다.
  const init = rhwp.default;
  if (typeof init === "function") {
    try {
      await (init as () => Promise<unknown>)();
    } catch (e) {
      console.error(`  (rhwp init 경고: ${errText(e)})`);
    }
  }

  const HwpDocument = rhwp.HwpDocument;
  if (typeof HwpDocument === "function") {
    attempts.push(
      await attempt("@rhwp/core.HwpDocument(new)", () => {
        const doc = new (HwpDocument as new (b: Uint8Array) => unknown)(bytes);
        const d = doc as Record<string, unknown>;
        // 텍스트 추출 메서드를 찾아 호출.
        for (const m of ["toMarkdown", "toText", "text", "toJson", "toString"]) {
          if (typeof d[m] === "function") return (d[m] as () => unknown).call(doc);
        }
        return doc;
      }),
    );
    // 정적 팩토리 형태도 시도.
    const statics = HwpDocument as unknown as Record<string, unknown>;
    for (const m of ["parse", "fromBytes", "load"]) {
      if (typeof statics[m] === "function") {
        attempts.push(
          await attempt(`@rhwp/core.HwpDocument.${m}`,
            () => (statics[m] as (b: Uint8Array) => unknown)(bytes)),
        );
      }
    }
  }
} catch (e) {
  attempts.push({
    entry: "@rhwp/core (import)", ok: false, chars: null, ms: null, head: null,
    error: errText(e),
  });
}

for (const a of attempts) {
  console.error(
    `${a.ok ? "OK  " : "FAIL"} ${a.entry.padEnd(32)} chars=${a.chars ?? "-"} ms=${
      a.ms?.toFixed(1) ?? "-"
    }`,
  );
  if (a.error) console.error(`      ${a.error}`);
  if (a.head) console.error(`      head: ${a.head}`);
}

console.log(JSON.stringify(attempts, null, 2));
