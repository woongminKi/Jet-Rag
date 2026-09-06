/**
 * pydantic 이 `Literal` 쿼리 파라미터를 거부할 때 내는 422 본문 규칙.
 *
 * `/stats/trend` 와 `/admin/*` 이 같은 모양을 쓴다. 한쪽에만 두면 고칠 때 갈리므로
 * 여기로 모았다.
 *
 * ## 문구 규칙 — **마지막만 `or`, 나머지는 쉼표**
 * ```
 * 3 개: 'a', 'b' or 'c'      2 개: 'a' or 'b'
 * ```
 * 전부 `or` 로 이으면 2 개일 때만 우연히 맞고 3 개 이상에서 갈린다. in-process 대조로는
 * 안 잡힌다(핸들러를 직접 부르면 FastAPI 검증 계층을 안 거친다) — 배포 후 HTTP 로
 * 재고서야 드러났던 항목이다.
 */

export function literalExpectedText(allowed: readonly string[]): string {
  const q = allowed.map((v) => `'${v}'`);
  if (q.length <= 1) return q.join("");
  return `${q.slice(0, -1).join(", ")} or ${q[q.length - 1]}`;
}

export interface LiteralPicker {
  /** 값을 고르고, 허용 목록 밖이면 오류를 쌓은 뒤 기본값을 돌려준다. */
  pick(name: string, allowed: readonly string[], dflt: string): string;
  /** 쌓인 422 오류. 선언 순서가 곧 배열 순서다. */
  errors: unknown[];
}

export function makeLiteralPicker(sp: URLSearchParams): LiteralPicker {
  const errors: unknown[] = [];
  return {
    errors,
    pick(name, allowed, dflt) {
      const raw = sp.get(name);
      if (raw === null) return dflt;
      if (!allowed.includes(raw)) {
        const expected = literalExpectedText(allowed);
        errors.push({
          type: "literal_error",
          loc: ["query", name],
          msg: `Input should be ${expected}`,
          input: raw,
          ctx: { expected },
        });
        return dflt;
      }
      return raw;
    },
  };
}
