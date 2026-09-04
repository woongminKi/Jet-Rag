/**
 * 표지·목차 청크 가드 — `search.py` 의 `_is_cover_chunk` · `_is_toc_chunk` 포팅.
 *
 * 둘 다 최종 점수에 **×0.3** 을 곱한다(`_COVER_GUARD_PENALTY`). 순위를 직접 흔드는
 * 자리라 판정 하나가 갈리면 top-1 이 바뀐다.
 *
 * | 가드 | 기본값 | 노리는 것 |
 * |---|---|---|
 * | cover | 항상 켜짐 | 30 자 이하의 짧은 표지 청크가 dense 유사도로 top-1 을 먹는 것 |
 * | toc | **켜짐** (`JETRAG_TOC_GUARD_ENABLED`) | vision 으로 뽑은 목차 본문 |
 *
 * 서로 직교한다 — cover 는 짧은 표지, toc 는 긴 목차 본문이다. 둘 다 걸리면 0.09 가 된다.
 *
 * ## 질의가 목차를 달라고 하면 건너뛴다
 * "목차 보여줘" 같은 질의에 목차를 깎으면 사용자 의도를 뒤집는다. 그래서 질의가
 * `_TOC_INTENT_PATTERN` 에 걸리면 toc penalty 를 통째로 건너뛴다(cover 는 그대로).
 *
 * ## `\s` 가 언어마다 다르다
 * Python `re` 의 `\s`(str 패턴)와 JS 의 `\s` 는 문자 집합이 다르다 — 실측하면
 * Python 만 `U+001C`–`U+001F`·`U+0085` 를 잡고, JS 만 `U+FEFF` 를 잡는다.
 * 정규식을 그대로 옮기면 그 문자가 낀 청크에서 판정이 갈리므로 명시 클래스를 쓴다.
 */

/**
 * Python `re` 의 `\s`(str 패턴)와 같은 문자 집합 — **대괄호 없는 본문**이다.
 * 다른 클래스 안에 끼워 넣을 일이 있어서 클래스와 본문을 나눠 둔다
 * (`[\n.${S}]` 처럼 클래스를 클래스에 넣으면 대괄호가 중첩돼 조용히 깨진다 — 실제로 밟았다).
 * 2026-09-04 실측.
 */
const SP = " \\t\\n\\r\\f\\v\\u001c-\\u001f\\u0085\\u00a0\\u1680\\u2000-\\u200a" +
  "\\u2028\\u2029\\u202f\\u205f\\u3000";
const S = `[${SP}]`;

/** 본문 head 가 목차/차례로 시작하는지. `차례` 는 앞이 줄바꿈·마침표·공백일 때만 센다. */
const TOC_PATTERN = new RegExp(`(?:목${S}*차)|(?:^|[\\n.${SP}])(?:차${S}+례|차례)(?=${S}|$)`);

/** 질의 자체가 목차/차례를 명시적으로 요구하는지. 뒤에 조사 3 글자까지 붙어도 된다. */
const TOC_INTENT_PATTERN = new RegExp(
  `(?:목${S}*차|차${S}*례)(?:[가-힣]{0,3})?(?=${S}|$|[?!.,])`,
);

/** 표지 판정 길이 한계. 이보다 길면 표지가 아니라고 본다. */
const COVER_GUARD_TEXT_LEN = 30;
/** toc 판정에 쓰는 본문 head 길이. */
export const TOC_GUARD_HEAD_LEN = 100;
/** cover·toc 둘 다 같은 penalty 를 쓴다. */
export const COVER_GUARD_PENALTY = 0.3;

const VISION_META_PREFIX = "[문서]";
const VISION_META_BODY_SEP = "\n\n";
const VISION_SECTION_PREFIX = "(vision)";

export const TOC_GUARD_ENABLED_ENV = "JETRAG_TOC_GUARD_ENABLED";

/** 가드가 보는 청크 요약. 원본의 `cover_guard_meta` 항목과 같은 모양이다. */
export interface GuardMeta {
  chunkIdx: number | null;
  page: number | null;
  textLen: number;
  sectionTitle: string;
  textHead: string;
}

/** 청크 원본에서 가드용 요약을 만든다. */
export function buildGuardMeta(
  chunk: {
    chunk_idx?: number | null;
    page?: number | null;
    text?: string | null;
    section_title?: string | null;
  },
): GuardMeta {
  const text = chunk.text ?? "";
  // Python `len()`·슬라이스는 코드포인트 단위다. `.length`/`.slice` 는 UTF-16 단위라
  // 이모지 같은 astral 문자가 섞이면 값이 달라진다(HWP 포팅에서 같은 함정을 밟았다).
  const cp = [...text];
  return {
    chunkIdx: chunk.chunk_idx ?? null,
    page: chunk.page ?? null,
    textLen: cp.length,
    sectionTitle: chunk.section_title ?? "",
    textHead: cp.slice(0, TOC_GUARD_HEAD_LEN).join(""),
  };
}

/**
 * 표지 청크 판정. 짧고(30 자 이하) **동시에** 문서 맨 앞(첫 청크 또는 1 쪽)이어야 한다.
 * 둘 다 요구하므로 "결론" 같은 짧은 헤딩은 걸리지 않는다 — 그건 뒤쪽에 있다.
 */
export function isCoverChunk(meta: GuardMeta | undefined): boolean {
  if (!meta) return false;
  if (meta.textLen > COVER_GUARD_TEXT_LEN) return false;
  return meta.chunkIdx === 0 || meta.page === 1;
}

/** 질의가 목차/차례를 명시적으로 요구하는지. 요구하면 toc penalty 를 건너뛴다. */
export function queryWantsToc(cleanQ: string): boolean {
  return TOC_INTENT_PATTERN.test(cleanQ);
}

/**
 * vision OCR 이 앞에 붙이는 `[문서] … \n\n` 메타 설명을 떼고 본문 head 를 돌려준다.
 * 메타 설명에만 "목차" 가 있는 청크를 목차로 오판하던 걸 막는다. prefix 가 없으면 원본.
 */
export function stripVisionMetaPrefix(textHead: string): string {
  if (!textHead.startsWith(VISION_META_PREFIX)) return textHead;
  const idx = textHead.indexOf(VISION_META_BODY_SEP);
  if (idx === -1) return textHead;
  return textHead.slice(idx + VISION_META_BODY_SEP.length);
}

/** `JETRAG_TOC_GUARD_ENABLED` 해석. 기본 켜짐 — `"false"` 로만 끈다. */
export function tocGuardEnabled(read: (k: string) => string | undefined): boolean {
  return (read(TOC_GUARD_ENABLED_ENV) ?? "true").toLowerCase() === "true";
}

/**
 * 목차 청크 판정. **vision 으로 뽑은 청크만** 본다 — PDF 본문에 실린 목차와 구분하려는
 * 의도라 `section_title` 이 `(vision)` 으로 시작하지 않으면 바로 false 다.
 */
export function isTocChunk(
  meta: GuardMeta | undefined,
  opts: { enabled: boolean; queryWantsToc: boolean },
): boolean {
  if (!opts.enabled) return false;
  if (opts.queryWantsToc) return false;
  if (!meta) return false;
  if (!meta.sectionTitle.startsWith(VISION_SECTION_PREFIX)) return false;
  return TOC_PATTERN.test(stripVisionMetaPrefix(meta.textHead));
}

/** 가드 두 개를 적용한 점수. 원본은 cover → toc 순으로 곱한다(둘 다 걸리면 0.09). */
export function applyGuards(
  score: number,
  meta: GuardMeta | undefined,
  opts: { skip: boolean; tocEnabled: boolean; queryWantsToc: boolean },
): number {
  if (opts.skip) return score;
  let out = score;
  if (isCoverChunk(meta)) out *= COVER_GUARD_PENALTY;
  if (isTocChunk(meta, { enabled: opts.tocEnabled, queryWantsToc: opts.queryWantsToc })) {
    out *= COVER_GUARD_PENALTY;
  }
  return out;
}
