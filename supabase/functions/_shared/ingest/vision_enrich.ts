/**
 * `_enrich_pdf_with_vision` 포팅 — 텍스트 PDF 의 표·그림을 vision 으로 보강한다.
 *
 * PyMuPDF 파서는 이미지 블록(type=1)을 통째로 무시하고, 표는 셀 순서가 뒤섞인 채로
 * 나온다. 그래서 페이지를 그림으로 구워 Gemini 에 물어보고 그 결과를 **추가 섹션**으로
 * 붙인다 — 기존 섹션을 덮지 않는다.
 *
 * ## 원본은 문서 하나를 한 번에 돌지만 여기서는 창(window) 단위다
 * Edge 는 요청당 CPU 2초다. 페이지 굽는 데만 최대 271ms 실측(282 페이지 표본) 이라
 * 한 태스크에 `VISION_PAGES_PER_TASK` 페이지씩만 맡는다. 누적 카운터는 다음 태스크로
 * 넘긴다(`VisionCarry`).
 *
 * ### 이 때문에 생기는 차이 — 숨기지 않고 적는다
 * 1. **sweep 범위.** 원본은 문서 전체를 1 차로 돌고 실패한 페이지만 2 차로 돈다.
 *    여기서는 **창 안에서** sweep 한다. 페이지당 최대 시도 횟수(`MAX_SWEEPS`)는 같아서
 *    결과 집합은 같고, 달라지는 건 재시도 시점뿐이다. 비용 cap 이 딱 그 사이에서
 *    걸리는 경우에만 어느 페이지가 잘리는지가 갈린다.
 * 2. **cap 메시지의 "남은 페이지 N".** 1 차 sweep 에서는 원본과 같은 값이 나오도록
 *    문서 전체 기준으로 계산한다(`processCount`, `processCount - pageNum`).
 *    2 차 sweep 은 문서 전체의 실패 목록을 알 수 없어 창 기준이다.
 *
 * ## 렌더는 sweep 재시도에서 다시 하지 않는다
 * 원본은 재시도마다 `get_pixmap` 을 다시 부른다. 같은 페이지는 결정적으로 같은 바이트가
 * 나오므로(실측) 창 안에서는 한 번 구운 걸 재사용한다 — 관찰 결과는 같고 CPU 만 아낀다.
 * 실패 원인은 렌더가 아니라 API 쪽(503 등)이다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { type BudgetStatus, checkCombined, checkDocPageCap } from "./budget_guard.ts";
import type { ExtractedSection } from "./hwp_extract.ts";
import { composeResult } from "./image_parser.ts";
import { renderPageForVision, SCAN_RENDER_DPI } from "./pdf_raster.ts";
import { pyStrError } from "../pyerror.ts";
import { pageArea, STEXT_OPTS, toPageDict } from "../pdf_dict.ts";
import { pyStrip } from "../search/pystr.ts";
import { scorePage } from "./vision_need_score.ts";
import * as visionCache from "./vision_cache.ts";
import { captionImage, type VisionClientDeps } from "./vision_client.ts";
import type { VisionCaption } from "./vision_caption.ts";
import { recordCall } from "./vision_metrics.ts";

/** 원본 `_VISION_ENRICH_MAX_PAGES`. */
export const VISION_ENRICH_MAX_PAGES_DEFAULT = 50;
/** 원본 `_VISION_ENRICH_MAX_SWEEPS`. */
export const VISION_ENRICH_MAX_SWEEPS_DEFAULT = 2;
/** 원본 `_BUDGET_RECHECK_EVERY_N_PAGES`. */
export const BUDGET_RECHECK_EVERY_N_PAGES_DEFAULT = 5;

/**
 * 태스크 하나가 맡는 페이지 수. **원본에 없는 값이다** — Edge CPU 2초 예산에서 나왔다.
 *
 * 실측(282 페이지 표본): `renderPageForVision` 중앙 157~192ms, 최대 271ms.
 * 4 × 271ms ≈ 1.1s + 아티팩트/DB 왕복 여유. 8 페이지면 2.2s 로 한도를 넘는다.
 */
export const VISION_PAGES_PER_TASK = 4;

export interface VisionEnv {
  enabled: boolean;
  maxPages: number;
  maxSweeps: number;
  budgetRecheckEveryNPages: number;
  needScoreEnabled: boolean;
  pageCapPerDoc: number;
  docBudgetUsd: number;
  dailyBudgetUsd: number;
  sliding24hBudgetUsd: number;
  geminiApiKey: string;
}

/** 원본 `config._parse_bool` — `true/1/yes/on` 만 참, 그 외 값은 기본값 유지. */
function parseBool(raw: string | undefined, dflt: boolean): boolean {
  if (raw === undefined || raw === "") return dflt;
  const n = raw.trim().toLowerCase();
  if (n === "true" || n === "1" || n === "yes" || n === "on") return true;
  if (n === "false" || n === "0" || n === "no" || n === "off") return false;
  return dflt;
}

/** 원본 `config._parse_int` — 비숫자면 기본값. **음수는 허용**(cap 무한 토글). */
function parseInt_(raw: string | undefined, dflt: number): number {
  if (raw === undefined || raw === "") return dflt;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) && /^\s*[+-]?\d+\s*$/.test(raw) ? n : dflt;
}

/** 원본 `config._parse_float` — 비숫자·**음수**면 기본값. */
function parseFloat_(raw: string | undefined, dflt: number): number {
  if (raw === undefined || raw === "") return dflt;
  const n = Number.parseFloat(raw);
  if (!Number.isFinite(n) || n < 0) return dflt;
  return n;
}

export function readVisionEnv(env: Record<string, string | undefined>): VisionEnv {
  const daily = parseFloat_(env["JETRAG_DAILY_BUDGET_USD"], 0.5);
  return {
    // 원본 `extract.py` 는 `_parse_bool` 이 아니라 `== "true"` 다 — "1"·"yes" 는 안 먹는다.
    enabled: (env["JETRAG_PDF_VISION_ENRICH"] ?? "false").trim().toLowerCase() === "true",
    // 원본은 `int(...)` 라 잘못된 값이면 import 때 죽는다. 여기서는 기본값으로 간다 —
    // 태스크 하나를 죽이는 것보다 낫고, 잘못된 ENV 는 로그에 남는다.
    maxPages: parseInt_(env["JETRAG_PDF_VISION_ENRICH_MAX_PAGES"], VISION_ENRICH_MAX_PAGES_DEFAULT),
    maxSweeps: parseInt_(
      env["JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS"],
      VISION_ENRICH_MAX_SWEEPS_DEFAULT,
    ),
    budgetRecheckEveryNPages: parseInt_(
      env["JETRAG_BUDGET_RECHECK_EVERY_N_PAGES"],
      BUDGET_RECHECK_EVERY_N_PAGES_DEFAULT,
    ),
    needScoreEnabled: parseBool(env["JETRAG_VISION_NEED_SCORE_ENABLED"], true),
    pageCapPerDoc: parseInt_(env["JETRAG_VISION_PAGE_CAP_PER_DOC"], 50),
    docBudgetUsd: parseFloat_(env["JETRAG_DOC_BUDGET_USD"], 0.10),
    dailyBudgetUsd: daily,
    sliding24hBudgetUsd: parseFloat_(env["JETRAG_24H_BUDGET_USD"], daily),
    geminiApiKey: env["GEMINI_API_KEY"] ?? "",
  };
}

/**
 * 원본 `_resolve_page_cap_for_doc` — 전역 kill switch 가 mode 별 override 를 이긴다.
 */
export function resolvePageCapForDoc(
  override: number | null | undefined,
  pageCapPerDoc: number,
): number {
  if (pageCapPerDoc <= 0) return 0;
  if (override === null || override === undefined) return pageCapPerDoc;
  return override;
}

/** 태스크 사이로 넘기는 누적 상태. 원본에서는 그냥 지역 변수였다. */
export interface VisionCarry {
  calledCount: number;
  budgetPagesSinceCheck: number;
  completed: number;
  /** 로그 한 줄에만 쓰인다(1-based). */
  skippedByNeedScore: number[];
  budgetExceeded: BudgetStatus | null;
  pageCapExceeded: BudgetStatus | null;
}

export function emptyCarry(): VisionCarry {
  return {
    calledCount: 0,
    budgetPagesSinceCheck: 0,
    completed: 0,
    skippedByNeedScore: [],
    budgetExceeded: null,
    pageCapExceeded: null,
  };
}

export interface VisionWindowResult {
  sections: ExtractedSection[];
  rawParts: string[];
  warnings: string[];
  carry: VisionCarry;
  /** cap 도달 — 이후 페이지를 더 돌면 안 된다. */
  stopped: boolean;
  /** 이 창에서 `maxSweeps` 번 시도하고도 실패한 페이지(1-based). */
  failedPages: number[];
  /**
   * `vision_usage_log` 적재 실패 사유. **비어 있어야 정상이다.**
   * 이게 차면 `budget_guard` 의 비용 SUM 이 0 이 되어 한도가 안 걸린다.
   */
  metricErrors: string[];
}

export interface VisionRunDeps {
  client: SupabaseClient;
  env: Record<string, string | undefined>;
  visionEnv: VisionEnv;
  nowMs: number;
  /** 테스트 주입 — 실제 Gemini 호출 대신. */
  caption?: (bytes: Uint8Array, mimeType: string) => Promise<VisionCaption>;
  /** 테스트 주입 — 실제 mupdf 대신. */
  mupdf?: unknown;
}

/**
 * 페이지 창 하나를 처리한다. 0-based 페이지 인덱스를 쓴다(원본 `doc[i]` 와 같다).
 *
 * ## 연속 범위가 아니라 **목록**을 받는다
 * 전체 인제스트는 `[from, from+count)` 지만 증분 재인제스트(`reingest-missing`)는
 * 누락 페이지가 띄엄띄엄하다. 두 흐름이 같은 sweep·cap·합성 로직을 쓰도록 목록으로 받는다.
 *
 * ## cap 메시지의 "남은 페이지 N" 을 맞추려면 창 밖을 알아야 한다
 * 원본은 문서 하나를 한 번에 돌아서 `len(pending)` 이 곧 문서 전체 잔여다. 창 단위인
 * 여기서는 그 값을 못 구하므로 호출자가 알려준다:
 * - `pendingTotal` — 문서 전체의 1 차 sweep 대상 수
 * - `pendingIndexBase` — 이 창의 첫 페이지가 그 목록에서 몇 번째인지
 *
 * 2 차 sweep 은 문서 전체의 실패 목록을 알 수 없어 창 기준으로 센다(§34.4).
 */
export async function runVisionWindow(
  deps: VisionRunDeps,
  opts: {
    bytes: Uint8Array;
    jobId: string;
    docId: string;
    fileName: string;
    sha256: string | null;
    /** 이 창이 맡을 0-based 페이지 목록. */
    pages: number[];
    pendingTotal: number;
    pendingIndexBase: number;
    pageCap: number;
    carry: VisionCarry;
    /** 로그·경고 접두사. 전체는 `vision_enrich`, 증분은 `incremental_vision`. */
    label?: string;
    /** 진행 표시를 쓸지. 원본 증분 경로는 안 쓴다. */
    progressTotal?: number | null;
  },
): Promise<VisionWindowResult> {
  const label = opts.label ?? "vision_enrich";
  const { visionEnv: ve } = deps;
  const sections: ExtractedSection[] = [];
  const rawParts: string[] = [];
  const warnings: string[] = [];
  const metricErrors: string[] = [];
  const carry: VisionCarry = { ...opts.carry, skippedByNeedScore: [...opts.carry.skippedByNeedScore] };

  // deno-lint-ignore no-explicit-any
  const mupdf = (deps.mupdf ?? await import("mupdf")) as any;
  const doc = mupdf.Document.openDocument(opts.bytes, "application/pdf");

  /** 창 안에서만 쓰는 렌더 캐시 — sweep 재시도가 다시 굽지 않게 한다. */
  const rendered = new Map<number, { jpeg: Uint8Array; mimeType: string }>();
  const clientDeps: VisionClientDeps = { apiKey: ve.geminiApiKey, env: deps.env };

  try {
    let pending: number[] = [...opts.pages];

    for (let sweepIdx = 1; sweepIdx <= ve.maxSweeps; sweepIdx++) {
      if (pending.length === 0) break;
      if (carry.budgetExceeded !== null) break;
      if (carry.pageCapExceeded !== null) break;
      if (sweepIdx > 1) {
        console.info(
          `${label} sweep ${sweepIdx}/${ve.maxSweeps}: 누락 ${pending.length} 페이지 ` +
            `재시도 [${pending.map((p) => p + 1).join(", ")}] (file=${opts.fileName})`,
        );
      }
      const failedInSweep: number[] = [];
      for (const pageNum of pending) {
        // --- 비용 cap 재검사 (N 페이지마다) ---
        if (opts.docId && carry.budgetPagesSinceCheck >= ve.budgetRecheckEveryNPages) {
          carry.budgetPagesSinceCheck = 0;
          const status = await checkCombined(
            { client: deps.client, env: deps.env, nowMs: deps.nowMs },
            {
              docId: opts.docId,
              docCapUsd: ve.docBudgetUsd,
              dailyCapUsd: ve.dailyBudgetUsd,
              sliding24hCapUsd: ve.sliding24hBudgetUsd,
            },
          );
          if (!status.allowed) {
            carry.budgetExceeded = status;
            // 원본은 1 차 sweep 에서 `len(pending_pages)` = 문서 전체 잔여를 쓴다.
            const remain = sweepIdx === 1 ? opts.pendingTotal : pending.length;
            const msg = `${label}: budget cap 도달 — ${status.reason} ` +
              `(남은 페이지 ${remain} skip)`;
            warnings.push(msg);
            console.warn(`${msg} (file=${opts.fileName})`);
            break;
          }
        }
        carry.budgetPagesSinceCheck += 1;

        // --- 페이지 cap (in-memory, DB 안 본다) ---
        const pageCapStatus = checkDocPageCap(deps.env, {
          calledPages: carry.calledCount,
          pageCap: opts.pageCap,
        });
        if (!pageCapStatus.allowed) {
          carry.pageCapExceeded = pageCapStatus;
          const remain = sweepIdx === 1
            ? opts.pendingTotal - (opts.pendingIndexBase + opts.pages.indexOf(pageNum))
            : pending.length - pending.indexOf(pageNum);
          const msg = `${label}: page cap 도달 — ${pageCapStatus.reason} ` +
            `(남은 페이지 ${remain} skip)`;
          warnings.push(msg);
          console.warn(`${msg} (file=${opts.fileName})`);
          break;
        }

        try {
          // --- needs_vision OR 규칙 ---
          if (ve.needScoreEnabled && !pageNeedsVision(doc, pageNum, opts.fileName)) {
            if (sweepIdx === 1) carry.skippedByNeedScore.push(pageNum + 1);
            carry.completed += 1;
            if (opts.progressTotal) {
              await updateStageProgress(
                deps.client,
                opts.jobId,
                carry.completed,
                opts.progressTotal,
              );
            }
            continue;
          }

          // 원본은 **캐시 조회 전에** 렌더하고 `called_count` 를 올린다. 즉 캐시 hit 도
          // page cap 을 깎는다 — 의도로 보이진 않지만 관찰되는 값이라 그대로 맞춘다.
          // (needs_vision skip 은 `continue` 가 먼저라 여전히 안 깎인다.)
          carry.calledCount += 1;

          // --- 캐시 조회 (원본은 ImageParser.parse 안에서 한다) ---
          const cacheDeps = { client: deps.client, env: deps.env };
          let caption = await visionCache.lookup(cacheDeps, opts.sha256, pageNum + 1);

          if (caption === null) {
            // 렌더만 여기로 미뤘다. 캐시 hit 이면 결과에 안 쓰이므로 관찰되지 않고,
            // 전 페이지 캐시 hit 재인제스트에서 13초쯤 되는 CPU 를 아낀다.
            let img = rendered.get(pageNum);
            if (!img) {
              const r = renderPageForVision(mupdf, doc, pageNum, SCAN_RENDER_DPI);
              img = { jpeg: r.jpeg, mimeType: r.mimeType };
              rendered.set(pageNum, img);
            }
            try {
              caption = deps.caption
                ? await deps.caption(img.jpeg, img.mimeType)
                : await captionImage(clientDeps, img.jpeg, img.mimeType);
            } catch (e) {
              const me = await recordCall(deps.client, deps.env, deps.nowMs, {
                success: false,
                errorMsg: pyStrError(e),
                sourceType: "pdf_vision_enrich",
                docId: opts.docId,
                page: pageNum + 1,
                retryAttempt: (e as { retryAttempt?: number })?.retryAttempt ?? null,
              });
              if (me !== null) metricErrors.push(`p${pageNum + 1} 실패기록: ${me}`);
              throw e;
            }
            const me = await recordCall(deps.client, deps.env, deps.nowMs, {
              success: true,
              sourceType: "pdf_vision_enrich",
              usage: caption.usage as unknown as Record<string, unknown> | null,
              docId: opts.docId,
              page: pageNum + 1,
              retryAttempt: caption.usage?.retry_attempt ?? null,
            });
            if (me !== null) metricErrors.push(`p${pageNum + 1}: ${me}`);
            const cost = caption.usage?.estimated_cost;
            await visionCache.upsert(cacheDeps, opts.sha256, pageNum + 1, {
              caption,
              estimatedCost: typeof cost === "number" ? cost : null,
            });
          }

          // --- 섹션 합성 + 페이지 표기 ---
          // `warnings: []` 인 이유: 원본에서 이 자리에 warning 을 넣는 건 Pillow 의
          // 디코드·EXIF 실패뿐인데, PDF 경로에서는 mupdf 가 만든 픽셀을 그대로 쓰므로
          // 그 두 경로가 존재하지 않는다. 항상 빈 배열이다.
          const pageResult = composeResult(caption, { warnings: [] });
          for (const sec of pageResult.sections) {
            const baseTitle = pyStrip(sec.section_title ?? "");
            sections.push({
              text: sec.text,
              page: pageNum + 1,
              section_title: baseTitle !== ""
                ? pyStrip(`(vision) p.${pageNum + 1} ${baseTitle}`)
                : `(vision) p.${pageNum + 1}`,
              bbox: null,
              metadata: { ...sec.metadata },
            });
          }
          if (pageResult.raw_text) rawParts.push(pageResult.raw_text);
          warnings.push(...pageResult.warnings);
          carry.completed += 1;
          if (opts.progressTotal) {
            await updateStageProgress(
              deps.client,
              opts.jobId,
              carry.completed,
              opts.progressTotal,
            );
          }
        } catch (e) {
          // 페이지 하나가 죽어도 문서는 살린다.
          failedInSweep.push(pageNum);
          if (sweepIdx === ve.maxSweeps) {
            warnings.push(
              `${label}: page ${pageNum + 1} 실패 ` +
                `(sweep ${sweepIdx}/${ve.maxSweeps} 최종): ${e}`,
            );
          }
          console.warn(
            `${label} page ${pageNum + 1} 실패 (sweep ${sweepIdx}/${ve.maxSweeps}): ` +
              `${e} (file=${opts.fileName})`,
          );
        }
      }
      pending = failedInSweep;
    }

    if (pending.length > 0) {
      const msg = `${label}: ${ve.maxSweeps} sweep 후에도 누락: ` +
        `[${pending.map((p) => p + 1).join(", ")}]`;
      warnings.push(msg);
      console.error(`${msg} (file=${opts.fileName})`);
    }

    return {
      sections,
      rawParts,
      warnings,
      carry,
      stopped: carry.budgetExceeded !== null || carry.pageCapExceeded !== null,
      failedPages: pending.map((p) => p + 1),
      metricErrors,
    };
  } finally {
    doc.destroy?.();
  }
}

/**
 * 원본 `_page_needs_vision` — 점수 계산이 깨지면 **호출하는 쪽으로** 판정한다.
 * 조용히 건너뛰면 보강이 통째로 사라지므로 보수적 기본값이 `true` 다.
 */
// deno-lint-ignore no-explicit-any
export function pageNeedsVision(doc: any, pageNum: number, fileName: string): boolean {
  try {
    const page = doc.loadPage(pageNum);
    try {
      const st = page.toStructuredText(STEXT_OPTS);
      try {
        const dict = toPageDict(st, page.getBounds());
        const score = scorePage(dict, {
          pageNum: pageNum + 1,
          pageAreaPt2: pageArea(dict),
        });
        return Boolean(score.needs_vision);
      } finally {
        st.destroy?.();
      }
    } finally {
      page.destroy?.();
    }
  } catch (e) {
    console.warn(
      `vision_need_score 계산 실패 (graceful, vision 호출 진행): ` +
        `page=${pageNum + 1} file=${fileName} err=${e}`,
    );
    return true;
  }
}

/**
 * 원본 `jobs.update_stage_progress` — `ingest_jobs.stage_progress` 에 `{current,total,unit}`.
 *
 * **원본에서 이걸 쓰는 곳은 vision 페이지 루프뿐이다.** `/documents/active` 의 진행
 * 카드와 ETA 가 여기서만 데이터를 받는다 — 빠뜨리면 진행 표시가 통째로 빈다.
 *
 * 실패는 best-effort 로 삼킨다(원본은 모듈 flag 로 이후 호출을 끄지만, Edge 는 상주
 * 프로세스가 아니라 그 flag 가 의미가 없어 매번 시도한다).
 */
async function updateStageProgress(
  client: SupabaseClient,
  jobId: string,
  current: number,
  total: number,
): Promise<void> {
  try {
    const { error } = await client
      .from("ingest_jobs")
      .update({ stage_progress: { current, total, unit: "pages" } })
      .eq("id", jobId);
    if (error) throw new Error(error.message);
  } catch (e) {
    console.debug(`stage_progress 갱신 실패 (best-effort): ${e}`);
  }
}

/** 원본 `clear_stage_progress` — 다음 stage 가 sub-progress 를 안 쓸 수 있다. */
export async function clearStageProgress(
  client: SupabaseClient,
  jobId: string,
): Promise<void> {
  try {
    await client.from("ingest_jobs").update({ stage_progress: null }).eq("id", jobId);
  } catch (e) {
    console.debug(`stage_progress 초기화 실패 (best-effort): ${e}`);
  }
}
