/**
 * `chunk` 작업 핸들러 — extract 산출물을 **창 단위로** 읽어 청크 레코드를 만든다.
 *
 * ## 왜 창으로 쪼갰나
 * 예전엔 extract 산출물을 **전부** 모아 한 태스크에서 처리했다. SK 사업보고서
 * (1,513p · 추출물 20MB · 청크 25,831)가 그 자리에서 Edge 런타임에 3회 kill 됐다.
 * 랩탑 실측 3,811ms · 피크 RSS 405MB — Edge vCPU 는 더 느리니 CPU 2s 예산을 넘는다.
 * O(n²) 는 없고 전부 문자 수 비례라, 입력을 나누면 그대로 예산 안에 든다.
 *
 * ## 어디서 자르나 — `page` 가 바뀌는 지점뿐
 * 병합(`chunk_merge.ts`)은 `buf.page === section.page` 일 때만 일어난다. 그래서
 * 직전·다음 섹션의 page 가 다른 지점에서 자르면 결과가 **byte-identical** 이다.
 * 창 끝에서 "마지막 page 값을 공유하는 꼬리 묶음" 을 떼어 다음 창으로 넘긴다
 * (`chunk_window.splitTailByPage`, 근거와 반례는 `chunk_window_test.ts`).
 *
 * ## 유일한 교차 창 의존 — 머리말/꼬리말
 * `chunk_filter` 의 `header_footer` 는 **문서 전체**에서 3회 이상 반복되는 짧은
 * 텍스트다. 창 안에서는 알 수 없다. 그래서 여기서는 **카운트만 캐리에 누적**하고,
 * 마지막 창이 판정 결과(`header_footer_texts`)를 payload 에 적는다. 실제 마킹은
 * `load` 가 한다 — `load` 는 이미 part 창 단위다.
 *
 * ## seq 순서가 곧 문서 순서다
 * extract 는 `seq = page_from` 으로 저장한다. **정렬 없이 읽으면 안 된다** —
 * PostgREST 기본 순서는 보장되지 않는다.
 *
 * ## 산출물 한 창 = 아티팩트 한 행
 * `stage='chunk'`, `seq = 창 인덱스`. `load` 는 그 part 를 하나씩 읽어 upsert 한다.
 * 창 하나는 40페이지 ≈ 700청크 ≈ 350KB 라 `load` 가 드는 메모리도 그만큼이다.
 *
 * ## 다음 단계
 * 마지막 창이 아니면 다음 창을 큐에 넣는다. 마지막 창이면 문서 flags 를 OR 로 머지하고
 * 잔존 아티팩트를 지운 뒤 `tag_summarize` 를 넣는다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { type ChunkRecord, readChunkEnv, runChunkStage } from "../chunk_records.ts";
import { complete } from "../../llm/gemini.ts";
import { loadRawText } from "../raw_text.ts";
import { type DocLlmPair, generateDocLlmPairs, injectSynonyms } from "../synonym_inject.ts";
import type { ExtractedSection } from "../pdf_extract.ts";
import { stripNulls } from "../strip_nul.ts";
import { headerFooterTexts } from "../chunk_filter.ts";
import { runContentGateStage } from "../content_gate.ts";
import {
  accumulateHfCounts,
  type DocFlagsCarry,
  emptyDocFlags,
  mergeFlagsOr,
  readArtifactsPerTask,
  type SourceRef,
  splitTailByPage,
  windowPlan,
} from "../chunk_window.ts";
import type { TaskHandler, TaskPayload } from "../worker.ts";

export interface ChunkDeps {
  client: SupabaseClient;
  /** 테스트 주입 — ENV 를 직접 준다. */
  env?: ReturnType<typeof readChunkEnv>;
  /** 테스트 주입 — 태스크 하나가 읽을 소스 아티팩트 수(창 크기). */
  artifactsPerTask?: number;
  /** 원시 ENV — 동의어 LLM 후보 생성이 `GEMINI_API_KEY` 를 읽는다. */
  rawEnv?: Record<string, string | undefined>;
}

/**
 * `factory._GEMINI_DEFAULT_MODELS["synonym"]`. 짧은 입출력이라 flash-lite 다.
 * `JETRAG_LLM_MODEL_SYNONYM` 으로 덮을 수 있다(원본의 `_MODEL_ENV_PREFIX` 패턴).
 */
const DEFAULT_SYNONYM_MODEL = "gemini-2.5-flash-lite";
/** 원본 `_LLM_INPUT_CHARS` — 문서 앞부분만 본다. */
const SYNONYM_LLM_INPUT_CHARS = 3000;

/** 소스 stage 3종. `windowPlan` 이 순서 규칙을 안다. */
const SOURCE_STAGES = ["extract", "scan", "vision"];

/**
 * 소스 플랜 한 번에 읽을 최대 행 수.
 *
 * **PostgREST 는 기본 상한(`db-max-rows`, Supabase 기본 1,000)을 넘으면 조용히 자른다.**
 * 잘린 플랜으로 돌면 `totalWindows` 가 작아져 뒷부분이 통째로 사라지고, 잡은 성공으로
 * 끝난다 — 가장 찾기 어려운 실패다. 그래서 상한을 명시하고, 그 값에 **닿으면** 잘렸다고
 * 보고 던진다.
 *
 * 5,000 인 근거: SK 사업보고서 1,513p 가 extract 152 + vision 최대 ~379 = 531 행이다.
 * 열 배 여유다.
 */
const PLAN_MAX = 5000;

/**
 * 창을 넘기는 상태.
 *
 * `sections` 는 "마지막 page 값을 공유하는 꼬리 묶음" 이라 **한 페이지분**으로 유계다.
 * `hfCounts` 만 문서 크기에 비례해 자란다(SK 실측 최종 11,500키 = 0.64MB).
 */
interface ChunkCarry {
  sections: ExtractedSection[];
  /** 다음 창의 첫 청크가 받을 `chunk_idx`. */
  nextChunkIdx: number;
  hfCounts: Record<string, number>;
  docFlags: DocFlagsCarry;
  /**
   * 동의어 LLM 후보. 원본은 **doc 당 정확히 1 회** 만든다 — 창마다 다시 부르면
   * 호출 수가 창 수만큼 늘고 후보가 창마다 달라진다. 그래서 창 0 에서 만들어 나른다.
   */
  docLlmPairs: DocLlmPair[] | null;
}

function emptyCarry(): ChunkCarry {
  return {
    sections: [],
    nextChunkIdx: 0,
    hfCounts: {},
    docFlags: emptyDocFlags(),
    docLlmPairs: null,
  };
}

/**
 * `documents.flags` 현재 값.
 *
 * 마지막 창이 이 값을 **두 가지로** 쓴다: ① 머지 베이스(덮어쓰지 않으려고)
 * ② `chunk_finalized_job` 으로 `tag_summarize` 중복 투입 판정. 그래서 읽기와 쓰기를
 * 나눠 뒀다 — 한 함수로 묶으면 같은 행을 두 번 읽게 된다.
 */
async function readDocFlags(
  client: SupabaseClient,
  docId: string,
): Promise<Record<string, unknown>> {
  const { data, error } = await client
    .from("documents").select("flags").eq("id", docId).limit(1);
  if (error) throw new Error(`flags 조회 실패: ${error.message}`);
  return ((data ?? [])[0] as { flags?: Record<string, unknown> } | undefined)?.flags ?? {};
}

/** 원본 `content_gate._merge_doc_flags` — 기존 flags 에 덧쓴다(덮어쓰지 않는다). */
async function writeDocFlags(
  client: SupabaseClient,
  docId: string,
  existing: Record<string, unknown>,
  updates: Record<string, unknown>,
): Promise<void> {
  const { error } = await client
    .from("documents").update({ flags: { ...existing, ...updates } }).eq("id", docId);
  if (error) throw new Error(`flags 갱신 실패: ${error.message}`);
}

/**
 * 직전 창이 남긴 캐리. `window === 0` 이면 새 문서라 빈 값이다.
 *
 * 직전 창이 끝나야 다음이 큐에 들어가므로(순차 보장) 그 행은 이미 있어야 한다 —
 * 없으면 순서가 깨진 것이므로 던진다(`extract.loadCarryTitle` 과 같은 계약).
 */
async function loadCarry(
  client: SupabaseClient,
  jobId: string,
  window: number,
): Promise<ChunkCarry> {
  if (window <= 0) return emptyCarry();
  const { data, error } = await client
    .from("ingest_artifacts")
    .select("seq, carry:payload->carry")
    .eq("job_id", jobId)
    .eq("stage", "chunk")
    .eq("seq", window - 1)
    .limit(1);
  if (error) throw new Error(`직전 chunk 창 산출물 조회 실패: ${error.message}`);
  const row = (data ?? [])[0] as { carry?: unknown } | undefined;
  if (!row) {
    throw new Error(
      `직전 chunk 창 산출물이 없다 (job=${jobId}, window=${window}). ` +
        "chunk 는 순차로 돌아야 한다 — 순서가 깨졌다.",
    );
  }
  const c = row.carry;
  if (!c || typeof c !== "object") {
    throw new Error(
      `직전 chunk 창(seq=${window - 1})에 carry 가 없다 (job=${jobId}). 순서가 깨졌다.`,
    );
  }
  const carry = c as Partial<ChunkCarry>;
  return {
    sections: carry.sections ?? [],
    nextChunkIdx: carry.nextChunkIdx ?? 0,
    hfCounts: carry.hfCounts ?? {},
    docFlags: carry.docFlags ?? emptyDocFlags(),
    docLlmPairs: carry.docLlmPairs ?? null,
  };
}

interface WindowRow {
  stage: string;
  seq: number;
  sections?: ExtractedSection[] | null;
  metadata?: Record<string, unknown> | null;
}

/**
 * 이 창이 읽을 소스 아티팩트의 payload 를 가져온다.
 *
 * `payload` 를 통째로 읽지 않는다 — `raw_text` 가 payload 의 절반이고 여기서는 안 쓴다
 * (`tag_summarize`·`doc_embed` 가 `loadRawText` 로 따로 읽는다). 창 하나는 stage 가
 * 많아야 둘(본문 + vision)이라 쿼리도 둘이면 끝난다.
 */
async function loadWindowRows(
  client: SupabaseClient,
  jobId: string,
  refs: SourceRef[],
): Promise<Map<string, WindowRow>> {
  const byStage = new Map<string, number[]>();
  for (const r of refs) {
    const list = byStage.get(r.stage);
    if (list) list.push(r.seq);
    else byStage.set(r.stage, [r.seq]);
  }
  const out = new Map<string, WindowRow>();
  for (const [stage, seqs] of byStage) {
    const { data, error } = await client
      .from("ingest_artifacts")
      .select("stage, seq, sections:payload->sections, metadata:payload->metadata")
      .eq("job_id", jobId)
      .eq("stage", stage)
      .in("seq", seqs);
    if (error) throw new Error(`${stage} 산출물 조회 실패: ${error.message}`);
    for (const row of (data ?? []) as WindowRow[]) out.set(`${row.stage}:${row.seq}`, row);
  }
  return out;
}

export function makeChunkHandler(deps: ChunkDeps): TaskHandler {
  const defaultPerTask = deps.artifactsPerTask ?? readArtifactsPerTask();

  return async (task: TaskPayload) => {
    // 창 크기는 **메시지에 실려 온 값이 우선**이다. 문서 중간에 ENV 가 바뀌어도
    // 한 문서 안에서는 창 경계가 흔들리지 않아야 `seq = from / count` 가 맞는다.
    const per = Math.max(1, Math.floor(task.count ?? defaultPerTask));
    const from = task.from ?? 0;
    const window = Math.floor(from / per);

    // 1) 소스 플랜 — payload 없이 `stage, seq` 만 읽는다.
    const { data: planData, error: planErr } = await deps.client
      .from("ingest_artifacts")
      .select("stage, seq")
      .eq("job_id", task.job_id)
      .in("stage", SOURCE_STAGES)
      .order("seq", { ascending: true })
      .range(0, PLAN_MAX - 1);
    if (planErr) throw new Error(`소스 산출물 목록 조회 실패: ${planErr.message}`);
    const allRefs = (planData ?? []) as SourceRef[];
    if (allRefs.length >= PLAN_MAX) {
      // 잘린 플랜으로 돌면 문서 뒷부분이 통째로 사라지는데 잡은 성공으로 끝난다.
      throw new Error(
        `소스 산출물이 ${allRefs.length}행 — 플랜 상한 ${PLAN_MAX} 에 닿았다 ` +
          `(job=${task.job_id}). 잘린 플랜으로 청킹하면 문서 뒷부분이 조용히 사라진다.`,
      );
    }

    const extractSeqs = allRefs.filter((r) => r.stage === "extract").map((r) => r.seq);
    if (extractSeqs.length === 0) {
      // extract 가 하나도 없으면 순서가 깨진 것이다. 빈 청크를 만들어 덮으면
      // **문서가 조용히 사라진다** — 던져서 재시도·failed 로 보낸다.
      throw new Error(`extract 산출물이 없다 (job=${task.job_id}). 순서가 깨졌다.`);
    }
    // seq 가 겹치면 같은 페이지 범위를 두 번 넣게 된다. **빠진 구간(gap)까지는 못 본다** —
    // extract 의 seq 는 `page_from` 이고 간격이 창 길이에 따라 달라져서, 여기서 읽는
    // `stage, seq` 만으로는 "10 다음이 20 인 게 정상인지" 를 알 수 없다. 그걸 보려면
    // `page_count` 를 같이 읽어야 하는데, 그건 이 쿼리를 무겁게 만든다. 누락은 아래
    // "창의 섹션이 0 개" 검사와 `load` 의 part 누락 검사가 잡는다.
    const dup = extractSeqs.filter((s, i) => extractSeqs.indexOf(s) !== i);
    if (dup.length > 0) {
      throw new Error(`extract 산출물 seq 가 중복됐다: ${[...new Set(dup)].join(", ")}`);
    }

    const { plan, totalWindows, extractCount, scanCount, visionCount } = windowPlan(allRefs, per);
    if (window >= totalWindows) {
      // 조용히 넘기면 잡이 성공으로 끝난다. 이 상태는 둘 중 하나인데 **둘 다 사고**다:
      // 재인제스트로 창 수가 줄어 옛 메시지가 남았거나, 플랜이 잘렸거나.
      // 던져서 재시도·archive 로 보내고 잡을 failed 로 남긴다.
      throw new Error(
        `창 ${window} 는 범위 밖 (total=${totalWindows}, job=${task.job_id}) — ` +
          "재인제스트로 창 수가 줄었거나 플랜이 잘렸다.",
      );
    }
    const isLast = window === totalWindows - 1;

    // 2) 이 창의 섹션 — 플랜 순서대로 이어 붙인다.
    const refs = plan.slice(window * per, window * per + per);
    const rows = await loadWindowRows(deps.client, task.job_id, refs);
    const windowSections: ExtractedSection[] = [];
    let resolved = 0;
    for (const ref of refs) {
      const part = rows.get(`${ref.stage}:${ref.seq}`)?.sections;
      // 빈 배열도 "풀린" 것이다 — 섹션이 0 개인 페이지 범위는 정상이다.
      if (Array.isArray(part)) {
        resolved++;
        windowSections.push(...part);
      }
    }
    if (refs.length > 0 && resolved === 0) {
      // 플랜에는 있는데 payload 에서 배열이 하나도 안 나왔다. 그냥 두면 이 창이
      // **청크 0 개**를 만들고 잡은 성공으로 끝난다 — 청크가 사라졌는데 아무도 모른다.
      // 실제 원인 후보 둘: ① `sections:payload->sections` 별칭이 안 먹어 키가 안 옴
      // ② 플랜을 읽은 뒤 그 행들이 사라짐(재인제스트 중 삭제).
      throw new Error(
        `창 ${window} 의 소스 ${refs.length}행에서 sections 를 하나도 못 읽었다 ` +
          `(job=${task.job_id}, refs=${refs.map((r) => `${r.stage}:${r.seq}`).join(",")}). ` +
          "`payload->sections` 별칭이 안 먹었거나 아티팩트 행이 사라졌다.",
      );
    }

    // 3) 캐리 — 직전 창의 꼬리 섹션·chunk_idx·카운트·flags.
    const carry = await loadCarry(deps.client, task.job_id, window);

    const chunkEnv = deps.env ?? readChunkEnv();

    // 원본 `run_chunk_stage` — LLM 후보는 **doc 당 정확히 1 회**, chunk 루프 **밖**에서
    // 만든다. ENV 가 꺼져 있으면(기본) 부르지 않는다. 창 0 에서 만들어 캐리로 나른다.
    let docLlmPairs: DocLlmPair[] | null = carry.docLlmPairs;
    if (chunkEnv.synonymLlmEnabled && window === 0) {
      const env = deps.rawEnv ?? Deno.env.toObject();
      // 원본은 `extraction.raw_text` 를 그대로 넘긴다. Edge 는 창으로 쪼개 저장하므로
      // 되붙여서 넘긴다 — 어차피 앞 3000 자만 쓴다.
      const rawText = await loadRawText(deps.client, task.job_id, SYNONYM_LLM_INPUT_CHARS);
      docLlmPairs = await generateDocLlmPairs(rawText, (system, user) =>
        complete(
          [{ role: "system", content: system }, { role: "user", content: user }],
          {
            apiKey: env["GEMINI_API_KEY"] ?? "",
            model: env["JETRAG_LLM_MODEL_SYNONYM"] ?? DEFAULT_SYNONYM_MODEL,
          },
          { temperature: 0.1, jsonMode: true },
        ));
    }

    // 4) 컷 — 마지막 창이 아니면 꼬리 묶음을 다음 창으로 넘긴다.
    const combined = carry.sections.concat(windowSections);
    const { head, tail } = isLast
      ? { head: combined, tail: [] as ExtractedSection[] }
      : splitTailByPage(combined);

    // 5) 처리 — split → merge → records. `chunk_filter` 는 여기서 **안 돈다**
    //    (문서 전체 반복 횟수를 알아야 해서 `load` 로 옮겼다).
    let records: ChunkRecord[] = runChunkStage({
      docId: task.doc_id,
      sections: head,
      env: chunkEnv,
      docLlmPairs,
      injectSynonyms,
      idxOffset: carry.nextChunkIdx,
    });

    // `vision_type` 은 `ExtractionResult.metadata` 에서 온다 — 단독 이미지 업로드에서만
    // 채워진다(스캔 PDF 경로는 원본이 그 값을 안 넘긴다). 문서의 **첫** 소스 행에만
    // 있으므로 창 0 에서만 본다. 나머지 창은 undefined → false, OR 누적이라 결과가 같다.
    const visionType = window === 0 && refs[0]
      ? rows.get(`${refs[0].stage}:${refs[0].seq}`)?.metadata?.["vision_type"]
      : undefined;
    const gated = runContentGateStage({ chunks: records, visionType });
    records = gated.chunks;

    const nextCarry: ChunkCarry = {
      sections: tail,
      nextChunkIdx: carry.nextChunkIdx + records.length,
      hfCounts: accumulateHfCounts(carry.hfCounts, records),
      docFlags: mergeFlagsOr(carry.docFlags, gated.flagsUpdate),
      docLlmPairs,
    };

    // 6) 저장 — 창 하나 = 아티팩트 한 행.
    const payload: Record<string, unknown> = {
      part: window,
      total_parts: totalWindows,
      // **이 창의** 수다(문서 전체가 아니다). 전부 더하면 문서 합계가 된다.
      chunk_count: records.length,
      section_count: windowSections.length,
      extract_parts: extractCount,
      vision_parts: visionCount,
      scan_parts: scanCount,
      chunk_idx_from: carry.nextChunkIdx,
      next_chunk_idx: nextCarry.nextChunkIdx,
      records,
    };
    if (isLast) {
      // 마지막 창은 캐리를 안 남긴다 — 읽을 다음 창이 없다. 대신 `load` 가 쓸
      // 판정 결과만 적는다(SK 실측 11,500 카운트 → 531 텍스트).
      payload["header_footer_texts"] = [...headerFooterTexts(Object.entries(nextCarry.hfCounts))];
      payload["doc_chunk_count"] = nextCarry.nextChunkIdx;
    } else {
      payload["carry"] = nextCarry;
    }

    // extract 가 이미 씻었지만 여기서도 본다 — 청킹이 만든 문자열(제목 합성 등)에도
    // NUL 이 섞일 수 있고, 놓치면 저장이 통째로 실패한다.
    const cleaned = stripNulls(payload);
    if (cleaned.removed > 0) cleaned.value["nul_removed"] = cleaned.removed;

    const { error: upErr } = await deps.client
      .from("ingest_artifacts")
      .upsert({
        job_id: task.job_id,
        doc_id: task.doc_id,
        stage: "chunk",
        seq: window,
        payload: cleaned.value,
      }, { onConflict: "job_id,stage,seq" });
    if (upErr) throw new Error(`chunk 산출물 저장 실패 (창=${window}): ${upErr.message}`);

    // 7) 저장이 **다 끝난 뒤에** 다음 단계를 넣는다. 순서가 반대면 다음 창이 아직 없는
    //    아티팩트에서 캐리를 찾다가 던진다.
    if (!isLast) {
      const { error: sendErr } = await deps.client.rpc("ingest_queue_send", {
        payload: {
          job_id: task.job_id,
          doc_id: task.doc_id,
          stage: "chunk",
          from: from + per,
          count: per,
        },
      });
      if (sendErr) throw new Error(`다음 chunk 창 enqueue 실패: ${sendErr.message}`);
      return;
    }

    // 마지막 창 — 문서 flags 를 **누적값으로** 머지한다. 창마다 덮어쓰면 마지막 창에
    // PII 가 없다는 이유로 앞 창의 true 가 지워진다.
    const existingFlags = await readDocFlags(deps.client, task.doc_id);
    // 이 잡의 마지막 창이 이미 끝났는가. `tag_summarize` 는 LLM 2 회 + load 사슬 전체를
    // 다시 돌리므로, 창이 재배달될 때 두 번 들어가면 안 된다.
    const alreadyFinalized = existingFlags["chunk_finalized_job"] === task.job_id;

    // 재인제스트로 창 수가 줄면 옛 part 가 남는다. 그걸 `load` 가 읽으면 이번에 안 만든
    // 청크가 되살아난다 — 넘기기 전에 지운다. flags 쓰기 **앞**에 둔다: 아래 두 줄
    // (flags 쓰기 → enqueue) 사이가 좁을수록 "flags 만 쓰고 죽어 잡이 멈추는" 창이 좁다.
    const { error: delErr } = await deps.client
      .from("ingest_artifacts")
      .delete()
      .eq("job_id", task.job_id)
      .eq("stage", "chunk")
      .gte("seq", totalWindows);
    if (delErr) throw new Error(`잔존 chunk 산출물 정리 실패: ${delErr.message}`);

    const flagsUpdate: Record<string, unknown> = {
      ...nextCarry.docFlags,
      // 이 잡의 chunk 가 끝났다는 표식. 재인제스트는 job_id 가 달라 자동으로 풀린다.
      chunk_finalized_job: task.job_id,
    };
    await writeDocFlags(deps.client, task.doc_id, existingFlags, flagsUpdate);
    console.info(
      `content_gate: doc=${task.doc_id} has_pii=${flagsUpdate.has_pii} ` +
        `has_watermark=${flagsUpdate.has_watermark} ` +
        `third_party=${flagsUpdate.third_party} windows=${totalWindows} ` +
        `chunks=${nextCarry.nextChunkIdx}`,
    );

    if (alreadyFinalized) {
      console.warn(
        `chunk: 마지막 창 재배달 (job=${task.job_id}) — tag_summarize 를 다시 넣지 않는다`,
      );
      return;
    }

    // 원본 순서: chunk_filter → content_gate → **tag_summarize** → load.
    const { error: sendErr } = await deps.client.rpc("ingest_queue_send", {
      payload: { job_id: task.job_id, doc_id: task.doc_id, stage: "tag_summarize" },
    });
    if (sendErr) throw new Error(`tag_summarize enqueue 실패: ${sendErr.message}`);
  };
}
