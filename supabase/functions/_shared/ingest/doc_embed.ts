/**
 * `ingest/stages/doc_embed.py` 포팅 — 문서 하나를 대표하는 1024 차원 벡터.
 *
 * `documents.doc_embedding` 에 저장한다. 문서 단위 검색 가산과 `dedup` 의 Tier 2/3
 * 판정이 이 값을 쓴다.
 *
 * ## 소스 우선순위
 * 1. `summary` (+ `implications`) — `tag_summarize` 가 성공한 경우
 * 2. `raw_text` 앞 3,000 자 — 요약이 없을 때
 * 3. 둘 다 없으면 **건너뛴다**(스테이지는 성공, 벡터는 NULL)
 *
 * ## NFC 정규화가 있다
 * HWP/HWPX 의 `raw_text` 가 NFD 로 나오는 경우가 있는데, 청크는 NFC 로 저장된다.
 * 정규화를 안 하면 같은 문서인데 문서 벡터와 청크 벡터의 분포가 어긋난다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { embedBatch, type EmbedDeps } from "./embed_provider.ts";
import { pyStrip } from "../search/pystr.ts";

const RAW_FALLBACK_CHARS = 3000;

/** Python 슬라이스는 코드포인트 단위다. */
function cpSlice(s: string, end: number): string {
  let out = "";
  let n = 0;
  for (const ch of s) {
    if (n >= end) break;
    out += ch;
    n++;
  }
  return out;
}

/**
 * 원본 `_pick_source`.
 *
 * `summary` 가 있으면 `implications` 를 붙여 쓰고, 없으면 본문 앞부분을 쓴다.
 * **자르고 나서 strip 한다** — 순서가 바뀌면 3,000 자 경계가 달라진다.
 */
export function pickSource(opts: {
  summary?: string | null;
  implications?: string | null;
  rawText: string;
}): string | null {
  const { summary, implications, rawText } = opts;
  if (summary && pyStrip(summary) !== "") {
    const parts = [pyStrip(summary)];
    if (implications && pyStrip(implications) !== "") parts.push(pyStrip(implications));
    return parts.join("\n\n").normalize("NFC");
  }
  if (rawText && pyStrip(rawText) !== "") {
    return pyStrip(cpSlice(rawText, RAW_FALLBACK_CHARS)).normalize("NFC");
  }
  return null;
}

export interface DocEmbedDeps {
  client: SupabaseClient;
  embedDeps: EmbedDeps;
  /** 테스트 주입 — 실제 임베딩 API 를 안 부른다. */
  embed?: (texts: string[]) => Promise<number[][]>;
}

/**
 * 원본 `run_doc_embed_stage` — 벡터를 채웠으면 `true`.
 *
 * 반환값이 `dedup` 실행 여부를 정한다(원본 `if doc_embedded`).
 */
export async function runDocEmbedStage(
  deps: DocEmbedDeps,
  docId: string,
  rawText: string,
): Promise<boolean> {
  const { data, error } = await deps.client
    .from("documents")
    .select("summary, implications")
    .eq("id", docId)
    .limit(1);
  if (error) throw new Error(`documents 조회 실패: ${error.message}`);
  const row = (data ?? [])[0] as
    | { summary?: string | null; implications?: string | null }
    | undefined;
  // 원본은 `.data[0]` 을 그냥 인덱싱한다 — 행이 없으면 IndexError 로 스테이지가 실패한다.
  if (!row) throw new Error(`documents 레코드 없음: ${docId}`);

  const source = pickSource({
    summary: row.summary,
    implications: row.implications,
    rawText,
  });
  if (!source) {
    console.info(`doc_embed: doc=${docId} 소스 텍스트 없음 → 스킵`);
    return false;
  }

  const run = deps.embed ?? ((texts: string[]) => embedBatch(texts, deps.embedDeps));
  const vectors = await run([source]);
  if (vectors.length !== 1) {
    throw new Error(`임베딩 개수 불일치: got=${vectors.length}, expect=1`);
  }

  const { error: upErr } = await deps.client
    .from("documents")
    .update({ doc_embedding: vectors[0] })
    .eq("id", docId);
  if (upErr) throw new Error(`doc_embedding 저장 실패: ${upErr.message}`);
  return true;
}
