/**
 * HWP 5.x 추출 — `adapters/impl/hwp_parser.py` 의 `HwpParser.parse` 포팅.
 *
 * ## 파서 선택은 Phase 0 실측으로 정해졌다
 * `@ohah/hwpjs`(napi-rs/emnapi)는 **Edge 에서 로드조차 안 된다** — `node:wasi` 의
 * `Context` 미지원 + `WebAssembly.Memory({shared:true})` 불가. 그래서 `_shared/hwp_text.ts`
 * 는 로컬 전용이고, Edge 경로는 **`@rhwp/core` 의 `getTextFileText()`** 다(유사도 1.0000).
 *
 * ## 출력이 **JSON 인코딩된 문자열**이다 (2026-09-07 실측)
 * `getTextFileText()` 는 평문이 아니라 `"\r\n문서번호\r\n…"` 처럼 **따옴표로 감싸이고
 * 개행이 `\r` `\n` 두 글자로 이스케이프된** 문자열을 준다. 그대로 쓰면 단락 분할이
 * 통째로 어긋난다(실측: 섹션 py 36 개 vs ts 1 개).
 *
 * Phase 0 이 이걸 못 잡은 이유는 채점이 `strip_ws()` 로 **공백을 전부 지우고** 비교하는
 * 방식이었기 때문이다 — 유사도 1.0000 은 "공백 무시" 조건 아래의 값이다.
 * 여기서는 `JSON.parse` 로 풀고, 실패하면 원문을 그대로 쓴다(버전이 바뀌어 평문을 주면
 * 그 경로가 맞다).
 *
 * ## 숫자 엔티티 디코딩이 필수다
 * `getTextFileText()` 는 특수문자를 HTML 숫자 엔티티로 낸다(`&#65378;` = `｢`).
 * Phase 0 실측: **디코딩 전 0.9637 → 후 1.0000**, 샘플 1개에서 6 곳.
 *
 * **명명 엔티티(`&amp;` 등)는 건드리지 않는다.** 실측으로 확인된 건 숫자 엔티티뿐이고,
 * 범위를 넓히면 원문에 진짜 `&amp;` 라는 글자가 있을 때 Python(그대로) 과 갈린다.
 *
 * ## 단락 분할 규칙 (원본 `hwp_parser.py:91`)
 * `\n\n` 로 나누고 각각 strip, 빈 것 제거. **결과가 1 개 이하면 `\n` 로 다시** 나눈다.
 * `page` · `section_title` · `bbox` 는 HWP 경로에서 전부 `null` 이다.
 */

import { pyStrip } from "../search/pystr.ts";

/** `adapters/parser.py` 의 `ExtractedSection`. HWP 는 text 외에 채우는 값이 없다. */
export interface ExtractedSection {
  text: string;
  page: number | null;
  section_title: string | null;
  bbox: [number, number, number, number] | null;
  metadata: Record<string, unknown>;
}

/** `adapters/parser.py` 의 `ExtractionResult`. */
export interface ExtractionResult {
  source_type: string;
  sections: ExtractedSection[];
  raw_text: string;
  warnings: string[];
  metadata: Record<string, unknown>;
}

/**
 * `&#123;` · `&#x1F;` 형태만 되돌린다.
 *
 * 범위를 넓히지 않는 이유는 위 헤더 참조. 잘못된 코드포인트는 **원문 그대로 둔다** —
 * 임의로 버리면 Python 출력과 길이가 달라진다.
 */
export function decodeNumericEntities(text: string): string {
  return text.replace(/&#(x[0-9a-fA-F]+|\d+);/g, (whole, body: string) => {
    const cp = body[0] === "x" || body[0] === "X"
      ? Number.parseInt(body.slice(1), 16)
      : Number.parseInt(body, 10);
    if (!Number.isFinite(cp) || cp < 0 || cp > 0x10FFFF) return whole;
    try {
      return String.fromCodePoint(cp);
    } catch {
      return whole;
    }
  });
}

/**
 * 원본 `hwp_parser.py:91` 의 단락 분할.
 *
 * Python `p.strip()` 은 `trim()` 과 문자 집합이 다르므로 `pyStrip` 을 쓴다.
 */
export function splitParagraphs(text: string): string[] {
  let parts = text.split("\n\n").map(pyStrip).filter((p) => p !== "");
  if (parts.length <= 1) {
    parts = text.split("\n").map(pyStrip).filter((p) => p !== "");
  }
  return parts;
}

/** 추출된 평문 → `ExtractionResult`. 텍스트 추출과 분리해 둬야 대조가 쉽다. */
export function buildHwpResult(text: string, warnings: string[] = []): ExtractionResult {
  // 원본: `if not text or not text.strip()` — 공백뿐이어도 빈 결과다.
  if (!text || pyStrip(text) === "") {
    return {
      source_type: "hwp",
      sections: [],
      raw_text: "",
      warnings: [...warnings, "HWP 추출 결과가 빈 문자열입니다 (텍스트 없음 또는 추출 실패)."],
      metadata: {},
    };
  }
  return {
    source_type: "hwp",
    sections: splitParagraphs(text).map((p) => ({
      text: p,
      page: null,
      section_title: null,
      bbox: null,
      metadata: {},
    })),
    raw_text: pyStrip(text),
    warnings,
    metadata: {},
  };
}

let rhwpMod: Record<string, unknown> | null = null;

/** WASM 모듈은 한 번만 로드한다. 아이솔레이트가 살아 있는 동안 재사용된다. */
async function loadRhwp(): Promise<Record<string, unknown>> {
  if (rhwpMod) return rhwpMod;
  const mod = await import("@rhwp/core") as Record<string, unknown>;
  const init = mod.default;
  if (typeof init === "function") {
    try {
      await (init as () => Promise<unknown>)();
    } catch { /* init 없이 동작하는 빌드가 있어 실패를 삼킨다(Phase 0 과 동일) */ }
  }
  rhwpMod = mod;
  return mod;
}

/**
 * `getTextFileText()` 출력이 JSON 인코딩이면 풀어서 평문으로.
 *
 * 방어적으로 간다 — 따옴표로 시작하고 파싱에 성공할 때만 푼다. 버전이 바뀌어 평문을 주면
 * 그대로 통과한다.
 */
export function unwrapJsonString(out: string): string {
  const t = out.trimStart();
  if (!t.startsWith('"')) return out;
  try {
    const parsed = JSON.parse(out);
    return typeof parsed === "string" ? parsed : out;
  } catch {
    return out;
  }
}

/** HWP 바이트 → 평문. JSON 언랩 + 엔티티 디코딩까지 포함한다. */
export async function extractHwpText(bytes: Uint8Array): Promise<string> {
  const mod = await loadRhwp();
  const HwpDocument = mod.HwpDocument as (new (b: Uint8Array) => Record<string, unknown>) | undefined;
  if (typeof HwpDocument !== "function") {
    throw new Error("@rhwp/core 에 HwpDocument 가 없습니다 — 패키지 버전을 확인하세요.");
  }
  const doc = new HwpDocument(bytes);
  const fn = doc.getTextFileText;
  if (typeof fn !== "function") {
    throw new Error("getTextFileText 가 없습니다 — @rhwp/core 버전을 확인하세요.");
  }
  const out = (fn as () => unknown).call(doc);
  if (typeof out !== "string") {
    throw new Error(`getTextFileText 가 문자열을 주지 않았습니다: ${typeof out}`);
  }
  return decodeNumericEntities(unwrapJsonString(out));
}

/** HWP 바이트 → `ExtractionResult`. */
export async function extractHwp(bytes: Uint8Array): Promise<ExtractionResult> {
  return buildHwpResult(await extractHwpText(bytes));
}
