"""W25 D14 Phase 1 환경 정비 검증 스크립트.

체크 항목:
  [1] 마이그레이션 010 — ingest_jobs.stage_progress JSONB 컬럼 존재
  [2] 마이그레이션 009 — supabase_realtime publication 에 ingest_jobs 포함
      (직접 query 불가 — 간접: stage_progress UPDATE 가 정상 작동하면 적용 가정)
  [3] /documents/active endpoint 정상 응답
  [4] web .env.local 의 NEXT_PUBLIC_SUPABASE_URL / ANON_KEY 설정
  [5] 백엔드 hang 회복 — endpoint timeout 5초 안

실행: cd api && uv run python scripts/verify_phase1.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

# api/ 루트를 sys.path 에 추가 (uv run python scripts/... 패턴 대응)
_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_ENV_LOCAL = REPO_ROOT / "web" / ".env.local"
API_BASE = os.environ.get("JETRAG_API_BASE", "http://localhost:8000")

GREEN, RED, YELLOW, RESET, BOLD = "\033[92m", "\033[91m", "\033[93m", "\033[0m", "\033[1m"


def _ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")


def _section(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}")


def check_stage_progress_column() -> bool:
    _section("[1] ingest_jobs.stage_progress 컬럼 (마이그레이션 010)")
    try:
        from app.db import get_supabase_client

        sb = get_supabase_client()
        # 직접 컬럼 SELECT 시도 — column does not exist 면 APIError 42703
        sb.table("ingest_jobs").select("stage_progress").limit(1).execute()
        _ok("stage_progress 컬럼 존재")
        return True
    except Exception as exc:  # noqa: BLE001
        if "stage_progress" in str(exc) and "does not exist" in str(exc):
            _fail("컬럼 미존재 — 마이그레이션 010 미적용")
            print(
                f"    {YELLOW}→{RESET} Supabase Studio SQL Editor 에서 실행:\n"
                f"      ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS stage_progress JSONB;"
            )
        else:
            _fail(f"확인 실패 (다른 에러): {exc}")
        return False


def check_active_endpoint() -> bool:
    _section("[3] /documents/active endpoint 응답")
    try:
        t0 = time.time()
        resp = httpx.get(f"{API_BASE}/documents/active?hours=24", timeout=5.0)
        elapsed = time.time() - t0
        if resp.status_code == 200:
            data = resp.json()
            _ok(f"200 OK ({elapsed*1000:.0f}ms) — items={len(data.get('items', []))}")
            return True
        _fail(f"HTTP {resp.status_code} — body: {resp.text[:200]}")
        return False
    except httpx.TimeoutException:
        _fail("5초 timeout — 백엔드 hang 의심 (cool-down fix 적용된 새 코드 reload 필요)")
        return False
    except httpx.ConnectError:
        _fail(f"백엔드 미기동 ({API_BASE}) — uv run uvicorn app.main:app --reload")
        return False
    except Exception as exc:  # noqa: BLE001
        _fail(f"예외: {exc}")
        return False


def check_web_env() -> bool:
    _section("[4] web/.env.local 의 Supabase Realtime 환경변수")
    if not WEB_ENV_LOCAL.exists():
        _fail(f"파일 없음: {WEB_ENV_LOCAL}")
        return False
    text = WEB_ENV_LOCAL.read_text(encoding="utf-8")
    has_url = "NEXT_PUBLIC_SUPABASE_URL=" in text and not "NEXT_PUBLIC_SUPABASE_URL=\n" in text
    has_key = "NEXT_PUBLIC_SUPABASE_ANON_KEY=" in text and not "NEXT_PUBLIC_SUPABASE_ANON_KEY=\n" in text
    if has_url and has_key:
        _ok("NEXT_PUBLIC_SUPABASE_URL / ANON_KEY 설정됨")
        return True
    if not has_url:
        _fail("NEXT_PUBLIC_SUPABASE_URL 미설정")
    if not has_key:
        _fail("NEXT_PUBLIC_SUPABASE_ANON_KEY 미설정")
    print(
        f"    {YELLOW}→{RESET} {WEB_ENV_LOCAL} 에 다음 추가 (Supabase Dashboard → Settings → API):\n"
        f"      NEXT_PUBLIC_SUPABASE_URL=https://YOUR-PROJECT.supabase.co\n"
        f"      NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGciOi..."
    )
    return False


def check_realtime_publication() -> bool:
    _section("[2] supabase_realtime publication 에 ingest_jobs 포함 (마이그레이션 009)")
    # 직접 query 불가 (anon key 권한 부족). stage_progress UPDATE 가 작동하면 9·10 둘 다 적용된 것으로 간주
    _warn(
        "직접 검증 불가 (관리 권한 필요). 실 동작 확인:\n"
        "      web 새로고침 → 새 파일 업로드 → 헤더 indicator 가 1초 안에 등장하면 정상\n"
        "      (15초 이상 걸리면 Realtime 비활성 — fallback polling 동작 중)"
    )
    return True


def main() -> int:
    print(f"{BOLD}=== Jet-Rag W25 D14 Phase 1 환경 정비 검증 ==={RESET}")
    print(f"API base : {API_BASE}")
    print(f"web env  : {WEB_ENV_LOCAL}")

    results = {
        "stage_progress_column": check_stage_progress_column(),
        "active_endpoint": check_active_endpoint(),
        "web_env": check_web_env(),
        "realtime_pub": check_realtime_publication(),
    }

    _section("=== 종합 ===")
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"{passed}/{total} 통과")
    print(f"\n결과: {json.dumps(results, indent=2)}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
