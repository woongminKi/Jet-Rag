/**
 * `U+0000`(NUL) 재귀 제거 — Postgres 가 TEXT/JSONB 에 NUL 을 받지 않는다.
 *
 * ## 실제로 터졌다
 * arXiv(LaTeX) PDF 를 실제 Supabase 로 돌리자
 * `ingest_artifacts 저장 실패: unsupported Unicode escape sequence` 로 죽었다.
 * 가짜 클라이언트 테스트는 이걸 잡지 못한다 — **붙여 봐야 나오는 종류의 버그다.**
 *
 * 원본은 `SupabasePgVectorStore._strip_null_bytes` 로 **`chunks` 저장 직전**에만 지운다
 * (주석: "arXiv 같은 LaTeX PDF 추출 보호"). 계약을 그대로 옮겼다:
 * 문자열은 제거, dict/list 는 재귀, 나머지는 그대로.
 *
 * ## 제거 시점이 Edge 에서는 더 이르다 — 그래도 결과가 같은지 쟀다
 * Edge 는 중간 산출물을 jsonb 에 넣어야 해서 **extract 단계에서** 지워야 한다. 그러면
 * 청킹 입력의 길이가 줄어 800 자 분할 경계가 밀릴 수 있다. 실측으로 확인했다
 * (2026-09-07, 자산 3 건):
 *
 * | 자산 | NUL | 늦게 지움(현행 Python) | 일찍 지움(Edge) |
 * |---|---|---|---|
 * | arXiv 56p | 96 개 | 749 청크 | 749 청크 — **동일** |
 * | sample-report 60p | 0 | 525 | 525 |
 * | SK 60p | 0 | 758 | 758 |
 *
 * **이 자산들에서 같다는 뜻이지 일반 보장은 아니다.** NUL 이 800 자 경계에 정확히
 * 걸릴 만큼 많으면 갈릴 수 있다. 갈리기 시작하면
 * `verify_pdf_pipeline_baseline.py` 가 잡는다.
 */

const NUL = "\u0000";

/** 제거한 NUL 개수. 0 이 아니면 원본 문서에 NUL 이 있었다는 뜻이라 기록해 둘 값이다. */
export interface StripResult<T> {
  value: T;
  removed: number;
}

function walk(value: unknown, counter: { n: number }): unknown {
  if (typeof value === "string") {
    if (!value.includes(NUL)) return value;
    // `split().length - 1` 이 NUL 개수다.
    counter.n += value.split(NUL).length - 1;
    return value.replaceAll(NUL, "");
  }
  if (Array.isArray(value)) return value.map((v) => walk(v, counter));
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      // 키에도 NUL 이 올 수 있다. 원본 dict 재귀는 값만 훑지만, 키가 NUL 을 담으면
      // 그대로 jsonb 에서 터지므로 여기서는 키도 씻는다.
      out[walk(k, counter) as string] = walk(v, counter);
    }
    return out;
  }
  return value;
}

/** 문자열은 제거, 배열·객체는 재귀, 나머지(number/boolean/null)는 그대로. */
export function stripNulls<T>(value: T): StripResult<T> {
  const counter = { n: 0 };
  const out = walk(value, counter) as T;
  return { value: out, removed: counter.n };
}
