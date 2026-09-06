/**
 * `api/app/routers/admin.py` 의 순수 집계 부분 포팅.
 *
 * DB 를 안 건드린다 — 행 배열을 받아 응답 조각을 만든다. `/admin/queries/stats` 와
 * `/admin/feedback/stats` 가 **같은 KST 일별 GROUP BY 패턴**을 쓰므로 둘 다 여기 있다.
 *
 * ## 옮기면서 조심한 것
 * - `round(x, 4)` 는 banker's rounding 이다 → `pyRound` 재사용.
 * - `int(sum/len)` 은 0 방향 절단이다 → `Math.trunc`. 음수 지연은 없겠지만 계약이다.
 * - `_parse_recorded_at_kst` 는 `datetime.fromisoformat` 이라 `Date.parse` 와 받는
 *   형식이 다르다 → `parseIsoDatetime`(fromisoformat 호환 파서)를 쓴다.
 *   특히 **tz 지정자가 없으면 원본은 UTC 로 본다.** `Date.parse("2026-09-06T12:00:00")`
 *   은 로컬 시간으로 읽으므로 그대로 쓰면 갈린다.
 * - `rating = "up" if helpful else "down"` — `helpful` 이 `null` 이면 `"down"` 이다.
 *   `=== false` 가 아니라 **truthy 검사**라서, 여기만 `is False` 규칙과 다르다.
 *   (같은 함수 안에서 카운트는 `is True`/`is False` 로 세는데 라벨은 truthy 다.)
 */

import { pyRound } from "../pynum.ts";
import { parseIsoDatetime } from "../search/iso_datetime.ts";
import { KST_OFFSET_MINUTES } from "../stats/aggregate.ts";

/** 실패 케이스 응답에 넣을 최근 샘플 수 (`_FAILED_SAMPLES_LIMIT`). */
export const FAILED_SAMPLES_LIMIT = 10;
/** 최근 코멘트 노출 수 (`_RECENT_COMMENTS_LIMIT`). */
export const RECENT_COMMENTS_LIMIT = 10;

/** `_RANGE_TO_DAYS`. 이 3 종 외 값은 pydantic 이 422 로 막는다. */
export const RANGE_TO_DAYS: Record<string, number> = { "7d": 7, "14d": 14, "30d": 30 };

/** `_COMMENT_CATEGORIES` — 응답은 sample 0 건이어도 **항상 4 키**를 낸다. 순서 유지. */
export const COMMENT_CATEGORIES = [
  "search_issue",
  "answer_issue",
  "source_issue",
  "other",
] as const;
export type CommentCategory = typeof COMMENT_CATEGORIES[number];

const KEYWORDS_SOURCE_ISSUE = ["출처", "근거 없", "어디서", "인용", "페이지", "이상한 자료"];
const KEYWORDS_SEARCH_ISSUE = ["검색", "찾을 수 없", "관련 없", "나오지 않", "chunk", "검색 결과"];
const KEYWORDS_ANSWER_ISSUE = ["답변", "정확하지 않", "잘못", "틀린", "오답", "환각"];

/**
 * 코멘트를 4 카테고리로. 우선순위는 source → search → answer → other.
 *
 * 원본은 `text.strip().lower()` 다. `toLowerCase()` 는 유니코드 1.1M 자 중 27 자에서
 * Python `lower()` 와 갈리지만 키워드가 한글 + `chunk` 뿐이라 실제로는 영향이 없다
 * (대조 케이스로 확인한다).
 */
export function classifyComment(text: string): CommentCategory {
  if (!text) return "other";
  const normalized = text.trim().toLowerCase();
  if (!normalized) return "other";
  if (KEYWORDS_SOURCE_ISSUE.some((kw) => normalized.includes(kw))) return "source_issue";
  if (KEYWORDS_SEARCH_ISSUE.some((kw) => normalized.includes(kw))) return "search_issue";
  if (KEYWORDS_ANSWER_ISSUE.some((kw) => normalized.includes(kw))) return "answer_issue";
  return "other";
}

/**
 * `_parse_recorded_at_kst` — TIMESTAMPTZ 문자열 → **KST 벽시계 epoch(ms)**.
 * 파싱 실패는 `null`(행을 건너뛴다).
 */
export function parseRecordedAtKstMs(value: unknown): number | null {
  if (typeof value !== "string" || !value) return null;
  const normalized = value.endsWith("Z") ? `${value.slice(0, -1)}+00:00` : value;
  const iso = parseIsoDatetime(normalized);
  if (iso === null) return null;
  // 오프셋이 없으면 원본은 UTC 로 간주한다(`dt.replace(tzinfo=utc)`).
  const hasOffset = /[+-]\d{2}:\d{2}$/.test(iso);
  const t = Date.parse(hasOffset ? iso : `${iso}+00:00`);
  if (Number.isNaN(t)) return null;
  return t + KST_OFFSET_MINUTES * 60_000;
}

/** KST 벽시계 epoch → `YYYY-MM-DD`. */
function kstDateStr(wallMs: number): string {
  return new Date(wallMs).toISOString().slice(0, 10);
}

/** `now` 기준 KST 오늘 날짜 문자열. */
export function kstToday(nowMs: number): string {
  return kstDateStr(nowMs + KST_OFFSET_MINUTES * 60_000);
}

/** `days` 일 전 ~ 오늘까지의 KST 날짜 문자열, **오래된 → 최신** 순. */
function dateWindow(nowMs: number, days: number): string[] {
  const todayWall = nowMs + KST_OFFSET_MINUTES * 60_000;
  const out: string[] = [];
  for (let i = days - 1; i >= 0; i--) {
    out.push(kstDateStr(todayWall - i * 86_400_000));
  }
  return out;
}

// ---------------------------------------------------------------- queries

export interface MetricsRow {
  recorded_at?: string | null;
  took_ms?: number | null;
  fused?: number | null;
  fallback_reason?: string | null;
  query_text?: string | null;
}

export interface DailyBucket {
  date: string;
  count: number;
  success_count: number;
  fail_count: number;
}

export type FailureReason = "permanent_4xx" | "transient_5xx" | "no_hits";

export interface FailedSample {
  query: string;
  ts: string;
  reason: FailureReason;
}

/**
 * 성공 판정. `fallback_reason` 이 있으면 실패, `fused` 가 0 이하/없으면 실패.
 * **`fused` 가 `null` 이면 실패**다 — 원본 `fused is not None and fused > 0`.
 */
export function rowIsSuccess(row: MetricsRow): boolean {
  if (row.fallback_reason) return false;
  const fused = row.fused;
  return fused !== null && fused !== undefined && fused > 0;
}

/** 실패 사유 분류. 성공 행이면 `null`. */
export function classifyFailureReason(row: MetricsRow): FailureReason | null {
  const fr = row.fallback_reason;
  if (fr === "permanent_4xx" || fr === "transient_5xx") return fr;
  const fused = row.fused;
  if (fused !== null && fused !== undefined && fused === 0) return "no_hits";
  return null;
}

/** 일별 집계 — 빈 날짜도 0 으로 채운다(sparkline zero-fill). */
export function buildDailyBuckets(
  rows: readonly MetricsRow[],
  days: number,
  nowMs: number,
): DailyBucket[] {
  const counts = new Map<string, { count: number; success_count: number; fail_count: number }>();
  for (const row of rows) {
    const wall = parseRecordedAtKstMs(row.recorded_at);
    if (wall === null) continue;
    const key = kstDateStr(wall);
    let b = counts.get(key);
    if (!b) {
      b = { count: 0, success_count: 0, fail_count: 0 };
      counts.set(key, b);
    }
    b.count += 1;
    if (rowIsSuccess(row)) b.success_count += 1;
    else b.fail_count += 1;
  }
  return dateWindow(nowMs, days).map((date) => {
    const b = counts.get(date) ?? { count: 0, success_count: 0, fail_count: 0 };
    return { date, count: b.count, success_count: b.success_count, fail_count: b.fail_count };
  });
}

/**
 * 9 라벨 분포. **0 인 라벨도 키로 남긴다** — 프론트가 0 건 행을 그린다.
 *
 * `classify` 를 주입받는 이유: 원본이 분류기 예외를 잡고 그 행만 건너뛴다.
 * 그 방어를 여기서 재현하려면 예외를 던질 수 있는 함수여야 한다.
 */
export function buildQueryTypeDistribution(
  rows: readonly MetricsRow[],
  labels: readonly string[],
  classify: (q: string) => string,
): Record<string, number> {
  const distribution: Record<string, number> = {};
  for (const label of labels) distribution[label] = 0;
  const counter = new Map<string, number>();
  for (const row of rows) {
    const query = (row.query_text ?? "").trim();
    if (!query) continue;
    let label: string;
    try {
      label = classify(query);
    } catch {
      continue; // 분류기 자체 방어 — 원본도 그 행만 버린다.
    }
    counter.set(label, (counter.get(label) ?? 0) + 1);
  }
  // 9 라벨 밖 값이 와도 넣는다(원본도 방어적으로 그렇게 한다).
  for (const [label, count] of counter) distribution[label] = count;
  return distribution;
}

/** 실패 행 중 최근 N 건. 입력이 `recorded_at desc` 정렬이라는 전제는 호출부가 지킨다. */
export function extractFailedSamples(rows: readonly MetricsRow[]): FailedSample[] {
  const samples: FailedSample[] = [];
  for (const row of rows) {
    if (samples.length >= FAILED_SAMPLES_LIMIT) break;
    const reason = classifyFailureReason(row);
    if (reason === null) continue;
    samples.push({
      query: (row.query_text ?? "").slice(0, 200),
      ts: String(row.recorded_at ?? ""),
      reason,
    });
  }
  return samples;
}

/** 성공률 — 표본 0 건이면 `null`. `round(x, 4)`. */
export function successRate(rows: readonly MetricsRow[]): number | null {
  const total = rows.length;
  if (!total) return null;
  const success = rows.filter(rowIsSuccess).length;
  return pyRound(success / total, 4);
}

/** 평균 지연 — `took_ms` 가 있는 행만. 0 건이면 `null`. `int()` 는 0 방향 절단. */
export function avgLatencyMs(rows: readonly MetricsRow[]): number | null {
  const xs = rows
    .map((r) => r.took_ms)
    .filter((v): v is number => v !== null && v !== undefined);
  if (!xs.length) return null;
  return Math.trunc(xs.reduce((a, b) => a + b, 0) / xs.length);
}

// --------------------------------------------------------------- feedback

export interface FeedbackRow {
  created_at?: string | null;
  helpful?: boolean | null;
  comment?: string | null;
  query?: string | null;
}

export interface FeedbackDailyBucket {
  date: string;
  up: number;
  down: number;
  total: number;
}

export interface FeedbackComment {
  query: string;
  rating: "up" | "down";
  comment: string;
  category: CommentCategory;
  ts: string;
}

/** 일별 👍/👎 — queries 쪽과 같은 zero-fill 패턴. */
export function buildFeedbackDailyBuckets(
  rows: readonly FeedbackRow[],
  days: number,
  nowMs: number,
): FeedbackDailyBucket[] {
  const counts = new Map<string, { up: number; down: number }>();
  for (const row of rows) {
    const wall = parseRecordedAtKstMs(row.created_at);
    if (wall === null) continue;
    const key = kstDateStr(wall);
    let b = counts.get(key);
    if (!b) {
      b = { up: 0, down: 0 };
      counts.set(key, b);
    }
    // `is True` / `is False` — null 은 어느 쪽도 아니다.
    if (row.helpful === true) b.up += 1;
    else if (row.helpful === false) b.down += 1;
  }
  return dateWindow(nowMs, days).map((date) => {
    const b = counts.get(date) ?? { up: 0, down: 0 };
    return { date, up: b.up, down: b.down, total: b.up + b.down };
  });
}

/** 코멘트 카테고리 분포 + 최근 N 건. 빈 코멘트는 분류·노출 모두 제외. */
export function buildCommentAnalysis(
  rows: readonly FeedbackRow[],
): { categories: Record<string, number>; comments: FeedbackComment[] } {
  const categories: Record<string, number> = {};
  for (const k of COMMENT_CATEGORIES) categories[k] = 0;
  const comments: FeedbackComment[] = [];
  for (const row of rows) {
    const raw = (row.comment ?? "").trim();
    if (!raw) continue;
    const category = classifyComment(raw);
    categories[category] += 1;
    if (comments.length < RECENT_COMMENTS_LIMIT) {
      // **truthy 검사다** — `helpful` 이 null 이면 "down" 이 된다(원본 그대로).
      comments.push({
        query: (row.query ?? "").slice(0, 200),
        rating: row.helpful ? "up" : "down",
        comment: raw.slice(0, 500),
        category,
        ts: String(row.created_at ?? ""),
      });
    }
  }
  return { categories, comments };
}

/** 만족도 — `up / (up + down)`, 표본 0 건이면 `null`. */
export function satisfactionRate(up: number, down: number): number | null {
  const total = up + down;
  return total ? pyRound(up / total, 4) : null;
}
