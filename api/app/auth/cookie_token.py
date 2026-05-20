"""Supabase auth 쿠키 파싱 — 순수 함수 (D1 Phase B, plan §1.1).

아키텍처 B (크로스 서브도메인 httpOnly 쿠키): 프론트(`@supabase/ssr` createServerClient)가
세션을 httpOnly 쿠키 `sb-<project-ref>-auth-token` 에 저장한다. 백엔드는 Authorization
헤더가 없을 때 이 쿠키에서 access_token(JWT)을 추출해 검증한다 (브라우저 직접 데이터 콜이
`credentials: 'include'` 로 쿠키만 보내는 경로 대응).

쿠키 형식 (`@supabase/ssr` 0.10.x):
- 이름: `sb-<project-ref>-auth-token`. project-ref = SUPABASE_URL hostname 의 첫 segment
  (`https://abcd1234.supabase.co` → `abcd1234`).
- 값이 3180자를 넘으면 `.0` / `.1` … 청크로 분할된다. 순서대로 concat 해 원본 복원.
- 복원한 값이 `base64-` prefix 면 그 뒤를 base64url 디코드 → JSON. prefix 없으면 그대로 JSON.
- JSON 의 `access_token` 필드가 검증 대상 JWT.

순수 함수 — Settings/Request 의존 없음 (project_ref·cookies dict 를 인자로 받음).
어떤 입력 오류도 None 반환 (graceful) — 호출부가 401 로 변환한다.
"""

from __future__ import annotations

import base64
import binascii
import json
from urllib.parse import urlsplit

# `@supabase/ssr` 쿠키 이름 패턴. project-ref 만 런타임에 결합.
_COOKIE_NAME_PREFIX = "sb-"
_COOKIE_NAME_SUFFIX = "-auth-token"

# base64 인코딩 쿠키 값의 prefix (ssr 0.10.x). 디코드 대상 표식.
_BASE64_VALUE_PREFIX = "base64-"

# 분할 쿠키의 청크 인덱스 상한 (방어). ssr 는 3180자/청크라 정상 세션은 수 청크 이내.
_MAX_COOKIE_CHUNKS = 16


def derive_project_ref(supabase_url: str) -> str | None:
    """SUPABASE_URL 에서 project-ref(hostname 첫 segment)를 유도. 실패 시 None.

    `https://abcd1234.supabase.co` → `abcd1234`. URL 미설정/형식 오류는 None (graceful).
    """
    if not supabase_url:
        return None
    host = urlsplit(supabase_url).hostname
    if not host:
        return None
    ref = host.split(".", 1)[0]
    return ref or None


def _join_chunked_cookie(cookies: dict[str, str], base_name: str) -> str | None:
    """`base_name` (단일) 또는 `base_name.0`, `base_name.1` … (분할) 쿠키를 결합.

    단일 쿠키가 있으면 그대로. 없으면 `.0` 부터 연속 인덱스를 concat (gap 에서 중단).
    어느 것도 없으면 None.
    """
    single = cookies.get(base_name)
    if single is not None:
        return single

    parts: list[str] = []
    for idx in range(_MAX_COOKIE_CHUNKS):
        chunk = cookies.get(f"{base_name}.{idx}")
        if chunk is None:
            break
        parts.append(chunk)
    if not parts:
        return None
    return "".join(parts)


def _decode_cookie_value(raw: str) -> object | None:
    """쿠키 원본 값을 JSON 객체로 디코드. `base64-` prefix 면 base64url 선해독.

    파싱 실패는 None (graceful).
    """
    payload = raw
    if payload.startswith(_BASE64_VALUE_PREFIX):
        encoded = payload[len(_BASE64_VALUE_PREFIX) :]
        try:
            # base64url + padding 보정 (ssr 는 padding 제거 후 저장 가능).
            padded = encoded + "=" * (-len(encoded) % 4)
            decoded_bytes = base64.urlsafe_b64decode(padded)
            payload = decoded_bytes.decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return None
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return None


def extract_access_token(cookies: dict[str, str], project_ref: str) -> str | None:
    """Supabase auth 쿠키에서 access_token(JWT) 추출. 실패 시 None (graceful).

    - cookies: 요청 쿠키 dict (name → value).
    - project_ref: derive_project_ref 결과. None/빈값이면 추출 불가 → None.
    """
    if not project_ref:
        return None

    base_name = f"{_COOKIE_NAME_PREFIX}{project_ref}{_COOKIE_NAME_SUFFIX}"
    raw = _join_chunked_cookie(cookies, base_name)
    if raw is None:
        return None

    decoded = _decode_cookie_value(raw)
    # ssr 는 세션 객체(JSON)를 저장하나, 일부 버전은 access/refresh 토큰 배열을 저장.
    # dict.access_token 우선, list 면 첫 원소를 토큰으로 간주 (graceful).
    if isinstance(decoded, dict):
        token = decoded.get("access_token")
        return token if isinstance(token, str) and token else None
    if isinstance(decoded, list) and decoded:
        first = decoded[0]
        return first if isinstance(first, str) and first else None
    return None
