/**
 * `ingest/stages/dedup.py` 포팅 — 문서 임베딩 기반 중복 감지 (Tier 2 / Tier 3).
 *
 * Tier 1(SHA-256 완전 일치)은 업로드 단계가 이미 처리한다. 여기는 **내용이 비슷한**
 * 문서를 찾는다.
 *
 * | Tier | 조건 | 남기는 flags |
 * |---|---|---|
 * | 2 | cosine ≥ 0.95 | `duplicate_of` — "거의 같은 자료" |
 * | 3 | cosine ≥ 0.85 **그리고** 파일명 유사도 ≥ 0.6 | `previous_version_of` — "이전 버전 추정" |
 *
 * 검출만 한다. 병합·경고는 하지 않는다.
 *
 * ## 후보를 `default_user_id` 로 고른다 — 원본 그대로 옮겼다
 * 문서 소유자가 아니라 `settings.default_user_id` 의 문서와 비교한다. 다중 사용자에서는
 * 뜻대로 동작하지 않을 값이지만 **원본이 그렇게 한다.** 여기서 고치면 같은 입력에
 * 다른 결과가 나오므로 두고, 이 주석으로 남긴다.
 *
 * ## 파일명 유사도는 `difflib` 그대로다
 * 임계 0.6 이 걸린 값이라 근사로 대체하면 판정이 뒤집힌다 — `pydifflib.ts` 참조.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { pyFloat, pyRound, pySum } from "../pynum.ts";
import { sequenceMatcherRatio } from "../pydifflib.ts";

const TIER2_THRESHOLD = 0.95;
const TIER3_SIM_THRESHOLD = 0.85;
const TIER3_FILENAME_THRESHOLD = 0.6;

interface DocRow {
  id: string;
  title?: string | null;
  storage_path?: string | null;
  doc_embedding?: unknown;
}

/**
 * 원본 `_parse_vec` — 배열이거나 JSON 문자열. 그 외는 `TypeError`.
 *
 * 문자열 안이 배열이 아니어도 원본은 **그대로 순회한다**(`[float(x) for x in parsed]`).
 * `'"x"'` 를 주면 문자 `x` 를 `float()` 에 넣어 **ValueError** 가 난다 — TypeError 가
 * 아니다. 대조가 이 차이를 잡았다.
 */
export function parseVec(raw: unknown): number[] {
  const toFloats = (items: Iterable<unknown>): number[] => {
    const out: number[] = [];
    for (const x of items) {
      const v = pyFloat(x);
      if (v === null) {
        throw new RangeError(`could not convert to float: ${JSON.stringify(x)}`);
      }
      out.push(v);
    }
    return out;
  };
  if (Array.isArray(raw)) return toFloats(raw);
  if (typeof raw === "string") {
    const parsed = JSON.parse(raw);
    // 숫자처럼 순회 불가한 값은 Python 이 TypeError 를 낸다.
    if (parsed === null || typeof parsed === "number" || typeof parsed === "boolean") {
      throw new TypeError(`'${typeof parsed}' object is not iterable`);
    }
    if (typeof parsed === "string") return toFloats(parsed); // 글자 단위 — ValueError 유도
    if (Array.isArray(parsed)) return toFloats(parsed);
    return toFloats(Object.keys(parsed as object)); // dict 는 키를 순회한다
  }
  throw new TypeError(`doc_embedding 파싱 실패: ${raw === null ? "NoneType" : typeof raw}`);
}

/**
 * 원본 `_cosine`. 길이가 다르거나 비면 0.
 *
 * **`sum()` 은 보정 합이다** — 단순 루프로 옮기면 1024 차원에서 마지막 자리가 어긋나고,
 * 임계(0.95/0.85) 근처에서 판정이 바뀔 수 있다. `pySum` 참조.
 */
export function cosine(a: number[], b: number[]): number {
  if (a.length === 0 || b.length === 0 || a.length !== b.length) return 0.0;
  const prods = new Array<number>(a.length);
  const sqA = new Array<number>(a.length);
  const sqB = new Array<number>(b.length);
  for (let i = 0; i < a.length; i++) {
    prods[i] = a[i] * b[i];
    sqA[i] = a[i] * a[i];
    sqB[i] = b[i] * b[i];
  }
  const dot = pySum(prods);
  const ra = Math.sqrt(pySum(sqA));
  const rb = Math.sqrt(pySum(sqB));
  if (ra === 0 || rb === 0) return 0.0;
  return dot / (ra * rb);
}

/** 원본 `_filename_similarity` — 한쪽이라도 비면 0. 소문자로 낮춰 비교한다. */
export function filenameSimilarity(a: string, b: string): number {
  if (!a || !b) return 0.0;
  return sequenceMatcherRatio(a.toLowerCase(), b.toLowerCase());
}

export interface DedupDeps {
  client: SupabaseClient;
  /** `settings.default_user_id` — 원본이 후보를 이 값으로 고른다. */
  defaultUserId: string;
}

export type DedupMatch = Record<string, unknown>;

/**
 * 원본 `run_dedup_stage`. 매칭이 있으면 그 정보를, 없으면 `null`.
 *
 * `doc_embedding` 이 없으면 스킵이다 — 원본은 스테이지를 `skipped` 로 남긴다.
 */
export async function runDedupStage(
  deps: DedupDeps,
  docId: string,
): Promise<{ match: DedupMatch | null; skipped: boolean; reason?: string }> {
  const { data: meData, error: meErr } = await deps.client
    .from("documents")
    .select("id, title, storage_path, doc_embedding")
    .eq("id", docId)
    .limit(1);
  if (meErr) throw new Error(`documents 조회 실패: ${meErr.message}`);
  const me = (meData ?? [])[0] as DocRow | undefined;
  if (!me || !me.doc_embedding) {
    return { match: null, skipped: true, reason: "doc_embedding 이 없어 스킵" };
  }

  const myVec = parseVec(me.doc_embedding);
  const myName = me.storage_path || me.title || "";

  const { data: candData, error: cErr } = await deps.client
    .from("documents")
    .select("id, title, storage_path, doc_embedding")
    .eq("user_id", deps.defaultUserId)
    .is("deleted_at", null)
    .neq("id", docId)
    .not("doc_embedding", "is", null);
  if (cErr) throw new Error(`후보 조회 실패: ${cErr.message}`);
  const candidates = (candData ?? []) as DocRow[];
  if (candidates.length === 0) {
    console.info(`dedup: doc=${docId} 비교 대상 없음`);
    return { match: null, skipped: false };
  }

  const ranked: [number, DocRow][] = candidates.map((c) => [
    cosine(myVec, parseVec(c.doc_embedding)),
    c,
  ]);
  // 원본 `ranked.sort(key=lambda x: x[0], reverse=True)` — Python 정렬은 **안정적**이라
  // 점수가 같으면 조회 순서가 유지된다. JS `sort` 도 안정적이다(ES2019+).
  ranked.sort((x, y) => y[0] - x[0]);

  const [topSim, topRow] = ranked[0];
  const topName = topRow.storage_path || topRow.title || "";
  const fnameSim = filenameSimilarity(myName, topName);

  let match: DedupMatch | null = null;
  if (topSim >= TIER2_THRESHOLD) {
    match = {
      duplicate_tier: 2,
      duplicate_of: topRow.id,
      duplicate_similarity: pyRound(topSim, 4),
    };
  } else if (topSim >= TIER3_SIM_THRESHOLD && fnameSim >= TIER3_FILENAME_THRESHOLD) {
    match = {
      duplicate_tier: 3,
      previous_version_of: topRow.id,
      duplicate_similarity: pyRound(topSim, 4),
      filename_similarity: pyRound(fnameSim, 4),
    };
  }

  if (match) {
    const { data: fData, error: fErr } = await deps.client
      .from("documents").select("flags").eq("id", docId).limit(1);
    if (fErr) throw new Error(`flags 조회 실패: ${fErr.message}`);
    const existing =
      ((fData ?? [])[0] as { flags?: Record<string, unknown> } | undefined)?.flags ?? {};
    const { error: uErr } = await deps.client
      .from("documents").update({ flags: { ...existing, ...match } }).eq("id", docId);
    if (uErr) throw new Error(`flags 갱신 실패: ${uErr.message}`);
    console.info(
      `dedup: doc=${docId} tier=${match.duplicate_tier} other=${topRow.id} ` +
        `sim=${topSim.toFixed(4)}`,
    );
  } else {
    console.info(
      `dedup: doc=${docId} 매칭 없음 (top_sim=${topSim.toFixed(4)}, ` +
        `fname=${fnameSim.toFixed(4)})`,
    );
  }
  return { match, skipped: false };
}
