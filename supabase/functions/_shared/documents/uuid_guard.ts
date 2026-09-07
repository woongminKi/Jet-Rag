/**
 * `doc_id` 가 UUID 인지 본다 — **DB 에 닿기 전에.**
 *
 * ## 왜 필요한가
 * `documents.id` 는 `uuid` 컬럼이라 `eq("id", "url")` 을 보내면 Postgres 가
 * `invalid input syntax for type uuid` 를 낸다. 그게 그대로 새어 **500** 이 된다.
 * 사용자가 주소창에 아무거나 넣으면 만들 수 있는 500 이다.
 *
 * ```
 * GET /documents/not-a-uuid            → 500   (고치기 전)
 * GET /documents/{없는 UUID}            → 404
 * ```
 *
 * ## 원본도 같지만 여기서는 고친다
 * 실측(2026-09-07): Railway 도 세 경로 모두 500 이다. 이관 중에는 동작을 맞추려고
 * 재현해 뒀지만, `/documents/*` 읽기는 **이미 100% Edge** 라(프록시가 Railway 로 안
 * 보낸다) 여기서 고쳐도 두 백엔드가 갈리는 구간이 없다.
 *
 * ## 없는 UUID 와 같은 답을 준다
 * 존재할 수 없는 id 이므로 "못 찾았다" 가 맞다. 404 를 새로 만드는 게 아니라
 * **이미 있는 404 로 합류시키는 것**이다 — 응답 본문도 같다.
 */

/** RFC 4122 표기. 버전·variant 는 보지 않는다 — Postgres 가 받는 범위와 같게 둔다. */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(value: string): boolean {
  return UUID_RE.test(value);
}
