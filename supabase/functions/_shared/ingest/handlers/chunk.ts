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
 * ## 산출물을 **쪼개서** 저장한다
 * 처음엔 레코드 전부를 `seq=0` 한 행에 넣었다. 실측하니 SK 사업보고서가 25,831 청크 ≈
 * 13MB 였다(삼성 8,477 청크 = 4.4MB 실측). `load` 가 그걸 통째로 읽으면 Edge 메모리
 * 상한 240MB(Phase 0 실측) 위에 JSON 파싱 힙이 얹힌다. 그래서 `CHUNKS_PER_ARTIFACT`
 * 개씩 나눠 `seq = 0, 1, 2 …` 로 저장하고, 각 행에 `total_parts` 를 적어 `load` 가
 * 어디까지 있는지 알게 한다.
 *
 * ## 다음 단계
 * 저장이 다 끝난 뒤 `load` 를 큐에 넣는다. `load` 는 part 를 하나씩 처리하며 스스로
 * 다음 part 를 큐에 넣는다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { readChunkEnv, runChunkStage } from "../chunk_records.ts";
import type { ExtractedSection } from "../pdf_extract.ts";
import { stripNulls } from "../strip_nul.ts";
import { runChunkFilterStage } from "../chunk_filter.ts";
import { runContentGateStage } from "../content_gate.ts";
import type { TaskHandler, TaskPayload } from "../worker.ts";

/**
 * 아티팩트 한 행에 담을 청크 수.
 *
 * 실측 청크당 약 500B(삼성 8,477 청크 = 4.4MB). 1,000 개면 한 행 ≈ 500KB 로, SK 최대
 * 문서도 26 행에 담긴다. `load` 가 한 번에 드는 메모리도 그만큼이다.
 */
export const CHUNKS_PER_ARTIFACT = 1000;

export interface ChunkDeps {
  client: SupabaseClient;
  /** 테스트 주입 — ENV 를 직접 준다. */
  env?: ReturnType<typeof readChunkEnv>;
  /** 테스트 주입 — 분할 크기. */
  chunksPerArtifact?: number;
}

interface ExtractArtifact {
  seq: number;
  payload: { sections?: ExtractedSection[]; metadata?: Record<string, unknown> } | null;
}

/** 원본 `content_gate._merge_doc_flags` — 기존 flags 를 읽어 머지한다(덮어쓰지 않는다). */
async function mergeDocFlags(
  client: SupabaseClient,
  docId: string,
  updates: Record<string, unknown>,
): Promise<void> {
  const { data, error } = await client
    .from("documents").select("flags").eq("id", docId).limit(1);
  if (error) throw new Error(`flags 조회 실패: ${error.message}`);
  const existing = ((data ?? [])[0] as { flags?: Record<string, unknown> } | undefined)
    ?.flags ?? {};
  const { error: uErr } = await client
    .from("documents").update({ flags: { ...existing, ...updates } }).eq("id", docId);
  if (uErr) throw new Error(`flags 갱신 실패: ${uErr.message}`);
}

export function makeChunkHandler(deps: ChunkDeps): TaskHandler {
  const perPart = deps.chunksPerArtifact ?? CHUNKS_PER_ARTIFACT;

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

    // 스캔 PDF 는 `scan` 산출물이 extract 를 **대체**한다. 원본이
    // `result = _reroute_pdf_to_image(...)` 로 결과를 통째로 갈아끼우기 때문이다 —
    // 텍스트가 거의 없는 extract 결과를 함께 넣으면 원본에 없는 청크가 생긴다.
    const { data: sData, error: sErr } = await deps.client
      .from("ingest_artifacts")
      .select("seq, payload")
      .eq("job_id", task.job_id)
      .eq("stage", "scan")
      .order("seq", { ascending: true });
    if (sErr) throw new Error(`scan 산출물 조회 실패: ${sErr.message}`);
    const scanRows = (sData ?? []) as ExtractArtifact[];

    const sections: ExtractedSection[] = [];
    if (scanRows.length > 0) {
      for (const r of scanRows) {
        const part = r.payload?.sections;
        if (Array.isArray(part)) sections.push(...part);
      }
    } else {
      for (const r of rows) {
        const part = r.payload?.sections;
        if (Array.isArray(part)) sections.push(...part);
      }
    }

    // vision 섹션은 **텍스트 섹션 전부 뒤에** 온다. 원본 `_enrich_pdf_with_vision` 이
    // `sections = list(base_result.sections)` 로 시작해 페이지 루프에서 append 하기
    // 때문이다. 창 단위로 번갈아 섞으면 순서가 깨진다 — 그래서 stage 를 나눠 뒀다.
    const { data: vData, error: vErr } = await deps.client
      .from("ingest_artifacts")
      .select("seq, payload")
      .eq("job_id", task.job_id)
      .eq("stage", "vision")
      .order("seq", { ascending: true });
    if (vErr) throw new Error(`vision 산출물 조회 실패: ${vErr.message}`);
    const vRows = (vData ?? []) as ExtractArtifact[];
    // 스캔 문서는 vision enrich 를 안 돈다(원본이 elif 라 배타적이다). 산출물이 있을
    // 리 없지만, 있어도 안 섞는다.
    if (scanRows.length === 0) {
      for (const r of vRows) {
        const part = r.payload?.sections;
        if (Array.isArray(part)) sections.push(...part);
      }
    }

    let records = runChunkStage({
      docId: task.doc_id,
      sections,
      env: deps.env ?? readChunkEnv(),
    });

    // 원본 파이프라인 순서: chunk → **chunk_filter → content_gate** → … → load.
    // 둘 다 청크를 지우지 않는다. 표시만 남기고 검색 쪽 쿼리가 그걸 보고 거른다.
    const filtered = runChunkFilterStage(records);
    records = filtered.chunks;
    if (filtered.filterRatio > 0.05) {
      // 원본과 같은 경고 — 오탐이 늘어난 신호일 수 있다.
      console.warn(
        `chunk_filter: doc=${task.doc_id} 마킹 비율 ` +
          `${(filtered.filterRatio * 100).toFixed(1)}% > 5% — false positive risk 검토 필요`,
      );
    }
    console.info(
      `chunk_filter: doc=${task.doc_id} total=${records.length} ` +
        `table_noise=${filtered.counts.table_noise} ` +
        `header_footer=${filtered.counts.header_footer} ` +
        `empty=${filtered.counts.empty} extreme_short=${filtered.counts.extreme_short} ` +
        `filter_ratio=${filtered.filterRatio.toFixed(3)}`,
    );

    // `vision_type` 은 `ExtractionResult.metadata` 에서 온다 — 단독 이미지 업로드에서만
    // 채워진다(스캔 PDF 경로는 원본이 그 값을 안 넘긴다).
    const srcRows = scanRows.length > 0 ? scanRows : rows;
    const visionType =
      (srcRows[0]?.payload as { metadata?: Record<string, unknown> } | null)
        ?.metadata?.["vision_type"];
    const gated = runContentGateStage({ chunks: records, visionType });
    records = gated.chunks;
    await mergeDocFlags(deps.client, task.doc_id, gated.flagsUpdate);
    console.info(
      `content_gate: doc=${task.doc_id} has_pii=${gated.flagsUpdate.has_pii} ` +
        `has_watermark=${gated.flagsUpdate.has_watermark} ` +
        `third_party=${gated.flagsUpdate.third_party} ` +
        `chunks_with_pii=${gated.chunksWithPii} ` +
        `chunks_with_watermark=${gated.chunksWithWatermark}`,
    );

    // 빈 문서라도 part 를 **하나는** 남긴다. 없으면 `load` 가 "순서가 깨졌다" 로 오해한다.
    const totalParts = Math.max(1, Math.ceil(records.length / perPart));
    for (let part = 0; part < totalParts; part++) {
      const slice = records.slice(part * perPart, (part + 1) * perPart);
      // extract 가 이미 씻었지만 여기서도 본다 — 청킹이 만든 문자열(제목 합성 등)에도
      // NUL 이 섞일 수 있고, 놓치면 저장이 통째로 실패한다.
      const cleaned = stripNulls({
        part,
        total_parts: totalParts,
        chunk_count: records.length,
        section_count: sections.length,
        extract_parts: rows.length,
        vision_parts: vRows.length,
        scan_parts: scanRows.length,
        records: slice,
      } as Record<string, unknown>);
      if (cleaned.removed > 0) cleaned.value["nul_removed"] = cleaned.removed;

      const { error: upErr } = await deps.client
        .from("ingest_artifacts")
        .upsert({
          job_id: task.job_id,
          doc_id: task.doc_id,
          stage: "chunk",
          seq: part,
          payload: cleaned.value,
        }, { onConflict: "job_id,stage,seq" });
      if (upErr) throw new Error(`chunk 산출물 저장 실패 (part=${part}): ${upErr.message}`);
    }

    // 저장이 **다 끝난 뒤에** 다음 단계를 넣는다.
    const { error: sendErr } = await deps.client.rpc("ingest_queue_send", {
      payload: { job_id: task.job_id, doc_id: task.doc_id, stage: "load", from: 0 },
    });
    if (sendErr) throw new Error(`load enqueue 실패: ${sendErr.message}`);
  };
}
