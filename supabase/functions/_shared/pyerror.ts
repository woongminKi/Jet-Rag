/**
 * Python `str(exc)` — 예외의 **메시지만** 준다.
 *
 * JS `String(err)` 는 `"Error: 메시지"` 처럼 **클래스 이름을 앞에 붙인다.**
 * 로그로만 쓰면 상관없지만 이 값이 DB 컬럼으로 들어가면 원본과 다른 데이터가 쌓인다:
 * - `payment_history.detail` (결제 실패 사유 — 관리자 화면에 보인다)
 * - `vision_usage_log.error_msg` (비용·오류 분석의 입력)
 * - PPTX Vision 실패 `warnings` (문서에 저장돼 사용자에게 보인다)
 *
 * `payment_history` 대조에서 걸렸고, 같은 패턴이 vision 경로 3 곳 + PPTX 1 곳에 있었다.
 * **DB 로 나가는 자리에는 이 함수를 쓴다.** 콘솔 로그는 그대로 둬도 된다.
 */
export function pyStrError(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
