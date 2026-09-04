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
  // Phase 2 에서 해제: [/^\/search/, "api-search"], [/^\/answer/, "api-answer"],
  //                   [/^\/stats/, "api-account"], [/^\/me\//, "api-account"],
  //                   [/^\/admin/, "api-account"],
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
