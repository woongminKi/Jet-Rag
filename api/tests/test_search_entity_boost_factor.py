"""S4-B — `_parse_factor_env` (JETRAG_ENTITY_BOOST_FACTOR) 단위 테스트.

배경
- entity_boost factor 를 `JETRAG_ENTITY_BOOST_FACTOR` ENV 화 (default 1.10, [0.5, 3.0]).
  ablation 측정용 — production default 변경은 사용자 사인오프 후.
- 헬퍼는 모듈 import 시 1회 호출되어 상수처럼 쓰이므로, 동작 검증은 헬퍼 함수를 직접
  호출 (모듈 reimport 없이).

검증 포인트
- ENV 미설정 → default 1.10
- `=1.5` → 1.5
- `=99` (상한 밖) → default
- `=0.1` (하한 밖) → default
- `=-1` (음수) → default
- `=abc` (비숫자) → default
- 빈 문자열 → default

stdlib unittest only.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

from app.routers.search import (  # noqa: E402
    _ENTITY_BOOST_FACTOR_DEFAULT,
    _ENTITY_BOOST_FACTOR_ENV,
    _parse_factor_env,
)

_ENV = _ENTITY_BOOST_FACTOR_ENV


class ParseFactorEnvTest(unittest.TestCase):
    def test_unset_returns_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_ENV, None)
            self.assertEqual(_parse_factor_env(_ENV), _ENTITY_BOOST_FACTOR_DEFAULT)
            self.assertEqual(_ENTITY_BOOST_FACTOR_DEFAULT, 1.10)

    def test_empty_string_returns_default(self) -> None:
        with mock.patch.dict(os.environ, {_ENV: ""}):
            self.assertEqual(_parse_factor_env(_ENV), _ENTITY_BOOST_FACTOR_DEFAULT)

    def test_valid_in_range(self) -> None:
        with mock.patch.dict(os.environ, {_ENV: "1.5"}):
            self.assertEqual(_parse_factor_env(_ENV), 1.5)
        with mock.patch.dict(os.environ, {_ENV: "2.0"}):
            self.assertEqual(_parse_factor_env(_ENV), 2.0)
        with mock.patch.dict(os.environ, {_ENV: "0.5"}):  # 경계 포함
            self.assertEqual(_parse_factor_env(_ENV), 0.5)
        with mock.patch.dict(os.environ, {_ENV: "3.0"}):  # 경계 포함
            self.assertEqual(_parse_factor_env(_ENV), 3.0)

    def test_above_max_returns_default(self) -> None:
        with mock.patch.dict(os.environ, {_ENV: "99"}):
            self.assertEqual(_parse_factor_env(_ENV), _ENTITY_BOOST_FACTOR_DEFAULT)
        with mock.patch.dict(os.environ, {_ENV: "3.01"}):
            self.assertEqual(_parse_factor_env(_ENV), _ENTITY_BOOST_FACTOR_DEFAULT)

    def test_below_min_returns_default(self) -> None:
        with mock.patch.dict(os.environ, {_ENV: "0.1"}):
            self.assertEqual(_parse_factor_env(_ENV), _ENTITY_BOOST_FACTOR_DEFAULT)
        with mock.patch.dict(os.environ, {_ENV: "0.49"}):
            self.assertEqual(_parse_factor_env(_ENV), _ENTITY_BOOST_FACTOR_DEFAULT)

    def test_negative_returns_default(self) -> None:
        with mock.patch.dict(os.environ, {_ENV: "-1"}):
            self.assertEqual(_parse_factor_env(_ENV), _ENTITY_BOOST_FACTOR_DEFAULT)
        with mock.patch.dict(os.environ, {_ENV: "-0.5"}):
            self.assertEqual(_parse_factor_env(_ENV), _ENTITY_BOOST_FACTOR_DEFAULT)

    def test_non_numeric_returns_default(self) -> None:
        with mock.patch.dict(os.environ, {_ENV: "abc"}):
            self.assertEqual(_parse_factor_env(_ENV), _ENTITY_BOOST_FACTOR_DEFAULT)
        with mock.patch.dict(os.environ, {_ENV: "1.1x"}):
            self.assertEqual(_parse_factor_env(_ENV), _ENTITY_BOOST_FACTOR_DEFAULT)

    def test_custom_default_and_bounds(self) -> None:
        # 헬퍼 시그니처가 lo/hi/default 를 받는지 — 다른 ENV 에서 재사용 가능성 검증.
        with mock.patch.dict(os.environ, {"X_FACTOR": "5"}):
            self.assertEqual(
                _parse_factor_env("X_FACTOR", default=2.0, lo=1.0, hi=10.0), 5.0
            )
        with mock.patch.dict(os.environ, {"X_FACTOR": "0.5"}):
            self.assertEqual(
                _parse_factor_env("X_FACTOR", default=2.0, lo=1.0, hi=10.0), 2.0
            )


if __name__ == "__main__":
    unittest.main()
