/**
 * `api/app/db/client.py` 포팅 — 백엔드 전용 Supabase 클라이언트.
 *
 * **service_role 키를 쓴다 = RLS 를 우회한다.** 프론트·공개 번들에 절대 노출하지 않는다.
 * 사용자 격리는 RLS 가 아니라 호출부가 `user_id` 로 필터해서 지킨다 — `current_user.ts` 의
 * `userId` 가 그 격리 키다.
 *
 * ## 원본의 HTTP/1.1 우회는 옮기지 않는다
 * Python 쪽에는 postgrest 세션을 HTTP/1.1 로 갈아끼우는 코드가 있다. 이유는 postgrest-py 가
 * `http2=True` 를 하드코딩해서, Supabase 게이트웨이가 long-idle 뒤 GOAWAY(COMPRESSION_ERROR)
 * 를 보내면 `@lru_cache` 싱글톤이 죽은 연결을 재사용해 500 이 나던 문제였다.
 *
 * Edge 에서는 그 조건이 성립하지 않는다. supabase-js 는 런타임의 `fetch` 를 쓰고 HPACK
 * 상태를 직접 들고 있지 않으며, 애초에 여기서는 **싱글톤을 만들지 않는다**(아래 참조).
 * 그래서 우회 자체가 필요 없다 — 옮기지 않은 게 누락이 아니라 판단이다.
 *
 * ## 캐시하지 않는다
 * 원본은 `@lru_cache` 로 싱글톤을 만든다. 상주 프로세스 전제다. Edge 는 인스턴스 재사용이
 * 보장되지 않으므로 상태를 두면 디버깅 불가능한 불일치가 생긴다. 클라이언트 생성은
 * 객체 조립일 뿐이라(연결을 미리 열지 않는다) 요청마다 만들어도 비용이 없다.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

export interface DbSettings {
  supabaseUrl: string;
  supabaseServiceRoleKey: string;
}

export function createServiceClient(settings: DbSettings): SupabaseClient {
  if (!settings.supabaseUrl || !settings.supabaseServiceRoleKey) {
    throw new Error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 가 설정되지 않았습니다.");
  }
  return createClient(settings.supabaseUrl, settings.supabaseServiceRoleKey, {
    auth: {
      // Edge 에는 브라우저 저장소도 세션 수명도 없다. 켜 두면 불필요한 동작만 붙는다.
      persistSession: false,
      autoRefreshToken: false,
    },
  });
}
