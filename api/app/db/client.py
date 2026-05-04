from functools import lru_cache

import httpx
from supabase import Client, create_client

from app.config import get_settings


@lru_cache
def get_supabase_client() -> Client:
    """백엔드 전용 Supabase 클라이언트. service_role 키를 사용해 RLS 를 우회한다.

    프론트엔드·공개 번들에 절대 노출 금지.
    """
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 가 설정되지 않았습니다. .env 확인."
        )
    client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    _force_postgrest_http1(client)
    return client


def _force_postgrest_http1(client: Client) -> None:
    """postgrest httpx 세션을 HTTP/1.1 로 재구성 (W25 D14 Sprint C).

    Why: postgrest-py 가 http2=True 하드코딩 (postgrest/_sync/client.py:102).
    Supabase 게이트웨이가 long-idle 후 GOAWAY error_code:9 (COMPRESSION_ERROR /
    HPACK 동적 테이블 손상) 보내면 lru_cache 싱글톤이 stale connection 재사용 시
    httpx.RemoteProtocolError 가 /stats, /documents 등에서 500 으로 노출.
    HTTP/1.1 은 HPACK 미사용이라 해당 클래스 에러 자체가 발생하지 않음.

    How: client.postgrest property 를 한 번 trigger 해 lazy init 후, 내부 httpx
    세션을 닫고 동일 base_url/headers/timeout 으로 http2=False 새 인스턴스로 교체.
    storage/auth/functions 는 별도 httpx 인스턴스라 영향 없음.
    """
    pg = client.postgrest
    old = pg.session
    new = httpx.Client(
        base_url=str(old.base_url),
        headers=dict(old.headers),
        timeout=old.timeout,
        follow_redirects=True,
        http2=False,
    )
    new._jetrag_http1_only = True
    old.close()
    pg.session = new
