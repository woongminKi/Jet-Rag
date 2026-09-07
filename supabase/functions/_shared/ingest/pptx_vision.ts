/**
 * `PptxParser._vision_ocr_largest_picture` 포팅 — 텍스트가 거의 없는 슬라이드를 Vision 으로 읽는다.
 *
 * ## 이게 없으면 문서가 통째로 빈다
 * 실측(2026-09-07): 코퍼스의 `브랜딩_스튜디오앤드오어.pptx` 는 **11 장 전부 텍스트 0 자 +
 * 그림 다수**다. `extractPptx` 는 텍스트 0 슬라이드를 버리므로 Edge 만으로는 **0 청크**가
 * 된다. Railway 가 넣어 둔 실제 값은 **5 청크**이고 제목이 전부 `p.N (Vision OCR)` 이다 —
 * 즉 이 경로로 만들어진 것이다. 안 옮기면 재인제스트가 그 문서를 지운다.
 *
 * ## 두 모드가 있다
 * | 슬라이드 텍스트 | 모드 | `vision_usage_log.source_type` | 결과 |
 * |---|---|---|---|
 * | 0 자 | rerouting | `pptx_rerouting` | OCR 만 사용, 제목 없으면 `p.N (Vision OCR)` |
 * | 1~49 자 | augment | `pptx_augment` | 기존 텍스트 **뒤에** OCR 을 붙인다 |
 * | 50 자 이상 | — | — | 부르지 않는다 |
 *
 * ## 상한은 "시도" 기준이다
 * `MAX_VISION_SLIDES = 5` 는 **성공이 아니라 시도**를 센다(원본 W9 Day 3 에서 고친 것).
 * 성공만 세면 실패할 때마다 다음 슬라이드로 넘어가 결국 전 슬라이드를 부른다.
 *
 * ## quota 를 만나면 즉시 멈춘다
 * 한 번 `RESOURCE_EXHAUSTED` 를 보면 남은 슬라이드는 호출조차 하지 않는다.
 *
 * ## CPU 는 재 보고 한 태스크에 넣었다
 * 실측(같은 파일, 상위 5 장): 정규화 합계 **432ms**. `extract` 의 2s 예산 안이라
 * `scan` 처럼 창을 나누지 않는다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { pyStrip } from "../search/pystr.ts";
import { extractPptxSlides } from "../ooxml_text.ts";
import { pyStrError } from "../pyerror.ts";
import type { ExtractedSection } from "./hwp_extract.ts";
import { parseImage } from "./image_parser.ts";
import { isQuotaExhausted } from "./quota_detect.ts";

/** 원본 `_MAX_VISION_SLIDES`. **시도** 기준이다. */
export const MAX_VISION_SLIDES = 5;
/** 원본 `_VISION_AUGMENT_TEXT_THRESHOLD`. 이 길이 미만이면 Vision 을 부른다. */
export const VISION_AUGMENT_TEXT_THRESHOLD = 50;

export interface PictureRef {
  /** `<a:blip r:embed="rIdN">` 의 rId. */
  rid: string;
  /** `width * height` (EMU). 원본 `_picture_area` 와 같은 기준이다. */
  area: number;
}

/** `_rels` XML → `rId` → target 경로. */
export function parseRels(xml: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const m of xml.matchAll(/<Relationship\b([^>]*)>/g)) {
    const attrs = m[1];
    const id = attrs.match(/\bId="([^"]*)"/)?.[1];
    const target = attrs.match(/\bTarget="([^"]*)"/)?.[1];
    if (id && target) out.set(id, target);
  }
  return out;
}

/** `../media/image1.png` → `ppt/media/image1.png`. 슬라이드 기준 상대경로다. */
export function resolveTarget(slidePath: string, target: string): string {
  if (/^https?:/i.test(target)) return ""; // 외부 링크 그림은 바이트가 없다
  const base = slidePath.replace(/\/[^/]*$/, ""); // ppt/slides
  const segs = base.split("/");
  for (const seg of target.split("/")) {
    if (seg === "..") segs.pop();
    else if (seg !== "." && seg !== "") segs.push(seg);
  }
  return segs.join("/");
}

/**
 * 원본 `_collect_pictures` — `<p:grpSp>` 안쪽까지 재귀로 그림을 모은다.
 *
 * python-pptx 는 `.image` 속성 유무로 Picture 를 가리는데, XML 에서는 `<p:pic>` 이
 * 그 자리다. 그룹은 `<p:grpSp>` 안에 다시 `<p:pic>` 을 갖는다 — `<p:pic` 를 그냥
 * 전부 훑으면 그룹 안쪽도 자연히 포함된다(원본 재귀와 결과가 같다).
 */
export function collectPictures(treeInner: string): PictureRef[] {
  const out: PictureRef[] = [];
  // `<p:pic` 로 잘라 각 조각의 첫 blip 과 첫 a:ext 를 본다.
  const chunks = treeInner.split(/<p:pic[\s>]/);
  for (const chunk of chunks.slice(1)) {
    const rid = chunk.match(/<a:blip\b[^>]*r:embed="([^"]+)"/)?.[1];
    if (!rid) continue;
    // `<a:xfrm>` 안의 `<a:ext cx cy>`. 없으면 0 — 원본도 `or 0` 이다.
    const ext = chunk.match(/<a:ext\b[^>]*\bcx="(\d+)"[^>]*\bcy="(\d+)"/);
    out.push({ rid, area: ext ? Number(ext[1]) * Number(ext[2]) : 0 });
  }
  return out;
}

/**
 * 가장 큰 그림 하나. **동점이면 앞의 것**이다 — Python `max()` 가 그렇다.
 * (`>=` 로 쓰면 뒤의 것이 뽑혀 원본과 다른 그림을 읽는다.)
 */
export function largestPicture(pics: PictureRef[]): PictureRef | null {
  let best: PictureRef | null = null;
  for (const p of pics) {
    if (best === null || p.area > best.area) best = p;
  }
  return best;
}

export interface PptxVisionDeps {
  client: SupabaseClient;
  env: Record<string, string | undefined>;
  geminiApiKey: string;
  nowMs: number;
  docId?: string | null;
  /** 테스트 주입 — Gemini 를 부르지 않는다. */
  caption?: Parameters<typeof parseImage>[0]["caption"];
  /**
   * 테스트 주입 — `parseImage` 자체를 갈아끼운다.
   *
   * 대조에서 필요하다: `caption` 만 바꾸면 캡셔너가 **정규화된** 바이트를 보게 되어
   * 이미지 정규화 차이(§45.3, 별도로 163건 대조함)가 섞인다. 이 포팅이 책임지는 건
   * **어느 그림을 골랐는가**이므로, 원본 blob 을 그대로 볼 수 있는 자리가 필요하다.
   */
  parseImageFn?: (blob: Uint8Array, pseudoName: string, sourceType: string) => Promise<
    { result: { raw_text: string }; metricErrors: string[] }
  >;
}

export interface SlideOcr {
  /** OCR 텍스트. 그림이 없거나 실패하면 `null`. */
  text: string | null;
  quotaExhausted: boolean;
  metricErrors: string[];
}

/** 원본 `_vision_ocr_largest_picture` — 슬라이드 1 장. **예외를 던지지 않는다.** */
export async function visionOcrLargestPicture(
  deps: PptxVisionDeps,
  opts: {
    files: Record<string, Uint8Array>;
    slidePath: string;
    treeInner: string;
    slideIdx: number; // 0-based
    fileName: string;
    warnings: string[];
    sourceType: string;
  },
): Promise<SlideOcr> {
  const { files, slidePath, slideIdx, fileName, warnings } = opts;
  const pics = collectPictures(opts.treeInner);
  if (pics.length === 0) return { text: null, quotaExhausted: false, metricErrors: [] };

  const largest = largestPicture(pics)!;
  const relsPath = slidePath.replace(/\/([^/]+)$/, "/_rels/$1.rels");
  const relsBytes = files[relsPath];
  const rels = relsBytes ? parseRels(new TextDecoder().decode(relsBytes)) : new Map();
  const target = rels.get(largest.rid);
  const mediaPath = target ? resolveTarget(slidePath, target) : "";
  const blob = mediaPath ? files[mediaPath] : undefined;
  if (!blob) {
    // 원본의 `image.blob` 예외 자리 — 경로를 못 풀거나 바이트가 없다.
    warnings.push(
      `PPTX slide ${slideIdx + 1} picture blob 추출 실패: ${largest.rid} → ${target ?? "(rels 없음)"}`,
    );
    return { text: null, quotaExhausted: false, metricErrors: [] };
  }

  const dot = mediaPath.lastIndexOf(".");
  const ext = (dot >= 0 ? mediaPath.slice(dot + 1) : "png").toLowerCase();
  // 원본과 같은 가짜 파일명 — `parseImage` 가 확장자로 mime 을 정한다.
  const pseudoName = `${fileName}#slide${slideIdx + 1}.${ext}`;

  try {
    const r = deps.parseImageFn
      ? await deps.parseImageFn(blob, pseudoName, opts.sourceType)
      : await parseImage({
        client: deps.client,
        env: deps.env,
        geminiApiKey: deps.geminiApiKey,
        nowMs: deps.nowMs,
        caption: deps.caption,
        sourceType: opts.sourceType,
      }, { data: blob, fileName: pseudoName, docId: deps.docId ?? null });
    const text = pyStrip(r.result.raw_text ?? "");
    return { text: text === "" ? null : text, quotaExhausted: false, metricErrors: r.metricErrors };
  } catch (e) {
    // `${e}` 를 그냥 쓰면 JS 가 "Error: " 를 앞에 붙인다 — 이 문자열은 문서의
    // `warnings` 로 저장돼 사용자에게 보인다. Python `str(exc)` 는 메시지만 준다.
    warnings.push(`PPTX slide ${slideIdx + 1} Vision OCR 실패 (graceful): ${pyStrError(e)}`);
    console.warn(`PPTX Vision OCR 실패 (file=${fileName} slide=${slideIdx + 1}): ${e}`);
    // quota 면 호출자가 남은 슬라이드를 건너뛴다.
    return { text: null, quotaExhausted: isQuotaExhausted(e), metricErrors: [] };
  }
}

/** 텍스트 조각 길이 합 — Python `sum(len(p) for p in parts)` 는 **코드포인트** 기준이다. */
function cpLenSum(parts: string[]): number {
  let n = 0;
  for (const p of parts) for (const _ of p) n++;
  return n;
}

export interface PptxVisionResult {
  sections: ExtractedSection[];
  rawParts: string[];
  warnings: string[];
  /** 시도/성공 — 로그용. 원본도 둘을 따로 센다. */
  attempted: number;
  succeeded: number;
  metricErrors: string[];
}

/**
 * 원본 `PptxParser.parse` 의 슬라이드 루프 — Vision 보강 포함.
 *
 * 순서가 계약이다:
 * 1. 텍스트 추출 → 길이 합
 * 2. `needsOcr` 판정 (image_parser 있음 · quota 안 걸림 · 시도 < 5 · 길이 < 50)
 * 3. **판정되면 시도 카운트를 먼저 올린다** — 결과와 무관하다(quota 보호)
 * 4. OCR 성공 시 rerouting(대체) 또는 augment(뒤에 붙임)
 * 5. 텍스트가 여전히 0 이면 그 슬라이드는 버린다
 */
export async function extractPptxWithVision(
  bytes: Uint8Array,
  fileName: string,
  deps: PptxVisionDeps | null,
): Promise<PptxVisionResult> {
  const { files, slides } = extractPptxSlides(bytes);
  const sections: ExtractedSection[] = [];
  const rawParts: string[] = [];
  const warnings: string[] = [];
  const metricErrors: string[] = [];
  let attempted = 0;
  let succeeded = 0;
  let quotaExhausted = false;

  for (const slide of slides) {
    let title = slide.title;
    let parts = slide.parts;
    const textLen = cpLenSum(parts);

    const needsOcr = deps !== null && !quotaExhausted &&
      attempted < MAX_VISION_SLIDES && textLen < VISION_AUGMENT_TEXT_THRESHOLD;

    if (needsOcr) {
      // 상한은 **시도** 기준 — 결과를 보기 전에 올린다.
      attempted++;
      const sourceType = textLen === 0 ? "pptx_rerouting" : "pptx_augment";
      const ocr = await visionOcrLargestPicture(deps, {
        files,
        slidePath: slide.slidePath,
        treeInner: slide.treeInner,
        slideIdx: slide.page - 1,
        fileName,
        warnings,
        sourceType,
      });
      metricErrors.push(...ocr.metricErrors);
      if (ocr.quotaExhausted) {
        quotaExhausted = true;
        warnings.push(`PPTX Vision quota 감지 — slide ${slide.page} 이후 skip`);
      }
      if (ocr.text) {
        succeeded++;
        if (parts.length === 0) {
          // rerouting — 텍스트가 아예 없던 슬라이드
          if (!title) title = `p.${slide.page} (Vision OCR)`;
          parts = [ocr.text];
        } else {
          // augment — 기존 텍스트 **뒤에** 붙인다
          parts = [...parts, ocr.text];
        }
      }
    }

    if (parts.length === 0) continue;
    const text = parts.join("\n");
    sections.push({ text, page: slide.page, section_title: title, bbox: null, metadata: {} });
    rawParts.push(text);
  }

  if (attempted > 0) {
    console.info(
      `PPTX Vision OCR: attempted=${attempted} success=${succeeded} ` +
        `(file=${fileName}, cap=${MAX_VISION_SLIDES})`,
    );
  }
  return { sections, rawParts, warnings, attempted, succeeded, metricErrors };
}
