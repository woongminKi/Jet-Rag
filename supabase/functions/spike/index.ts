/**
 * Phase 0 타당성 스파이크 — CPU 계측 하네스.
 *
 * 목적: Edge Functions 의 "요청당 CPU 2s" 제한 아래에서 WASM 파서가 실제로 동작하는지를
 * 실측한다. 로컬 `supabase functions serve` 는 CPU 제한을 적용하지 않으므로 이 하네스는
 * 반드시 **배포된 함수**로 호출해서 재야 의미가 있다.
 *
 * 계측 방식:
 * - performance.now() 는 wall clock 이다. Supabase 가 재는 CPU time 과 다르다.
 *   따라서 계측 구간에 async I/O(fetch/await)를 절대 넣지 않는다 — 동기 파싱 블록만 감싼다.
 *   그래야 wall clock ≈ CPU time 이 되어 2s 제한과 직접 비교할 수 있다.
 * - 요청 body 읽기(await req.arrayBuffer())는 I/O 이므로 계측 시작 전에 끝낸다.
 *
 * 사용:
 *   GET  ?kind=noop                     하네스 자체 동작 확인
 *   GET  ?kind=env                      런타임 능력 조사 (SAB / Worker / shared wasm memory)
 *   GET  ?kind=hwp-import               HWP WASM 모듈 로드만 시도 (S1 의 진짜 관문)
 *   POST ?kind=<hwp|hwp-rhwp>           body = HWP 바이트, 실제 파싱 + CPU 계측
 *
 * 응답의 cpuMs 가 2000 에 근접하면 그 작업 단위는 더 잘게 쪼개야 한다는 신호다.
 */

interface SpikeResult {
  kind: string;
  cpuMs: number | null;
  wallMs: number;
  bytesIn: number;
  error: string | null;
  result: unknown;
}

function errText(e: unknown): string {
  return e instanceof Error ? `${e.name}: ${e.message}\n${e.stack ?? ""}` : String(e);
}

/**
 * HWP WASM 후보 로드.
 *
 * `@ohah/hwpjs` 를 그냥 import 하면 안 된다 — Deno 는 `node` export 조건을 골라
 * `dist/index.js`(NAPI 로더) → `hwpjs.linux-x64-gnu.node` 네이티브 애드온을 찾는다.
 * Edge Functions 는 네이티브 애드온을 못 쓰므로 **wasm32-wasi 서브패키지를 직접 지목**한다.
 * (로컬 Deno 2.8 실측: 순수 WASM 경로로 toJson 238,962자 / 15ms / RSS +35MB — 네이티브와 동일 출력)
 *
 * 이 경로가 Edge 에서 살아남으려면 emnapi 부트스트랩이 요구하는 3가지가 필요하다:
 *   1. SharedArrayBuffer + shared WebAssembly.Memory(initial 4000페이지 = 250MB **예약**)
 *   2. fetch(file:) 로 번들 내 .wasm 읽기
 *   3. Worker 생성 (비동기 작업 풀 — 동기 호출만 쓰면 안 탈 수도 있다)
 * 셋 중 하나라도 막히면 여기서 예외가 난다. 그 예외 전문이 곧 S1 판정 근거다.
 */
async function loadHwpWasm(): Promise<{ mod: Record<string, unknown>; importMs: number }> {
  const t0 = performance.now();
  const mod = await import("@ohah/hwpjs-wasm32-wasi") as Record<string, unknown>;
  return { mod, importMs: performance.now() - t0 };
}

/** 대안 후보 — wasm-bindgen 계열이라 SAB/Worker 를 요구하지 않을 가능성이 있다. */
async function loadRhwp(): Promise<{ mod: Record<string, unknown>; importMs: number }> {
  const t0 = performance.now();
  const mod = await import("@rhwp/core") as Record<string, unknown>;
  const init = mod.default;
  if (typeof init === "function") {
    try {
      await (init as () => Promise<unknown>)();
    } catch { /* init 없이도 동작하는 빌드가 있어 실패를 삼킨다 */ }
  }
  return { mod, importMs: performance.now() - t0 };
}

/** 동기 블록만 감싸 CPU 시간을 근사한다. async 함수를 넘기지 말 것. */
function measure<T>(fn: () => T): { cpuMs: number; value: T } {
  const t0 = performance.now();
  const value = fn();
  return { cpuMs: performance.now() - t0, value };
}

async function handle(req: Request): Promise<SpikeResult> {
  const url = new URL(req.url);
  const kind = url.searchParams.get("kind") ?? "noop";
  const wallStart = performance.now();

  // body 는 계측 밖에서 미리 읽는다 (네트워크 I/O 를 CPU 로 오계상하지 않도록).
  const bytes = req.method === "POST"
    ? new Uint8Array(await req.arrayBuffer())
    : new Uint8Array(0);

  const base = { kind, bytesIn: bytes.byteLength };

  try {
    switch (kind) {
      case "noop": {
        const { cpuMs, value } = measure(() => ({ ok: true }));
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      /**
       * 하네스 자체의 계측 정확도 확인용 — 의도적으로 CPU 를 태운다.
       * ?kind=burn&ms=500 → cpuMs 가 500 근처로 나와야 measure() 를 신뢰할 수 있다.
       */
      case "burn": {
        const target = Number(url.searchParams.get("ms") ?? "100");
        const { cpuMs, value } = measure(() => {
          const end = performance.now() + target;
          let n = 0;
          while (performance.now() < end) n++;
          return { iterations: n, targetMs: target };
        });
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      /** 런타임 능력 조사 — hwp 케이스가 실패했을 때 "무엇 때문인지"를 가르는 대조군. */
      case "env": {
        const probe = (fn: () => unknown) => {
          try {
            return fn();
          } catch (e) {
            return errText(e).split("\n")[0];
          }
        };
        const { cpuMs, value } = measure(() => ({
          denoVersion: (globalThis as { Deno?: { version?: { deno?: string } } }).Deno?.version?.deno ?? null,
          hasSharedArrayBuffer: typeof SharedArrayBuffer !== "undefined",
          hasWorker: typeof Worker !== "undefined",
          // emnapi 가 실제로 요구하는 크기(4000페이지 = 250MB 예약)를 그대로 시험한다.
          sharedMemory250MB: probe(() => {
            new WebAssembly.Memory({ initial: 4000, maximum: 65536, shared: true });
            return true;
          }),
          sharedMemory1Page: probe(() => {
            new WebAssembly.Memory({ initial: 1, maximum: 2, shared: true });
            return true;
          }),
        }));
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      /**
       * S1 의 진짜 관문 — 파일 없이 GET 으로 부를 수 있다.
       * 여기서 실패하면 `@ohah/hwpjs` 는 Edge 에서 쓸 수 없고, 품질 수치는 볼 필요도 없다.
       */
      case "hwp-import": {
        const { mod, importMs } = await loadHwpWasm();
        const { cpuMs, value } = measure(() => ({
          importMs,
          exports: Object.keys(mod),
          hasToJson: typeof mod.toJson === "function",
        }));
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      /** POST body = HWP 바이트. import 는 계측 밖(비동기), 파싱만 measure 로 감싼다. */
      case "hwp": {
        const { mod, importMs } = await loadHwpWasm();
        const toJson = mod.toJson as (b: Uint8Array) => unknown;
        const toHtml = mod.toHtml as (b: Uint8Array) => unknown;
        const { cpuMs, value } = measure(() => {
          const j = toJson(bytes);
          const h = toHtml(bytes);
          const js = typeof j === "string" ? j : JSON.stringify(j);
          const hs = typeof h === "string" ? h : JSON.stringify(h);
          // 본문 전체를 응답에 실으면 수백 KB 다. 판정에 필요한 건 길이와 앞머리뿐.
          return {
            importMs,
            jsonChars: js.length,
            htmlChars: hs.length,
            jsonHead: js.slice(0, 300),
          };
        });
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      /** 대안 후보 판정. hwp 가 죽었을 때만 의미가 있다. */
      case "hwp-rhwp": {
        const { mod, importMs } = await loadRhwp();
        const { cpuMs, value } = measure(() => {
          const HwpDocument = mod.HwpDocument as (new (b: Uint8Array) => unknown) | undefined;
          if (typeof HwpDocument !== "function") {
            return { importMs, exports: Object.keys(mod), note: "HwpDocument 없음" };
          }
          const doc = new HwpDocument(bytes) as Record<string, unknown>;
          const methods = Object.getOwnPropertyNames(Object.getPrototypeOf(doc));
          return { importMs, exports: Object.keys(mod), methods };
        });
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      /**
       * `@rhwp/core` 의 메서드를 이름으로 호출한다.
       *
       * 이 패키지는 파서가 아니라 **에디터 엔진**이라 메서드가 411개고, 어느 것이 문서 전체
       * 평문을 주는지 문서화돼 있지 않다. 후보(getTextFileText / getTextFileUnicode /
       * getPageText / exportHml …)를 하나씩 시험해야 하는데, 매번 재배포하면 왕복이 길다.
       * 그래서 메서드명을 쿼리로 받는다 — 스파이크 한정이며 Phase 1 로 넘기지 않는다.
       *
       * POST body = HWP 바이트
       *   ?kind=rhwp-call&method=getTextFileText[&args=[0]]
       */
      case "rhwp-call": {
        const method = url.searchParams.get("method") ?? "getTextFileText";
        const rawArgs = url.searchParams.get("args");
        const callArgs = rawArgs ? JSON.parse(rawArgs) as unknown[] : [];
        const { mod, importMs } = await loadRhwp();
        const HwpDocument = mod.HwpDocument as new (b: Uint8Array) => Record<string, unknown>;

        const ctor = measure(() => new HwpDocument(bytes));
        const doc = ctor.value;
        const fn = doc[method];
        if (typeof fn !== "function") {
          return {
            ...base,
            cpuMs: ctor.cpuMs,
            wallMs: performance.now() - wallStart,
            error: `메서드 없음: ${method}`,
            result: null,
          };
        }

        const { cpuMs, value } = measure(() => {
          const out = (fn as (...a: unknown[]) => unknown).apply(doc, callArgs);
          const text = typeof out === "string" ? out : JSON.stringify(out);
          return {
            importMs,
            ctorCpuMs: ctor.cpuMs,
            method,
            chars: text.length,
            // 판정용 원문. 기준선 샘플이 1KB 대라 이 상한이면 전문이 다 온다.
            text: text.slice(0, 120_000),
          };
        });
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      // 0.3 에서 "pdf", 0.4 에서 "fernet", 0.5 에서 "docx" case 가 여기 추가된다.

      default:
        return {
          ...base,
          cpuMs: null,
          wallMs: performance.now() - wallStart,
          error: `unknown kind: ${kind}`,
          result: null,
        };
    }
  } catch (e) {
    // 스파이크에서는 실패도 유효한 결과다 — 에러 전문을 그대로 보존해 판정표에 옮긴다.
    return {
      ...base,
      cpuMs: null,
      wallMs: performance.now() - wallStart,
      error: errText(e),
      result: null,
    };
  }
}

Deno.serve(async (req: Request) => {
  const out = await handle(req);
  return new Response(JSON.stringify(out, null, 2), {
    status: out.error ? 500 : 200,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
});
