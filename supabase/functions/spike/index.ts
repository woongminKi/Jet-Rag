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
 *   GET  ?kind=pdf-import               mupdf 로드만 시도 (S2 의 진짜 관문)
 *   GET  ?kind=pdf-unpdf                pdfjs 계열 대안 후보 로드 시도
 *   POST ?kind=pdf&page=N[&full=1]      body = PDF 바이트, structured text 원본(asJSON) 덤프
 *   POST ?kind=pdf-walk&page=N          walk() 콜백 인자 원본 덤프 (추출기 작성 전 확인용)
 *   POST ?kind=pdf-dict&page=N          **S2 본 판정** — PyMuPDF get_text("dict") 호환 dict
 *   POST ?kind=pdf-pages&from=&count=   페이지 단위 CPU 추이 (573p 는 CPU 2s 초과로 546)
 *   POST ?kind=pdf-render&page=&dpi=    **vision 용 래스터화 CPU** (Phase 0 미측정 구간)
 *   POST ?kind=fernet                   body = {key, token, plaintext} — S3 판정
 *   POST ?kind=<docx|pptx>              body = 파일 바이트 — S4 판정
 *   POST ?kind=<hwpx|hwpml>             body = 파일 바이트 — HWPX/HWPML 판정
 *   POST ?kind=mem&mb=N[&parse=…]       할당 사다리로 메모리 상한 실측 — S5 판정
 *
 * 응답의 cpuMs 가 2000 에 근접하면 그 작업 단위는 더 잘게 쪼개야 한다는 신호다.
 */

import { pageArea, STEXT_OPTS, toPageDict } from "../_shared/pdf_dict.ts";
import { decryptFernet, encryptFernet } from "../_shared/fernet.ts";
import { extractDocx, extractPptx } from "../_shared/ooxml_text.ts";
import { extractHwpml, extractHwpx } from "../_shared/hwp_xml_text.ts";

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

/**
 * S2 후보 — `mupdf`(mupdf.js). Emscripten WASM 이라 emnapi 계열과 부트스트랩이 다르다.
 * S1 에서 Edge 를 죽인 `WebAssembly.Memory({shared:true})`·Worker 를 요구하지 않는지가 관문이다.
 * 기준선이 PyMuPDF 1.27.2(MuPDF 1.27.2)라 엔진이 사실상 같다 — 그래서 1순위다.
 */
async function loadMupdf(): Promise<{ mod: Record<string, unknown>; importMs: number }> {
  const t0 = performance.now();
  const mod = await import("mupdf") as unknown as Record<string, unknown>;
  return { mod, importMs: performance.now() - t0 };
}

/** 대안 후보 — pdfjs 서버리스 빌드(순수 JS). WASM 이 아예 없어 로드 위험이 가장 낮다. */
async function loadUnpdf(): Promise<{ mod: Record<string, unknown>; importMs: number }> {
  const t0 = performance.now();
  const mod = await import("unpdf") as unknown as Record<string, unknown>;
  return { mod, importMs: performance.now() - t0 };
}

/** 객체가 실제로 제공하는 메서드명. "있을 것"이라 가정하지 않고 실측해서 응답에 싣는다. */
function methodsOf(o: unknown): string[] {
  if (o === null || o === undefined) return [];
  const proto = Object.getPrototypeOf(o);
  return proto ? Object.getOwnPropertyNames(proto) : [];
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

      /**
       * S2 의 관문 — 파일 없이 GET 으로 부른다.
       * 여기서 죽으면 mupdf 는 Edge 에서 쓸 수 없고, span/bbox 품질은 볼 필요도 없다.
       */
      /**
       * **미측정으로 남아 있던 구간** — vision 용 페이지 래스터화.
       *
       * Phase 0 S2 는 텍스트+span 추출만 쟀다. 그런데 현행은 vision 대상 페이지를
       * `page.get_pixmap(dpi=150)` → `pix.tobytes("png")` 로 **이미지로 굽는다**
       * (`extract.py:440`, `:685`). 래스터화는 CPU 집약적이라 2s 예산의 실제 소비자가
       * 여기일 수 있는데 재 본 적이 없다.
       *
       * API 를 짐작하지 않는다 — `methodsOf` 로 실제 메서드를 응답에 실어 보내고,
       * 없으면 그 사실이 그대로 드러나게 한다.
       *
       *   POST ?kind=pdf-render&page=0&dpi=150
       */
      case "pdf-render": {
        const pageIdx = Number(url.searchParams.get("page") ?? "0");
        const dpi = Number(url.searchParams.get("dpi") ?? "150");
        const { mod, importMs } = await loadMupdf();

        const { cpuMs, value } = measure(() => {
          const M = mod as Record<string, unknown> & {
            Document: { openDocument(b: Uint8Array, mime: string): unknown };
          };
          const doc = M.Document.openDocument(bytes, "application/pdf") as {
            countPages(): number;
            loadPage(n: number): Record<string, unknown>;
          };
          const pageCount = doc.countPages();
          const page = doc.loadPage(pageIdx);

          // 원본과 같은 배율. PDF 기본 해상도가 72dpi 라 zoom = dpi/72 다.
          const zoom = dpi / 72;
          const Matrix = M.Matrix as { scale(x: number, y: number): unknown } | undefined;
          const ColorSpace = M.ColorSpace as { DeviceRGB: unknown } | undefined;

          const api = {
            pageMethods: methodsOf(page).filter((n) => /pixmap|png|render|bound/i.test(n)),
            hasMatrix: typeof Matrix?.scale === "function",
            hasColorSpace: ColorSpace?.DeviceRGB !== undefined,
          };
          if (!api.hasMatrix || !api.hasColorSpace || typeof page.toPixmap !== "function") {
            // 못 하겠으면 **못 한다고 말한다.** 추측으로 다른 경로를 시도하지 않는다.
            return { pageCount, importMs, dpi, api, rendered: false };
          }

          const tRender = performance.now();
          const pix = (page.toPixmap as (
            m: unknown, cs: unknown, alpha: boolean, extras: boolean,
          ) => Record<string, unknown>)(
            Matrix!.scale(zoom, zoom), ColorSpace!.DeviceRGB, false, true,
          );
          const renderMs = performance.now() - tRender;

          const pixMethods = methodsOf(pix).filter((n) => /png|buffer|width|height/i.test(n));
          let pngMs: number | null = null;
          let pngBytes: number | null = null;
          if (typeof pix.asPNG === "function") {
            const tPng = performance.now();
            const png = (pix.asPNG as () => Uint8Array)();
            pngMs = performance.now() - tPng;
            pngBytes = png?.length ?? null;
          }
          const w = typeof pix.getWidth === "function" ? (pix.getWidth as () => number)() : null;
          const h = typeof pix.getHeight === "function" ? (pix.getHeight as () => number)() : null;

          return {
            pageCount, importMs, dpi, api, pixMethods,
            rendered: true, renderMs, pngMs, pngBytes, width: w, height: h,
          };
        });
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      case "pdf-import": {
        const { mod, importMs } = await loadMupdf();
        const { cpuMs, value } = measure(() => ({
          importMs,
          exports: Object.keys(mod).slice(0, 60),
          exportCount: Object.keys(mod).length,
          hasDocument: typeof mod.Document === "function",
          documentStatics: mod.Document ? Object.getOwnPropertyNames(mod.Document) : [],
        }));
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      /**
       * POST body = PDF 바이트. **1페이지의 structured text 원본을 그대로 덤프한다.**
       *
       * 추출기를 먼저 쓰지 않는 이유: mupdf 의 JSON 이 PyMuPDF 의 `get_text("dict")` 와
       * 같은 모양이라는 보장이 없다(특히 spans 배열의 유무). 상상해서 파서를 쓰면 틀린다 —
       * 원본을 1회 받아 눈으로 읽고 나서 매핑을 정한다.
       *
       *   ?kind=pdf&page=0&opts=preserve-spans,preserve-images&full=1
       */
      case "pdf": {
        const pageIdx = Number(url.searchParams.get("page") ?? "0");
        // mupdf 의 structured-text 옵션 문자열. 기본값은 span 보존 + 이미지 블록 유지.
        const opts = url.searchParams.get("opts") ?? "preserve-whitespace,preserve-spans,preserve-images";
        const wantFull = url.searchParams.get("full") === "1";
        const { mod, importMs } = await loadMupdf();

        const { cpuMs, value } = measure(() => {
          const M = mod as {
            Document: { openDocument(b: Uint8Array, mime: string): Record<string, unknown> };
          };
          const tOpen = performance.now();
          const doc = M.Document.openDocument(bytes, "application/pdf") as unknown as {
            countPages(): number;
            loadPage(n: number): Record<string, unknown>;
          };
          const pageCount = doc.countPages();
          const openMs = performance.now() - tOpen;

          const tPage = performance.now();
          const page = doc.loadPage(pageIdx);
          const loadMs = performance.now() - tPage;

          const bounds = typeof page.getBounds === "function"
            ? (page.getBounds as () => unknown)()
            : null;

          const tSt = performance.now();
          const st = (page.toStructuredText as (o: string) => Record<string, unknown>)(opts);
          const stMs = performance.now() - tSt;

          const tJson = performance.now();
          const raw = (st.asJSON as () => string)();
          const jsonMs = performance.now() - tJson;

          return {
            importMs,
            openMs,
            loadMs,
            stMs,
            jsonMs,
            pageCount,
            bounds,
            pageMethods: methodsOf(page).slice(0, 40),
            stMethods: methodsOf(st),
            jsonChars: raw.length,
            // 판정에 필요한 건 "무엇이 들어있나"다. full=1 이면 전문, 아니면 앞머리만.
            json: wantFull ? raw : raw.slice(0, 3000),
          };
        });
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      /**
       * 페이지 단위 팬아웃 아키텍처의 실제 단가 — N 페이지를 연속 처리하며 페이지당 CPU 를 잰다.
       * 판정 포인트는 "합계"가 아니라 **페이지 1건 최댓값 < 2s** 와 메모리 증가 추이다.
       *
       *   ?kind=pdf-pages&from=0&count=20
       */
      case "pdf-pages": {
        const from = Number(url.searchParams.get("from") ?? "0");
        const count = Number(url.searchParams.get("count") ?? "10");
        const opts = url.searchParams.get("opts") ?? STEXT_OPTS;
        const { mod, importMs } = await loadMupdf();

        const { cpuMs, value } = measure(() => {
          const M = mod as {
            Document: { openDocument(b: Uint8Array, mime: string): Record<string, unknown> };
          };
          const doc = M.Document.openDocument(bytes, "application/pdf") as unknown as {
            countPages(): number;
            loadPage(n: number): Record<string, unknown>;
          };
          const pageCount = doc.countPages();
          const rss = () => {
            try {
              const mu = (globalThis as { Deno?: { memoryUsage?: () => { rss: number } } }).Deno?.memoryUsage;
              return mu ? Math.round(mu().rss / 1e6) : null;
            } catch {
              return null;
            }
          };
          const rssStart = rss();
          const per: { page: number; ms: number; chars: number }[] = [];
          const end = Math.min(from + count, pageCount);
          for (let i = from; i < end; i++) {
            const t = performance.now();
            const page = doc.loadPage(i);
            // 운영에서 실제로 돌 경로 그대로 잰다 — walk + toPageDict. asJSON 으로 재면 숫자가 의미 없다.
            const st = (page.toStructuredText as (
              o: string,
            ) => { walk(w: Record<string, unknown>): void; destroy?: () => void }).call(page, opts);
            const dict = toPageDict(st, (page.getBounds as () => number[])());
            let chars = 0;
            for (const b of dict.blocks) {
              for (const l of b.lines ?? []) for (const s of l.spans) chars += s.text.length;
            }
            per.push({ page: i, ms: Math.round((performance.now() - t) * 10) / 10, chars });
            // 페이지 객체를 즉시 놓아준다 — 이게 없으면 573p 문서에서 메모리가 선형 증가한다.
            if (typeof st.destroy === "function") (st.destroy as () => void)();
            if (typeof page.destroy === "function") (page.destroy as () => void)();
          }
          const msList = per.map((p) => p.ms);
          return {
            importMs,
            pageCount,
            pagesMeasured: per.length,
            maxPageMs: msList.length ? Math.max(...msList) : null,
            avgPageMs: msList.length ? Math.round((msList.reduce((a, b) => a + b, 0) / msList.length) * 10) / 10 : null,
            rssStartMB: rssStart,
            rssEndMB: rss(),
            per,
          };
        });
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      /** mupdf 가 Edge 에서 죽었을 때만 의미가 있는 대안 후보. 로드 가능성만 먼저 본다. */
      case "pdf-unpdf": {
        const { mod, importMs } = await loadUnpdf();
        const { cpuMs, value } = measure(() => ({
          importMs,
          exports: Object.keys(mod),
        }));
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      /**
       * S2 의 본 판정 — Edge 에서 **PyMuPDF `get_text("dict")` 호환 dict** 를 만들어 그대로 돌려준다.
       * 이 응답을 `api/scripts/spike_pdf_compare.py` 로 기준선과 대조하면 이관 가능 여부가
       * "텍스트가 비슷하다"가 아니라 **필드 단위 일치**로 판정된다.
       *
       *   ?kind=pdf-dict&page=39
       */
      case "pdf-dict": {
        const pageIdx = Number(url.searchParams.get("page") ?? "0");
        const { mod, importMs } = await loadMupdf();

        const { cpuMs, value } = measure(() => {
          const M = mod as {
            Document: { openDocument(b: Uint8Array, mime: string): Record<string, unknown> };
          };
          const doc = M.Document.openDocument(bytes, "application/pdf") as unknown as {
            countPages(): number;
            loadPage(n: number): Record<string, unknown>;
          };
          const pageCount = doc.countPages();
          const page = doc.loadPage(pageIdx);
          const bounds = (page.getBounds as () => number[])();
          // 메서드를 변수에 담아 호출하면 `this` 가 끊겨 mupdf 내부에서
          // `Cannot read properties of undefined (reading 'pointer')` 가 난다. 반드시 수신자를 붙여 호출한다.
          const st = (page.toStructuredText as (o: string) => { walk(w: Record<string, unknown>): void })
            .call(page, STEXT_OPTS);
          const dict = toPageDict(st, bounds);

          let spanCount = 0;
          let lineCount = 0;
          let textBlocks = 0;
          let imageBlocks = 0;
          for (const b of dict.blocks) {
            if (b.type === 1) {
              imageBlocks++;
              continue;
            }
            textBlocks++;
            for (const l of b.lines ?? []) {
              lineCount++;
              spanCount += l.spans.length;
            }
          }
          return {
            importMs,
            pageCount,
            pageArea: pageArea(dict),
            blockCount: dict.blocks.length,
            textBlocks,
            imageBlocks,
            lineCount,
            spanCount,
            dict,
          };
        });
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      /**
       * `StructuredText.walk()` 의 **콜백 인자 원본**을 그대로 덤프한다.
       *
       * asJSON 경로는 두 가지가 막혔다(2026-09-04 실측):
       *   1. bbox 가 정수로 반올림된다 (기준선 대비 최대 1.93pt 편차)
       *   2. `preserve-spans` 를 켜면 **블록 분할 자체가 달라진다**
       *      (sample-report p0: 7블록/5텍스트 → 6블록/4텍스트). 두 호출을 인덱스로 짝지을 수 없다.
       * walk 가 char 단위 (font, size, quad) 를 주면 한 번의 순회로 PyMuPDF 의 span 정의
       * (= 같은 font·size 의 연속 run) 를 그대로 복원할 수 있다. 인자 모양을 확인하는 게 먼저다.
       */
      case "pdf-walk": {
        const pageIdx = Number(url.searchParams.get("page") ?? "0");
        const limit = Number(url.searchParams.get("limit") ?? "12");
        const { mod, importMs } = await loadMupdf();

        const { cpuMs, value } = measure(() => {
          const M = mod as {
            Document: { openDocument(b: Uint8Array, mime: string): Record<string, unknown> };
          };
          const doc = M.Document.openDocument(bytes, "application/pdf") as unknown as {
            loadPage(n: number): Record<string, unknown>;
          };
          const page = doc.loadPage(pageIdx);
          const st = (page.toStructuredText as (o: string) => Record<string, unknown>)
            .call(page, STEXT_OPTS);

          const events: unknown[] = [];
          let charCount = 0;
          const rec = (name: string, args: unknown[]) => {
            if (events.length < limit) {
              events.push({ name, args: args.map((a) => (typeof a === "object" && a !== null ? { ...a } : a)) });
            }
          };
          // 어떤 콜백이 실제로 불리는지 모른다 → 후보를 전부 등록하고 호출된 것만 센다.
          const called: Record<string, number> = {};
          const names = [
            "beginTextBlock",
            "endTextBlock",
            "beginLine",
            "endLine",
            "beginStruct",
            "endStruct",
            "onChar",
            "onImageBlock",
            "onVector",
          ];
          const walker: Record<string, unknown> = {};
          for (const n of names) {
            walker[n] = (...args: unknown[]) => {
              called[n] = (called[n] ?? 0) + 1;
              if (n === "onChar") charCount++;
              rec(n, args);
            };
          }
          (st.walk as (w: unknown) => void).call(st, walker);
          return { importMs, called, charCount, events };
        });
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      /**
       * S3 — Fernet 호환. **기존 암호문을 읽을 수 있는가**가 판정 대상이다.
       * 세 가지를 한 번에 확인한다: ① 기존 토큰 복호 ② Deno 가 만든 토큰(Python 이 읽을 것)
       * ③ 변조 토큰 거부. ③ 이 빠지면 "복호는 되는데 위조도 통과"인 구현을 PASS 로 오판한다.
       *
       *   POST ?kind=fernet  body = {"key": "...", "token": "...", "plaintext": "..."}
       * 운영 키·운영 SID 는 절대 넣지 않는다 — 호출 측이 일회용 키를 생성해서 보낸다.
       */
      case "fernet": {
        const body = JSON.parse(new TextDecoder().decode(bytes)) as {
          key: string;
          token: string;
          plaintext?: string;
        };

        const t0 = performance.now();
        const dec = await decryptFernet(body.key, body.token);
        const decryptMs = performance.now() - t0;

        const t1 = performance.now();
        const reEncoded = await encryptFernet(body.key, dec.plaintext);
        const encryptMs = performance.now() - t1;

        // 변조 검출 — 마지막 바이트(HMAC 끝)를 뒤집으면 반드시 거부돼야 한다.
        let tamperRejected: string | boolean = false;
        try {
          const raw = body.token.replace(/-/g, "+").replace(/_/g, "/");
          const bin = atob(raw + "=".repeat((4 - (raw.length % 4)) % 4));
          const arr = Uint8Array.from(bin, (c) => c.charCodeAt(0));
          arr[arr.length - 1] ^= 0x01;
          let s = "";
          for (const b of arr) s += String.fromCharCode(b);
          const tampered = btoa(s).replace(/\+/g, "-").replace(/\//g, "_");
          await decryptFernet(body.key, tampered);
          tamperRejected = false; // 여기 닿으면 위조 토큰을 받아들인 것 = FAIL
        } catch (e) {
          tamperRejected = e instanceof Error ? e.message : String(e);
        }

        const { cpuMs, value } = measure(() => ({
          decryptMs,
          encryptMs,
          plaintext: dec.plaintext,
          timestamp: dec.timestamp,
          matchesExpected: body.plaintext === undefined ? null : dec.plaintext === body.plaintext,
          reEncoded,
          tamperRejected,
        }));
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      /**
       * S4 — DOCX / PPTX. POST body = 파일 바이트.
       * 응답에 섹션 전문을 실어 기준선(`spike_ooxml_baseline.json`)과 직접 대조한다.
       */
      case "docx":
      case "pptx":
      case "hwpx":
      case "hwpml": {
        const { cpuMs, value } = measure(() => {
          const out = kind === "docx"
            ? extractDocx(bytes)
            : kind === "pptx"
            ? extractPptx(bytes)
            : kind === "hwpx"
            ? extractHwpx(bytes)
            : extractHwpml(bytes);
          const joined = out.sections.map((s) => s.text).join("\n");
          return {
            sourceType: out.sourceType,
            sectionCount: out.sections.length,
            // Python `len()` 은 코드포인트, JS `.length` 는 UTF-16 코드유닛이다.
            // 그대로 두면 이모지 1개당 1씩 어긋나 텍스트가 같은데도 달라 보인다
            // (승인글 템플릿1: 📌 15개 → 54,086 vs 54,101). 코드포인트로 맞춘다.
            chars: [...joined].length,
            titles: [...new Set(out.sections.map((s) => s.sectionTitle).filter(Boolean))].sort(),
            warnings: out.warnings,
            sections: out.sections,
          };
        });
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

      /**
       * S5 — 메모리. **직접 계측이 막혀 있다**: Edge 의 `Deno.memoryUsage()` 는 0 을 돌려준다
       * (2026-09-04 실측 — `pdf-pages` 의 rssStartMB/rssEndMB 가 전부 0).
       *
       * 그래서 "얼마나 쓰는가" 대신 **"얼마나 더 쓸 수 있는가"** 를 잰다. 할당 사다리로 죽는
       * 지점을 찾으면 그게 상한이고, **파싱 전후 상한의 차이**가 파서가 붙잡고 있는 양이다.
       * 없는 계기를 두 번의 임계 측정의 차이로 대체하는 것이다.
       *
       * 할당만 하면 안 된다 — 페이지를 건드리지 않으면 실제로 커밋되지 않아 상한을 못 만나고
       * 그냥 통과한다. 4KB 마다 1바이트씩 쓴다.
       *
       *   POST ?kind=mem&mb=N[&parse=pdf|hwpx|docx]   body = (parse 지정 시) 파일 바이트
       */
      case "mem": {
        const mb = Number(url.searchParams.get("mb") ?? "64");
        const parse = url.searchParams.get("parse") ?? "none";
        const CHUNK_MB = 8;

        // 파싱 산출물을 **살려둔 채** 할당한다. 참조를 놓으면 GC 가 걷어가 차이가 0 이 된다.
        let held: unknown = null;
        let parseMs: number | null = null;
        if (parse !== "none") {
          const t = performance.now();
          if (parse === "pdf") {
            const { mod } = await loadMupdf();
            const M = mod as {
              Document: { openDocument(b: Uint8Array, mime: string): Record<string, unknown> };
            };
            const doc = M.Document.openDocument(bytes, "application/pdf") as unknown as {
              countPages(): number;
              loadPage(n: number): Record<string, unknown>;
            };
            const page = doc.loadPage(0);
            const st = (page.toStructuredText as (o: string) => { walk(w: Record<string, unknown>): void })
              .call(page, STEXT_OPTS);
            held = toPageDict(st, (page.getBounds as () => number[])());
          } else if (parse === "hwpx") {
            held = extractHwpx(bytes);
          } else if (parse === "docx") {
            held = extractDocx(bytes);
          }
          parseMs = performance.now() - t;
        }

        const { cpuMs, value } = measure(() => {
          const blocks: Uint8Array[] = [];
          let allocatedMB = 0;
          for (let i = 0; i < Math.ceil(mb / CHUNK_MB); i++) {
            const block = new Uint8Array(CHUNK_MB * 1024 * 1024);
            // 페이지를 실제로 커밋시킨다 — 안 건드리면 가상 할당으로 끝나 상한을 못 만난다.
            for (let off = 0; off < block.length; off += 4096) block[off] = 1;
            blocks.push(block);
            allocatedMB += CHUNK_MB;
          }
          // 최적화로 blocks 가 사라지지 않도록 값을 읽는다.
          let checksum = 0;
          for (const b of blocks) checksum += b[0];
          return {
            parse,
            parseMs,
            requestedMB: mb,
            allocatedMB,
            checksum,
            heldKind: held === null ? null : typeof held,
          };
        });
        return { ...base, cpuMs, wallMs: performance.now() - wallStart, error: null, result: value };
      }

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
