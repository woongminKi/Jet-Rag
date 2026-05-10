"""evals/uvicorn_watchdog.py 단위 테스트.

검증 범위
- check_health: 정상 / non-200 / URLError / unexpected exception
- pid_alive: 존재하는 PID (현재 프로세스) / 존재 안 함 PID
- find_uvicorn_pids: pgrep 호출 실패 시 빈 list

stdlib unittest + mock only.
"""

from __future__ import annotations

import os
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS_DIR))


class CheckHealthTest(unittest.TestCase):
    def test_ok_status_200_with_ok_body(self) -> None:
        from uvicorn_watchdog import check_health

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"status":"ok"}'
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        with patch("uvicorn_watchdog.urllib.request.urlopen", return_value=mock_resp):
            ok, reason = check_health("http://localhost:8000/health", 5)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_non_200_status_fails(self) -> None:
        from uvicorn_watchdog import check_health

        mock_resp = MagicMock()
        mock_resp.status = 503
        mock_resp.read.return_value = b'{"status":"down"}'
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        with patch("uvicorn_watchdog.urllib.request.urlopen", return_value=mock_resp):
            ok, reason = check_health("http://localhost:8000/health", 5)
        self.assertFalse(ok)
        self.assertIn("non-200", reason)

    def test_url_error_fails_gracefully(self) -> None:
        from uvicorn_watchdog import check_health

        with patch(
            "uvicorn_watchdog.urllib.request.urlopen",
            side_effect=URLError("Connection refused"),
        ):
            ok, reason = check_health("http://localhost:8000/health", 5)
        self.assertFalse(ok)
        self.assertIn("URLError", reason)

    def test_unexpected_body_fails(self) -> None:
        from uvicorn_watchdog import check_health

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'unexpected garbage'
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        with patch("uvicorn_watchdog.urllib.request.urlopen", return_value=mock_resp):
            ok, reason = check_health("http://localhost:8000/health", 5)
        self.assertFalse(ok)
        self.assertIn("unexpected body", reason)


class PidAliveTest(unittest.TestCase):
    def test_current_pid_alive(self) -> None:
        from uvicorn_watchdog import pid_alive

        self.assertTrue(pid_alive(os.getpid()))

    def test_nonexistent_pid_not_alive(self) -> None:
        from uvicorn_watchdog import pid_alive

        # PID 99999999 — 매우 큰 값, 일반적으로 미할당
        self.assertFalse(pid_alive(99999999))


class FindUvicornPidsTest(unittest.TestCase):
    def test_returns_empty_when_pgrep_fails(self) -> None:
        from uvicorn_watchdog import find_uvicorn_pids

        mock_result = MagicMock()
        mock_result.returncode = 1  # not found
        mock_result.stdout = ""
        with patch("uvicorn_watchdog.subprocess.run", return_value=mock_result):
            self.assertEqual(find_uvicorn_pids(), [])

    def test_parses_pgrep_output(self) -> None:
        from uvicorn_watchdog import find_uvicorn_pids

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345\n67890\n"
        with patch("uvicorn_watchdog.subprocess.run", return_value=mock_result):
            pids = find_uvicorn_pids()
        self.assertEqual(pids, [12345, 67890])

    def test_skips_non_digit_lines(self) -> None:
        from uvicorn_watchdog import find_uvicorn_pids

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345\nnonsense\n67890\n"
        with patch("uvicorn_watchdog.subprocess.run", return_value=mock_result):
            pids = find_uvicorn_pids()
        self.assertEqual(pids, [12345, 67890])


if __name__ == "__main__":
    unittest.main()
