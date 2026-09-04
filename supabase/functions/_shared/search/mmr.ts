/**
 * MMR 다양성 재정렬 — `services/mmr.py` 포팅.
 *
 * `score(c) = λ·rel(c) − (1−λ)·max_{s∈S} sim(c, s)` 를 greedy 로 돌려, 같은 성격의
 * 문서가 상위를 독점하지 않게 한다. **T1(cross-doc) 질의에서만** 호출되고, 그때는
 * 최종 문서 순서를 바꾼다.
 *
 * ## 결정성이 계약이다
 * 원본은 동률에서 **id 사전순**으로 끊는다. 그래서 두 군데에 정렬이 들어 있다:
 * 첫 선택은 `(-rel, id)` 로 정렬한 뒤 맨 앞, 이후 반복은 `sorted(remaining)` 순회에
 * `>` 비교(같으면 갱신 안 함)라 결국 작은 id 가 남는다. 순회 순서를 바꾸면 동률에서
 * 다른 문서가 뽑힌다.
 *
 * ## 문자열 비교 단위
 * Python 의 `<` 는 **코드포인트** 순, JS 의 `<` 는 UTF-16 코드유닛 순이다.
 * 서로게이트 구간(U+E000~U+FFFF vs astral)에서 순서가 뒤집힌다. 식별자는 UUID 라
 * 실제로는 ASCII 지만, 정렬 규칙이 곧 순위라서 코드포인트 비교로 맞췄다.
 *
 * ## 부동소수 — 실측해서 닫은 것
 * 코사인은 왼쪽부터 한 번에 누적하므로 연산 순서가 같으면 IEEE754 로 같은 값이 나온다.
 * 다만 원본이 `x ** 0.5` 를 쓰는데 이건 libm `pow` 라, macOS 에서 재면 `Math.sqrt` 와
 * 20 만 건 중 261 건이 **1 ulp** 어긋난다. 그래서 "1 ulp 가 순위를 바꾸는가" 를 Python
 * 안에서 두 방식으로 직접 재도록 채점기에 넣었다 (`verify_search_intent_mmr_parity.py`).
 * 결과는 그 스크립트 출력에 남는다 — libm 종류와 무관하게 답이 나온다.
 */

const DEFAULT_LAMBDA = 0.7;
/** embedding 이 없으면 다양성 항을 0 으로 둔다 — relevance 만 반영. */
const MISSING_SIM = 0.0;

export const ENV_LAMBDA = "JETRAG_MMR_LAMBDA";
export const ENV_DISABLE = "JETRAG_MMR_DISABLE";

/** `JETRAG_MMR_DISABLE=1` 일 때만 끈다. 기본은 **켜짐**. */
export function isDisabled(read: (k: string) => string | undefined): boolean {
  return (read(ENV_DISABLE) ?? "0").trim() === "1";
}

/** Python `re` 의 `\s` 문자 집합 — JS `trim()` 과 달리 U+FEFF 를 포함하지 않는다. */
const PY_SP = " \\t\\n\\r\\f\\v\\u001c-\\u001f\\u0085\\u00a0\\u1680\\u2000-\\u200a" +
  "\\u2028\\u2029\\u202f\\u205f\\u3000";
const PY_STRIP_RE = new RegExp(`^[${PY_SP}]+|[${PY_SP}]+$`, "gu");

/**
 * 유니코드 십진 숫자(카테고리 Nd)를 ASCII 로 옮긴다. Python `float()` 은 파싱 전에
 * 이 변환을 하므로 `float("０.５")` 가 0.5 다 — Nd 문자 680 개 전량이 그렇다(전수 확인).
 * Nd 는 언제나 10 개씩 연속된 구간으로 나타나므로(구간 64 개 전부 확인) 구간의 `0` 까지
 * 내려가 차이를 구하면 값이 나온다.
 */
function ndToAscii(s: string): string {
  if (!/\p{Nd}/u.test(s)) return s;
  return [...s].map((ch) => {
    if (!/^\p{Nd}$/u.test(ch)) return ch;
    const cp = ch.codePointAt(0)!;
    let base = cp;
    while (cp - base < 9 && base > 0 && /^\p{Nd}$/u.test(String.fromCodePoint(base - 1))) {
      base--;
    }
    return String(cp - base);
  }).join("");
}

/**
 * Python `float()` 의 파싱 규약 — 유니코드 공백·십진 숫자 허용, 자리 구분 밑줄 허용,
 * `inf`/`nan` 허용(대소문자 무시). JS `trim()`·`Number()` 와는 받는 범위가 다르다.
 */
function pyFloat(raw: string): number | null {
  const t = ndToAscii(raw.replace(PY_STRIP_RE, ""));
  if (t === "") return null;
  // 밑줄은 숫자 사이에만 올 수 있다.
  const cleaned = t.replace(/(?<=[0-9])_(?=[0-9])/g, "");
  if (cleaned.includes("_")) return null;
  if (/^[+-]?(inf|infinity)$/i.test(cleaned)) {
    return cleaned.startsWith("-") ? -Infinity : Infinity;
  }
  if (/^[+-]?nan$/i.test(cleaned)) return NaN;
  if (!/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(cleaned)) return null;
  return Number(cleaned);
}

/**
 * `JETRAG_MMR_LAMBDA` → 0.0~1.0. 못 읽거나 범위를 벗어나면 기본값.
 *
 * 원본은 `NaN` 을 걸러내지 못한다 — `value < 0.0 or value > 1.0` 이 `NaN` 에는 둘 다
 * `False` 라 그대로 통과한다. 그 동작까지 옮겼다.
 */
export function resolveLambda(read: (k: string) => string | undefined): number {
  const raw = read(ENV_LAMBDA);
  if (raw === undefined || raw === "") return DEFAULT_LAMBDA;
  const value = pyFloat(raw);
  if (value === null) return DEFAULT_LAMBDA;
  if (value < 0.0 || value > 1.0) return DEFAULT_LAMBDA;
  return value;
}

/** Python `str` 비교와 같은 코드포인트 순. 음수/0/양수를 돌려준다. */
export function comparePython(a: string, b: string): number {
  const ca = [...a];
  const cb = [...b];
  const n = Math.min(ca.length, cb.length);
  for (let i = 0; i < n; i++) {
    const x = ca[i].codePointAt(0)!;
    const y = cb[i].codePointAt(0)!;
    if (x !== y) return x < y ? -1 : 1;
  }
  return ca.length - cb.length;
}

/** 코사인 유사도. 차원이 다르거나 영벡터면 `null`. */
export function cosine(a: readonly number[], b: readonly number[]): number | null {
  if (a.length !== b.length) return null;
  let dot = 0.0;
  let normA = 0.0;
  let normB = 0.0;
  // 누적 순서가 곧 결과값이다 — 원본과 같은 왼쪽부터 한 번의 루프.
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }
  if (normA <= 0.0 || normB <= 0.0) return null;
  return dot / (Math.sqrt(normA) * Math.sqrt(normB));
}

function relOf(relevance: Map<string, number>, cid: string): number {
  return relevance.get(cid) ?? 0.0;
}

function maxSimToSelected(
  cid: string,
  selected: readonly string[],
  embeddings: Map<string, number[]>,
): number {
  const embC = embeddings.get(cid);
  if (embC === undefined) return MISSING_SIM;
  let best = MISSING_SIM;
  for (const sid of selected) {
    const embS = embeddings.get(sid);
    if (embS === undefined) continue;
    const sim = cosine(embC, embS);
    if (sim === null) continue;
    if (sim > best) best = sim;
  }
  return best;
}

export interface RerankOptions {
  relevance: Map<string, number>;
  embeddingsById: Map<string, number[]>;
  topK: number;
  lambda: number;
}

/**
 * greedy MMR 선택. 후보가 모자라면 있는 만큼만 돌려준다.
 *
 * 첫 항목은 relevance 최대(동률이면 id 사전순 앞), 이후는 MMR 점수 최대를 반복한다.
 */
export function rerank(candidateIds: Iterable<string>, opts: RerankOptions): string[] {
  const candidates = [...candidateIds];
  if (candidates.length === 0 || opts.topK <= 0) return [];

  const remaining = [...candidates];
  const selected: string[] = [];

  // 첫 선택 — `(-rel, id)` 오름차순의 맨 앞. `max` 대신 정렬을 쓰는 건 결정성 때문이다.
  const sortedByRel = [...remaining].sort((a, b) => {
    const ra = -relOf(opts.relevance, a);
    const rb = -relOf(opts.relevance, b);
    if (ra !== rb) return ra < rb ? -1 : 1;
    return comparePython(a, b);
  });
  const first = sortedByRel[0];
  selected.push(first);
  remaining.splice(remaining.indexOf(first), 1);

  while (remaining.length > 0 && selected.length < opts.topK) {
    let bestId: string | null = null;
    let bestScore = -Infinity;
    // 사전순 순회 + `>` 비교 → 동률이면 작은 id 가 남는다.
    for (const cid of [...remaining].sort(comparePython)) {
      const rel = relOf(opts.relevance, cid);
      const simMax = maxSimToSelected(cid, selected, opts.embeddingsById);
      const score = opts.lambda * rel - (1.0 - opts.lambda) * simMax;
      if (score > bestScore) {
        bestScore = score;
        bestId = cid;
      }
    }
    if (bestId === null) break;
    selected.push(bestId);
    remaining.splice(remaining.indexOf(bestId), 1);
  }

  return selected;
}

/**
 * pgvector 응답(문자열 `"[1.0,2.0,…]"` 또는 배열) → 숫자 배열. 실패 시 `null`.
 *
 * 배열로 올 때만 1024 차원을 확인한다 — 문자열 경로에는 그 검사가 없다(원본 그대로).
 */
export function coerceEmbedding(raw: unknown): number[] | null {
  if (!raw) return null;
  // Python `strip("[]")` 는 양 끝의 `[` `]` 를 종류 구분 없이 전부 떼어낸다.
  if (typeof raw === "string") {
    const out: number[] = [];
    for (const part of raw.replace(/^[\[\]]+|[\[\]]+$/g, "").split(",")) {
      const v = pyFloat(part);
      if (v === null) return null;
      out.push(v);
    }
    return out;
  }
  if (Array.isArray(raw) && raw.length === 1024) {
    const out: number[] = [];
    for (const x of raw) {
      const v = typeof x === "number" ? x : pyFloat(String(x));
      if (v === null) return null;
      out.push(v);
    }
    return out;
  }
  return null;
}
