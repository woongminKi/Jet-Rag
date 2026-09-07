/**
 * `ingest/stages/content_gate.py` 포팅 — 개인정보·워터마크·메신저 감지.
 *
 * 차단하지 않는다. **표시만 한다** — 문서 상세 화면이 그 값을 읽어 경고를 띄운다.
 *
 * | 저장 위치 | 값 |
 * |---|---|
 * | `documents.flags` | `has_pii` · `has_watermark` · `third_party` · `watermark_hits[]` |
 * | `chunks.metadata` | `pii_ranges: [[start,end], …]` · `watermark_hits[]` |
 *
 * ## 계좌번호는 일부러 안 본다
 * 한국 은행 계좌 형식이 너무 다양해 오탐이 크다는 게 원본의 결정(DE-44)이다.
 *
 * ## 정규식을 그대로 옮기면 안 된다
 * Python `\d` 는 유니코드 `Nd`, `\s` 는 유니코드 공백, `\b` 는 유니코드 단어 경계다.
 * JS 는 셋 다 ASCII 라 아랍-인도 숫자로 쓴 주민번호를 놓친다. `entity_extract.ts` 와
 * 같은 방식으로 풀어 쓴다(`v` 플래그 + 집합 뺄셈).
 *
 * ## 오프셋이 코드포인트다
 * `m.start()` / `m.end()` 는 Python 에서 코드포인트 단위인데 JS 정규식은 UTF-16 이다.
 * 이모지가 앞에 있으면 그만큼 밀린다. `pii_ranges` 는 화면에서 가리기(마스킹)에 쓰이는
 * 값이라 밀리면 엉뚱한 자리를 가린다 — 변환해서 넣는다.
 */

import { PY_SP } from "../search/pystr.ts";
import { PY_WORD_CLASS, pyIsDigit } from "../pychar.ts";
import { pyInt } from "../pynum.ts";
import type { ChunkRecord } from "./chunk_records.ts";

/** Python `\b` — `PY_WORD_CLASS` 의 경계. `v` 플래그가 있어야 집합 뺄셈이 산다. */
const B = `(?:(?<=${PY_WORD_CLASS})(?!${PY_WORD_CLASS})|(?<!${PY_WORD_CLASS})(?=${PY_WORD_CLASS}))`;
const D = "\\p{Nd}";

/** 주민등록번호 — `YYMMDD[-공백]?NXXXXXX`. 앞 6 자리는 날짜 검증까지 한다. */
// `v` 플래그에서 `-` 는 문자 클래스 안의 예약 문자다 — 반드시 이스케이프한다.
// 안 하면 정규식 생성 자체가 SyntaxError 로 죽는다(실제로 여기서 걸렸다).
const RRN_RE = new RegExp(`${B}(${D}{6})[\\-${PY_SP}]?(${D}{7})${B}`, "gv");

/**
 * 카드번호 — 4 자리 × 3 그룹 + 마지막 4~7 자리(16~19 자리).
 *
 * 한국 휴대폰 번호(11 자리)와 자릿수로 갈린다 — 그래서 마지막 그룹 하한이 4 다.
 */
const CARD_RE = new RegExp(
  `${B}${D}{4}[${PY_SP}\\-]?${D}{4}[${PY_SP}\\-]?${D}{4}[${PY_SP}\\-]?${D}{4,7}${B}`,
  "gv",
);

export const WATERMARK_KEYWORDS = [
  "대외비",
  "내부자료",
  "보안",
  "CONFIDENTIAL",
  "INTERNAL",
] as const;

/** `re.IGNORECASE` 대응. 한글은 대소문자가 없어 영문 두 개만 실제로 영향받는다. */
const WATERMARK_RE = new RegExp(WATERMARK_KEYWORDS.join("|"), "giu");

/** UTF-16 인덱스 → 코드포인트 인덱스. Python 오프셋과 맞추려면 필요하다. */
function cpIndexMap(text: string): number[] {
  // `map[utf16Index] = codePointIndex`. 서로게이트 뒤쪽 자리도 채워 둔다.
  const map = new Array<number>(text.length + 1);
  let cp = 0;
  let i = 0;
  for (const ch of text) {
    for (let k = 0; k < ch.length; k++) map[i + k] = cp;
    i += ch.length;
    cp++;
  }
  map[text.length] = cp;
  return map;
}

/** 원본 `_is_valid_yymmdd` — 월 1~12, 일 1~31. 윤년·말일은 안 본다. */
export function isValidYymmdd(yymmdd: string): boolean {
  // 원본은 `len()` 과 `isdigit()` 을 본다. 정규식이 이미 `Nd` 6 자리를 보장해서
  // `isdigit()` 은 그 경로에서 항상 참이지만, **함수 자체를 원본과 같게 둔다** —
  // 안 그러면 `"90 101"` 같은 입력에서 갈린다(대조가 실제로 잡았다).
  // `str.isdigit()` 은 `Nd` 보다 넓다(위첨자 ² 등) — `pyIsDigit` 이 그 집합이다.
  let n = 0;
  for (const ch of yymmdd) {
    if (!pyIsDigit(ch)) return false;
    n++;
  }
  if (n !== 6) return false;
  const mm = pyInt(yymmdd.slice(2, 4));
  const dd = pyInt(yymmdd.slice(4, 6));
  if (mm === null || dd === null) return false;
  return mm >= 1 && mm <= 12 && dd >= 1 && dd <= 31;
}

/** 원본 `_detect_pii` — 정렬·중복 제거된 `[start, end]` 목록(코드포인트 기준). */
export function detectPii(text: string): [number, number][] {
  const map = cpIndexMap(text);
  const raw: [number, number][] = [];

  RRN_RE.lastIndex = 0;
  for (const m of text.matchAll(RRN_RE)) {
    if (isValidYymmdd(m[1])) {
      raw.push([map[m.index!], map[m.index! + m[0].length]]);
    }
  }
  CARD_RE.lastIndex = 0;
  for (const m of text.matchAll(CARD_RE)) {
    raw.push([map[m.index!], map[m.index! + m[0].length]]);
  }

  // `sorted(set(...))` — 튜플 정렬은 start, 그다음 end 다.
  const seen = new Set<string>();
  const out: [number, number][] = [];
  for (const r of raw) {
    const k = `${r[0]},${r[1]}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(r);
  }
  out.sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
  return out;
}

/** 코드포인트 순 비교 — JS 기본 정렬은 UTF-16 단위라 astral 에서 갈린다. */
function cpCompare(a: string, b: string): number {
  const ai = [...a];
  const bi = [...b];
  for (let i = 0; i < Math.min(ai.length, bi.length); i++) {
    const d = ai[i].codePointAt(0)! - bi[i].codePointAt(0)!;
    if (d !== 0) return d;
  }
  return ai.length - bi.length;
}

/** 원본 `_detect_watermark` — 매칭된 **표기 그대로** 보존한다(대소문자 포함). */
export function detectWatermark(text: string): string[] {
  const hits = new Set<string>();
  WATERMARK_RE.lastIndex = 0;
  for (const m of text.matchAll(WATERMARK_RE)) hits.add(m[0]);
  return [...hits].sort(cpCompare);
}

export interface ContentGateResult {
  chunks: ChunkRecord[];
  flagsUpdate: Record<string, unknown>;
  chunksWithPii: number;
  chunksWithWatermark: number;
}

/**
 * 원본 `run_content_gate_stage` — 청크 metadata 를 채우고 문서 flags 를 만든다.
 *
 * DB 쓰기는 하지 않는다. 호출자가 `flagsUpdate` 를 기존 flags 에 머지한다.
 *
 * `visionType` 은 `ExtractionResult.metadata.vision_type` 이다. 단독 이미지 업로드에서만
 * 채워진다 — **스캔 PDF 경로는 원본이 그 값을 안 넘긴다**(`_reroute_pdf_to_image` 가
 * `ExtractionResult` 에 metadata 를 안 준다). 그래서 PDF 는 항상 `third_party=false` 다.
 */
export function runContentGateStage(opts: {
  chunks: ChunkRecord[];
  visionType?: unknown;
}): ContentGateResult {
  let hasPii = false;
  let hasWatermark = false;
  const watermarkHitsDoc = new Set<string>();
  const updated: ChunkRecord[] = [];

  for (const chunk of opts.chunks) {
    const piiRanges = detectPii(chunk.text);
    const watermarkHits = detectWatermark(chunk.text);
    if (piiRanges.length > 0) hasPii = true;
    if (watermarkHits.length > 0) {
      hasWatermark = true;
      for (const h of watermarkHits) watermarkHitsDoc.add(h);
    }
    const metadata = { ...chunk.metadata };
    if (piiRanges.length > 0) metadata["pii_ranges"] = piiRanges;
    if (watermarkHits.length > 0) metadata["watermark_hits"] = watermarkHits;
    updated.push({ ...chunk, metadata });
  }

  const flagsUpdate: Record<string, unknown> = {
    has_pii: hasPii,
    has_watermark: hasWatermark,
    third_party: opts.visionType === "메신저대화",
  };
  if (watermarkHitsDoc.size > 0) {
    flagsUpdate["watermark_hits"] = [...watermarkHitsDoc].sort(cpCompare);
  }

  return {
    chunks: updated,
    flagsUpdate,
    chunksWithPii: updated.filter((c) => "pii_ranges" in c.metadata).length,
    chunksWithWatermark: updated.filter((c) => "watermark_hits" in c.metadata).length,
  };
}
