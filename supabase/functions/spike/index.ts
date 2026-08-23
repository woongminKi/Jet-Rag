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
 *   POST ?kind=<hwp|pdf|docx|...>       body = 파일 바이트, Task 0.2~0.5 에서 case 추가
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

      // Task 0.2 에서 "hwp" / "hwp-rhwp", 0.3 에서 "pdf",
      // 0.4 에서 "fernet", 0.5 에서 "docx" case 가 여기 추가된다.

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
      error: e instanceof Error ? `${e.name}: ${e.message}\n${e.stack ?? ""}` : String(e),
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
