/**
 * CPython `difflib.SequenceMatcher(None, a, b).ratio()` 포팅.
 *
 * `dedup` 이 파일명 유사도를 이걸로 잰다. 값이 조금만 달라도 Tier 3 판정(임계 0.6)이
 * 뒤집히므로 **알고리즘을 그대로** 옮긴다 — 흔한 "레벤슈타인으로 대충" 은 다른 값이 나온다.
 *
 * ## 알고리즘 (Ratcliff/Obershelp 변형)
 * 1. `b` 의 원소 → 등장 위치 목록(`b2j`)
 * 2. 가장 긴 일치 구간을 찾고, 그 좌우를 재귀적으로 다시 찾는다
 * 3. `ratio = 2 * (일치한 총 길이) / (len(a) + len(b))`
 *
 * ## autojunk 를 빼먹으면 안 된다
 * `b` 가 **200 자 이상**이면, `b` 안에서 `len(b)/100 + 1` 회를 초과해 나오는 원소를
 * "너무 흔하다" 며 색인에서 **뺀다**. 긴 파일명에서 이게 실제로 값을 바꾼다.
 *
 * ## 문자열은 코드포인트 단위다
 * Python 은 문자열을 코드포인트로 순회한다. JS `.length` / 인덱싱은 UTF-16 이라
 * 이모지가 든 파일명에서 길이부터 갈린다. `[...s]` 로 쪼개서 다룬다.
 *
 * `isjunk` 는 원본이 `None` 을 넘기므로 **junk 집합은 늘 비어 있다.** 그래서
 * `find_longest_match` 의 junk 확장 루프 두 개는 절대 실행되지 않는다 — 옮기지 않았고,
 * 그게 누락이 아니라는 걸 여기 적어 둔다.
 */

/** 원본 `_calculate_ratio`. 길이가 0 이면 1.0 이다(빈 문자열 두 개는 "같다"). */
function calculateRatio(matches: number, length: number): number {
  if (length) return (2.0 * matches) / length;
  return 1.0;
}

interface Chain {
  b2j: Map<string, number[]>;
}

/** 원본 `__chain_b` — `b` 색인 + autojunk 제거. */
function chainB(b: string[], autojunk: boolean): Chain {
  const b2j = new Map<string, number[]>();
  for (let i = 0; i < b.length; i++) {
    const arr = b2j.get(b[i]);
    if (arr) arr.push(i);
    else b2j.set(b[i], [i]);
  }
  // `isjunk` 가 없으므로 junk 제거 단계는 통째로 건너뛴다.
  const n = b.length;
  if (autojunk && n >= 200) {
    const ntest = Math.floor(n / 100) + 1;
    const popular: string[] = [];
    for (const [elt, idxs] of b2j) if (idxs.length > ntest) popular.push(elt);
    for (const elt of popular) b2j.delete(elt);
  }
  return { b2j };
}

/**
 * 원본 `find_longest_match(alo, ahi, blo, bhi)`.
 *
 * 반환은 `[i, j, size]` — `a[i:i+size] == b[j:j+size]` 인 가장 긴 구간.
 * 동점이면 **`i` 가 가장 작은 것**, 그다음 `j` 가 가장 작은 것이다(원본의 `>` 비교).
 */
function findLongestMatch(
  a: string[],
  b: string[],
  chain: Chain,
  alo: number,
  ahi: number,
  blo: number,
  bhi: number,
): [number, number, number] {
  let besti = alo;
  let bestj = blo;
  let bestsize = 0;

  let j2len = new Map<number, number>();
  for (let i = alo; i < ahi; i++) {
    const newj2len = new Map<number, number>();
    const js = chain.b2j.get(a[i]);
    if (js) {
      for (const j of js) {
        if (j < blo) continue;
        if (j >= bhi) break;
        const k = (j2len.get(j - 1) ?? 0) + 1;
        newj2len.set(j, k);
        if (k > bestsize) {
          besti = i - k + 1;
          bestj = j - k + 1;
          bestsize = k;
        }
      }
    }
    j2len = newj2len;
  }

  // 색인에서 빠진(popular) 원소도 양옆으로는 이어 붙인다.
  while (
    besti > alo && bestj > blo && a[besti - 1] === b[bestj - 1]
  ) {
    besti--;
    bestj--;
    bestsize++;
  }
  while (
    besti + bestsize < ahi && bestj + bestsize < bhi &&
    a[besti + bestsize] === b[bestj + bestsize]
  ) {
    bestsize++;
  }
  return [besti, bestj, bestsize];
}

/**
 * 일치 구간의 **총 길이**. `ratio()` 에는 이 합만 필요하다.
 *
 * 원본은 구간을 모아 정렬하고 인접한 것을 합치지만, 그 과정이 총 길이를 바꾸지 않는다.
 */
function totalMatches(a: string[], b: string[], chain: Chain): number {
  let total = 0;
  const queue: [number, number, number, number][] = [[0, a.length, 0, b.length]];
  while (queue.length > 0) {
    // 원본은 `queue.pop()` — 끝에서 꺼낸다. 총합만 쓰므로 순서는 결과를 안 바꾼다.
    const [alo, ahi, blo, bhi] = queue.pop()!;
    const [i, j, k] = findLongestMatch(a, b, chain, alo, ahi, blo, bhi);
    if (k) {
      total += k;
      if (alo < i && blo < j) queue.push([alo, i, blo, j]);
      if (i + k < ahi && j + k < bhi) queue.push([i + k, ahi, j + k, bhi]);
    }
  }
  return total;
}

/**
 * `SequenceMatcher(None, a, b).ratio()`.
 *
 * `b` 쪽만 색인한다 — **인자 순서가 값을 바꾼다.** 원본 호출 순서를 지켜야 한다.
 */
export function sequenceMatcherRatio(a: string, b: string, autojunk = true): number {
  const aa = [...a];
  const bb = [...b];
  const chain = chainB(bb, autojunk);
  return calculateRatio(totalMatches(aa, bb, chain), aa.length + bb.length);
}
