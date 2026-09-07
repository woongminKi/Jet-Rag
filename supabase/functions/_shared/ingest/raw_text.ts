/**
 * 창으로 나뉜 산출물에서 **문서 전체 `raw_text`** 를 되살린다.
 *
 * 원본은 파서가 문서를 통째로 읽어 `ExtractionResult.raw_text` 하나를 만든다.
 * `tag_summarize` · `doc_embed` 가 그 값을 받는다. Edge 는 창으로 나뉘어 있어 여기서
 * 다시 붙인다.
 *
 * ## 이어 붙이는 순서가 원본과 같아야 한다
 * `_enrich_pdf_with_vision` 은 `[base_result.raw_text] + [페이지별 vision raw_text…]` 를
 * `"\n\n"` 로 잇는다. 그래서 **extract 전부 → vision 전부** 순이다.
 * 스캔 PDF 는 결과를 통째로 갈아끼우므로 scan 만 쓴다.
 *
 * ## 빈 창은 빼야 한다
 * `raw_parts` 가 빈 창의 `raw_text` 는 `""` 다. 그대로 이으면 구분자 `"\n\n"` 가 하나 더
 * 끼어 길이가 밀린다 — `raw_part_count` 로 걸러낸다(§37.2 와 같은 이유).
 *
 * ## 필요한 만큼만 읽는다
 * `tag_summarize` 는 앞 12,000 자만 본다. 1,513 페이지 문서의 본문을 전부 끌어오면
 * 메모리가 위험하다. seq 순으로 조금씩 읽다가 목표 길이를 넘으면 멈춘다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

/** 한 번에 읽을 아티팩트 수. 창 하나가 보통 2 만 자쯤이라 3 이면 대개 한 번에 끝난다. */
const FETCH_BATCH = 3;

function cpLen(s: string): number {
  let n = 0;
  for (const _ of s) n++;
  return n;
}

async function fetchStage(
  client: SupabaseClient,
  jobId: string,
  stage: string,
  offset: number,
  limit: number,
): Promise<{ raw_text?: string | null; raw_part_count?: number }[]> {
  const { data, error } = await client
    .from("ingest_artifacts")
    .select("seq, payload->>raw_text, payload->raw_part_count")
    .eq("job_id", jobId)
    .eq("stage", stage)
    .order("seq", { ascending: true })
    .range(offset, offset + limit - 1);
  if (error) throw new Error(`${stage} 산출물 조회 실패: ${error.message}`);
  return (data ?? []) as { raw_text?: string | null; raw_part_count?: number }[];
}

/**
 * `maxChars` 코드포인트를 채울 때까지 읽어 이어 붙인 문자열.
 *
 * 반환값은 `maxChars` 보다 길 수 있다 — 호출자가 자른다(원본도 슬라이스를 따로 한다).
 */
export async function loadRawText(
  client: SupabaseClient,
  jobId: string,
  maxChars: number,
): Promise<string> {
  // 스캔 PDF 면 scan 이 extract 를 대체한다 — 있는지부터 본다.
  const scanHead = await fetchStage(client, jobId, "scan", 0, 1);
  const stages = scanHead.length > 0 ? ["scan"] : ["extract", "vision"];

  const parts: string[] = [];
  let total = 0;
  for (const stage of stages) {
    let offset = 0;
    for (;;) {
      if (total >= maxChars) return parts.join("\n\n");
      const rows = await fetchStage(client, jobId, stage, offset, FETCH_BATCH);
      if (rows.length === 0) break;
      for (const r of rows) {
        // 원본 `raw_parts` 가 비어 있던 창은 join 대상이 아니다.
        if (Number(r.raw_part_count ?? 0) <= 0) continue;
        const t = r.raw_text ?? "";
        parts.push(t);
        total += cpLen(t) + 2; // 구분자까지 대략 세면 충분하다(상한 판단에만 쓴다)
      }
      if (rows.length < FETCH_BATCH) break;
      offset += FETCH_BATCH;
    }
  }
  return parts.join("\n\n");
}
