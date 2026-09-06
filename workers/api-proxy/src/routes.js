/**
 * 경로 → 대상 매핑. **점진 전환의 스위치**다.
 *
 * 여기에 한 줄을 더하면 그 경로만 Supabase 로 넘어가고, 지우면 즉시 Railway 로 되돌아온다.
 * Phase 2~5 는 각 Phase 가 끝날 때 해당 줄의 주석을 푸는 방식으로 진행한다.
 *
 * 순수 함수로 뽑아 둔 이유: Worker 를 배포하지 않고도 라우팅 규칙을 시험할 수 있어야
 * "배포해 봐야 아는" 구간을 줄일 수 있다.
 */

/** @type {[RegExp, string][]} */
export const ROUTES = [
  [/^\/auth\//, "api-account"],
  [/^\/health$/, "api-account"],
  // Phase 2 (2026-09-05) — `/search` 전환. 골든셋 123행 응답 불일치 0, 실제 HTTP 26건 일치.
  // `/search/` 처럼 후행 슬래시가 붙어도 잡히도록 `$` 를 안 붙였다 (함수가 슬래시를 뗀다).
  [/^\/search/, "api-search"],
  // 2026-09-06 전환 — `/stats` 와 `/stats/trend`. 응답 대조 HTTP 13건 일치.
  [/^\/stats/, "api-account"],
  // 2026-09-06 전환 — `/me/*` 4개. 응답 대조 HTTP 13건 일치(비인증 401·405·404 순서 포함).
  // **슬래시를 요구한다** — `/me` 단독과 `/mefoo` 는 원본에 라우트가 없어 404 인데,
  // 그건 Railway 가 이미 내주고 있으므로 굳이 Edge 로 넘길 이유가 없다.
  [/^\/me\//, "api-account"],
  // 2026-09-06 전환 — `/admin` 의 **읽기 2개만**. 응답 대조 HTTP 19건 일치.
  // `/admin/subscriptions` 는 같은 경로에 POST(구독 수동 변경)가 붙어 있어 여기서 열지
  // 않는다 — 경로로는 메서드를 못 가른다. Edge 쪽 구현·대조는 끝났고 Phase 3(쓰기)에서
  // 한 줄만 더하면 된다.
  [/^\/admin\/queries\//, "api-account"],
  [/^\/admin\/feedback\//, "api-account"],
  // Phase 2 나머지: [/^\/answer/, "api-answer"]
  // Phase 3 에서 해제: [/^\/admin\/subscriptions/, "api-account"],
  // Phase 3 에서 해제: [/^\/documents/, "api-documents"],
  // Phase 4 에서 해제: [/^\/payments/, "api-payments"], [/^\/billing/, "billing-run"],
  // Phase 5 에서 해제: [/^\/email/, "email-webhook"],
];

/**
 * 경로를 어디로 보낼지 정한다.
 *
 * @param {string} pathname
 * @returns {string | null} Edge Function 이름. null 이면 기존 백엔드로.
 */
export function resolveTarget(pathname) {
  const match = ROUTES.find(([re]) => re.test(pathname));
  return match ? match[1] : null;
}
