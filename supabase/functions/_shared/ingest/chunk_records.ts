/**
 * 청크 레코드 조립 — `chunk.py` 의 `_split_long_sections` · vision caption 합성 ·
 * `_to_chunk_records` · `run_chunk_stage` 포팅.
 *
 * ## caption ENV 는 실제로 켜진 적이 있다
 * `JETRAG_CAPTION_PREFIX_ENABLED` 는 default false 지만 **운영 DB 에 ON 흔적이 있다** —
 * `[표 p.N: ` 로 시작하는 chunk 101 행, `[그림 p.N: ` 37 행, 전부 2026-05-13 하루치
 * (문서 3 개). 그 뒤 2026-05-14 대량 인제스트(34,000 행 이상)와 2026-07-07 최신 건은
 * 0 행이라 **지금은 OFF** 다. 그래도 양쪽 경로를 다 옮긴다 — ENV 하나로 결과가 갈리는
 * 코드에서 한쪽만 옮기면 나중에 조용히 다르게 동작한다.
 *
 * ## NFC 정규화가 두 번 걸린다
 * 합성 후 한 번, synonym 마커 주입 후 또 한 번. HWP/HWPX 는 파서가 NFD 로 뱉는 경향이
 * 있어 이게 sparse(PGroonga Mecab) 매칭을 좌우한다. JS `normalize("NFC")` 와 Python
 * `unicodedata.normalize("NFC", ...)` 는 같은 UAX#15 라 일치한다.
 *
 * ## `char_range` 는 코드포인트
 * `(0, len(text_nfc))` 의 `len()` 이 Python 코드포인트 길이다.
 */

import { splitBySentence } from "./chunk_split.ts";
import { MAX_SIZE, mergeShortSections } from "./chunk_merge.ts";
import { entitiesEmpty, extractEntities } from "./entity_extract.ts";
import type { ExtractedSection } from "./hwp_extract.ts";

/** `_VISION_TITLE_PREFIX` · `_CAPTION_PREFIX_MAX_LEN`. */
const VISION_TITLE_PREFIX = "(vision)";
const CAPTION_PREFIX_MAX_LEN = 200;

export interface ChunkRecord {
  doc_id: string;
  chunk_idx: number;
  text: string;
  page: number | null;
  section_title: string | null;
  bbox: [number, number, number, number] | null;
  char_range: [number, number];
  metadata: Record<string, unknown>;
  /**
   * `chunks.flags` JSONB. `chunk_filter` 가 `{filtered_reason}` 를 넣으면
   * `search_hybrid_rrf` 의 `WHERE flags->>'filtered_reason' IS NULL` 이 그 청크를
   * 검색에서 뺀다. 청크를 만드는 단계에서는 안 채운다 — 그래서 선택 필드다.
   */
  flags?: Record<string, unknown>;
}

/** 인제스트 시점 ENV. 원본이 **함수 호출마다** 평가하므로 여기서도 매번 읽는다. */
export interface ChunkEnv {
  captionPrefixEnabled: boolean;
  synonymInjectionEnabled: boolean;
  synonymLlmEnabled: boolean;
}

/** 원본 `_env_true` — `true/1/yes/on` 만 참(caption). synonym 쪽은 `true` 만. */
function envTrueSet(v: string | undefined): boolean {
  return ["true", "1", "yes", "on"].includes((v ?? "false").trim().toLowerCase());
}
function envTrueStrict(v: string | undefined): boolean {
  return (v ?? "false").trim().toLowerCase() === "true";
}

export function readChunkEnv(get = Deno.env.get): ChunkEnv {
  return {
    // `_caption_prefix_enabled` 는 {true,1,yes,on} 을 받는다.
    captionPrefixEnabled: envTrueSet(get("JETRAG_CAPTION_PREFIX_ENABLED")),
    // `synonym_inject._env_true` 는 `== "true"` 뿐 — "1"/"yes" 는 거짓이다. 다르다.
    synonymInjectionEnabled: envTrueStrict(get("JETRAG_SYNONYM_INJECTION_ENABLED")),
    synonymLlmEnabled: envTrueStrict(get("JETRAG_SYNONYM_INJECTION_LLM")),
  };
}

function cpLen(s: string): number {
  let n = 0;
  for (const _ of s) n++;
  return n;
}
function cpSlice(s: string, start: number, end?: number): string {
  return [...s].slice(start, end).join("");
}

/**
 * vision 유래 섹션인지 — caption 주입·합성의 진입 조건.
 *
 * `vision_incremental` 은 운영 DB 에 **0 행**이지만(2026-09-07 실측) 코드 경로라 옮긴다.
 */
export function isVisionDerived(section: ExtractedSection): boolean {
  if (section.metadata?.["vision_incremental"]) return true;
  return (section.section_title ?? "").startsWith(VISION_TITLE_PREFIX);
}

/**
 * vision chunk 의 text 합성.
 *
 * - ENV ON: table 우선 1 개만, 200 자 초과 시 199 자 + `…`, **base 앞에** prefix
 * - ENV OFF: 양쪽 다 붙이고 **base 뒤에** suffix (`\n\n[표: ...]\n[그림: ...]`)
 *
 * ON 은 `.strip()` 후 빈 caption 을 버리지만 OFF 는 truthy 검사뿐이라 `" "` 도 붙인다 —
 * 원본의 비대칭을 그대로 둔다.
 */
export function composeVisionText(
  baseText: string,
  opts: {
    tableCaption: string | null;
    figureCaption: string | null;
    page?: number | null;
    captionPrefixEnabled: boolean;
  },
): string {
  const { tableCaption, figureCaption, page = null, captionPrefixEnabled } = opts;

  if (captionPrefixEnabled) {
    let caption: string;
    let marker: string;
    // `pyStrip` 이 아니라 `.strip()` — 여기서 갈릴 여지는 caption 양끝의 U+001C/U+FEFF
    // 뿐이고, 잘림 판정에도 쓰이므로 Python 규칙을 따른다.
    if (tableCaption && tableCaption.trim()) {
      caption = tableCaption.trim();
      marker = "표";
    } else if (figureCaption && figureCaption.trim()) {
      caption = figureCaption.trim();
      marker = "그림";
    } else {
      return baseText;
    }
    if (cpLen(caption) > CAPTION_PREFIX_MAX_LEN) {
      caption = cpSlice(caption, 0, CAPTION_PREFIX_MAX_LEN - 1) + "…";
    }
    const prefix = page !== null && page !== undefined
      ? `[${marker} p.${page}: ${caption}]`
      : `[${marker}: ${caption}]`;
    return `${prefix}\n\n${baseText}`;
  }

  const extras: string[] = [];
  if (tableCaption) extras.push(`[표: ${tableCaption}]`);
  if (figureCaption) extras.push(`[그림: ${figureCaption}]`);
  if (extras.length === 0) return baseText;
  return baseText + "\n\n" + extras.join("\n");
}

/** 2차 분할 — `MAX_SIZE` 초과 섹션만 문장 단위로 쪼갠다. bbox·metadata 는 승계. */
export function splitLongSections(sections: ExtractedSection[]): ExtractedSection[] {
  const out: ExtractedSection[] = [];
  for (const section of sections) {
    if (cpLen(section.text) <= MAX_SIZE) {
      out.push(section);
      continue;
    }
    for (const pieceText of splitBySentence(section.text)) {
      out.push({
        text: pieceText,
        page: section.page,
        section_title: section.section_title,
        bbox: section.bbox, // 분할 조각은 원 bbox 를 공유 (근사)
        metadata: { ...section.metadata }, // 복사 — 원본 dict 공유 금지
      });
    }
  }
  return out;
}

/**
 * synonym 마커 주입 훅. **아직 안 옮겼다(조각 c2).**
 *
 * ENV OFF 면 원본도 빈 후보 → text/metadata 무변경이라 여기서도 no-op 이면 정확하다.
 * ENV ON 이면 결과가 갈리므로 **조용히 넘어가지 않고 던진다** — 운영 DB 의 synonym
 * 주입 흔적 199 행은 전부 2026-05-13 하루치이고 그 뒤 인제스트에는 0 행이라, 지금
 * 켜져 있을 이유가 없다. 켜져 있다면 그게 사고다.
 */
export type SynonymInjector = (
  text: string,
  docLlmPairs: [string, string[]][] | null,
) => { text: string; candidates: string[] } | null;

const notPortedInjector: SynonymInjector = () => {
  throw new Error(
    "JETRAG_SYNONYM_INJECTION_ENABLED=true 인데 synonym_inject 는 아직 Edge 로 " +
      "안 옮겼다(조각 c2). 조용히 다른 청크를 만들지 않도록 여기서 멈춘다.",
  );
};

export function toChunkRecords(opts: {
  docId: string;
  sections: ExtractedSection[];
  env: ChunkEnv;
  docLlmPairs?: [string, string[]][] | null;
  injectSynonyms?: SynonymInjector;
}): ChunkRecord[] {
  const {
    docId,
    sections,
    env,
    docLlmPairs = null,
    injectSynonyms = notPortedInjector,
  } = opts;

  const records: ChunkRecord[] = [];
  for (let idx = 0; idx < sections.length; idx++) {
    const section = sections[idx];
    const metadata: Record<string, unknown> = {};
    if (idx > 0) {
      // 원본 TODO 그대로 — 정확히는 split 인접만 overlap 이지만 idx>0 에 일괄 표시.
      metadata["overlap_with_prev_chunk_idx"] = idx - 1;
    }

    let tableCaption: string | null = null;
    let figureCaption: string | null = null;
    if (isVisionDerived(section)) {
      // Python `.get()` 은 없는 키에 None. JS `undefined` 를 null 로 맞춘다 —
      // `is not None` 검사가 키 주입 여부를 가른다.
      tableCaption = (section.metadata?.["table_caption"] as string) ?? null;
      figureCaption = (section.metadata?.["figure_caption"] as string) ?? null;
      if (tableCaption !== null) metadata["table_caption"] = tableCaption;
      if (figureCaption !== null) metadata["figure_caption"] = figureCaption;
      if (section.metadata?.["vision_incremental"]) metadata["vision_incremental"] = true;
    }

    const synthesized = composeVisionText(section.text, {
      tableCaption,
      figureCaption,
      page: section.page,
      captionPrefixEnabled: env.captionPrefixEnabled,
    });

    let textNfc = synthesized.normalize("NFC");
    const titleNfc = section.section_title
      ? section.section_title.normalize("NFC")
      : section.section_title ?? null;

    // 원본은 `try/except Exception: pass` — 엔티티 추출 실패가 chunk 저장을 막지 않는다.
    try {
      const entities = extractEntities(textNfc);
      if (!entitiesEmpty(entities)) metadata["entities"] = entities;
    } catch {
      // 의도적 무시 (원본 계약)
    }

    if (env.synonymInjectionEnabled) {
      try {
        const injected = injectSynonyms(textNfc, docLlmPairs);
        if (injected && injected.candidates.length > 0) {
          textNfc = injected.text.normalize("NFC");
          metadata["synonym_candidates"] = injected.candidates;
          metadata["synonym_source"] = docLlmPairs ? "dict+llm" : "dict";
        }
      } catch (e) {
        // 원본은 여기서도 삼키지만, "아직 안 옮김" 은 삼키면 안 된다.
        if (injectSynonyms === notPortedInjector) throw e;
      }
    }

    records.push({
      doc_id: docId,
      chunk_idx: idx,
      text: textNfc,
      page: section.page,
      section_title: titleNfc,
      bbox: section.bbox,
      char_range: [0, cpLen(textNfc)],
      metadata,
    });
  }
  return records;
}

/** `run_chunk_stage` — split → merge → records. */
export function runChunkStage(opts: {
  docId: string;
  sections: ExtractedSection[];
  env?: ChunkEnv;
  docLlmPairs?: [string, string[]][] | null;
  injectSynonyms?: SynonymInjector;
}): ChunkRecord[] {
  const env = opts.env ?? readChunkEnv();
  const split = splitLongSections(opts.sections);
  const merged = mergeShortSections(split);
  return toChunkRecords({
    docId: opts.docId,
    sections: merged,
    env,
    docLlmPairs: opts.docLlmPairs ?? null,
    injectSynonyms: opts.injectSynonyms,
  });
}
