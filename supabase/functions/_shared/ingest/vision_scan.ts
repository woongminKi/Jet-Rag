/**
 * `_reroute_pdf_to_image` 포팅 — 텍스트 레이어가 없는 **스캔 PDF** 를 vision 으로 읽는다.
 *
 * PyMuPDF 가 글자를 거의 못 뽑으면(`raw_text.strip()` 이 50 자 이하) 그건 이미지로
 * 스캔한 PDF 다. 페이지를 그림으로 구워 Gemini 에 통째로 맡긴다.
 *
 * ## vision enrich 와 규칙이 다르다 — 같은 기계를 쓰지만 정책이 다르다
 * | | enrich (`vision_enrich.ts`) | scan (여기) |
 * |---|---|---|
 * | 페이지 상한 | 50 | **5** (`MAX_SCAN_PAGES`) |
 * | sweep 재시도 | 2 회 | **없다** — 한 번 실패하면 그 페이지는 버린다 |
 * | `needs_vision` 판정 | 한다 | **안 한다** — 전 페이지 호출 |
 * | 비용·페이지 cap | 검사한다 | **안 한다** |
 * | `vision_page_cache` | 쓴다 | **안 쓴다** (원본이 `sha256` 을 안 넘긴다) |
 * | 섹션 제목 | `(vision) p.N …` | `p.N …` |
 * | `metadata` | 승계한다 | **버린다** (원본이 `ExtractedSection` 에 안 넘긴다) |
 * | `source_type` | `pdf_vision_enrich` | `pdf_scan` |
 *
 * 그래서 `runVisionWindow` 를 재사용하지 않았다. 인자로 끄고 켤 수는 있지만 경고
 * 문구까지 갈려서, 껍데기만 같고 속이 다른 함수가 된다.
 *
 * ## 캐시를 안 쓰는 게 손해 아닌가
 * 손해다 — 같은 스캔 PDF 를 다시 올리면 5 페이지를 다시 부른다. 그래도 원본을 따랐다.
 * 캐시 키는 `(sha256, page, prompt_version)` 이고 enrich 가 이미 그 키로 쓰고 있어서,
 * 스캔 경로가 끼어들면 **같은 키에 다른 정책의 결과**가 섞인다. 바꾸려면 원본부터
 * 바꿔야 한다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import type { ExtractedSection } from "./hwp_extract.ts";
import { composeResult } from "./image_parser.ts";
import { renderPageForVision, SCAN_RENDER_DPI } from "./pdf_raster.ts";
import { pyStrError } from "../pyerror.ts";
import { pyStrip } from "../search/pystr.ts";
import type { VisionCaption } from "./vision_caption.ts";
import { captionImage, type VisionClientDeps } from "./vision_client.ts";
import { recordCall } from "./vision_metrics.ts";

/** 원본 `_SCAN_TEXT_THRESHOLD`. */
export const SCAN_TEXT_THRESHOLD = 50;
/** 원본 `_MAX_SCAN_PAGES` — Vision 비용 cap. 5 페이지 ≈ 50 초. */
export const MAX_SCAN_PAGES = 5;

/** Python `len()` 은 코드포인트 수다. */
function cpLen(s: string): number {
  let n = 0;
  for (const _ of s) n++;
  return n;
}

/**
 * 원본 `_is_scan_pdf` — `raw_text.strip()` 이 임계 이하면 스캔으로 본다.
 *
 * **문서 전체**의 `raw_text` 다. 창 하나만 보고 판단할 수 없다.
 */
export function isScanPdf(rawText: string): boolean {
  return cpLen(pyStrip(rawText)) <= SCAN_TEXT_THRESHOLD;
}

export interface ScanWindowResult {
  sections: ExtractedSection[];
  rawParts: string[];
  warnings: string[];
  /** `vision_usage_log` 적재 실패 사유. 비어 있어야 정상이다. */
  metricErrors: string[];
  calledCount: number;
}

export interface ScanRunDeps {
  client: SupabaseClient;
  env: Record<string, string | undefined>;
  geminiApiKey: string;
  nowMs: number;
  /** 테스트 주입 — 실제 Gemini 호출 대신. */
  caption?: (bytes: Uint8Array, mimeType: string) => Promise<VisionCaption>;
  /** 테스트 주입 — 실제 mupdf 대신. */
  mupdf?: unknown;
}

/**
 * 페이지 목록(0-based)을 vision 으로 읽는다.
 *
 * 페이지 하나가 죽어도 문서는 살린다 — 원본과 같이 경고만 남기고 넘어간다.
 * **재시도는 없다.**
 */
export async function runScanWindow(
  deps: ScanRunDeps,
  opts: {
    bytes: Uint8Array;
    docId: string;
    fileName: string;
    pages: number[];
  },
): Promise<ScanWindowResult> {
  const sections: ExtractedSection[] = [];
  const rawParts: string[] = [];
  const warnings: string[] = [];
  const metricErrors: string[] = [];
  let calledCount = 0;

  // deno-lint-ignore no-explicit-any
  const mupdf = (deps.mupdf ?? await import("mupdf")) as any;
  // 원본은 여기서 열기에 실패하면 **던진다**(enrich 는 graceful 이다). 스캔 경로는
  // 이 결과가 문서의 전부라, 조용히 빈 결과를 내면 문서가 통째로 사라진다.
  let doc;
  try {
    doc = mupdf.Document.openDocument(opts.bytes, "application/pdf");
  } catch (e) {
    throw new Error(`스캔 PDF rerouting: PDF 열기 실패: ${opts.fileName}: ${e}`);
  }

  const clientDeps: VisionClientDeps = { apiKey: deps.geminiApiKey, env: deps.env };
  try {
    for (const pageIdx of opts.pages) {
      try {
        const img = renderPageForVision(mupdf, doc, pageIdx, SCAN_RENDER_DPI);
        calledCount += 1;
        let caption: VisionCaption;
        try {
          caption = deps.caption
            ? await deps.caption(img.jpeg, img.mimeType)
            : await captionImage(clientDeps, img.jpeg, img.mimeType);
        } catch (e) {
          const me = await recordCall(deps.client, deps.env, deps.nowMs, {
            success: false,
            errorMsg: pyStrError(e),
            sourceType: "pdf_scan",
            docId: opts.docId,
            page: pageIdx + 1,
            retryAttempt: (e as { retryAttempt?: number })?.retryAttempt ?? null,
          });
          if (me !== null) metricErrors.push(`p${pageIdx + 1} 실패기록: ${me}`);
          throw e;
        }
        const me = await recordCall(deps.client, deps.env, deps.nowMs, {
          success: true,
          sourceType: "pdf_scan",
          usage: caption.usage as unknown as Record<string, unknown> | null,
          docId: opts.docId,
          page: pageIdx + 1,
          retryAttempt: caption.usage?.retry_attempt ?? null,
        });
        if (me !== null) metricErrors.push(`p${pageIdx + 1}: ${me}`);

        const pageResult = composeResult(caption, { warnings: [] });
        for (const sec of pageResult.sections) {
          // 원본은 `sec.section_title or ""` 로 받아 **strip 하지 않고** 합친 뒤
          // 전체를 strip 한다. enrich 는 base 를 먼저 strip 한다 — 다르다.
          const baseTitle = sec.section_title ?? "";
          sections.push({
            text: sec.text,
            page: pageIdx + 1,
            section_title: baseTitle !== "" ? pyStrip(`p.${pageIdx + 1} ${baseTitle}`) : `p.${pageIdx + 1}`,
            bbox: null,
            // **metadata 를 안 넘긴다.** 원본이 `ExtractedSection(...)` 호출에서
            // 빼먹었고(enrich 는 넘긴다), 그 결과 caption 메타가 여기서는 사라진다.
            metadata: {},
          });
        }
        if (pageResult.raw_text) rawParts.push(pageResult.raw_text);
        warnings.push(...pageResult.warnings);
      } catch (e) {
        const msg = `page ${pageIdx + 1} 스캔 fallback 실패: ${e}`;
        warnings.push(msg);
        console.warn(`${msg} (file=${opts.fileName})`);
      }
    }
  } finally {
    doc.destroy?.();
  }

  return { sections, rawParts, warnings, metricErrors, calledCount };
}
