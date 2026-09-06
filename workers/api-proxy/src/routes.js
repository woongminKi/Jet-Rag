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
  // Phase 2 (2026-09-05) — `/search` 전환. 골든셋 123행 응답 불일치 0.
  // **끝을 고정한다.** 처음엔 `/^\/search/` 로 열었다가 원본에 실재하는
  // `/search/eval-precision`(GET·POST) 까지 삼켜 운영에서 404 를 냈다(2026-09-06 실측:
  // Railway 200/422/401/405 ↔ 프록시 전부 404). 접두어 규칙을 쓸 때는 그 접두어로 시작하는
  // **원본 라우트를 전수 확인**해야 한다 — 아래 `이관 선언` 테스트가 그걸 강제한다.
  [/^\/search\/?$/, "api-search"],
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
  // 2026-09-06 전환 — `/answer` **본체만**. LLM 미호출 경로 11건 + LLM 경로 3건 일치.
  // **끝을 고정한다** — `/answer/feedback` 과 `/answer/eval-ragas` 는 아직 Railway 다.
  // `/search` 를 접두어로 열었다가 `/search/eval-precision` 을 삼킨 사고(5a74ea6)와
  // 같은 실수를 하지 않기 위해서다. 아래 `이관 선언` 테스트가 이걸 강제한다.
  [/^\/answer\/?$/, "api-answer"],
  // 2026-09-06 전환 — `/answer/feedback`. 순서 계약(JSON 파싱만 인증보다 먼저)까지 14건 일치.
  [/^\/answer\/feedback$/, "api-answer"],
  // 2026-09-06 전환 — `/admin/subscriptions` (GET+POST). POST 는 구독을 바꾸는 **쓰기**다.
  // 코드·대조는 /admin 묶음(d6bc9e6)에서 끝났고 여기서 프록시만 연다.
  [/^\/admin\/subscriptions$/, "api-account"],
  // **이관 불가 — Railway 에 남는다.** `/answer/eval-ragas` 와 `/search/eval-precision` 은
  // POST 가 `ragas` + `langchain-google-genai` + `datasets`(전부 Python 전용)를 쓴다.
  // GET(캐시 조회)만 옮기고 싶어도 POST 와 경로가 같아 규칙으로 가를 수 없다.
  // Phase 6(Railway 제거)의 실질적 차단 요인 — 별도 결정 필요.
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
