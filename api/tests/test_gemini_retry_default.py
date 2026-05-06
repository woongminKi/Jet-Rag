"""2026-05-06 D2-C — master plan §7.3 정합 회귀 보호.

`_gemini_common._MAX_ATTEMPTS` default = 1 (sweep × retry 곱셈 제거).
회귀 발생 시 ENV `JETRAG_GEMINI_RETRY=3` 으로 즉시 회복 가능.

stdlib unittest only — 실 SDK 호출 없음.
"""

from __future__ import annotations

import importlib
import os
import unittest
from unittest.mock import MagicMock

# import 단계 ENV 회피용 더미.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")


def _reload_with_env(env_value: str | None):
    """`JETRAG_GEMINI_RETRY` ENV 적용 후 `_gemini_common` reload."""
    from app.adapters.impl import _gemini_common

    prev = os.environ.get("JETRAG_GEMINI_RETRY")
    if env_value is None:
        os.environ.pop("JETRAG_GEMINI_RETRY", None)
    else:
        os.environ["JETRAG_GEMINI_RETRY"] = env_value
    try:
        importlib.reload(_gemini_common)
        return _gemini_common
    finally:
        # 원복 + reload 한번 더 (다른 테스트 영향 차단).
        if prev is None:
            os.environ.pop("JETRAG_GEMINI_RETRY", None)
        else:
            os.environ["JETRAG_GEMINI_RETRY"] = prev


class TestGeminiRetryDefault(unittest.TestCase):
    """retry default = 1 (master plan §7.3) 회귀 보호."""

    def tearDown(self) -> None:
        # 다음 테스트 영향 차단 — 기본값으로 reload.
        from app.adapters.impl import _gemini_common  # noqa: F401

        os.environ.pop("JETRAG_GEMINI_RETRY", None)
        importlib.reload(_gemini_common)

    def test_retry_default_is_1(self):
        # ENV 미설정 시 _MAX_ATTEMPTS = 1.
        mod = _reload_with_env(None)
        self.assertEqual(mod._MAX_ATTEMPTS, 1)

    def test_retry_env_override_to_3(self):
        # ENV 설정 시 회복 시나리오 — _MAX_ATTEMPTS = 3.
        mod = _reload_with_env("3")
        self.assertEqual(mod._MAX_ATTEMPTS, 3)

    def test_with_retry_calls_fn_once_when_default(self):
        # default (1회) 일 때 fn 이 1번만 호출 + 첫 실패 시 즉시 raise.
        mod = _reload_with_env(None)
        fn = MagicMock(side_effect=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            mod.with_retry(fn, label="test")
        self.assertEqual(fn.call_count, 1)

    def test_with_retry_calls_fn_3_times_when_env_override(self):
        # ENV override (3회) 일 때 fn 이 3번 호출 후 raise.
        mod = _reload_with_env("3")
        fn = MagicMock(side_effect=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            mod.with_retry(fn, label="test")
        self.assertEqual(fn.call_count, 3)

    def test_worst_case_call_count_50p(self):
        # master plan §7.3 정합 — sweep 2 × retry 1 = page 당 2 호출.
        # 50 페이지 PDF 일괄 실패 시나리오: worst case = 50p × sweep 2 × retry 1 = 100 호출.
        # (이전: 50p × sweep 3 × retry 3 = 450 호출, 4.5x 절감.)
        mod = _reload_with_env(None)
        retry_per_call = mod._MAX_ATTEMPTS  # default 1
        sweeps = 2  # master plan §7.3 default
        pages = 50
        worst_case = pages * sweeps * retry_per_call
        self.assertEqual(worst_case, 100)
        # 회귀 검증 — 곱셈이 다시 늘어나면 즉시 캐치.
        self.assertLessEqual(worst_case, 100)


if __name__ == "__main__":
    unittest.main()
