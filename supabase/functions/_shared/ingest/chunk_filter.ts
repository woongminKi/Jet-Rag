/**
 * `ingest/stages/chunk_filter.py` 포팅 — 검색에서 뺄 청크를 **마킹**한다(삭제 아니다).
 *
 * `flags.filtered_reason` 이 붙으면 `search_hybrid_rrf` 의
 * `WHERE flags->>'filtered_reason' IS NULL` 이 자동으로 제외한다. 행은 남아 있어서
 * 나중에 판정을 바꾸면 되돌릴 수 있다.
 *
 * | 사유 | 규칙 |
 * |---|---|
 * | `empty` | `strip()` 이 빈 문자열 |
 * | `extreme_short` | 20 자 미만 + **한글/영문 글자가 하나도 없음** (표 셀 "2,800" 같은 것) |
 * | `header_footer` | 같은 문서에서 100 자 미만 텍스트가 3 회 이상 반복 |
 * | `table_noise` | 50 자 이상 + 짧은 줄 비율 ≥ 0.90 + 숫자·기호 비율 ≥ 0.70 |
 *
 * 판정 **순서가 규칙의 일부다** — `header_footer` 가 `table_noise` 보다 먼저다.
 * 짧고 반복되는 텍스트는 표보다 머리말 의도가 강하다는 게 원본의 판단이다.
 *
 * ## 임계값이 진단용보다 높다
 * 같은 휴리스틱을 쓰는 진단 도구는 0.70/0.50 인데 여기는 0.90/0.70 이다. 자동 마킹은
 * 검색에서 빠지므로 오탐 비용이 진단보다 크다 — 보수적으로 잡은 것이다.
 */

import { PY_WORD_CLASS, pyIsSpace } from "../pychar.ts";
import { pyStrip } from "../search/pystr.ts";
import type { ChunkRecord } from "./chunk_records.ts";

/** 짧은 줄 기준(글자 수). 표 셀의 통상 길이다. */
const SHORT_LINE_LEN = 30;
const SHORT_LINE_RATIO_TH = 0.90;
const DIGIT_PUNCT_RATIO_TH = 0.70;

const HEADER_FOOTER_REPEAT_TH = 3;
const HEADER_FOOTER_MAX_LEN = 100;

/** 한글·영문이 0 이고 이보다 짧으면 검색에서 의미가 없다고 본다. */
const EXTREME_SHORT_LEN = 20;

/** `table_noise` 는 이보다 짧은 청크에서는 판정하지 않는다. */
const TABLE_NOISE_MIN_LEN = 50;

/** Python `len()` 은 코드포인트 수다. */
function cpLen(s: string): number {
  let n = 0;
  for (const _ of s) n++;
  return n;
}

/**
 * 원본 `_DIGIT_PUNCT_PATTERN = re.compile(r"[\d\W_]", re.UNICODE)` 의 한 글자 매칭.
 *
 * JS 로 그대로 옮기면 안 된다 — JS 의 `\d` 는 ASCII 숫자뿐이고 `\w` 도 ASCII 다.
 * Python 은 `\d` = 유니코드 `Nd`, `\w` = 글자·숫자·밑줄이다.
 *
 * 뜻을 풀면 **"글자가 아닌 것"** 이다: `\W`(단어문자 아님) ∪ `\d`(숫자) ∪ `_`.
 * 숫자와 밑줄은 `\w` 라 `\W` 에서 빠지는데 앞의 두 항이 도로 넣는다.
 */
const WORD_RE = new RegExp(`^${PY_WORD_CLASS}$`, "v");
const ND_RE = /^\p{Nd}$/u;
function isDigitPunct(ch: string): boolean {
  if (ND_RE.test(ch)) return true;
  if (ch === "_") return true;
  return !WORD_RE.test(ch); // `\W`
}

/** Python `str.isalpha()` — 유니코드 `L*` 범주. */
const ALPHA_RE = /^\p{L}$/u;

/**
 * 원본 `_has_meaningful_letter` — 한글 음절이나 영문 글자가 하나라도 있으면 true.
 *
 * 뒤의 한글 범위 검사는 사실 앞의 `isalpha()` 에 포함된다(한글 음절은 `Lo`).
 * 그래도 원본 그대로 둔다 — 판정이 같고, 원본을 읽는 사람이 대조하기 쉽다.
 */
export function hasMeaningfulLetter(text: string): boolean {
  for (const c of text) {
    if (ALPHA_RE.test(c)) return true;
    if (c >= "가" && c <= "힣") return true;
  }
  return false;
}

/** 원본 `_line_metrics` — (짧은 줄 비율, 숫자·기호 비율). */
export function lineMetrics(text: string): [number, number] {
  const lines = text.split("\n");
  const lineCount = lines.length;
  // 원본에 `if line_count == 0` 가드가 있지만 `"".split("\n")` 은 `[""]` 라 0 이 안 된다.
  // 죽은 가지다 — 옮기되 그 사실을 적어 둔다.
  if (lineCount === 0) return [0.0, 0.0];
  let shortLines = 0;
  for (const ln of lines) if (cpLen(pyStrip(ln)) < SHORT_LINE_LEN) shortLines++;
  const shortLineRatio = shortLines / lineCount;

  let digitPunct = 0;
  let nonWs = 0;
  for (const ch of text) {
    if (isDigitPunct(ch)) digitPunct++;
    if (!pyIsSpace(ch)) nonWs++;  // Python `str.isspace()`
  }
  const digitPunctRatio = nonWs ? digitPunct / nonWs : 0.0;
  return [shortLineRatio, digitPunctRatio];
}

/** 원본 `_detect_header_footer_texts` — 문서 안에서 반복되는 짧은 텍스트. */
export function detectHeaderFooterTexts(chunks: ChunkRecord[]): Set<string> {
  const counts = new Map<string, number>();
  for (const c of chunks) {
    const t = pyStrip(c.text ?? "");
    if (t !== "" && cpLen(t) < HEADER_FOOTER_MAX_LEN) {
      counts.set(t, (counts.get(t) ?? 0) + 1);
    }
  }
  const out = new Set<string>();
  for (const [t, n] of counts) if (n >= HEADER_FOOTER_REPEAT_TH) out.add(t);
  return out;
}

/** 원본 `_classify_chunk` — `null` 이면 통과. **순서가 규칙이다.** */
export function classifyChunk(
  chunk: ChunkRecord,
  headerFooterTexts: Set<string>,
): string | null {
  const text = chunk.text ?? "";
  const stripped = pyStrip(text);

  if (stripped === "") return "empty";
  if (cpLen(stripped) < EXTREME_SHORT_LEN && !hasMeaningfulLetter(stripped)) {
    return "extreme_short";
  }
  if (headerFooterTexts.has(stripped)) return "header_footer";
  // 짧은 청크는 표 노이즈 판정을 하지 않는다 — 다른 가지가 이미 처리했다.
  if (cpLen(text) < TABLE_NOISE_MIN_LEN) return null;

  const [shortLineRatio, digitPunctRatio] = lineMetrics(text);
  if (shortLineRatio >= SHORT_LINE_RATIO_TH && digitPunctRatio >= DIGIT_PUNCT_RATIO_TH) {
    return "table_noise";
  }
  return null;
}

export interface ChunkFilterResult {
  chunks: ChunkRecord[];
  counts: Record<string, number>;
  filterRatio: number;
}

/**
 * 원본 `run_chunk_filter_stage` — 마킹한 새 배열을 돌려준다. 입력은 안 건드린다.
 *
 * DB 갱신은 하지 않는다. `load` 가 `flags` 를 그대로 실어 보낸다.
 */
export function runChunkFilterStage(chunks: ChunkRecord[]): ChunkFilterResult {
  const counts: Record<string, number> = {
    table_noise: 0,
    header_footer: 0,
    empty: 0,
    extreme_short: 0,
  };
  if (chunks.length === 0) return { chunks, counts, filterRatio: 0.0 };

  const headerFooterTexts = detectHeaderFooterTexts(chunks);
  const out: ChunkRecord[] = [];
  let marked = 0;
  for (const chunk of chunks) {
    const reason = classifyChunk(chunk, headerFooterTexts);
    if (reason === null) {
      out.push(chunk);
      continue;
    }
    const flags = { ...((chunk as { flags?: Record<string, unknown> }).flags ?? {}) };
    flags["filtered_reason"] = reason;
    out.push({ ...chunk, flags } as ChunkRecord);
    counts[reason] = (counts[reason] ?? 0) + 1;
    marked++;
  }
  return { chunks: out, counts, filterRatio: marked / chunks.length };
}
