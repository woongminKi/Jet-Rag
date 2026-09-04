/**
 * 스니펫 + 하이라이트 — `_make_snippet_with_highlights` 와 `strip_synonym_marker` 포팅.
 *
 * 프론트가 `highlight` 의 `[start, end]` 로 스니펫 문자열을 잘라 강조한다. 인덱스가
 * 하나만 어긋나도 엉뚱한 글자가 강조되므로 **문자열과 인덱스 둘 다 완전 일치**가 기준이다.
 *
 * ## 코드포인트로 센다 (실측 근거 있음)
 * Python 의 `len`·슬라이스·`find` 는 **코드포인트** 단위고 JS 는 **UTF-16** 단위다.
 * U+FFFF 를 넘는 문자가 하나라도 있으면 그 뒤 인덱스가 전부 1 씩 밀린다.
 * 운영 청크 37,080 건 중 **20 건**이 astral 문자를 포함한다(수식 이탤릭 `𝑖`·`𝜎` 등,
 * 2026-09-04 실측). 이론이 아니라 실제로 갈리는 자리다.
 *
 * ## 원본의 인덱스 어긋남까지 그대로 옮긴다
 * 원본은 `text.lower()` 에서 찾은 인덱스로 **원본 `text`** 를 자른다. 소문자화가 길이를
 * 바꾸는 문자(`İ` → `i̇`, 1 → 2)가 앞에 있으면 잘리는 위치가 밀린다. 하이라이트 길이도
 * 소문자화 이전의 `len(query)` 를 쓴다. 이식은 **동작을 맞추는 일**이라 여기서 고치지
 * 않았다 — 고치면 Railway 와 Edge 의 응답이 달라진다.
 *
 * ## `.lower()` 매핑 차이
 * Python 3.12(유니코드 15.0)와 Deno 의 `toLowerCase` 는 코드포인트 110 만 개 중 **27 개**
 * 에서 갈린다(2026-09-04 전수 비교). 전부 Python 쪽에 소문자 매핑이 아직 없는 신규 문자
 * (가라이 문자 U+10D50~, 키릴 U+1C89 등)라 한국어·업무 문서에는 나타나지 않는다.
 * 유니코드 버전이 올라가면 자연히 사라지는 차이라 별도 매핑표를 두지 않았다.
 */

/** `SEARCH_SNIPPET_AROUND` 기본값. 매칭 위치 앞뒤로 이만큼씩 잘라 낸다. */
export const SNIPPET_AROUND = 240;

/** Python `re` 의 `\s`(str 패턴) 문자 집합 — 대괄호 없는 본문. `guards.ts` 와 같다. */
const SP = " \\t\\n\\r\\f\\v\\u001c-\\u001f\\u0085\\u00a0\\u1680\\u2000-\\u200a" +
  "\\u2028\\u2029\\u202f\\u205f\\u3000";

const MARKER_PREFIX = "[검색어: ";
/** 본문 끝의 `\n*[검색어: …]` (+ 뒤 공백). Python `$` 는 끝 또는 끝 직전 줄바꿈에 붙는다. */
const MARKER_RE = new RegExp(`\\n*\\[검색어:[^\\]]*\\][${SP}]*$`);

/**
 * 인제스트가 청크 끝에 붙인 동의어 마커를 뗀다. 마커가 없으면 원본 그대로.
 * 스니펫을 만들기 **전에** 불러야 사용자에게 마커가 안 보인다.
 */
export function stripSynonymMarker(text: string): string {
  if (!text || !text.includes(MARKER_PREFIX)) return text;
  return text.replace(MARKER_RE, "");
}

/** 코드포인트 배열에서 부분열을 찾는다. Python `str.find` 와 같은 규약(못 찾으면 -1). */
function indexOfCp(hay: readonly string[], needle: readonly string[], from = 0): number {
  if (needle.length === 0) return from <= hay.length ? from : -1;
  outer:
  for (let i = Math.max(0, from); i + needle.length <= hay.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (hay[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}

export interface Snippet {
  text: string;
  /** 스니펫 문자열 안의 `[start, end]` 구간들. 코드포인트 인덱스다. */
  highlights: [number, number][];
}

/**
 * 매칭 위치 ±`around` 로 자른 스니펫과 그 안의 매칭 구간.
 *
 * **리터럴 부분 문자열 매칭만** 한다 — 하이브리드 RRF 결과의 청크가 질의를 리터럴로
 * 포함한다는 보장이 없어서, 못 찾으면 본문 앞부분만 돌려주고 하이라이트는 비운다.
 */
export function makeSnippetWithHighlights(
  text: string,
  query: string,
  around: number = SNIPPET_AROUND,
): Snippet {
  const textCp = [...text];
  if (!text || !query) return { text: textCp.slice(0, around * 2).join(""), highlights: [] };

  // 소문자화한 쪽에서 찾고, 자르기는 원본에서 한다 (원본 동작 그대로).
  const textLowerCp = [...text.toLowerCase()];
  const qLowerCp = [...query.toLowerCase()];
  // 하이라이트 길이는 소문자화 **이전** 질의 길이다.
  const qLen = [...query].length;

  const firstIdx = indexOfCp(textLowerCp, qLowerCp);
  if (firstIdx === -1) return { text: textCp.slice(0, around * 2).join(""), highlights: [] };

  const start = Math.max(0, firstIdx - around);
  const end = Math.min(textCp.length, firstIdx + qLen + around);
  const prefix = start > 0 ? "…" : "";
  const suffix = end < textCp.length ? "…" : "";
  const snippet = `${prefix}${textCp.slice(start, end).join("")}${suffix}`;

  const snippetLowerCp = [...snippet.toLowerCase()];
  const highlights: [number, number][] = [];
  let pos = 0;
  for (;;) {
    const hit = indexOfCp(snippetLowerCp, qLowerCp, pos);
    if (hit === -1) break;
    highlights.push([hit, hit + qLen]);
    pos = hit + qLen;
    // 질의가 소문자화로 길어지면 `qLen` 이 0 보다 클 것이 보장되지만, 방어적으로 멈춘다.
    if (qLen <= 0) break;
  }
  return { text: snippet, highlights };
}
