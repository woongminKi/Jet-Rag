"""uvicorn 좀비 모니터링 — /health timeout 또는 non-200 시 alert.

motivation
----------
2026-05-10 RAGAS n=30 측정 시 uvicorn (PID 18399/18401) 이 좀비화 (응답 0,
30s timeout) → 측정 시작 전 강제 재기동 (kill -9 + nohup 재시동) 필요.
재발 방지 — 주기적 /health 점검 + alert 제공.

사용
----
    # 백그라운드 watchdog (60s 마다 점검, stderr 로 alert)
    uv run python ../evals/uvicorn_watchdog.py --interval 60

    # 1회 즉시 점검 (CI 같은 환경)
    uv run python ../evals/uvicorn_watchdog.py --once

    # alert 시 자동 kill + 재기동 (위험, 명시 opt-in)
    uv run python ../evals/uvicorn_watchdog.py --interval 60 --auto-restart

설계 원칙
- read-only default — 단순 점검 + stderr alert. 재기동은 명시 opt-in.
- 외부 의존성 0 (stdlib urllib + subprocess + psutil 없음).
- 자율 진행 안전 — 자동 재기동은 사용자 명시 필요 (CLAUDE.md guard).
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.request
from urllib.error import URLError

_DEFAULT_HEALTH_URL = "http://localhost:8000/health"
_DEFAULT_INTERVAL = 60  # seconds
_DEFAULT_TIMEOUT = 5    # seconds


def check_health(url: str, timeout: int) -> tuple[bool, str]:
    """/health endpoint 점검. (ok, reason) 반환."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            status = resp.status
            if status != 200:
                return False, f"non-200 status={status}"
            body = resp.read(200).decode("utf-8", errors="replace").strip()
            if not body or "ok" not in body.lower():
                return False, f"unexpected body: {body!r}"
            return True, "ok"
    except URLError as exc:
        return False, f"URLError: {exc!r}"
    except Exception as exc:  # noqa: BLE001
        return False, f"unexpected: {exc!r}"


def find_uvicorn_pids() -> list[int]:
    """`pgrep -f uvicorn` — uvicorn 프로세스 PID list (best-effort)."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "uvicorn app.main:app"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        return [int(p) for p in result.stdout.strip().split("\n") if p.strip().isdigit()]
    except Exception:  # noqa: BLE001
        return []


def kill_uvicorn(pids: list[int], force: bool = False) -> None:
    """SIGTERM (또는 force 시 SIGKILL) — 가능한 친절히."""
    sig = signal.SIGKILL if force else signal.SIGTERM
    for pid in pids:
        try:
            os.kill(pid, sig)
            print(f"[watchdog] kill -{sig} {pid}", file=sys.stderr)
        except ProcessLookupError:
            pass
        except Exception as exc:  # noqa: BLE001
            print(f"[watchdog] kill {pid} 실패: {exc}", file=sys.stderr)


def report(ok: bool, reason: str, *, ts: float) -> None:
    """1회 점검 결과 stderr 출력."""
    icon = "✅" if ok else "⚠"
    ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    print(f"[{ts_str}] watchdog {icon} {reason}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="uvicorn /health watchdog")
    p.add_argument("--url", default=_DEFAULT_HEALTH_URL, help="health URL")
    p.add_argument(
        "--timeout",
        type=int,
        default=_DEFAULT_TIMEOUT,
        help="HTTP timeout (s, default 5)",
    )
    p.add_argument(
        "--interval",
        type=int,
        default=_DEFAULT_INTERVAL,
        help="점검 주기 (s, default 60)",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="1 회 점검 후 종료 (CI 모드)",
    )
    p.add_argument(
        "--auto-restart",
        action="store_true",
        help="alert 시 자동 SIGTERM (5s 후 SIGKILL). 위험 — 명시 opt-in.",
    )
    p.add_argument(
        "--max-failures",
        type=int,
        default=3,
        help="연속 fail N 회 시 alert / restart (default 3)",
    )
    args = p.parse_args(argv)

    consecutive_fails = 0

    while True:
        ok, reason = check_health(args.url, args.timeout)
        report(ok, reason, ts=time.time())
        if ok:
            consecutive_fails = 0
        else:
            consecutive_fails += 1
            if consecutive_fails >= args.max_failures:
                print(
                    f"[watchdog] ⚠ 연속 fail {consecutive_fails} → 좀비 의심",
                    file=sys.stderr,
                    flush=True,
                )
                if args.auto_restart:
                    pids = find_uvicorn_pids()
                    print(
                        f"[watchdog] auto-restart: kill {pids}",
                        file=sys.stderr,
                        flush=True,
                    )
                    kill_uvicorn(pids, force=False)
                    time.sleep(5)
                    # 여전히 살아있으면 force kill
                    survivors = [p for p in pids if pid_alive(p)]
                    if survivors:
                        kill_uvicorn(survivors, force=True)
                    print(
                        "[watchdog] uvicorn killed — 사용자가 수동으로 재기동 필요",
                        file=sys.stderr,
                        flush=True,
                    )
                consecutive_fails = 0  # alert 발화 후 리셋
        if args.once:
            return 0 if ok else 1
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("[watchdog] interrupted", file=sys.stderr)
            return 0


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but we can't signal
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    sys.exit(main())
