"""D1 Phase B — Supabase auth 쿠키 파싱 단위 테스트 (plan §1.1).

대상: `app.auth.cookie_token` (순수 함수).

검증:
- derive_project_ref: 정상 URL → ref / 빈값·형식오류 → None
- extract_access_token: 평문 JSON / base64- prefix / 분할 청크 / list 형식 / 오류 graceful

외부 의존성 0 — stdlib unittest + base64/json 만.
실행: `python -m unittest tests.test_cookie_token`
"""

from __future__ import annotations

import base64
import json
import unittest

from app.auth.cookie_token import (
    derive_project_ref,
    extract_access_token,
)

_REF = "abcd1234"
_TOKEN = "eyJhbGciOi.payload.sig"
_COOKIE_NAME = f"sb-{_REF}-auth-token"


def _b64url_value(obj: object) -> str:
    """ssr base64- prefix 형식으로 인코딩 (padding 제거 흉내)."""
    raw = json.dumps(obj).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"base64-{encoded}"


class DeriveProjectRefTest(unittest.TestCase):
    def test_standard_url(self) -> None:
        self.assertEqual(
            derive_project_ref("https://abcd1234.supabase.co"), "abcd1234"
        )

    def test_url_with_path(self) -> None:
        self.assertEqual(
            derive_project_ref("https://abcd1234.supabase.co/rest/v1"), "abcd1234"
        )

    def test_empty_url_returns_none(self) -> None:
        self.assertIsNone(derive_project_ref(""))

    def test_malformed_url_returns_none(self) -> None:
        # hostname 이 없는 형식.
        self.assertIsNone(derive_project_ref("not-a-url"))


class ExtractAccessTokenTest(unittest.TestCase):
    def test_plain_json_value(self) -> None:
        value = json.dumps({"access_token": _TOKEN, "refresh_token": "r"})
        cookies = {_COOKIE_NAME: value}
        self.assertEqual(extract_access_token(cookies, _REF), _TOKEN)

    def test_base64_prefixed_value(self) -> None:
        value = _b64url_value({"access_token": _TOKEN})
        cookies = {_COOKIE_NAME: value}
        self.assertEqual(extract_access_token(cookies, _REF), _TOKEN)

    def test_chunked_value(self) -> None:
        value = _b64url_value({"access_token": _TOKEN})
        mid = len(value) // 2
        cookies = {
            f"{_COOKIE_NAME}.0": value[:mid],
            f"{_COOKIE_NAME}.1": value[mid:],
        }
        self.assertEqual(extract_access_token(cookies, _REF), _TOKEN)

    def test_chunked_stops_at_gap(self) -> None:
        # .0 만 있고 .1 없음 → .0 만 결합 (이후 인덱스 중단). 이 케이스는 깨진 JSON →
        # 디코드 실패 → None (graceful, 401 변환).
        value = _b64url_value({"access_token": _TOKEN})
        cookies = {f"{_COOKIE_NAME}.0": value[: len(value) // 2]}
        self.assertIsNone(extract_access_token(cookies, _REF))

    def test_list_form_value(self) -> None:
        # 일부 ssr 버전은 [access, refresh] 배열 저장 — 첫 원소가 토큰.
        value = json.dumps([_TOKEN, "refresh"])
        cookies = {_COOKIE_NAME: value}
        self.assertEqual(extract_access_token(cookies, _REF), _TOKEN)

    def test_no_matching_cookie_returns_none(self) -> None:
        cookies = {"unrelated": "x"}
        self.assertIsNone(extract_access_token(cookies, _REF))

    def test_empty_project_ref_returns_none(self) -> None:
        value = json.dumps({"access_token": _TOKEN})
        self.assertIsNone(extract_access_token({_COOKIE_NAME: value}, ""))

    def test_invalid_json_returns_none(self) -> None:
        cookies = {_COOKIE_NAME: "{not json"}
        self.assertIsNone(extract_access_token(cookies, _REF))

    def test_invalid_base64_returns_none(self) -> None:
        cookies = {_COOKIE_NAME: "base64-!!!not-base64!!!"}
        self.assertIsNone(extract_access_token(cookies, _REF))

    def test_missing_access_token_field_returns_none(self) -> None:
        value = json.dumps({"refresh_token": "r"})
        cookies = {_COOKIE_NAME: value}
        self.assertIsNone(extract_access_token(cookies, _REF))

    def test_empty_cookies_returns_none(self) -> None:
        self.assertIsNone(extract_access_token({}, _REF))


if __name__ == "__main__":
    unittest.main()
