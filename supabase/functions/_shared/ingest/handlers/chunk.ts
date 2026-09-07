/**
 * `chunk` 작업 핸들러 — extract 산출물을 **전부 모아** 청크 레코드를 만든다.
 *
 * ## 왜 페이지별로 청킹하지 않나
 * 청킹이 `_merge_short_sections` 로 **인접 섹션을 병합**한다. 페이지마다 따로 청킹하면
 * 페이지 경계에서 병합이 안 일어나 청크가 달라진다. 그래서 "페이지 추출 → 전부 모아
 * 청킹" 이어야 하고, 그 중간 자리가 `ingest_artifacts` 다(마이그 027).
 *
 * ## seq 순서가 곧 문서 순서다
 * extract 는 `seq = page_from` 으로 저장한다. **정렬 없이 읽으면 안 된다** —
 * PostgREST 기본 순서는 보장되지 않는다.
 *
 * ## 아직 다음 단계를 큐에 넣지 않는다
 * `chunk_filter` 이후 핸들러가 없다. 넣으면 "모르는 stage" 로 즉시 archive 되고 잡이
 * failed 가 된다(`worker.ts` 계약). 청킹까지가 이번 범위이므로 산출물만 남기고
 * **잡도 completed 로 만들지 않는다** — 임베딩·적재가 안 끝났으므로 완료가 아니다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { readChunkEnv, runChunkStage } from "../chunk_records.ts";
import type { ExtractedSection } from "../pdf_extract.ts";
import { stripNulls } from "../strip_nul.ts";
import type { TaskHandler, TaskPayload } from "../worker.ts";

export interface ChunkDeps {
  client: SupabaseClient;
  /** 테스트 주입 — ENV 를 직접 준다. */
  env?: ReturnType<typeof readChunkEnv>;
}

interface ExtractArtifact {
  seq: number;
  payload: { sections?: ExtractedSection[] } | null;
}

export function makeChunkHandler(deps: ChunkDeps): TaskHandler {
  return async (task: TaskPayload) => {
    const { data, error } = await deps.client
      .from("ingest_artifacts")
      .select("seq, payload")
      .eq("job_id", task.job_id)
      .eq("stage", "extract")
      .order("seq", { ascending: true });
    if (error) throw new Error(`extract 산출물 조회 실패: ${error.message}`);

    const rows = (data ?? []) as ExtractArtifact[];
    if (rows.length === 0) {
      // extract 가 하나도 없으면 순서가 깨진 것이다. 빈 청크를 만들어 덮으면
      // **문서가 조용히 사라진다** — 던져서 재시도·failed 로 보낸다.
      throw new Error(`extract 산출물이 없다 (job=${task.job_id}). 순서가 깨졌다.`);
    }

    // 페이지 범위가 빠짐없이 이어지는지 확인한다. 중간이 비면 그 페이지 내용이
    // 통째로 누락된 청크가 만들어지고, 그건 나중에 찾기 어렵다.
    const seqs = rows.map((r) => r.seq);
    const dup = seqs.filter((s, i) => seqs.indexOf(s) !== i);
    if (dup.length > 0) {
      throw new Error(`extract 산출물 seq 가 중복됐다: ${[...new Set(dup)].join(", ")}`);
    }

    const sections: ExtractedSection[] = [];
    for (const r of rows) {
      const part = r.payload?.sections;
      if (Array.isArray(part)) sections.push(...part);
    }

    const records = runChunkStage({
      docId: task.doc_id,
      sections,
      env: deps.env ?? readChunkEnv(),
    });

    // extract 가 이미 씻었지만 여기서도 한 번 더 본다 — 청킹이 만든 문자열(제목 합성 등)
    // 에도 NUL 이 섞일 수 있고, 놓치면 저장이 통째로 실패한다.
    const cleaned = stripNulls({
      chunk_count: records.length,
      section_count: sections.length,
      extract_parts: rows.length,
      records,
    } as Record<string, unknown>);
    if (cleaned.removed > 0) cleaned.value["nul_removed"] = cleaned.removed;

    const { error: upErr } = await deps.client
      .from("ingest_artifacts")
      .upsert({
        job_id: task.job_id,
        doc_id: task.doc_id,
        stage: "chunk",
        seq: 0,
        payload: cleaned.value,
      }, { onConflict: "job_id,stage,seq" });
    if (upErr) throw new Error(`chunk 산출물 저장 실패: ${upErr.message}`);
  };
}
