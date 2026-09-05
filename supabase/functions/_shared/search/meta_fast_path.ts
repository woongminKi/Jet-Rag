/**
 * 메타 필터 fast path — `services/meta_filter_fast_path.py` 포팅.
 *
 * 질의가 **순수 메타 조건**(날짜·태그·문서명)만 요구하면 임베딩·RPC 없이 documents
 * SELECT 한 번으로 답한다. 원본에서는 빈 질의 검사 직후에 돌고, 결과가 있으면 그대로
 * 반환한다 — 즉 **ENV 토글이 아니라 항상 켜진 분기**다.
 *
 * ## 왜 이걸 반드시 옮겨야 했나
 * 토글이 아니라서 `unsupported.ts` 로 막을 수 없다. 미이식 상태로 전환하면 fast path
 * 질의가 Edge 에서 **조용히 RAG 경로로** 처리된다 — 응답 모양은 같고 순서·`meta` 만
 * 달라지므로 발견이 늦다. Task 2.8 패리티에서 23 건 중 3 건이 이 이유로 건너뛰어졌다.
 *
 * ## 0 건이면 fast path 를 버린다
 * `run()` 이 0 행이면 호출자는 **RAG 경로로 되돌아간다**(`meta_fast_fallback`).
 * "SK 사업보고서 매출" 처럼 문서유형어와 내용어가 섞인 질의에서 제목 ILIKE 가 0 건이
 * 되는데, 예전엔 그대로 빈 결과를 반환해서 "어렴풋한 기억으로 검색" 의도를 배신했다.
 *
 * ## 원본이 죽는 입력을 그대로 재현한다
 * Python `datetime` 의 최대 연도는 9999 라, `9999-12-31`(하루 더하기) 과 `9999년 12월`
 * (다음 달 1 일) 에서 **잡히지 않은 예외**가 난다. 운영 실측(2026-09-05):
 * `/search?q=9999-12-31 자료` → **500 Internal Server Error**. 사용자가 만들 수 있는 500 이다.
 *
 * 여기서 조용히 고치면 Edge 만 200 을 돌려주게 되므로, 이관 중에는 **같이 던진다**.
 * 고치는 건 이관과 분리된 별도 작업이다(플랜의 결정 항목 참조).
 *
 * ## 판정이 보수적인 이유
 * 명사 단독 질의(`결론`, `시트 종류`)는 RAG 로 보낸다. 문서유형 접미어(`문서`·`보고서`…)가
 * 있거나 날짜·태그가 이미 잡혔을 때만 제목 매칭을 허용한다 — 아니면 일반 질의가 통째로
 * fast path 로 새어 검색 품질이 무너진다.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { ndToAscii, PY_SP, PY_SPACE_RUN_RE, pyStrip } from "./pystr.ts";

/** 문서유형 접미어 — 명사 단독 질의가 새는 걸 막는 가드. */
const DOC_SUFFIXES = [
  "문서",
  "보고서",
  "자료",
  "회의록",
  "기획서",
  "파일",
  "리포트",
  "요약",
  "공문",
  "계약서",
] as const;

/** 의문·서술 어구가 남아 있으면 메타 전용이 아니다 → RAG 로 보낸다. */
const QUESTION_VERBS = [
  "어떻게",
  "왜",
  "언제",
  "어디",
  "누가",
  "뭐야",
  "뭐",
  "무엇",
  "얼마",
  "몇",
  "어떤",
  "어느",
  "할까",
  "있나",
  "없나",
  "되나",
  "인가",
] as const;

/** 명령형 어구 — 이것만 남으면 메타 의도가 명확하다고 본다. */
const STOPWORD_VERBS = [
  "보여줘",
  "보여",
  "찾아줘",
  "찾아",
  "열어줘",
  "열어",
  "줘",
  "주세요",
  "알려줘",
  "알려",
] as const;

const PARTICLES = new Set(["을", "를", "의", "에", "는", "가", "이", "도", "만", "와", "과", "랑"]);

// `\d` 는 Python 이 유니코드 십진 숫자 전부를 받으므로 `\p{Nd}` 로 옮긴다.
const RE_ABS_YMD = /(\p{Nd}{4})[-./](\p{Nd}{1,2})[-./](\p{Nd}{1,2})/u;
const RE_KO_YMD = new RegExp(
  `(\\p{Nd}{4})[${PY_SP}]*년[${PY_SP}]*(\\p{Nd}{1,2})[${PY_SP}]*월[${PY_SP}]*(\\p{Nd}{1,2})[${PY_SP}]*일`,
  "u",
);
const RE_KO_YM = new RegExp(
  `(\\p{Nd}{4})[${PY_SP}]*년[${PY_SP}]*(\\p{Nd}{1,2})[${PY_SP}]*월`,
  "u",
);
const RE_TAG = /#([A-Za-z0-9가-힣_\-]+)/gu;

/**
 * 상대 날짜 키워드 → `[시작 offset(일), 길이(일)]`.
 * **순서가 계약이다** — 원본은 dict 를 순회하며 처음 걸리는 키워드를 쓴다.
 */
const RELATIVE_DATES: readonly [string, number, number][] = [
  ["오늘", 0, 1],
  ["어제", -1, 1],
  ["그저께", -2, 1],
  ["이번주", -6, 7], // 단순화: 오늘 포함 직전 7 일
  ["지난주", -13, 7],
  ["이번달", -29, 30],
  ["지난달", -59, 30],
];

/** fast path 는 빠른 응답이 본질 — 페이지네이션 대상이 아니다. */
export const FAST_PATH_LIMIT = 20;

export interface MetaFilterPlan {
  /** `[from, to)` — 반개구간이다. 없으면 null. */
  dateRange: [string, string] | null;
  tags: string[];
  titleIlike: string | null;
  /** `date` / `tag` / `title` 을 `+` 로 이은 식별자. */
  matchedKind: string;
  residualTokens: string[];
}

function normalize(query: string): string {
  return pyStrip(query).normalize("NFC").replace(PY_SPACE_RUN_RE, " ");
}

/** 어절 끝 1 자 조사 제거 — `보고서를 → 보고서`. 2 자 미만은 건드리지 않는다. */
function stripParticle(token: string): string {
  const cp = [...token];
  if (cp.length >= 2 && PARTICLES.has(cp[cp.length - 1])) return cp.slice(0, -1).join("");
  return token;
}

function splitTokens(text: string): string[] {
  return text.split(PY_SPACE_RUN_RE).filter((t) => t !== "");
}

function extractTags(text: string): string[] {
  const seen: string[] = [];
  for (const m of text.matchAll(RE_TAG)) {
    if (!seen.includes(m[1])) seen.push(m[1]);
  }
  return seen;
}

/** UTC 자정 기준 ISO — Python `datetime(y, m, d, tzinfo=utc).isoformat()` 과 같은 문자열. */
function utcMidnightIso(y: number, m: number, d: number): string {
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${p(y, 4)}-${p(m)}-${p(d)}T00:00:00+00:00`;
}

/** 달력상 실재하는 날짜인지 — Python `datetime(...)` 이 `ValueError` 를 내는 자리다. */
function validYmd(y: number, m: number, d: number): boolean {
  if (y < 1 || y > 9999 || m < 1 || m > 12 || d < 1) return false;
  const dim = [
    31,
    (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0 ? 29 : 28,
    31,
    30,
    31,
    30,
    31,
    31,
    30,
    31,
    30,
    31,
  ];
  return d <= dim[m - 1];
}

function addDays(y: number, m: number, d: number, days: number): [number, number, number] {
  const t = new Date(Date.UTC(y, m - 1, d) + days * 86400000);
  return [t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate()];
}

/** Python `datetime` 의 상한(9999 년)을 넘었을 때 원본이 내는 예외에 대응한다. */
export class MetaDateRangeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MetaDateRangeError";
  }
}

const MAX_YEAR = 9999;

function ymdToRange(y: number, m: number, d: number): [string, string] | null {
  if (!validYmd(y, m, d)) return null;
  // 원본: `start + timedelta(days=1)` 이 `OverflowError` 를 던진다 (try 밖이라 안 잡힌다).
  if (y === MAX_YEAR && m === 12 && d === 31) {
    throw new MetaDateRangeError("date value out of range");
  }
  const [ny, nm, nd] = addDays(y, m, d, 1);
  return [utcMidnightIso(y, m, d), utcMidnightIso(ny, nm, nd)];
}

function ymToRange(y: number, m: number): [string, string] | null {
  if (!validYmd(y, m, 1)) return null;
  // 원본: 다음 달 1 일 계산이 try 밖이라 `datetime(10000, 1, 1)` 의 ValueError 가 샌다.
  if (y === MAX_YEAR && m === 12) {
    throw new MetaDateRangeError("year 10000 is out of range");
  }
  const end = m === 12 ? utcMidnightIso(y + 1, 1, 1) : utcMidnightIso(y, m + 1, 1);
  return [utcMidnightIso(y, m, 1), end];
}

const num = (s: string) => Number.parseInt(ndToAscii(s), 10);

/** 오늘 날짜(UTC). 테스트 결정성을 위해 주입할 수 있게 열어 둔다. */
export type TodayFn = () => [number, number, number];

function defaultToday(): [number, number, number] {
  const n = new Date();
  return [n.getUTCFullYear(), n.getUTCMonth() + 1, n.getUTCDate()];
}

function extractDateRange(text: string, today: TodayFn): [string, string] | null {
  let m = RE_ABS_YMD.exec(text);
  if (m) return ymdToRange(num(m[1]), num(m[2]), num(m[3]));

  m = RE_KO_YMD.exec(text);
  if (m) return ymdToRange(num(m[1]), num(m[2]), num(m[3]));

  m = RE_KO_YM.exec(text);
  if (m) return ymToRange(num(m[1]), num(m[2]));

  const [ty, tm, td] = today();
  for (const [kw, startOffset, spanDays] of RELATIVE_DATES) {
    if (text.includes(kw)) {
      const [sy, sm, sd] = addDays(ty, tm, td, startOffset);
      const [ey, em, ed] = addDays(sy, sm, sd, spanDays);
      return [utcMidnightIso(sy, sm, sd), utcMidnightIso(ey, em, ed)];
    }
  }
  return null;
}

/** 날짜 표현을 지운 잔여 텍스트 — 상대 키워드는 **매칭 여부와 무관하게 전부** 지운다. */
function stripDateExpressions(text: string): string {
  let out = text.replace(new RegExp(RE_ABS_YMD.source, "gu"), " ");
  out = out.replace(new RegExp(RE_KO_YMD.source, "gu"), " ");
  out = out.replace(new RegExp(RE_KO_YM.source, "gu"), " ");
  for (const [kw] of RELATIVE_DATES) out = out.replaceAll(kw, " ");
  return pyStrip(out.replace(PY_SPACE_RUN_RE, " "));
}

function stripTags(text: string): string {
  return pyStrip(text.replace(RE_TAG, " "));
}

function hasQuestionVerb(tokens: readonly string[]): boolean {
  return tokens.some((tok) => QUESTION_VERBS.some((v) => tok.includes(v)));
}

function residualOnlyStopwords(tokens: readonly string[]): boolean {
  return tokens.every((tok) => (STOPWORD_VERBS as readonly string[]).includes(stripParticle(tok)));
}

function extractTitleIlike(residualText: string, hasDateOrTag: boolean): string | null {
  if (!residualText) return null;
  const rawTokens = splitTokens(residualText);
  if (rawTokens.length === 0) return null;
  if (rawTokens.length > 5) return null;
  if (hasQuestionVerb(rawTokens)) return null;

  const cleaned: string[] = [];
  for (const tok of rawTokens) {
    const c = stripParticle(tok);
    if (!c || (STOPWORD_VERBS as readonly string[]).includes(c)) continue;
    cleaned.push(c);
  }
  if (cleaned.length === 0) return null;

  const hasSuffix = cleaned.some((c) => DOC_SUFFIXES.some((suf) => c.endsWith(suf) || c.includes(suf)));
  // 날짜·태그 가드 없이 단독 명사구는 RAG 로 보낸다 — 일반 질의가 새는 걸 막는다.
  if (!hasSuffix && !hasDateOrTag) return null;

  return cleaned.join(" ");
}

/** 메타 전용 질의인지 판정. 아니면 null → 호출자는 RAG 경로로 간다. */
export function isMetaOnly(query: string, today: TodayFn = defaultToday): MetaFilterPlan | null {
  if (!query || pyStrip(query) === "") return null;

  const text = normalize(query);
  const tags = extractTags(text);
  const dateRange = extractDateRange(text, today);

  const residualText = stripDateExpressions(stripTags(text));
  const residualTokens = splitTokens(residualText);

  if (hasQuestionVerb(residualTokens)) return null;

  const hasDateOrTag = dateRange !== null || tags.length > 0;
  const titleIlike = extractTitleIlike(residualText, hasDateOrTag);

  // 의미 있는 잔여 토큰이 남았는데 제목도 못 뽑았으면 메타 의도가 약하다.
  if (residualTokens.length > 0 && titleIlike === null) {
    if (!residualOnlyStopwords(residualTokens)) return null;
  }

  if (dateRange === null && tags.length === 0 && titleIlike === null) return null;

  const kinds: string[] = [];
  if (dateRange !== null) kinds.push("date");
  if (tags.length > 0) kinds.push("tag");
  if (titleIlike) kinds.push("title");

  return {
    dateRange,
    tags,
    titleIlike,
    matchedKind: kinds.join("+"),
    residualTokens,
  };
}

export interface FastPathRow {
  id: string;
  title?: string | null;
  doc_type?: string | null;
  tags?: string[] | null;
  summary?: string | null;
  created_at?: string | null;
}

/**
 * plan → documents 조회 쿼리. **실행하지 않고 돌려준다** — 그래야 채점기가 네트워크 없이
 * 요청 URL 만 대조할 수 있다(`filters.ts` 와 같은 이유).
 *
 * 날짜는 `[from, to)` 반개구간이라 `lt` 다(`lte` 가 아니다). 태그는 `contains`(AND) 이고
 * `overlaps`(OR) 이 아니다 — 태그가 2 개 이상일 때만 갈린다.
 */
// deno-lint-ignore no-explicit-any
export function buildFastPathQuery(
  client: SupabaseClient,
  plan: MetaFilterPlan,
  userId: string,
  // deno-lint-ignore no-explicit-any
): any {
  // deno-lint-ignore no-explicit-any
  let q: any = client
    .from("documents")
    .select("id, title, doc_type, tags, summary, created_at")
    .eq("user_id", userId)
    .is("deleted_at", null);

  if (plan.dateRange !== null) {
    q = q.gte("created_at", plan.dateRange[0]).lt("created_at", plan.dateRange[1]);
  }
  if (plan.tags.length > 0) q = q.contains("tags", plan.tags);
  if (plan.titleIlike) q = q.ilike("title", `%${plan.titleIlike}%`);

  return q.order("created_at", { ascending: false }).limit(FAST_PATH_LIMIT);
}

/** plan 을 실행해 행을 돌려준다. 임베딩·RPC 호출 0. */
export async function runFastPath(
  client: SupabaseClient,
  plan: MetaFilterPlan,
  userId: string,
): Promise<FastPathRow[]> {
  const { data, error } = await buildFastPathQuery(client, plan, userId);
  if (error) throw new Error(`meta fast path 조회 실패: ${error.message}`);
  return (data ?? []) as FastPathRow[];
}
