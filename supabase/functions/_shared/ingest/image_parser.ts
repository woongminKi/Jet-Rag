/**
 * `api/app/adapters/impl/image_parser.py` 의 결과 합성 포팅.
 *
 * `VisionCaption` 한 건을 `ExtractionResult` 로 편다 — 최대 3 섹션:
 * 1. 분류 + 캡션 (`[type] caption`)
 * 2. OCR 텍스트 (있을 때만)
 * 3. 액션 아이템 (화이트보드 등 `structured.action_items` 가 있을 때만)
 *
 * ## caption metadata 는 세 섹션 모두에 붙는다
 * 2026-05-09 변경 — 원래 caption 섹션에만 붙였는데, 그러면 OCR 청크가 표/그림 캡션과
 * 매칭되지 않는다. 같은 페이지의 모든 섹션이 같은 caption metadata 를 공유하도록 바꿔
 * 매칭 효과가 4~5 배 됐다는 게 원본 주석의 기록이다.
 *
 * ## 이미지 정규화는 여기 없다
 * PDF 경로에서는 `pdf_raster.renderPageForVision` 이 렌더·축소·인코딩을 한 번에 한다.
 * 원본이 PNG 를 만들어 Pillow 에 넘기는 왕복은 무손실이라 관찰되지 않는다.
 * 단독 이미지 업로드(사용자 파일) 경로를 옮길 때는 EXIF transpose 와 HEIC 분기가
 * 추가로 필요하다 — 지금은 PDF 페이지만 다룬다.
 */

import type { ExtractedSection, ExtractionResult } from "./hwp_extract.ts";
import type { VisionCaption } from "./vision_caption.ts";
import { pyStr } from "./vision_caption.ts";
import { pyStrip } from "../search/pystr.ts";

/**
 * 원본 `_extract_action_items` — `structured.action_items` 를 불릿 라인으로 편다.
 *
 * str 이면 strip 후 빈 게 아니면 채택. dict 면 **값만** 모아 ` · ` 로 잇는다
 * (`{task, owner, due_date}` 같은 형태). 그 외 타입은 버린다.
 */
export function extractActionItems(structured: Record<string, unknown> | null): string[] {
  if (structured === null || typeof structured !== "object" || Array.isArray(structured)) {
    return [];
  }
  const raw = structured["action_items"];
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const item of raw) {
    if (typeof item === "string") {
      const cleaned = pyStrip(item);
      if (cleaned !== "") out.push(cleaned);
    } else if (item !== null && typeof item === "object" && !Array.isArray(item)) {
      // `str(v).strip() for v in item.values() if v` — falsy 값은 빠진다.
      // Python 은 빈 배열·빈 dict 도 falsy 다. `pyStr` 이 컨테이너 표기까지 맞춘다.
      const parts: string[] = [];
      for (const v of Object.values(item as Record<string, unknown>)) {
        if (!pyIsTruthyValue(v)) continue;
        parts.push(pyStrip(pyStr(v)));
      }
      if (parts.length > 0) out.push(parts.join(" · "));
    }
  }
  return out;
}

/** `if v` — Python 진리값. 빈 배열·빈 객체가 falsy 라는 게 JS 와 갈리는 지점이다. */
function pyIsTruthyValue(v: unknown): boolean {
  if (v === null || v === undefined) return false;
  if (typeof v === "boolean") return v;
  if (typeof v === "number") return v !== 0;
  if (typeof v === "string") return v !== "";
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === "object") return Object.keys(v as object).length > 0;
  return true;
}

/**
 * 원본 `ImageParser._compose_result`. 캐시 hit / miss 가 같은 구조를 내야 검색 결과가
 * 같다 — 분기를 두지 않는 게 계약이다.
 */
export function composeResult(
  caption: VisionCaption,
  opts: { warnings: string[] },
): ExtractionResult {
  const sections: ExtractedSection[] = [];

  const captionMetadata: Record<string, unknown> = {};
  if (caption.table_caption !== null) captionMetadata["table_caption"] = caption.table_caption;
  if (caption.figure_caption !== null) captionMetadata["figure_caption"] = caption.figure_caption;

  sections.push({
    text: pyStrip(`[${caption.type}] ${caption.caption}`),
    page: null,
    section_title: `이미지 분류: ${caption.type}`,
    bbox: null,
    metadata: { ...captionMetadata },
  });

  const ocrClean = pyStrip(caption.ocr_text);
  if (ocrClean !== "") {
    sections.push({
      text: ocrClean,
      page: null,
      section_title: "OCR 텍스트",
      bbox: null,
      metadata: { ...captionMetadata },
    });
  }

  const actionItems = extractActionItems(caption.structured);
  if (actionItems.length > 0) {
    sections.push({
      text: actionItems.map((i) => `- ${i}`).join("\n"),
      page: null,
      section_title: "액션 아이템",
      bbox: null,
      metadata: { ...captionMetadata },
    });
  }

  return {
    // 원본은 `self.source_type` — 호출자가 넘긴 source_type 이 아니라 항상 'image' 다.
    // (호출자의 'pdf_vision_enrich' 는 vision_usage_log 용이지 결과 타입이 아니다.)
    source_type: "image",
    sections,
    raw_text: sections.map((s) => s.text).join("\n\n"),
    warnings: opts.warnings,
    metadata: { vision_type: caption.type }, // content_gate 의 메신저대화 감지용
  };
}
