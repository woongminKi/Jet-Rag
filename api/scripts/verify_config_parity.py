"""`config.ts` ↔ `app/config.py` 동등성 교차 검증.

`config_test.ts` 는 내가 해석한 계약을 고정할 뿐, **원본과 같다는 증명은 아니다.**
같은 ENV 를 Python 과 Deno 양쪽에 넣고 결과를 직접 대조한다.

이 방식이 필요한 이유: 코드가 읽는 ENV 45개 중 Railway 에 설정된 건 24개뿐이고,
나머지는 **기본값이 곧 현재 운영 동작**이다(2026-09-04 Step 0). 기본값이 하나만 어긋나도
이관 후 검색 결과나 인증 동작이 조용히 달라진다.

사용:
    api/.venv/bin/python api/scripts/verify_config_parity.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONFIG_TS = os.path.join(ROOT, "supabase", "functions", "_shared", "config.ts")

sys.path.insert(0, os.path.join(ROOT, "api"))

# Python 필드명 → TS 필드명. 양쪽에 다 있는 것만 비교한다.
FIELD_MAP = {
    "supabase_url": "supabaseUrl",
    "supabase_key": "supabaseAnonKey",
    "supabase_service_role_key": "supabaseServiceRoleKey",
    "supabase_storage_bucket": "supabaseStorageBucket",
    "gemini_api_key": "geminiApiKey",
    "hf_api_token": "hfApiToken",
    "default_user_id": "defaultUserId",
    "owner_user_id": "ownerUserId",
    "auth_enabled": "authEnabled",
    "supabase_jwt_secret": "supabaseJwtSecret",
    "supabase_jwt_algorithm": "supabaseJwtAlgorithm",
    "supabase_jwks_url": "supabaseJwksUrl",
    "stale_ingest_job_hours": "staleIngestJobHours",
    "chunk_upsert_batch_size": "chunkUpsertBatchSize",
    "quota_enforcement_enabled": "quotaEnforcementEnabled",
}

REQUIRED = {
    "SUPABASE_URL": "https://abc.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "svc-key",
}

# (이름, 추가 ENV) — 경계값 위주로. 기본값 시나리오가 가장 중요하다.
SCENARIOS: list[tuple[str, dict[str, str]]] = [
    ("기본값만", {}),
    ("전부 설정", {
        "SUPABASE_KEY": "anon-key",
        "SUPABASE_STORAGE_BUCKET": "docs",
        "GEMINI_API_KEY": "g",
        "HF_API_TOKEN": "h",
        "DEFAULT_USER_ID": "11111111-1111-1111-1111-111111111111",
        "OWNER_USER_ID": "22222222-2222-2222-2222-222222222222",
        "JETRAG_AUTH_ENABLED": "true",
        "SUPABASE_JWT_SECRET": "s3cr3t",
        "SUPABASE_JWT_ALGORITHM": "ES256",
        "SUPABASE_JWKS_URL": "https://abc.supabase.co/auth/v1/.well-known/jwks.json",
        "JETRAG_STALE_INGEST_JOB_HOURS": "48",
        "JETRAG_CHUNK_UPSERT_BATCH_SIZE": "200",
        "JETRAG_QUOTA_ENFORCEMENT_ENABLED": "false",
    }),
    ("불리언 on/off", {"JETRAG_AUTH_ENABLED": "on", "JETRAG_QUOTA_ENFORCEMENT_ENABLED": "off"}),
    ("불리언 인식 불가", {"JETRAG_AUTH_ENABLED": "garbage", "JETRAG_QUOTA_ENFORCEMENT_ENABLED": "garbage"}),
    ("불리언 빈 문자열", {"JETRAG_AUTH_ENABLED": "", "JETRAG_QUOTA_ENFORCEMENT_ENABLED": ""}),
    ("clamp 상한 초과", {"JETRAG_STALE_INGEST_JOB_HOURS": "999"}),
    ("clamp 하한 미만", {"JETRAG_STALE_INGEST_JOB_HOURS": "0", "JETRAG_CHUNK_UPSERT_BATCH_SIZE": "0"}),
    ("음수", {"JETRAG_STALE_INGEST_JOB_HOURS": "-5", "JETRAG_CHUNK_UPSERT_BATCH_SIZE": "-3"}),
    ("비숫자", {"JETRAG_STALE_INGEST_JOB_HOURS": "abc", "JETRAG_CHUNK_UPSERT_BATCH_SIZE": "12abc"}),
    ("배치 상한 없음", {"JETRAG_CHUNK_UPSERT_BATCH_SIZE": "50000"}),
    ("선택값 빈 문자열", {"OWNER_USER_ID": "", "SUPABASE_JWT_SECRET": "", "SUPABASE_JWKS_URL": ""}),
    # 접두어 붙은 오타 이름은 무시돼야 한다 — 플랜 초안이 이 이름을 썼다.
    ("DEFAULT_USER_ID 오타", {"JETRAG_DEFAULT_USER_ID": "33333333-3333-3333-3333-333333333333"}),
]

DENO_SNIPPET = f"""
import {{ loadSettings }} from "file://{CONFIG_TS}";
console.log(JSON.stringify(loadSettings()));
"""


def run_deno(env: dict[str, str]) -> dict:
    proc = subprocess.run(
        # `deno eval` 은 기본으로 모든 권한을 갖는다 — `--allow-env` 를 붙이면 인자 오류가 난다.
        ["deno", "eval", DENO_SNIPPET],
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), **env},
        timeout=120,
    )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:800]}")
    return json.loads(proc.stdout)


def run_python(env: dict[str, str]) -> dict:
    from app.config import get_settings

    # 원본은 @lru_cache 라 시나리오마다 비워야 한다.
    get_settings.cache_clear()
    saved = dict(os.environ)
    try:
        # 이전 시나리오 잔재를 지운다 — 안 지우면 "기본값" 시나리오가 오염된다.
        for k in list(os.environ):
            if k.startswith(("JETRAG_", "SUPABASE_", "DEFAULT_USER_ID", "OWNER_USER_ID", "GEMINI_", "HF_")):
                del os.environ[k]
        os.environ.update(env)
        s = get_settings()
        return {k: getattr(s, k) for k in FIELD_MAP}
    finally:
        os.environ.clear()
        os.environ.update(saved)
        get_settings.cache_clear()


def main() -> None:
    fails = 0
    print(f"{'시나리오':<22}{'비교 필드':>10}{'불일치':>8}  상세")
    print("-" * 78)
    for name, extra in SCENARIOS:
        env = {**REQUIRED, **extra}
        ts = run_deno(env)
        py = run_python(env)
        diffs = []
        for pk, tk in FIELD_MAP.items():
            pv, tv = py[pk], ts[tk]
            if pv != tv:
                diffs.append(f"{pk}: py={pv!r} ts={tv!r}")
        if diffs:
            fails += 1
        print(f"{name:<22}{len(FIELD_MAP):>10}{len(diffs):>8}  {'; '.join(diffs) if diffs else 'OK'}")

    print()
    print(f"시나리오 {len(SCENARIOS)}개 × 필드 {len(FIELD_MAP)}개 대조")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
