"""Phase 1 S0 D1 보강 (P1-1) — `app.ingest.stages.extract` lazy import 안전성.

검증 포인트
- module-level 에서 `get_vision_captioner` / `ImageParser` 호출 0 (lazy 화 후)
- 알 수 없는 provider ENV 로도 module import 자체는 성공해야 함 (서버 startup 안전)
- `JETRAG_LLM_PROVIDER=openai` + `OPENAI_API_KEY` 부재 시 Gemini fallback (warn)

설계 — 격리 전략
- import-time 부작용 검증은 `subprocess` 로 별도 Python 프로세스에서 수행.
  같은 프로세스에서 `sys.modules.pop` + reload 하면 다른 테스트 파일이 이미 import
  한 함수 객체의 `__globals__` 가 다른 module 인스턴스를 가리켜 patch.object 가
  무효화되는 회귀가 발생.
- lazy 함수 (`_get_image_parser`) 캐싱 검증은 같은 프로세스에서 수행하되 cache_clear 로 격리.

stdlib unittest only.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import patch

# 다른 테스트와 동일 — import 단계 ENV 체크 회피용 더미.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")


def _run_in_subprocess(snippet: str, *, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """별도 Python 인터프리터로 snippet 실행 후 (rc, stdout, stderr) 반환.

    extract module 의 import-time 부작용 검증용. 같은 프로세스에서 reload 하면
    다른 테스트 파일과 module 인스턴스가 어긋나 회귀가 발생하므로 isolation 필수.
    """
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True, text=True, env=full_env, timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


class ExtractImportTimeSafetyTest(unittest.TestCase):
    """module-level 에서 vision factory 호출 0 + 안전한 ENV fallback — 별도 프로세스에서 검증."""

    def test_module_import_does_not_call_vision_factory(self) -> None:
        """import 만으로는 `get_vision_captioner` 호출 0 (lazy 화 후)."""
        snippet = textwrap.dedent("""
            from unittest.mock import patch
            with patch("app.adapters.factory.get_vision_captioner") as mock_factory:
                import app.ingest.stages.extract  # noqa: F401
                # 호출 0 검증 — print 로 통신
                if mock_factory.called:
                    print(f"FAIL: get_vision_captioner called {mock_factory.call_count} times")
                    raise SystemExit(1)
            print("OK")
        """)
        rc, stdout, stderr = _run_in_subprocess(snippet)
        self.assertEqual(rc, 0, f"subprocess fail. stdout={stdout} stderr={stderr}")
        self.assertIn("OK", stdout)

    def test_module_import_with_invalid_provider_does_not_fail(self) -> None:
        """JETRAG_LLM_PROVIDER=invalid 여도 module import 자체는 성공.

        이전 (P1-1 fix 전) 구현은 module-level 에서 즉시 factory 호출 →
        invalid ENV 시 ValueError 가 import 단계에서 raise → 서버 startup fail.
        """
        snippet = textwrap.dedent("""
            try:
                import app.ingest.stages.extract  # noqa: F401
                print("OK")
            except Exception as exc:
                print(f"FAIL: import 실패 {exc!r}")
                raise SystemExit(1)
        """)
        rc, stdout, stderr = _run_in_subprocess(
            snippet, env={"JETRAG_LLM_PROVIDER": "invalid_provider"}
        )
        self.assertEqual(rc, 0, f"invalid ENV import fail. stdout={stdout} stderr={stderr}")
        self.assertIn("OK", stdout)

    def test_module_import_with_openai_no_key_does_not_fail(self) -> None:
        """JETRAG_LLM_PROVIDER=openai + OPENAI_API_KEY 부재 → import 성공 (factory graceful fallback)."""
        snippet = textwrap.dedent("""
            try:
                import app.ingest.stages.extract  # noqa: F401
                print("OK")
            except Exception as exc:
                print(f"FAIL: import 실패 {exc!r}")
                raise SystemExit(1)
        """)
        # OPENAI_API_KEY 명시적으로 제거.
        env = {"JETRAG_LLM_PROVIDER": "openai"}
        # full_env 에서 OPENAI_API_KEY pop — _run_in_subprocess 가 os.environ 카피 후 update.
        # 직접 env override 가 우선이라 ""(빈 값) 으로 무력화.
        env["OPENAI_API_KEY"] = ""
        rc, stdout, stderr = _run_in_subprocess(snippet, env=env)
        self.assertEqual(rc, 0, f"openai+no-key import fail. stdout={stdout} stderr={stderr}")
        self.assertIn("OK", stdout)


class ExtractLazyInvocationTest(unittest.TestCase):
    """lazy 함수 (`_get_image_parser`) 가 호출 시점에 정확히 1회 hydrate 되는지."""

    def test_get_image_parser_lazy_caches(self) -> None:
        """`_get_image_parser` 는 첫 호출에만 factory 호출, 두 번째부터 캐시."""
        # 기존 module 인스턴스 그대로 — 함수 globals 일치 유지.
        from app.ingest.stages import extract
        # extract 가 `from ... import get_vision_captioner` 로 가져왔으므로
        # `app.ingest.stages.extract.get_vision_captioner` symbol 을 patch.
        with patch(
            "app.ingest.stages.extract.get_vision_captioner"
        ) as mock_factory:
            # cache_clear 로 lru_cache state 초기화.
            extract._get_image_parser.cache_clear()
            extract._get_image_parser()
            extract._get_image_parser()
            extract._get_image_parser()
        # 캐시되어 factory 는 1회만 호출.
        self.assertEqual(mock_factory.call_count, 1)
        # 다음 테스트 영향 차단 — cache 안에 mock 인스턴스가 남으면 회귀 가능.
        extract._get_image_parser.cache_clear()


if __name__ == "__main__":
    unittest.main()
