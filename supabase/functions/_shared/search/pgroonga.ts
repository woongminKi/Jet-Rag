/**
 * `api/app/routers/search.py` 의 `_strip_korean_particle` · `_build_pgroonga_query` 포팅.
 *
 * ## 왜 완전 일치여야 하나
 * 여기서 만든 문자열이 PGroonga `&@~` 질의로 **그대로** 들어간다. 한 글자만 달라져도
 * sparse 결과 집합이 바뀌고, RRF 융합을 거쳐 최종 순위가 통째로 달라진다.
 * "비슷하면 됨" 이 성립하지 않는 자리다.
 *
 * ## 왜 OR 로 바꾸나 (원본 주석 요약)
 * PGroonga query mode 는 **모든 토큰이 같은 chunk 에 다 있어야** 잡힌다(AND).
 * 사용자 자연어 질의는 3~5 단어라 한 단어만 vocab 에 없어도 전체가 0건이 된다.
 * OR 로 바꿔 한 단어라도 맞는 chunk 를 sparse 결과에 넣고, dense 와 RRF 로 합산한다.
 *
 * ## 조사 strip 의 경계 (원본 그대로)
 * Mecab 이 `전폭은` 같은 조사 결합 토큰을 못 쪼개 vocab 부재로 처리한다. 그래서 끝 1자만
 * 잘라낸다. 다만 세 가지 제약이 있다:
 * - **길이 3 미만이면 안 자른다** — 짧은 단어의 false positive 를 피한다.
 * - **`이` 는 목록에서 뺐다** — `디스플레이`·`알고리즘` 같은 외래어 명사 끝과 충돌한다.
 *   대신 `회사이` 같은 건 보존되는 trade-off 를 받아들인 것이다.
 * - 화이트리스트에 있는 글자만 자른다 — `얼마나`·`종류야` 같은 어미는 남긴다.
 */

/** 가장 흔한 1자 조사. `이` 는 의도적으로 제외 — 위 §조사 strip 의 경계 참조. */
const KOREAN_PARTICLES_1 = new Set(["는", "은", "가", "을", "를", "도", "만", "에", "의"]);

const PARTICLE_STRIP_MIN_LEN = 3;

/** Python `str.rstrip("?!.,;:")` — 끝에서 이 문자들이 연속되는 만큼 모두 제거한다. */
const TRAILING_PUNCT = /[?!.,;:]+$/;

/**
 * 조사 + 끝 구두점 정리. `전폭은?` → `전폭`.
 *
 * 길이는 **코드포인트**로 센다. JS `.length` 는 UTF-16 단위라 이모지가 든 토큰에서
 * Python `len()` 과 갈린다 — Phase 1 에서 같은 이유로 문자 수가 어긋난 적이 있다.
 */
export function stripKoreanParticle(token: string): string {
  const cleaned = token.replace(TRAILING_PUNCT, "");
  const chars = [...cleaned];
  if (chars.length < PARTICLE_STRIP_MIN_LEN) return cleaned;
  if (KOREAN_PARTICLES_1.has(chars[chars.length - 1])) {
    return chars.slice(0, -1).join("");
  }
  return cleaned;
}

/**
 * Python `str.split()` (인자 없음) 과 같은 분해.
 *
 * 인자 없는 `split()` 은 **모든 유니코드 공백**을 구분자로 보고 연속 공백을 하나로 묶으며
 * 앞뒤 빈 토큰을 만들지 않는다. `split(" ")` 과 다르다 — 전각 공백(U+3000)·탭·개행이 섞인
 * 질의에서 갈린다(운영 실측: `q=　` 도 빈 질의로 처리된다).
 */
function pythonSplit(s: string): string[] {
  return s.split(/\s+/u).filter((t) => t !== "");
}

/**
 * PGroonga 질의 문자열 생성.
 *
 * `expansion_enabled` 분기는 옮기지 않았다 — `JETRAG_QUERY_EXPANSION` 이 운영에서 꺼져 있고
 * (기본값 `"false"`, Railway 미설정) 동의어 사전을 통째로 끌고 와야 한다.
 * 켜졌을 때 조용히 무시되지 않도록 `unsupported.ts` 가 기동 시 막는다.
 */
export function buildPgroongaQuery(q: string): string {
  const tokens = pythonSplit(q.trim())
    .map(stripKoreanParticle)
    // strip 결과가 빈 토큰이 될 수 있다(구두점만 있던 토큰). 원본도 한 번 더 거른다.
    .filter((t) => t !== "");

  if (tokens.length === 0) return q.trim();
  if (tokens.length <= 1) return tokens[0];
  return tokens.join(" OR ");
}
