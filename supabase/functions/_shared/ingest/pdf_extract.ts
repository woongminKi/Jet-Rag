/**
 * PDF 추출 — `adapters/impl/pymupdf_parser.py` 포팅.
 *
 * `pdf_dict.ts` 가 mupdf → PyMuPDF `get_text("dict")` 호환 구조를 만들어 주므로,
 * 여기서는 그 위의 **블록 → 섹션 변환과 heading 판정**만 옮긴다.
 *
 * ## 이 파일이 운영의 99.3% 를 담당한다
 * 운영 chunk 36,818 / 37,080 이 PDF 에서 나온다(2026-09-07 실측). 여기가 틀리면
 * 나머지를 아무리 맞춰도 의미가 없다.
 *
 * ## `current_title` 은 **문서 전체 sticky** 다 — 페이지 분할의 진짜 제약
 * 원본은 페이지 루프 밖에서 `current_title` 을 들고 다니며 heading 을 만날 때까지
 * 직전 제목을 상속시킨다. 페이지 경계를 넘어서도 유지된다.
 *
 * 따라서 대형 PDF 를 여러 태스크로 쪼갤 때 **페이지 범위를 병렬로 돌리면 제목이
 * 어긋난다.** 순차로 돌리고 태스크 경계에서 `current_title` 을 넘겨야 한다 —
 * `extractPdfPages` 가 `carryTitle` 을 받고 `nextTitle` 을 돌려주는 이유다.
 * (CPU 는 제약이 아니다. Phase 0 Edge 실측 400p = 1,421ms, 문서 열기는 0.7~2ms.)
 *
 * ## Python↔JS 가 갈리는 지점
 * | 원본 | 문제 | 대응 |
 * |---|---|---|
 * | `statistics.median` | 짝수 개면 **두 중간값의 평균** | 직접 구현 |
 * | 정규식 `\s` | Python 은 `\x1c-\x1f`·`\x85` 포함, JS 는 `﻿` 포함 | `PY_SP` |
 * | 정규식 `\d` | Python 은 유니코드 Nd 전부 | `\p{Nd}` |
 * | 정규식 `$` | Python 은 **끝의 개행 하나 앞**에서도 매칭 | `(?=\n?$)` |
 * | `len(text)` | 코드포인트 | 직접 셈 |
 * | `.strip()` | `\x1c` 를 지우고 `﻿` 는 안 지움 | `pyStrip` |
 */

import { PY_SP, pyStrip } from "../search/pystr.ts";
import type { PdfBlock, PdfPageDict } from "../pdf_dict.ts";

/** `adapters/parser.py` 의 `ExtractedSection` 과 같은 모양. */
export interface ExtractedSection {
  text: string;
  page: number | null;
  section_title: string | null;
  bbox: [number, number, number, number] | null;
  metadata: Record<string, unknown>;
}

/** `_HEADING_FONT_RATIO` · `_HEADING_TEXT_MAX_LEN` · `_BLOCK_TYPE_TEXT`. */
const HEADING_FONT_RATIO = 1.15;
const HEADING_TEXT_MAX_LEN = 80;
const BLOCK_TYPE_TEXT = 0;

const D = "\\p{Nd}";
const S = `[${PY_SP}]`;
/** Python `$`(MULTILINE 없음) = 문자열 끝 **또는 끝의 개행 하나 앞**. */
const END = "(?=\\n?$)";

/**
 * heading 텍스트 패턴 — 한국어 조문 · 【…】 · Chapter/Section · 영어 학술.
 *
 * 원본이 `re.match` 라 문자열 **시작**에서만 본다(`^` 가 이미 패턴에 있다).
 * 판정에 boolean 만 쓰므로 `$` 를 lookahead 로 바꿔도 결과가 같다.
 */
const HEADING_TEXT_RE = new RegExp(
  "^(" +
    `제${S}*${D}+${S}*[조항장절편관]|부칙|별표${S}*${D}*|별첨${S}*${D}*` +
    "|【[^】]{1,30}】|\\[[^\\]]{1,30}\\]" +
    `|Chapter${S}*${D}*|Section${S}*${D}*` +
    `|${D}+(?:\\.${D}+){0,3}\\.?${S}+[A-Z][A-Za-z]*` +
    `|(?:Abstract|Introduction|Background|Related${S}+Work|Methodology|Methods?` +
    "|Experiments?|Evaluation|Results?|Findings?|Discussion|Conclusions?" +
    `|References|Bibliography|Acknowledg(?:e?)ments?|Appendix(?:${S}+[A-Z])?)` +
    ")" +
    `(?:[${PY_SP}(].*)?` +
    END,
  "u",
);

/**
 * 페이지 헤더·페이지 번호 블랙리스트 — font size 와 무관하게 heading 에서 제외.
 *
 * 원본은 `re.IGNORECASE`. 패턴 리터럴이 전부 ASCII 라 JS `i` 와 갈릴 여지가 없다
 * (Python 의 유니코드 casefold 가 ASCII 리터럴에 붙일 짝은 `K`↔`K`(U+212A) 같은
 * 특수 사례뿐인데 여기 패턴엔 `k` 가 없다).
 */
const PAGE_HEADER_RE = new RegExp(
  "^(" +
    `arXiv:${S}*${D}+\\.${D}+(v${D}+)?(${S}*\\[[A-Za-z\\-\\.]+\\])?(${S}+.+)?` +
    `|${D}{1,4}` +
    `|Page${S}*${D}+` +
    `|-${S}*${D}+${S}*-` +
    ")" +
    `${S}*` +
    END,
  "iu",
);

function cpLen(s: string): number {
  let n = 0;
  for (const _ of s) n++;
  return n;
}

/**
 * `statistics.median` — 정렬 후 홀수면 가운데, **짝수면 두 가운데 값의 평균**.
 * `sizes.sort()` 로 두면 문자열 정렬이라 `10 < 9` 가 된다. 비교 함수 필수.
 */
export function median(values: number[]): number {
  if (values.length === 0) return 0.0;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 === 1 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/** 페이지 본문 폰트 size 중앙값. 표지의 60pt 한 글자에 끌려가지 않도록 median 이다. */
export function pageMedianSize(pageDict: PdfPageDict): number {
  const sizes: number[] = [];
  for (const block of pageDict.blocks ?? []) {
    if ((block.type ?? 0) !== BLOCK_TYPE_TEXT) continue;
    for (const line of block.lines ?? []) {
      for (const span of line.spans ?? []) {
        const size = span.size;
        if (typeof size === "number" && Number.isFinite(size) && size > 0) {
          sizes.push(size);
        }
      }
    }
  }
  return median(sizes);
}

/** 블록 내 모든 span 의 max font size. */
export function blockMaxSize(block: PdfBlock): number {
  const sizes: number[] = [];
  for (const line of block.lines ?? []) {
    for (const span of line.spans ?? []) {
      const size = span.size;
      if (typeof size === "number" && Number.isFinite(size) && size > 0) {
        sizes.push(size);
      }
    }
  }
  return sizes.length === 0 ? 0.0 : Math.max(...sizes);
}

/** 블록 내 span 텍스트를 line 단위로 join. 빈 line 은 버린다. */
export function blockText(block: PdfBlock): string {
  const lines: string[] = [];
  for (const line of block.lines ?? []) {
    const lineText = (line.spans ?? []).map((s) => s.text ?? "").join("");
    if (lineText) lines.push(lineText);
  }
  return lines.join("\n");
}

/**
 * heading 후보인지.
 *
 * 순서가 의미를 갖는다 — (D) 블랙리스트가 **먼저**라 페이지 번호는 글꼴이 아무리 커도
 * heading 이 되지 않는다.
 */
export function isHeadingBlock(
  blockMax: number,
  pageMedian: number,
  text: string,
): boolean {
  if (cpLen(text) <= HEADING_TEXT_MAX_LEN && PAGE_HEADER_RE.test(text)) return false;
  if (pageMedian > 0 && blockMax >= pageMedian * HEADING_FONT_RATIO) return true;
  if (cpLen(text) <= HEADING_TEXT_MAX_LEN && HEADING_TEXT_RE.test(text)) return true;
  return false;
}

export interface PageExtractResult {
  sections: ExtractedSection[];
  rawParts: string[];
  /** 이 페이지 처리 후의 sticky title — 다음 페이지(다음 태스크)로 넘긴다. */
  nextTitle: string | null;
}

/** dict 모드 블록 순회 + heading sticky propagate. 원본 `_extract_dict_blocks`. */
export function extractDictBlocks(
  pageDict: PdfPageDict,
  opts: { pageNum: number; currentTitle: string | null },
): PageExtractResult {
  const pageMedian = pageMedianSize(pageDict);
  let currentTitle = opts.currentTitle;
  const sections: ExtractedSection[] = [];
  const rawParts: string[] = [];

  for (const block of pageDict.blocks ?? []) {
    if ((block.type ?? 0) !== BLOCK_TYPE_TEXT) continue;
    const text = pyStrip(blockText(block));
    if (!text) continue;

    if (isHeadingBlock(blockMaxSize(block), pageMedian, text)) {
      currentTitle = text;
    }

    const bbox = block.bbox;
    sections.push({
      text,
      page: opts.pageNum,
      section_title: currentTitle,
      bbox: bbox ? [bbox[0], bbox[1], bbox[2], bbox[3]] : null,
      metadata: {},
    });
    rawParts.push(text);
  }

  return { sections, rawParts, nextTitle: currentTitle };
}
