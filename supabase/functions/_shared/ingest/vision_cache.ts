/**
 * `api/app/services/vision_cache.py` 포팅 — `vision_page_cache` 조회/적재.
 *
 * `(sha256, page, prompt_version)` 3-튜플 키다. 같은 PDF 를 다시 인제스트해도 vision
 * 호출이 0 이 된다 — 재인제스트 비용의 대부분이 여기서 사라진다.
 *
 * ## prompt_version 을 올리면 캐시가 통째로 무효화된다
 * 기본값 `v2` 는 `table_caption` / `figure_caption` 2 필드가 추가된 시점이다. v1 row 는
 * 감사 목적으로 남아 있지만 v2 조회와 매칭되지 않는다 — cold start 때 비용이 한 번 튄다.
 *
 * ## 캐시 hit 이면 usage 가 없다
 * 새 호출이 아니므로 `usage: null` 이다. 그래서 `vision_usage_log` 에도 안 남고,
 * 절감이 별도 계산 없이 그대로 측정된다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { pyTruthy, type VisionCaption, type VisionCategory } from "./vision_caption.ts";

let firstWarnLogged = false;
function warnFirst(msg: string): void {
  if (!firstWarnLogged) {
    firstWarnLogged = true;
    console.warn(`${msg} — 마이그 015(vision_page_cache) 적용 후 자동 회복.`);
  } else {
    console.debug(msg);
  }
}

/** 테스트용 — 첫 warn 플래그 초기화. */
export function resetFirstWarnForTest(): void {
  firstWarnLogged = false;
}

/** 원본 `get_prompt_version()` — `.strip() or "v2"` 까지 같다(공백뿐이면 기본값). */
export function getPromptVersion(env: Record<string, string | undefined>): string {
  const raw = (env["JETRAG_VISION_PROMPT_VERSION"] ?? "v2").trim();
  return raw === "" ? "v2" : raw;
}

/** 원본 `is_enabled()` — `"0"` 일 때만 끈다. */
export function isEnabled(env: Record<string, string | undefined>): boolean {
  return env["JETRAG_VISION_CACHE_ENABLED"] !== "0";
}

/** 원본 `_serialize` — usage 는 별도 컬럼 영역이라 안 담는다. */
export function serialize(caption: VisionCaption): Record<string, unknown> {
  return {
    type: caption.type,
    ocr_text: caption.ocr_text,
    caption: caption.caption,
    structured: caption.structured,
    table_caption: caption.table_caption,
    figure_caption: caption.figure_caption,
  };
}

/**
 * 원본 `_deserialize` — 깨진 row 는 강제 마이그레이션 없이 그냥 `null`.
 *
 * `raw.get("ocr_text") or ""` 를 JS `||` 로 옮기면 틀린다 — 빈 배열·빈 객체가 Python
 * 에서는 falsy 인데 JS 에서는 truthy 다. `pyTruthy` 를 쓴다.
 */
export function deserialize(raw: unknown): VisionCaption | null {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  const capType = r["type"];
  const ocrText = pyTruthy(r["ocr_text"]) ? r["ocr_text"] : "";
  const captionText = pyTruthy(r["caption"]) ? r["caption"] : "";
  const structured = r["structured"];
  if (typeof capType !== "string") return null;

  let tableCaption = r["table_caption"];
  if (typeof tableCaption !== "string" || tableCaption.trim() === "") tableCaption = null;
  let figureCaption = r["figure_caption"];
  if (typeof figureCaption !== "string" || figureCaption.trim() === "") figureCaption = null;

  // 원본은 VisionCategory Literal 검증을 호출자에게 맡긴다 — 여기서 조이지 않는다.
  return {
    type: capType as VisionCategory,
    ocr_text: ocrText as string,
    caption: captionText as string,
    structured: (structured !== null && typeof structured === "object" &&
        !Array.isArray(structured))
      ? structured as Record<string, unknown>
      : null,
    usage: null, // 캐시 hit 은 새 호출이 아니다
    table_caption: tableCaption as string | null,
    figure_caption: figureCaption as string | null,
  };
}

export interface CacheDeps {
  client: SupabaseClient;
  env: Record<string, string | undefined>;
}

/** miss / 비활성 / DB 실패 모두 `null` — 호출자는 정상 vision 호출로 넘어간다. */
export async function lookup(
  deps: CacheDeps,
  sha256: string | null | undefined,
  page: number | null | undefined,
): Promise<VisionCaption | null> {
  if (!isEnabled(deps.env)) return null;
  if (!sha256 || page === null || page === undefined) return null;
  try {
    const { data, error } = await deps.client
      .from("vision_page_cache")
      .select("result")
      .eq("sha256", sha256)
      .eq("page", page)
      .eq("prompt_version", getPromptVersion(deps.env))
      .limit(1);
    if (error) throw new Error(error.message);
    const rows = (data ?? []) as Array<Record<string, unknown>>;
    if (rows.length === 0) return null;
    return deserialize(rows[0]["result"]);
  } catch (err) {
    warnFirst(`vision_page_cache lookup 실패 (graceful): ${err}`);
    return null;
  }
}

/**
 * 주어진 페이지 중 캐시 미스 개수.
 *
 * - `0`    : 전부 hit → 이번 재인제스트의 신규 비용이 0 이므로 사전 cap 검사를 건너뛸 수 있다
 * - `N>0`  : 미스 N 개 → 사전 cap 검사 적용
 * - `null` : DB 부재·비활성·인자 부적합 → 호출자는 보수적으로 사전 cap 검사 적용
 *
 * 페이지가 몇 개든 왕복 1회다(`in` 한 방).
 */
export async function countUncachedPages(
  deps: CacheDeps,
  sha256: string | null | undefined,
  pages: number[],
): Promise<number | null> {
  if (!isEnabled(deps.env)) return null;
  if (!sha256) return null;
  if (pages.length === 0) return 0; // 빈 목록 = "미스 0개" 와 같은 뜻
  try {
    const { data, error } = await deps.client
      .from("vision_page_cache")
      .select("page")
      .eq("sha256", sha256)
      .eq("prompt_version", getPromptVersion(deps.env))
      .in("page", pages);
    if (error) throw new Error(error.message);
    const cached = new Set<number>();
    for (const row of (data ?? []) as Array<Record<string, unknown>>) {
      const p = row["page"];
      // 원본은 `isinstance(p, int)` — Python 에서 True 도 int 지만 DB 가 낼 일은 없다.
      if (typeof p === "number" && Number.isInteger(p)) cached.add(p);
    }
    let miss = 0;
    for (const p of pages) if (!cached.has(p)) miss++;
    return miss;
  } catch (err) {
    warnFirst(`vision_page_cache count_uncached_pages 실패 (graceful): ${err}`);
    return null;
  }
}

/**
 * `ON CONFLICT DO NOTHING` 으로 적재 — 동시에 같은 페이지를 구워도 먼저 저장된 게 이긴다.
 *
 * 실패해도 삼킨다. 다음 재인제스트 때 다시 시도한다.
 */
export async function upsert(
  deps: CacheDeps,
  sha256: string | null | undefined,
  page: number | null | undefined,
  opts: { caption: VisionCaption; estimatedCost?: number | null },
): Promise<void> {
  if (!isEnabled(deps.env)) return;
  if (!sha256 || page === null || page === undefined) return;
  const row = {
    sha256,
    page,
    prompt_version: getPromptVersion(deps.env),
    result: serialize(opts.caption),
    estimated_cost: opts.estimatedCost ?? null,
  };
  try {
    const { error } = await deps.client
      .from("vision_page_cache")
      .upsert(row, { onConflict: "sha256,page,prompt_version", ignoreDuplicates: true });
    if (error) throw new Error(error.message);
  } catch (err) {
    warnFirst(`vision_page_cache upsert 실패 (graceful): ${err}`);
  }
}
