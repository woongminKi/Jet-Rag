/**
 * 페이지별 vision 필요 판정 — `services/vision_need_score.py` 포팅.
 *
 * PDF 한 페이지의 dict(=`pdf_dict.ts` 산출물)를 보고 **OCR/vision 을 태울지** 정한다.
 * 여기서 판정이 갈리면 vision 호출 대상 페이지가 달라지고, 그건 곧 **비용과 청크
 * 내용**이 달라진다는 뜻이다.
 *
 * ## OR 규칙 5 종 — 하나라도 걸리면 vision
 * | 신호 | 임계 | 뜻 |
 * |---|---|---|
 * | `low_density` | `< 1e-3` chars/pt² | 글자가 거의 없다(스캔·도표 페이지) |
 * | `table_like` | `>= 0.3` | 표처럼 열이 갈린 줄의 비율 |
 * | `image_area` | `>= 0.30` | 이미지 블록 면적비 |
 * | `text_quality_low` | `<= 0.40` | 깨진 문자 비율(OCR 필요) |
 * | `caption` | `>= 0.20` | "표 1"·"그림 2" 같은 캡션 줄 비율 |
 *
 * `entity` 신호는 D2 에서 **OR 규칙에서 빠졌다**(가중치 0). `composite_score` 에만
 * 남아 있고 판정에는 관여하지 않는다 — 옮길 때 되살리면 안 된다.
 *
 * ## Python↔JS 가 갈리는 지점
 * - `len(text)` 는 **코드포인트**다. `text_chars` 가 밀리면 density 가 밀린다.
 * - `str.isspace()` ≠ JS `\s` (§18 에서 전수 대조로 만든 `pyIsSpace` 를 쓴다).
 * - `re.IGNORECASE` 는 유니코드 폴딩. 캡션·엔티티 패턴이 ASCII 라 `i` 로 충분하다.
 * - `\s{2,}|\t` 의 `\s` 는 Python 집합이라 `PY_SP` 로 바꾼다.
 */

import { PY_SP, pyStrip } from "../search/pystr.ts";
import { pyIsSpace } from "../pychar.ts";
import type { PdfPageDict } from "../pdf_dict.ts";

/** OR 규칙 임계. 전부 S1.5 D3 결정값이다. */
const DENSITY_NEEDS_AT = 1e-3;
const TABLE_LIKE_NEEDS_AT = 0.3;
const IMAGE_AREA_NEEDS_AT = 0.30;
const TEXT_QUALITY_NEEDS_AT = 0.40;
const CAPTION_NEEDS_AT = 0.20;

const TABLE_MIN_SPANS_PER_LINE = 3;
const TABLE_FALLBACK_MIN_COLS = TABLE_MIN_SPANS_PER_LINE;
const CAPTION_MAX_LINE_LEN = 80;
const BLOCK_TABLE_MIN_LINES = 3;
const BLOCK_TABLE_X_TOL_PT = 4.0;
const BLOCK_TABLE_MIN_BUCKETS = 3;

const CAPTION_KEYWORDS = ["표", "그림", "도", "사진", "Figure", "Fig.", "Table", "Photo"];

/** `\s{2,}|\t` — Python `\s` 집합을 쓴다. */
const MULTISPACE_SPLIT = new RegExp(`[${PY_SP}]{2,}|\\t`, "u");

/** 엔티티 패턴. 판정에는 안 쓰이고 `composite_score`·진단용으로만 센다. */
const ENTITY_PATTERN = new RegExp(
  "(\\[표\\s*\\d+\\]?|\\[그림\\s*\\d+\\]?" +
    "|<표\\s*\\d+>|<그림\\s*\\d+>" +
    "|Figure\\s*\\d+|Table\\s*\\d+" +
    "|식\\s*\\(\\s*\\d+\\s*\\)|Eq\\.\\s*\\(\\s*\\d+\\s*\\))",
  "gi",
);

/**
 * 캡션 판정 — 키워드 뒤에 **숫자가 와야** 한다.
 * v3 에서 정밀화했다("그림 좋다"·"표면 처리"·"사진작가" 오탐 차단).
 */
const CAPTION_PATTERN =
  /(?:[\[\<\(]\s*)?(표|그림|도|사진|Figure|Fig\.?|Table|Photo)\s*\d+(?:[\-\.]\d+)?(?:\s*[\]\>\)])?/i;

/** composite score 가중치. 합 1.0. `entity_density` 는 deprecated 라 0 이다. */
export const DEFAULT_WEIGHTS: Record<string, number> = {
  text_density_inverse: 0.30,
  table_like_blocks: 0.25,
  image_area_ratio: 0.20,
  text_quality: 0.10,
  caption_existence: 0.15,
  entity_density: 0.0,
};

export interface PageScore {
  page: number;
  text_chars: number;
  page_area_pt2: number;
  text_density: number;
  entity_hits: number;
  table_like_score: number;
  needs_vision: boolean;
  image_area_ratio: number;
  text_quality: number;
  caption_score: number;
  composite_score: number;
  triggers: string[];
}

function cpLen(s: string): number {
  let n = 0;
  for (const _ of s) n++;
  return n;
}

/** OR 규칙 항목별 boolean. `needs_vision` 과 breakdown 의 공통 출처다. */
export function orRuleTriggers(opts: {
  textDensity: number;
  tableLikeScore: number;
  imageAreaRatio?: number;
  textQuality?: number;
  captionScore?: number;
  pageAreaPt2?: number;
}): Record<string, boolean> {
  const {
    textDensity,
    tableLikeScore,
    imageAreaRatio = 0,
    textQuality = 1,
    captionScore = 0,
    pageAreaPt2 = 1,
  } = opts;
  return {
    // 면적이 0 이하면 density 신호는 안 켠다(D1 호환).
    low_density: pageAreaPt2 > 0 && textDensity < DENSITY_NEEDS_AT,
    table_like: tableLikeScore >= TABLE_LIKE_NEEDS_AT,
    image_area: imageAreaRatio >= IMAGE_AREA_NEEDS_AT,
    text_quality_low: textQuality <= TEXT_QUALITY_NEEDS_AT,
    caption: captionScore >= CAPTION_NEEDS_AT,
  };
}

export function needsVision(opts: Parameters<typeof orRuleTriggers>[0]): boolean {
  return Object.values(orRuleTriggers(opts)).some(Boolean);
}

/** 가중 합산 composite. OR 규칙과 **별개**로 분포 진단·hybrid 검토용이다. */
export function computeScore(opts: {
  textDensity: number;
  tableLikeScore: number;
  imageAreaRatio?: number;
  textQuality?: number;
  captionScore?: number;
  entityHits?: number;
  pageAreaPt2?: number;
  weights?: Record<string, number>;
}): number {
  const {
    textDensity,
    tableLikeScore,
    imageAreaRatio = 0,
    textQuality = 1,
    captionScore = 0,
    entityHits = 0,
    pageAreaPt2 = 1,
    weights = DEFAULT_WEIGHTS,
  } = opts;
  const clamp = (x: number) => Math.max(0, Math.min(1, x));
  // density·quality 는 "낮을수록 vision" 이라 뒤집어 가산한다.
  const densitySignal = pageAreaPt2 > 0 ? clamp(1 - textDensity / (2 * DENSITY_NEEDS_AT)) : 0;
  const score = (weights["text_density_inverse"] ?? 0) * densitySignal +
    (weights["table_like_blocks"] ?? 0) * clamp(tableLikeScore) +
    (weights["image_area_ratio"] ?? 0) * clamp(imageAreaRatio) +
    (weights["text_quality"] ?? 0) * clamp(1 - textQuality) +
    (weights["caption_existence"] ?? 0) * clamp(captionScore) +
    (weights["entity_density"] ?? 0) * (entityHits > 0 ? 1 : 0);
  return clamp(score);
}

interface LineMeta {
  nonEmpty: { text?: string; bbox?: number[] }[];
  text: string;
  firstX: number | null;
}

/** line 의 첫 span 좌측 x. bbox 가 이상하면 `null` — cluster 에서 빠진다. */
function lineFirstX(spans: { bbox?: number[] }[]): number | null {
  if (spans.length === 0) return null;
  const bbox = spans[0].bbox;
  if (!bbox || bbox.length < 1) return null;
  const v = Number(bbox[0]);
  return Number.isFinite(v) ? v : null;
}

/** 정렬된 x 좌표에서 인접 차 ≤ tol 인 bucket 수. */
export function countXClusters(sortedXs: number[], tol: number): number {
  if (sortedXs.length === 0) return 0;
  let buckets = 1;
  let prev = sortedXs[0];
  for (const x of sortedXs.slice(1)) {
    if (x - prev > tol) buckets++;
    prev = x;
  }
  return buckets;
}

/** v3 — block 단위 표 판정. 줄 3+ & 첫 x 좌표 bucket 3+ 면 표로 본다. */
function isTableLikeBlock(lines: LineMeta[]): boolean {
  if (lines.length < BLOCK_TABLE_MIN_LINES) return false;
  const xs = lines.map((l) => l.firstX).filter((x): x is number => x !== null)
    .sort((a, b) => a - b);
  if (xs.length < BLOCK_TABLE_MIN_LINES) return false;
  return countXClusters(xs, BLOCK_TABLE_X_TOL_PT) >= BLOCK_TABLE_MIN_BUCKETS;
}

/** line 하나가 열 후보인지 — span 3+ 또는 (span 1 개일 때) 다중 공백 분리 3+. */
function isMultiColumnLine(spans: unknown[], lineText: string): boolean {
  if (spans.length >= TABLE_MIN_SPANS_PER_LINE) return true;
  if (spans.length === 1) {
    // span 2 개인 어중간한 경우는 fallback 을 안 본다(D1 정책 유지).
    const cols = lineText.split(MULTISPACE_SPLIT).filter((c) => pyStrip(c ?? ""));
    return cols.length >= TABLE_FALLBACK_MIN_COLS;
  }
  return false;
}

/** 캡션 줄 — 80자 이하 + 키워드 + **숫자가 붙은** 형태. */
export function isCaptionLine(lineText: string): boolean {
  const text = pyStrip(lineText);
  if (!text || cpLen(text) > CAPTION_MAX_LINE_LEN) return false;
  if (!CAPTION_KEYWORDS.some((kw) => text.includes(kw))) return false;
  return CAPTION_PATTERN.test(text);
}

interface TextFeatures {
  textChars: number;
  tableLikeScore: number;
  captionScore: number;
}

/** text_chars · table_like · caption 을 한 번의 순회로 뽑는다. */
function collectTextFeatures(dict: PdfPageDict): TextFeatures {
  let textChars = 0;
  let totalLines = 0;
  let multiColLines = 0;
  let captionLines = 0;

  for (const block of dict.blocks ?? []) {
    if ((block.type ?? 0) !== 0) continue;

    const metas: LineMeta[] = [];
    for (const line of block.lines ?? []) {
      const spans = line.spans ?? [];
      const nonEmpty = spans.filter((s) => pyStrip(s.text ?? ""));
      if (nonEmpty.length === 0) continue;
      const lineText = nonEmpty.map((s) => s.text ?? "").join("");
      metas.push({ nonEmpty, text: lineText, firstX: lineFirstX(nonEmpty) });
      // **코드포인트로 센다.** 여기가 밀리면 density 가 통째로 밀린다.
      for (const s of nonEmpty) textChars += cpLen(s.text ?? "");
    }
    if (metas.length === 0) continue;

    const blockIsTable = isTableLikeBlock(metas);
    totalLines += metas.length;

    if (blockIsTable) {
      // block 단위로 걸리면 그 block 의 **모든 줄**을 열 후보로 센다.
      // line 단위 판정과 중복으로 세지 않으려고 여기서 끊는다.
      multiColLines += metas.length;
      for (const m of metas) if (isCaptionLine(m.text)) captionLines++;
      continue;
    }

    for (const m of metas) {
      if (isMultiColumnLine(m.nonEmpty, m.text)) multiColLines++;
      if (isCaptionLine(m.text)) captionLines++;
    }
  }

  // block hit + line hit 가 겹치면 1.0 을 넘을 수 있어 clamp 한다.
  const tableScore = totalLines > 0 ? Math.min(1, multiColLines / totalLines) : 0;
  const captionScore = totalLines > 0 ? captionLines / totalLines : 0;
  return { textChars, tableLikeScore: tableScore, captionScore };
}

/** 이미지 블록 면적 합 / 페이지 면적. */
export function imageAreaRatio(dict: PdfPageDict, pageAreaPt2: number): number {
  if (pageAreaPt2 <= 0) return 0;
  let area = 0;
  for (const block of dict.blocks ?? []) {
    if ((block.type ?? 0) !== 1) continue;
    const bbox = block.bbox;
    if (!bbox || bbox.length !== 4) continue;
    const [x0, y0, x1, y1] = bbox;
    area += Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
  }
  return Math.min(1, area / pageAreaPt2);
}

/** 정상 printable 인지 — 아니면 OCR 깨짐 신호로 본다. */
export function isPrintable(ch: string): boolean {
  const cp = ch.codePointAt(0) ?? 0;
  if (cp >= 0x20 && cp <= 0x7E) return true; // ASCII 인쇄 가능
  if (cp >= 0xAC00 && cp <= 0xD7A3) return true; // 한글 음절
  if ((cp >= 0x1100 && cp <= 0x11FF) || (cp >= 0x3130 && cp <= 0x318F)) return true; // 자모
  if (cp >= 0x3400 && cp <= 0x9FFF) return true; // CJK 한자
  if ([0x300C, 0x300D, 0x300E, 0x300F, 0x3001, 0x3002, 0x00B7, 0x00A0].includes(cp)) {
    return true; // 한국어 문장 부호
  }
  if (cp >= 0x00A1 && cp <= 0x024F) return true; // 라틴 보충
  return false;
}

/** printable 비율 (0=깨짐, 1=정상). **공백은 세지 않는다.** */
export function textQuality(pageText: string): number {
  if (!pageText) return 1;
  let printable = 0;
  let total = 0;
  for (const ch of pageText) {
    // `str.isspace()` 는 JS `\s` 와 다르다 — 전수 대조로 만든 판정을 쓴다.
    if (pyIsSpace(ch)) continue;
    total++;
    if (isPrintable(ch)) printable++;
  }
  return total > 0 ? printable / total : 1;
}

/** 엔티티 스캔용 평문. **line 안 span 은 직접 붙인다** — 사이에 줄바꿈을 넣으면
 * `<표 1>` 같은 패턴이 쪼개져 누락된다. */
export function flattenText(dict: PdfPageDict): string {
  const parts: string[] = [];
  for (const block of dict.blocks ?? []) {
    if ((block.type ?? 0) !== 0) continue;
    for (const line of block.lines ?? []) {
      const t = (line.spans ?? []).map((s) => s.text ?? "").join("");
      if (t) parts.push(t);
    }
  }
  return parts.join("\n");
}

/** 페이지 하나의 6 신호 + composite + trigger 를 계산한다. */
export function scorePage(
  dict: PdfPageDict,
  opts: { pageNum: number; pageAreaPt2: number },
): PageScore {
  const f = collectTextFeatures(dict);
  const density = opts.pageAreaPt2 > 0 ? f.textChars / opts.pageAreaPt2 : 0;

  const pageText = flattenText(dict);
  ENTITY_PATTERN.lastIndex = 0; // `g` 정규식은 상태를 갖는다.
  const entityHits = (pageText.match(ENTITY_PATTERN) ?? []).length;

  const imgArea = imageAreaRatio(dict, opts.pageAreaPt2);
  const quality = textQuality(pageText);

  const common = {
    textDensity: density,
    tableLikeScore: f.tableLikeScore,
    imageAreaRatio: imgArea,
    textQuality: quality,
    captionScore: f.captionScore,
    pageAreaPt2: opts.pageAreaPt2,
  };
  const triggers = Object.entries(orRuleTriggers(common))
    .filter(([, hit]) => hit)
    .map(([kind]) => kind);

  return {
    page: opts.pageNum,
    text_chars: f.textChars,
    page_area_pt2: opts.pageAreaPt2,
    text_density: density,
    entity_hits: entityHits,
    table_like_score: f.tableLikeScore,
    needs_vision: triggers.length > 0,
    image_area_ratio: imgArea,
    text_quality: quality,
    caption_score: f.captionScore,
    composite_score: computeScore({ ...common, entityHits }),
    triggers,
  };
}
