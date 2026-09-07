/**
 * ZIP/XML 계열 파서 4 종을 인제스트 형태(`ExtractionResult`)로 잇는다.
 *
 * 텍스트 추출 자체는 Phase 0 에서 만든 `ooxml_text.ts` · `hwp_xml_text.ts` 가 한다
 * (그쪽 헤더에 재현 규칙과 알려진 한계가 적혀 있다). 여기서는 그 결과를 파이프라인이
 * 쓰는 모양으로 바꾸고, 원본 파서가 채우는 필드를 맞춘다.
 *
 * | doc_type | 파서 | `source_type` | page |
 * |---|---|---|---|
 * | `docx` | `DocxParser` | `docx` | 없음 |
 * | `pptx` | `PptxParser` | `pptx` | 슬라이드 번호(1-based) |
 * | `hwpx` | `HwpxParser` | `hwpx` | 없음 |
 * | `hwp`(XML) | `HwpmlParser` | **`hwpml`** | 없음 |
 *
 * ## HWPML 은 `doc_type` 이 `hwp` 다
 * DB CHECK 제약이 `hwpml` 을 모른다. 그래서 `documents.doc_type` 은 `hwp` 로 두고
 * `source_type` 만 `hwpml` 이다 — 원본 정책 그대로다. 어느 쪽인지는 **바이트로** 가른다
 * (확장자는 못 믿는다 — `law sample2.hwp` 가 실제로 HWPML 이다).
 *
 * ## PPTX 의 Vision 보강은 아직 없다
 * 원본 `PptxParser` 는 텍스트가 50 자 미만인 슬라이드에서 **가장 큰 그림**을 뽑아
 * Vision OCR 을 돌린다(`pptx_rerouting` / `pptx_augment`). 그 경로는 슬라이드의 이미지
 * 관계(`_rels`)를 풀어야 해서 별도 작업이고, 아직 안 옮겼다. 지금은 텍스트가 없는
 * 슬라이드가 **섹션 없이 지나간다** — 원본은 OCR 텍스트로 섹션을 만든다.
 */

import { extractDocx, extractPptx, type OoxmlResult } from "../ooxml_text.ts";
import { extractHwpml, extractHwpx, type HwpResult } from "../hwp_xml_text.ts";
import type { ExtractedSection, ExtractionResult } from "./hwp_extract.ts";

function toSections(
  rows: { text: string; page: number | null; sectionTitle: string | null }[],
): ExtractedSection[] {
  return rows.map((r) => ({
    text: r.text,
    page: r.page,
    section_title: r.sectionTitle,
    // 원본 파서 넷 다 `bbox=None` 이고 metadata 는 기본값(빈 dict)이다.
    bbox: null,
    metadata: {},
  }));
}

function toResult(
  r: OoxmlResult | HwpResult,
  metadata: Record<string, unknown>,
): ExtractionResult {
  const sections = toSections(r.sections);
  return {
    source_type: r.sourceType,
    sections,
    // 원본 `"\n\n".join(raw_parts)` — `raw_parts` 는 섹션 텍스트와 같은 순서·내용이다.
    raw_text: sections.map((s) => s.text).join("\n\n"),
    warnings: r.warnings,
    metadata,
  };
}

export function extractDocxResult(bytes: Uint8Array): ExtractionResult {
  return toResult(extractDocx(bytes), {});
}

export function extractPptxResult(bytes: Uint8Array): ExtractionResult {
  return toResult(extractPptx(bytes), {});
}

export function extractHwpxResult(bytes: Uint8Array): ExtractionResult {
  return toResult(extractHwpx(bytes), {});
}

export function extractHwpmlResult(bytes: Uint8Array): ExtractionResult {
  const r = extractHwpml(bytes);
  // HWPML 만 `<DOCSUMMARY>` 값을 metadata 에 싣는다.
  return toResult(r, r.metadata);
}
