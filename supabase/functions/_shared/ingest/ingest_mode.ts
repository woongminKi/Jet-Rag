/**
 * `api/app/services/ingest_mode.py` 포팅 — 운영 모드 → vision 페이지 cap.
 *
 * | 모드 | page cap |
 * |---|---|
 * | `fast` | `JETRAG_VISION_MODE_FAST_CAP` (기본 10) — 메모·짧은 문서용 |
 * | `default` | `JETRAG_VISION_PAGE_CAP_PER_DOC` (기본 50) |
 * | `precise` | 0 = 페이지 무제한 (비용 한도까지만) |
 *
 * ## 전역 kill switch 가 모드를 이긴다
 * `JETRAG_VISION_PAGE_CAP_PER_DOC` 가 0 이하면 모드와 무관하게 0(무한)이다.
 * 운영자가 비용 한도만으로 제어하고 싶을 때의 회복 토글이다.
 *
 * ## 모드는 문서에 붙는다
 * 원본은 `page_cap_override` 를 함수 인자로 나르지만, 그 값의 출처는 언제나
 * `documents.flags.ingest_mode` 다(업로드·URL·reingest 모두 거기에 쓴다).
 * Edge 는 단계가 큐로 나뉘어 있어 인자를 나르기보다 **필요한 곳에서 flags 를 읽어
 * 다시 계산하는** 편이 맞다 — 메시지가 재시도로 낡아도 문서의 모드가 진실이다.
 */

export const INGEST_MODES = ["fast", "default", "precise"] as const;
export type IngestMode = typeof INGEST_MODES[number];

export const DEFAULT_INGEST_MODE: IngestMode = "default";

const FAST_MODE_CAP_DEFAULT = 10;

/** 원본 `ingest_mode._parse_int` — 비숫자면 기본값. 음수는 그대로 통과시킨다. */
function parseIntEnv(raw: string | undefined, dflt: number): number {
  if (raw === undefined || raw.trim() === "") return dflt;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) && /^\s*[+-]?\d+\s*$/.test(raw) ? n : dflt;
}

/** `flags.ingest_mode` 처럼 신뢰할 수 없는 값 → 유효한 모드. 아니면 `null`. */
export function asIngestMode(raw: unknown): IngestMode | null {
  return typeof raw === "string" && (INGEST_MODES as readonly string[]).includes(raw)
    ? raw as IngestMode
    : null;
}

/**
 * 원본 `resolve_page_cap`.
 *
 * @param pageCapPerDoc `settings.vision_page_cap_per_doc`
 */
export function resolvePageCap(
  mode: IngestMode,
  opts: { pageCapPerDoc: number; env: Record<string, string | undefined> },
): number {
  if (opts.pageCapPerDoc <= 0) return 0; // 전역 kill switch
  if (mode === "fast") {
    return parseIntEnv(opts.env["JETRAG_VISION_MODE_FAST_CAP"], FAST_MODE_CAP_DEFAULT);
  }
  if (mode === "default") return opts.pageCapPerDoc;
  if (mode === "precise") return 0;
  throw new Error(`unknown ingest mode: ${JSON.stringify(mode)}`);
}

/** 원본 `_flags_with_ingest_mode` — 기존 flags 를 보존하고 모드만 갱신. */
export function flagsWithIngestMode(
  existing: Record<string, unknown> | null | undefined,
  mode: IngestMode,
): Record<string, unknown> {
  return { ...(existing ?? {}), ingest_mode: mode };
}

/**
 * Python 문자열 `repr` — 400 메시지가 `{raw!r}` 라 따옴표까지 같아야 한다.
 * `'` 가 있고 `"` 가 없으면 큰따옴표로 감싼다(Python 규칙).
 */
export function pyStrRepr(s: string): string {
  const useDouble = s.includes("'") && !s.includes('"');
  const quote = useDouble ? '"' : "'";
  let out = "";
  for (const ch of s) {
    if (ch === "\\") out += "\\\\";
    else if (ch === quote) out += "\\" + ch;
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else out += ch;
  }
  return quote + out + quote;
}

/**
 * 원본 `_validate_ingest_mode` — None·빈 문자열은 default, 그 외 무효값은 400.
 *
 * 예외 대신 결과를 돌려준다. 라우트마다 오류를 내는 방식이 달라(업로드는 즉시 응답,
 * reingest 는 내부 HttpError) 던지는 쪽으로 고정하면 한쪽이 어색해진다.
 */
export function validateIngestMode(
  raw: string | null | undefined,
): { mode: IngestMode } | { detail: string } {
  if (raw === null || raw === undefined || raw === "") {
    return { mode: DEFAULT_INGEST_MODE };
  }
  const mode = asIngestMode(raw);
  if (mode === null) {
    return {
      detail: `지원되지 않는 모드입니다: ${pyStrRepr(raw)} ` +
        `(허용: ${INGEST_MODES.join(", ")})`,
    };
  }
  return { mode };
}
