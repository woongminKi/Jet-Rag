from functools import lru_cache

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
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
