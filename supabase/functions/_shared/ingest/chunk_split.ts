/**
 * 긴 섹션 분할 — `ingest/stages/chunk.py` 의 문장 분할·날짜 마스킹·overlap 포팅.
 *
 * ## 정규식 3개가 전부 함정이다
 * | 원본 | 문제 | 대응 |
 * |---|---|---|
 * | `(?<=[가-힣\)\]][.!?])\s+` | Python `\s` ≠ JS `\s` | `PY_SP` 문자 클래스 |
 * | `\n\s*\n` | 〃 | 〃 |
 * | `(\d{2,4})\.\s+(\d{1,2})\.\s+(\d{1,2})\.` | Python `\d` 는 유니코드 Nd 전부 | `\p{Nd}` |
 *
 * lookbehind 는 Deno 가 지원한다(ES2018+). **매칭 본문에 `[.!?]` 를 넣으면 안 된다** —
 * 원본 주석대로 leftmost 우선이라 마침표가 좌측 청크에서 사라진다.
 *
 * ## 길이는 전부 코드포인트다
 * Python `len()`·슬라이스는 코드포인트 단위다. JS `.length`·`.slice()` 는 UTF-16 이라
 * 이모지·한자 확장이 섞이면 800/1000 임계와 overlap 100 자가 어긋난다.
 */

import { PY_SP, pyStrip } from "../search/pystr.ts";

export const TARGET_SIZE = 800;
export const MAX_SIZE = 1000;
export const OVERLAP_SIZE = 100;

/** 정규식·일반 문서에 등장할 수 없는 마커(원본 `_LEGAL_DATE_PLACEHOLDER`). */
const LEGAL_DATE_PLACEHOLDER = "\x00LEGALDATE\x01";

/** `2025. 7. 9.` 같은 법령 인용 날짜. 문장 분할이 여기서 끊기면 안 된다. */
const LEGAL_DATE_RE = new RegExp(
  `(\\p{Nd}{2,4})\\.[${PY_SP}]+(\\p{Nd}{1,2})\\.[${PY_SP}]+(\\p{Nd}{1,2})\\.`,
  "gu",
);

/** 문장 경계. (a) 한국어/닫는괄호 + 문장부호 뒤 공백, (b) 문단 break. */
const SENTENCE_END_RE = new RegExp(
  `(?<=[가-힣\\)\\]][.!?])[${PY_SP}]+|\\n[${PY_SP}]*\\n`,
  "u",
);

/** 코드포인트 배열. 길이·슬라이스를 Python 과 맞추려면 여기를 거쳐야 한다. */
function cps(s: string): string[] {
  return [...s];
}
function cpLen(s: string): number {
  return cps(s).length;
}
function cpSlice(s: string, start: number, end?: number): string {
  return cps(s).slice(start, end).join("");
}

/** 날짜를 placeholder 로 치환. 반환 배열은 복원 시 인덱스로 쓴다. */
export function maskLegalDates(text: string): { masked: string; matches: string[] } {
  const matches: string[] = [];
  const masked = text.replace(LEGAL_DATE_RE, (whole) => {
    matches.push(whole);
    return `${LEGAL_DATE_PLACEHOLDER}${matches.length - 1}${LEGAL_DATE_PLACEHOLDER}`;
  });
  return { masked, matches };
}

export function restoreLegalDates(text: string, matches: string[]): string {
  if (matches.length === 0) return text;
  // placeholder 는 제어문자라 정규식 특수문자가 없다. 그래도 원본처럼 이스케이프해 둔다.
  const esc = LEGAL_DATE_PLACEHOLDER.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return text.replace(new RegExp(`${esc}(\\d+)${esc}`, "g"), (_w, i: string) => matches[Number(i)]);
}

/**
 * 청크 끝이 인용문·괄호 중간인지. 홀수면 다음 문장을 흡수해 짝을 맞춘다.
 *
 * `'`(작은따옴표)는 apostrophe 와 구분이 안 돼 원본이 일부러 뺐다 — 여기서도 뺀다.
 */
export function isUnbalancedQuoteOrParen(text: string): boolean {
  const count = (needle: string) => {
    let n = 0, i = text.indexOf(needle);
    while (i !== -1) {
      n++;
      i = text.indexOf(needle, i + needle.length);
    }
    return n;
  };
  if (count('"') % 2 === 1) return true;
  if (count("“") > count("”")) return true;
  if (count("‘") > count("’")) return true;
  const pairs: [string, string][] = [
    ["(", ")"],
    ["[", "]"],
    ["{", "}"],
    ["「", "」"],
    ["『", "』"],
  ];
  for (const [o, c] of pairs) {
    if (count(o) > count(c)) return true;
  }
  return false;
}

/**
 * 인접 청크에 앞 청크 꼬리 `OVERLAP_SIZE` 자를 붙인다.
 *
 * 한 문장이 두 청크에 걸치면 양쪽 임베딩이 다 의미가 부족해진다. `MAX_SIZE` 보장이
 * 우선이라 예산이 모자라면 prefix 를 줄이고, 아예 없으면 생략한다.
 */
export function applyOverlap(pieces: string[]): string[] {
  if (pieces.length <= 1) return pieces;
  const out: string[] = [pieces[0]];
  for (let i = 1; i < pieces.length; i++) {
    const prev = pieces[i - 1];
    const cur = pieces[i];
    let prefix = cpLen(prev) > OVERLAP_SIZE ? cpSlice(prev, cpLen(prev) - OVERLAP_SIZE) : prev;
    const budget = MAX_SIZE - cpLen(cur) - 1;
    if (budget <= 0) {
      out.push(cur);
      continue;
    }
    if (cpLen(prefix) > budget) prefix = cpSlice(prefix, cpLen(prefix) - budget);
    out.push(prefix ? pyStrip(`${prefix} ${cur}`) : cur);
  }
  return out;
}

/** 문장 경계 기준으로 `TARGET_SIZE` 근방 분할. 날짜 마스킹 → 분할 → 복원 → overlap. */
export function splitBySentence(text: string): string[] {
  const { masked, matches } = maskLegalDates(text);

  // `re.split` 과 같다 — 캡처 그룹이 없으므로 구분자는 버려진다.
  const sentences = masked.split(SENTENCE_END_RE);
  const pieces: string[] = [];
  let current = "";
  for (const sent of sentences) {
    // Python 은 `if not sent: continue` — undefined 도 여기서 걸러진다.
    if (!sent) continue;
    if (current && cpLen(current) + cpLen(sent) + 1 > TARGET_SIZE) {
      if (isUnbalancedQuoteOrParen(current) && cpLen(current) + cpLen(sent) + 1 <= MAX_SIZE) {
        current = pyStrip(`${current} ${sent}`);
      } else {
        pieces.push(pyStrip(current));
        current = sent;
      }
    } else {
      current = current ? pyStrip(`${current} ${sent}`) : sent;
    }
  }
  if (current) pieces.push(pyStrip(current));

  // 문장 경계가 없는 긴 텍스트 대비 — 강제 분할.
  const bounded: string[] = [];
  for (const piece of pieces) {
    if (cpLen(piece) <= MAX_SIZE) {
      bounded.push(piece);
      continue;
    }
    const arr = cps(piece);
    for (let i = 0; i < arr.length; i += TARGET_SIZE) {
      bounded.push(arr.slice(i, i + TARGET_SIZE).join(""));
    }
  }

  return applyOverlap(bounded.map((p) => restoreLegalDates(p, matches)));
}
