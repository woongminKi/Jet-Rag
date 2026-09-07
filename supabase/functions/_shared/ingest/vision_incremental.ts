/**
 * `api/app/ingest/incremental.py` 의 조각들 — 증분 vision 재인제스트 전용.
 *
 * 전체 재인제스트는 chunks 를 **전부 지우고** 다시 만든다. 그 방식은 vision 이 503 으로
 * 한 페이지만 실패해도 그 페이지의 답이 통째로 사라진다(Sprint 4 에서 chunks 0 사태까지
 * 났다). 증분은 **기존 chunks 를 그대로 두고 누락 페이지만 덧붙인다.**
 *
 * 그래서 `chunk_filter` / `content_gate` / `tag_summarize` / `doc_embed` / `dedup` 을
 * 부르지 않는다 — 이미 적용된 메타를 다시 계산하면 보존이 아니게 된다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { type ChunkEnv, type ChunkRecord, composeVisionText } from "./chunk_records.ts";
import type { ExtractedSection } from "./hwp_extract.ts";

/** 원본 `_VISION_ENRICH_TITLE_PREFIX`. `chunk_records` 의 `"(vision)"` 보다 좁다. */
export const VISION_ENRICH_TITLE_PREFIX = "(vision) p.";

/** Python `len()` 은 코드포인트 수다. `char_range` 에 그대로 들어간다. */
function cpLen(s: string): number {
  let n = 0;
  for (const _ of s) n++;
  return n;
}

/**
 * 원본 `_vision_processed_pages` — 이미 vision 이 처리된 페이지 집합.
 *
 * `section_title` 이 `(vision) p.` 로 시작하고 `page` 가 **truthy** 인 청크만 센다.
 * Python 의 `if r.get("page")` 라 **`page = 0` 은 제외된다** — 페이지는 1-based 라
 * 실제로 0 이 나올 일은 없지만, 그대로 옮긴다.
 */
export async function visionProcessedPages(
  client: SupabaseClient,
  docId: string,
): Promise<Set<number>> {
  const { data, error } = await client
    .from("chunks")
    .select("page,section_title")
    .eq("doc_id", docId);
  if (error) throw new Error(`chunks 조회 실패: ${error.message}`);
  const pages = new Set<number>();
  for (const r of (data ?? []) as { page?: unknown; section_title?: unknown }[]) {
    const title = typeof r.section_title === "string" ? r.section_title : "";
    if (title.startsWith(VISION_ENRICH_TITLE_PREFIX) && r.page) {
      pages.add(Math.trunc(Number(r.page)));
    }
  }
  return pages;
}

/** 원본 `_max_chunk_idx` — 없으면 `-1`. 새 청크는 여기 +1 부터 붙는다. */
export async function maxChunkIdx(
  client: SupabaseClient,
  docId: string,
): Promise<number> {
  const { data, error } = await client
    .from("chunks")
    .select("chunk_idx")
    .eq("doc_id", docId)
    .order("chunk_idx", { ascending: false })
    .limit(1);
  if (error) throw new Error(`chunk_idx 조회 실패: ${error.message}`);
  const rows = (data ?? []) as { chunk_idx: number }[];
  return rows.length > 0 ? Math.trunc(Number(rows[0].chunk_idx)) : -1;
}

/**
 * 원본 `_sections_to_chunks` — vision 섹션 → `ChunkRecord`.
 *
 * `dense_vec` 은 안 채운다. `embed` 단계가 `dense_vec IS NULL` 을 보고 채운다.
 *
 * ## 전체 경로(`toChunkRecords`)와 다른 점 — 일부러 다르다
 * - **NFC 정규화를 안 한다.** 원본 `_sections_to_chunks` 에 그 호출이 없다.
 * - **섹션 병합·재분할이 없다.** 페이지당 vision 섹션이 그대로 청크 하나가 된다.
 * - `metadata.vision_incremental = true` 가 붙는다. `isVisionDerived` 가 이걸 본다.
 */
export function sectionsToChunks(
  sections: ExtractedSection[],
  opts: {
    docId: string;
    startChunkIdx: number;
    env: ChunkEnv;
    /** 동의어 주입. 원본은 ENV 가 꺼져 있으면 후보가 빈 배열이라 아무 일도 안 한다. */
    injectSynonyms?: (
      text: string,
    ) => { text: string; candidates: string[] } | null;
  },
): ChunkRecord[] {
  const out: ChunkRecord[] = [];
  for (let offset = 0; offset < sections.length; offset++) {
    const sec = sections[offset];
    const metadata: Record<string, unknown> = { vision_incremental: true };
    const tableCaption = (sec.metadata?.["table_caption"] ?? null) as string | null;
    const figureCaption = (sec.metadata?.["figure_caption"] ?? null) as string | null;
    if (tableCaption !== null && tableCaption !== undefined) {
      metadata["table_caption"] = tableCaption;
    }
    if (figureCaption !== null && figureCaption !== undefined) {
      metadata["figure_caption"] = figureCaption;
    }

    let synthesized = composeVisionText(sec.text, {
      tableCaption: tableCaption ?? null,
      figureCaption: figureCaption ?? null,
      page: sec.page,
      captionPrefixEnabled: opts.env.captionPrefixEnabled,
    });

    // 원본은 이 블록 전체를 `try/except: pass` 로 감싼다 — 동의어 때문에 청크 저장이
    // 막히면 안 되기 때문이다. 그 정책을 그대로 옮긴다.
    if (opts.env.synonymInjectionEnabled) {
      try {
        const injected = opts.injectSynonyms?.(synthesized) ?? notPorted();
        if (injected && injected.candidates.length > 0) {
          synthesized = injected.text;
          metadata["synonym_candidates"] = injected.candidates;
          metadata["synonym_source"] = "dict";
        }
      } catch {
        // 원본과 같이 삼킨다.
      }
    }

    out.push({
      doc_id: opts.docId,
      chunk_idx: opts.startChunkIdx + offset,
      text: synthesized,
      page: sec.page,
      section_title: sec.section_title,
      bbox: sec.bbox,
      char_range: [0, cpLen(synthesized)],
      metadata,
    });
  }
  return out;
}

function notPorted(): never {
  throw new Error(
    "JETRAG_SYNONYM_INJECTION_ENABLED=true 인데 synonym_inject 는 아직 Edge 로 " +
      "안 옮겼다(조각 c2).",
  );
}
