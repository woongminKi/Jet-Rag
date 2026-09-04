/**
 * 질의 의도 룰 — `services/intent_router.py` 포팅. 외부 호출 0, 순수 정규식·키워드.
 *
 * 7 개 신호(T1~T7)를 매기고, 그중 셋이 `/search` 동작을 실제로 바꾼다:
 *
 * | 쓰이는 곳 | 조건 | 효과 |
 * |---|---|---|
 * | MMR 재정렬 | **T1 단독** | 문서 순서가 다양성 기준으로 바뀐다 |
 * | 청크 미리보기 cap | T1·T2·T7 중 하나 | doc 당 3 → 8 |
 * | decomposition 게이트 | `needs_decomposition` | 운영에선 ENV OFF 라 무동작 |
 *
 * 즉 신호 하나가 잘못 발화하면 **결과 순서와 노출 청크 수가 통째로 바뀐다.**
 *
 * ## `\s`·`len`·`split` 이 언어마다 다르다
 * - Python `re` 의 `\s` 와 JS 의 `\s` 는 문자 집합이 다르다(`guards.ts` 와 같은 이유).
 *   `\S` 도 그 여집합이라 같이 어긋난다 → 명시 클래스를 쓴다.
 * - `len(text)` 는 코드포인트 단위다. T5 는 40 자 임계라 astral 문자가 섞이면 갈린다.
 * - `str.split()` 은 인자 없이 부르면 연속 공백을 하나로 보고 양 끝을 버린다.
 *   `split(/\s+/)` 은 앞이 비면 빈 문자열이 하나 생겨 토큰 수가 1 늘어난다.
 * - `str.strip()` 이 버리는 문자 집합은 `re` 의 `\s` 와 **같다**(전수 확인).
 * - `.{0,15}` 의 `.` 은 Python 이 코드포인트 1 개, JS 는 UTF-16 1 개를 센다. astral 문자가
 *   섞이면 창 크기가 달라지므로 정규식에 `u` 플래그를 붙였다. (`.` 이 `\r`·U+2028 을
 *   Python 과 달리 제외하는 차이는 정규화가 그 문자들을 공백으로 접어서 사라진다.)
 */

/** Python `re` 의 `\s`(str 패턴) 문자 집합 — 대괄호 없는 본문. */
const SP = " \\t\\n\\r\\f\\v\\u001c-\\u001f\\u0085\\u00a0\\u1680\\u2000-\\u200a" +
  "\\u2028\\u2029\\u202f\\u205f\\u3000";
const S_RE = new RegExp(`[${SP}]+`, "gu");
const STRIP_RE = new RegExp(`^[${SP}]+|[${SP}]+$`, "gu");

// --- T1 cross-doc -----------------------------------------------------------
const T1_CROSS_DOC = /(자료|문서|보고서).{0,15}(랑|와|과|및).{0,15}(자료|문서)/u;
const DOC_NOUN =
  "자료|문서|보고서|안내서|규정|내규|이력서|포트폴리오|포폴|템플릿|판결|계획|사업|매뉴얼|카탈로그|논문";
/** `NP1 (와|과|랑) NP2 …(0~15자)… 문서류명사` — NP2 가 고유명사여도 잡는다. */
const T1_PAIR = new RegExp(
  `[가-힣A-Za-z0-9]+[${SP}]*(?:와|과|랑)[${SP}]*[가-힣A-Za-z0-9].{0,15}(?:${DOC_NOUN})`,
  "u",
);
/** `문서류명사… (와|과|랑) NP2`. */
const T1_PAIR2 = new RegExp(
  `(?:${DOC_NOUN})[^${SP}]*[${SP}]*(?:와|과|랑)[${SP}]*[가-힣A-Za-z0-9]`,
  "u",
);
/** `문서류명사들 …(에서|에|중…)` — 복수형 명시일 때만. */
const T1_PLURAL = new RegExp(`(?:${DOC_NOUN})들[${SP}]*(?:에서|에|중에서|중에|중)`, "u");

// --- T2~T6 키워드 -----------------------------------------------------------
const T2_COMPARE_KEYWORDS = ["차이", "비교", "vs", "달라", "대비"] as const;
const T2_COMPARE_STEM = /다르[게지]|다른[가지]|다릅|상이/u;
const T3_CAUSAL_KEYWORDS = ["왜", "이유", "때문", "원인", "어째서"] as const;
const T4_CHANGE_KEYWORDS = ["달라진", "바뀐", "변경", "수정된", "업데이트"] as const;
/** `"그 "` 는 뒤 공백까지가 키워드다 — `"그것"` 같은 노이즈와 구분하려는 의도. */
const T6_AMBIGUOUS_KEYWORDS = [
  "그거",
  "그때",
  "그 ",
  "어디였더라",
  "뭐였지",
  "어떻게 됐더라",
] as const;

const T5_CHAR_THRESHOLD = 40;
const T5_TOKEN_THRESHOLD = 12;
const T7_PARTICLE_THRESHOLD = 2;

const CONFIDENCE_BASE = 1.0;
const CONFIDENCE_PER_SIGNAL = 0.15;
const CONFIDENCE_T6_PENALTY = 0.3;

export const SIGNAL_T1 = "T1_cross_doc";
export const SIGNAL_T2 = "T2_compare";
export const SIGNAL_T3 = "T3_causal";
export const SIGNAL_T4 = "T4_change";
export const SIGNAL_T5 = "T5_long_query";
export const SIGNAL_T6 = "T6_low_confidence";
export const SIGNAL_T7 = "T7_multi_target";

/** 미리보기 청크 cap 을 3 → 8 로 올리는 신호 집합. */
const CROSS_DOC_CLASS_SIGNALS: ReadonlySet<string> = new Set([
  SIGNAL_T1,
  SIGNAL_T2,
  SIGNAL_T7,
]);

export interface IntentDecision {
  needsDecomposition: boolean;
  /** 발화 순서는 언제나 T1→T7. */
  triggeredSignals: string[];
  confidenceScore: number;
  queryNormalized: string;
  matchedKeywords: string[];
}

/** NFC + 양 끝 공백 제거 + 내부 연속 공백을 하나로. */
function normalize(query: string): string {
  const stripped = query.replace(STRIP_RE, "");
  return stripped.normalize("NFC").replace(S_RE, " ");
}

function matchKeywords(text: string, keywords: readonly string[]): string[] {
  return keywords.filter((kw) => text.includes(kw));
}

/** Python `str.split()` — 연속 공백을 하나로 보고 양 끝 빈 토큰을 버린다. */
function pythonSplit(text: string): string[] {
  return text.split(new RegExp(`[${SP}]+`, "u")).filter((t) => t !== "");
}

function isLongQuery(text: string): boolean {
  // 코드포인트 기준 — `.length` 를 쓰면 astral 문자에서 임계가 어긋난다.
  if ([...text].length >= T5_CHAR_THRESHOLD) return true;
  return pythonSplit(text).length >= T5_TOKEN_THRESHOLD;
}

function countOccurrences(text: string, needle: string): number {
  let n = 0;
  let i = text.indexOf(needle);
  while (i !== -1) {
    n++;
    i = text.indexOf(needle, i + needle.length);
  }
  return n;
}

function countTargetParticles(text: string): number {
  return countOccurrences(text, "랑") + countOccurrences(text, "과");
}

/**
 * 질의를 7 신호로 분석한다. 빈 질의는 원본이 `ValueError` 를 던지므로 여기서도
 * `null` 을 돌려주고, 호출부(`isCrossDocQuery` 등)가 원본처럼 `false` 로 흡수한다.
 */
export function route(query: string | null | undefined): IntentDecision | null {
  if (query === null || query === undefined) return null;
  if (query.replace(STRIP_RE, "") === "") return null;

  const normalized = normalize(query);
  const signals: string[] = [];
  const matched: string[] = [];

  const t1Hit = T1_CROSS_DOC.test(normalized) || T1_PAIR.test(normalized) ||
    T1_PAIR2.test(normalized) || T1_PLURAL.test(normalized);
  if (t1Hit) signals.push(SIGNAL_T1);

  const t2Matches = matchKeywords(normalized, T2_COMPARE_KEYWORDS);
  const t2Stem = T2_COMPARE_STEM.exec(normalized);
  if (t2Matches.length || t2Stem) {
    signals.push(SIGNAL_T2);
    matched.push(...t2Matches);
    if (t2Stem) matched.push(t2Stem[0]);
  }

  const t3Matches = matchKeywords(normalized, T3_CAUSAL_KEYWORDS);
  if (t3Matches.length) {
    signals.push(SIGNAL_T3);
    matched.push(...t3Matches);
  }

  const t4Matches = matchKeywords(normalized, T4_CHANGE_KEYWORDS);
  if (t4Matches.length) {
    signals.push(SIGNAL_T4);
    matched.push(...t4Matches);
  }

  if (isLongQuery(normalized)) signals.push(SIGNAL_T5);

  const t6Matches = matchKeywords(normalized, T6_AMBIGUOUS_KEYWORDS);
  if (t6Matches.length) {
    signals.push(SIGNAL_T6);
    matched.push(...t6Matches);
  }

  // T7 은 T1 이 안 떴을 때만 본다 — 같은 현상을 두 번 세지 않으려는 의도.
  if (!t1Hit && countTargetParticles(normalized) >= T7_PARTICLE_THRESHOLD) {
    signals.push(SIGNAL_T7);
  }

  const fired = new Set(signals);
  const primary = fired.has(SIGNAL_T1) || fired.has(SIGNAL_T2) ||
    fired.has(SIGNAL_T3) || fired.has(SIGNAL_T7);
  const combined = fired.has(SIGNAL_T5) && fired.has(SIGNAL_T6);

  let score = CONFIDENCE_BASE - CONFIDENCE_PER_SIGNAL * signals.length;
  if (fired.has(SIGNAL_T6)) score -= CONFIDENCE_T6_PENALTY;

  return {
    needsDecomposition: primary || combined,
    triggeredSignals: signals,
    confidenceScore: Math.max(0.0, Math.min(1.0, score)),
    queryNormalized: normalized,
    matchedKeywords: matched,
  };
}

/** MMR 을 적용할지 — **T1 단독**이다. 더 넓은 집합을 쓰면 순서가 달라진다. */
export function isCrossDocQuery(query: string, decision?: IntentDecision | null): boolean {
  const d = decision !== undefined ? decision : route(query);
  return d !== null && d.triggeredSignals.includes(SIGNAL_T1);
}

/** 미리보기 청크 cap 을 올릴지 — T1·T2·T7 중 하나라도 발화하면. */
export function isCrossDocClassQuery(query: string, decision?: IntentDecision | null): boolean {
  const d = decision !== undefined ? decision : route(query);
  if (d === null) return false;
  return d.triggeredSignals.some((s) => CROSS_DOC_CLASS_SIGNALS.has(s));
}
