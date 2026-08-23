/**
 * Phase 0 / S1 선행 프로브 — WASM HWP 파서가 Deno 에서 import·동작하는지 로컬 확인.
 *
 * 이 스크립트는 Edge Functions 의 CPU 2s 제한을 재지 않는다(로컬엔 제한이 없다).
 * 답하려는 질문은 그보다 앞선 것이다: **Deno 에서 되긴 하는가?**
 *   - Node 전용 API(fs/path/Buffer)에 의존하면 여기서 실패한다 → Edge 에서도 실패한다.
 *   - 여기서 통과하면 그때 배포해서 CPU 를 잰다(Task 0.2 본 검증).
 *
 * 사용:
 *   deno run -A scripts/spike_wasm_probe.ts
 *
 * 산출: 후보별 { import 성공 여부, export 목록, 파싱 결과 char 수, 소요 ms, 에러 전문 }
 */

// 샘플 경로는 인자로 받는다 — 격리 디렉토리(node_modules 오염 방지)에서 실행할 때
// 스크립트 위치 기준 상대 경로가 깨지기 때문. 인자 없으면 리포지토리 기준 기본값.
const HWP_SAMPLE = Deno.args[0] ??
  `${new URL("..", import.meta.url).pathname}assets/public/law_sample1.hwp`;

interface ProbeReport {
  candidate: string;
  imported: boolean;
  exports: string[];
  parsed: boolean;
  chars: number | null;
  elapsedMs: number | null;
  error: string | null;
}

function errText(e: unknown): string {
  if (e instanceof Error) return `${e.name}: ${e.message}`;
  return String(e);
}

/** 모듈에서 파싱 함수처럼 보이는 것을 찾아 호출을 시도한다. */
async function tryParse(
  mod: Record<string, unknown>,
  bytes: Uint8Array,
): Promise<{ chars: number; elapsedMs: number; via: string }> {
  const candidates = [
    "parse", "parseHwp", "read", "readHwp", "load", "Document", "HWPDocument", "default",
  ];
  const errors: string[] = [];

  for (const name of candidates) {
    const fn = mod[name];
    if (typeof fn !== "function") continue;
    try {
      const t0 = performance.now();
      // 생성자일 수도, 함수일 수도 있다 — 함수 호출 먼저, 실패 시 new.
      let out: unknown;
      try {
        out = await (fn as (b: Uint8Array) => unknown)(bytes);
      } catch {
        out = new (fn as new (b: Uint8Array) => unknown)(bytes);
      }
      const elapsedMs = performance.now() - t0;
      const text = typeof out === "string" ? out : JSON.stringify(out);
      return { chars: text?.length ?? 0, elapsedMs, via: name };
    } catch (e) {
      errors.push(`${name}: ${errText(e)}`);
    }
  }
  throw new Error(`호출 가능한 파싱 진입점 없음. 시도: ${errors.join(" | ")}`);
}

async function probe(specifier: string): Promise<ProbeReport> {
  const report: ProbeReport = {
    candidate: specifier,
    imported: false,
    exports: [],
    parsed: false,
    chars: null,
    elapsedMs: null,
    error: null,
  };

  let mod: Record<string, unknown>;
  try {
    mod = await import(specifier) as Record<string, unknown>;
    report.imported = true;
    report.exports = Object.keys(mod).sort();
  } catch (e) {
    report.error = `import 실패 — ${errText(e)}`;
    return report;
  }

  let bytes: Uint8Array;
  try {
    bytes = await Deno.readFile(HWP_SAMPLE);
  } catch (e) {
    report.error = `샘플 읽기 실패(${HWP_SAMPLE}) — ${errText(e)}`;
    return report;
  }

  try {
    const { chars, elapsedMs, via } = await tryParse(mod, bytes);
    report.parsed = true;
    report.chars = chars;
    report.elapsedMs = elapsedMs;
    report.exports = [`(사용한 진입점: ${via})`, ...report.exports];
  } catch (e) {
    report.error = `파싱 실패 — ${errText(e)}`;
  }
  return report;
}

const CANDIDATES = ["npm:@ohah/hwpjs", "npm:@rhwp/core"];

const reports: ProbeReport[] = [];
for (const c of CANDIDATES) {
  console.error(`\n▶ 프로브: ${c}`);
  const r = await probe(c);
  reports.push(r);
  console.error(
    `  import=${r.imported} parsed=${r.parsed} chars=${r.chars} ms=${
      r.elapsedMs?.toFixed(1) ?? "-"
    }`,
  );
  if (r.error) console.error(`  error: ${r.error}`);
  if (r.imported) console.error(`  exports: ${r.exports.slice(0, 12).join(", ")}`);
}

console.log(JSON.stringify(reports, null, 2));
