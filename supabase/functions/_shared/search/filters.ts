/**
 * documents 메타 fetch + 메타 필터 4종 — `search.py` 4) 단계 포팅.
 *
 * 후보 문서 id 를 받아 documents 행을 가져오면서 `tags`·`doc_type`·`from_date`·`to_date`
 * 를 WHERE 로 적용한다. 여기서 걸러진 문서의 청크는 최종 결과에서 통째로 빠진다.
 *
 * ## 대조는 요청 URL 로 한다
 * 이건 순수 함수가 아니라 쿼리 빌더라, "같은 결과"의 기준이 곧 **PostgREST 에 나가는
 * 쿼리스트링**이다. postgrest-py 와 supabase-js 가 같은 문자열을 만드는 걸 실측으로
 * 확인했고(`verify_search_filters_parity.py`), 그래서 인코딩까지 바이트로 대조한다.
 * 값에 `,` 가 들어가면 `in.(a,"b,c")` 처럼 따옴표가 붙는 등 규칙이 미묘하다.
 *
 * ## falsy 는 필터를 안 건다
 * 원본이 `if doc_type:` / `if tags:` 라 빈 문자열·빈 배열이면 **필터 자체가 없다**.
 * 빈 배열에 `contains` 를 걸면 모든 행이 통과해버려 결과가 달라지므로 이 구분이 중요하다.
 * (`doc_type=""` 는 그 앞 검증에서 이미 400 이라 여기 도달하지 않는다.)
 */

import type { SupabaseClient } from "@supabase/supabase-js";

/**
 * doc-level RRF 가산에 `doc_embedding` 이 필요해 select 에 함께 넣는다.
 * 1024-dim × 8B ≈ 8KB/doc — 문서 50 건이면 400KB 다.
 */
export const DOCUMENTS_SELECT = "id, title, doc_type, tags, summary, created_at, doc_embedding";

export interface DocumentFilters {
  /** 호출자 본인 문서로 격리. RLS 와 별개로 여기서도 건다. */
  userId: string;
  candidateDocIds: string[];
  docType: string | null;
  tags: string[] | null;
  /** `iso_datetime.ts` 가 만든 isoformat 문자열. */
  fromDate: string | null;
  toDate: string | null;
}

/**
 * 빌더 타입을 클라이언트에서 유도한다 — `@supabase/postgrest-js` 를 직접 import 하면
 * supabase-js 에 딸려 오는 패키지라도 import map 에 새 항목이 필요해진다.
 */
type DocsQuery = ReturnType<ReturnType<SupabaseClient["from"]>["select"]>;

/**
 * documents 조회 쿼리를 만든다. 실행은 호출부가 한다 — 그래야 대조기가 URL 만 보고
 * 네트워크 없이 잴 수 있다.
 */
export function buildDocumentsQuery(client: SupabaseClient, f: DocumentFilters): DocsQuery {
  let q = client
    .from("documents")
    .select(DOCUMENTS_SELECT)
    .in("id", f.candidateDocIds)
    .eq("user_id", f.userId)
    .is("deleted_at", null) as DocsQuery;

  if (f.docType) q = q.eq("doc_type", f.docType);
  // GIN `tags @> ARRAY[...]` — 요청한 태그를 **모두** 가진 문서만 (AND).
  if (f.tags && f.tags.length) q = q.contains("tags", f.tags);
  if (f.fromDate) q = q.gte("created_at", f.fromDate);
  if (f.toDate) q = q.lte("created_at", f.toDate);
  return q;
}
