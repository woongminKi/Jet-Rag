/**
 * `chunk` 단계 창 분할의 **순수 함수**들.
 *
 * ## 왜 창으로 쪼개나
 * SK 사업보고서(1,513p · 추출물 20MB · 청크 25,831)가 `chunk` 단계에서 Edge 런타임에
 * 3회 kill 됐다. 랩탑 실측으로 전체 처리 3,811ms(피크 RSS 405MB) — Edge vCPU 는 더
 * 느리므로 CPU 2s 예산을 넘는다. O(n²) 는 없다. 전부 문자 수 비례라 **입력을 나누면**
 * 예산 안에 든다.
 *
 * ## 어디서 자를 수 있는가 — `page` 가 바뀌는 지점뿐
 * `chunk_merge.ts` 의 병합은 왼쪽 fold 이고, 넘어가는 상태는 `buf` 하나다. 병합 조건에
 * `buf.page === section.page` 가 있고 병합 결과도 `page: buf.page` 를 유지하므로,
 * **직전·다음 섹션의 `page` 가 다른 지점**에서는 어차피 병합이 일어나지 않는다. 거기서
 * 자르면 전체를 한 번에 처리한 것과 결과가 같다.
 *
 * 실측(실제 SK 보고서 섹션 1,903개 → 병합 170개, `chunk_window_test.ts` 가 고정):
 * - (A) page 가 바뀌는 지점에서만 컷 → **identical**
 * - (B) page 무시하고 고정 개수로 컷 → 다름 (180/174/173 vs 170)
 * - (D) extract 꼬리와 vision 첫 섹션이 같은 page 인데 그 사이를 컷 → 다름 (171 vs 170)
 *
 * `page` 가 `null` 인 섹션(HWP/DOCX)도 `null === null` 이라 서로 병합된다 — 그래서
 * "같은 page 값" 묶음에 null 묶음도 포함된다.
 *
 * ## 유일한 교차 창 의존 — 머리말/꼬리말
 * `chunk_filter.ts` 의 `header_footer` 는 **문서 전체**에서 3회 이상 반복되는 짧은
 * 텍스트를 찾는다. 창 안에서는 알 수 없다. 그래서 카운트만 캐리로 누적하고 마킹은
 * `load` 단계로 옮긴다(`handlers/load.ts`).
 */

import { cpCompare } from "./content_gate.ts";
import { collectShortCounts } from "./chunk_filter.ts";
import type { ChunkRecord } from "./chunk_records.ts";

/** `chunk` 태스크 하나가 읽을 소스 아티팩트 수. 10페이지/아티팩트이므로 40페이지다. */
export const CHUNK_ARTIFACTS_PER_TASK = 4;

/** ENV 오버라이드. 정수 ≥1 이 아니면 기본값. */
export function readArtifactsPerTask(get = Deno.env.get): number {
  const raw = get("JETRAG_CHUNK_ARTIFACTS_PER_TASK");
  const n = raw ? Number(raw) : NaN;
  return Number.isFinite(n) && n >= 1 ? Math.floor(n) : CHUNK_ARTIFACTS_PER_TASK;
}

/** 소스 아티팩트 한 행의 식별자. payload 없이 `stage, seq` 만으로 플랜을 짠다. */
export interface SourceRef {
  stage: string;
  seq: number;
}

export interface WindowPlan {
  /** 문서 순서대로 정렬된 소스 행 목록. */
  plan: SourceRef[];
  /** 창 개수. 빈 문서라도 1 이다 — part 를 하나는 남겨야 `load` 가 안 멈춘다. */
  totalWindows: number;
  extractCount: number;
  scanCount: number;
  visionCount: number;
}

/**
 * 소스 플랜 — **현행 `handlers/chunk.ts` 의 순서 규칙을 그대로 옮긴 것**이다.
 *
 * 1. 스캔 PDF 는 `scan` 이 `extract` 를 **대체**한다. 원본이
 *    `result = _reroute_pdf_to_image(...)` 로 결과를 통째로 갈아끼우기 때문이다 —
 *    텍스트가 거의 없는 extract 결과를 함께 넣으면 원본에 없는 청크가 생긴다.
 * 2. `vision` 섹션은 텍스트 섹션 **전부 뒤에** 온다. 원본 `_enrich_pdf_with_vision` 이
 *    `sections = list(base_result.sections)` 로 시작해 페이지 루프에서 append 한다.
 * 3. 스캔 문서는 vision enrich 를 안 돈다(원본이 elif 라 배타적) — 있어도 안 섞는다.
 *
 * 각 stage 안에서는 `seq` 오름차순이다. `seq` 가 곧 문서 순서다.
 */
export function windowPlan(rows: SourceRef[], count: number): WindowPlan {
  const per = Math.max(1, Math.floor(count));
  const bySeq = (a: SourceRef, b: SourceRef) => a.seq - b.seq;
  const extract = rows.filter((r) => r.stage === "extract").sort(bySeq);
  const scan = rows.filter((r) => r.stage === "scan").sort(bySeq);
  const vision = rows.filter((r) => r.stage === "vision").sort(bySeq);

  const base = scan.length > 0 ? scan : extract;
  const plan = scan.length > 0 ? [...base] : [...base, ...vision];

  return {
    plan,
    totalWindows: Math.max(1, Math.ceil(plan.length / per)),
    extractCount: extract.length,
    scanCount: scan.length,
    visionCount: vision.length,
  };
}

/** `page` 를 가진 무엇이든. 섹션 타입에 묶지 않는다 — 테스트가 최소 객체를 쓴다. */
interface HasPage {
  page: number | null;
}

/**
 * 꼬리에서 **마지막 섹션과 같은 `page` 값을 가진 연속 묶음**을 떼어낸다.
 *
 * 떼어낸 `tail` 은 다음 창으로 넘긴다. `head` 의 마지막과 `tail` 의 첫 섹션은 page 가
 * 다르므로 그 사이에서는 병합이 일어나지 않는다 — 잘라도 결과가 같다.
 *
 * 전부 같은 page 면 `head` 가 비고 전부 넘어간다. 그래도 정확하다(그 창은 청크 0 개).
 * 캐리 크기는 **한 페이지분**으로 유계다.
 */
export function splitTailByPage<T extends HasPage>(sections: T[]): { head: T[]; tail: T[] } {
  if (sections.length === 0) return { head: [], tail: [] };
  const lastPage = sections[sections.length - 1].page;
  let i = sections.length;
  // `null === null` 은 참이다 — page 가 없는 섹션끼리도 한 묶음이다.
  while (i > 0 && sections[i - 1].page === lastPage) i--;
  return { head: sections.slice(0, i), tail: sections.slice(i) };
}

/**
 * 창의 청크 텍스트를 누적 카운트(캐리)에 더한다.
 *
 * 수집 규칙은 `chunk_filter.collectShortCounts` 를 **그대로 가져다 쓴다**. 규칙이
 * 두 곳에 있으면 단일 창과 다중 창의 `filtered_reason` 이 조용히 갈린다.
 *
 * `__proto__` 같은 텍스트가 키로 올 수 있다. 보통 객체에 대입하면 **own property 가
 * 안 생기고 조용히 프로토타입이 바뀐다** — 그래서 null 프로토타입으로 복사해 쓴다.
 * (JSON.parse 결과는 `__proto__` 도 own property 라 읽기는 안전하다.)
 *
 * 실측(SK 사업보고서 1,513p): 문서 전체 고유 짧은 텍스트 11,500개 = JSON 0.64MB,
 * 그중 3회 이상은 531개. 창당 고유 텍스트는 평균 327 · 최대 889 다.
 */
export function accumulateHfCounts(
  counts: Record<string, number>,
  records: ChunkRecord[],
): Record<string, number> {
  const out: Record<string, number> = Object.assign(Object.create(null), counts);
  for (const [t, n] of collectShortCounts(records)) {
    out[t] = (out[t] ?? 0) + n;
  }
  return out;
}

/** `documents.flags` 중 창마다 나오는 값들. 창을 넘어 **OR** 로 누적한다. */
export interface DocFlagsCarry {
  has_pii: boolean;
  has_watermark: boolean;
  third_party: boolean;
  watermark_hits?: string[];
}

export function emptyDocFlags(): DocFlagsCarry {
  return { has_pii: false, has_watermark: false, third_party: false };
}

/**
 * 창별 `runContentGateStage().flagsUpdate` 를 누적값에 OR 로 합친다.
 *
 * `watermark_hits` 는 합집합 + 코드포인트 정렬이다 — 단일 창일 때 `content_gate` 가
 * 만드는 값과 **같은 순서**여야 한다. 키 순서도 단일 창(`has_pii` → `has_watermark`
 * → `third_party` → `watermark_hits`)과 맞춘다.
 */
export function mergeFlagsOr(
  acc: DocFlagsCarry,
  update: Record<string, unknown>,
): DocFlagsCarry {
  const hits = new Set<string>(acc.watermark_hits ?? []);
  const incoming = update["watermark_hits"];
  if (Array.isArray(incoming)) { for (const h of incoming) hits.add(String(h)); }

  const out: DocFlagsCarry = {
    has_pii: Boolean(acc.has_pii) || update["has_pii"] === true,
    has_watermark: Boolean(acc.has_watermark) || update["has_watermark"] === true,
    third_party: Boolean(acc.third_party) || update["third_party"] === true,
  };
  if (hits.size > 0) out.watermark_hits = [...hits].sort(cpCompare);
  return out;
}
