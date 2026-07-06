# 2026-07-06 세션 종합 — 수익화 W2 per-user rate limit + Gemini 유료 키 준비 (배포·검증 완료)

> 세션 시작 HEAD `9cf4170` → 마감 HEAD **`d71fc68`** (**push 완료** — Railway 백엔드 자동 재배포됨).
> 마이그 021 production 적용 완료 + smoke·카운터 row 실증까지 완료 → **W2 실질 종료**.
> 전체 테스트 **1348** (baseline 1332 + 신규 16), failures=4/errors=3/skipped=12 — 기존 flaky embed_cache/hwp 그대로, **회귀 0**.

---

## 0. 핵심 요약

수익화 sprint 의 **W2 = "익명 데모·로그인 사용자 모두에 일일 사용량 상한 → Gemini 유료 키 전환 후 비용 폭주·남용 방어 → 베타 업로드 안전 재개방"** 을 구현·배포했다.

**적용된 정책 (A안 — 사용자 확정):**
- **저장 = DB 카운터** (`usage_counters` 마이그 021) + 원자적 증가 RPC — 재시작·다중 워커 안전, W3-4 미터링 재사용
- **키 분리**: 로그인=`user_id` / 익명 데모=`ip:<X-Forwarded-For 첫 항목>` — 익명은 전부 OWNER uid 를 공유하므로 IP 로 분리해야 OWNER 본인과 안 뭉침
- **상한**: `JETRAG_RATE_LIMIT_ANSWERS_PER_DAY`(기본 50) / `JETRAG_RATE_LIMIT_DOCS_PER_DAY`(기본 30), **0/음수 = 무제한**(회복 토글)
- **초과 시 429** + 한국어 업그레이드 안내
- **적용 조건**: `auth_enabled=false`(로컬 dev)면 전면 skip — 기존 동작·테스트 100% 보존
- **increment-then-check 원자성**: RPC 가 UPSERT 로 +1 후 새 count 반환, `count > cap` 이면 429 (`count == cap` 은 통과)
- **fail-open**: RPC/DB 실패 시 통과(warning 로그) — DB blip 으로 정상 사용자 차단 회피

**실행 방식**: superpowers:subagent-driven-development — Task 별 fresh 서브에이전트 + 2단계 리뷰(spec → quality). 최종 홀리스틱 리뷰 verdict: **READY WITH NOTES** (blocker 0).

**Task 1 (마이그레이션)** 은 컨트롤러가 직접 작성, **Task 2~6** 은 서브에이전트, **Task 7** 은 사용자와 수동 롤아웃.

---

## 1. 완료된 작업

### 1.1 커밋 목록 (6건, push 완료)

| commit | 내용 |
|---|---|
| `d5557e0` | feat(migration-w2): usage_counters 테이블 + 원자적 증가 RPC (rate limit 기반) |
| `5c53901` | feat(config-w2): rate limit 일일 상한 설정 2개 (answers/docs, 무제한 토글) |
| `8acc46f` | feat(rate-limit-w2): usage_counters 기반 per-user 일일 rate limit 서비스 + 의존성 |
| `d4feac7` | feat(answer-w2): GET /answer 에 일일 답변 rate limit 게이트 (429) |
| `045ade2` | feat(documents-w2): 업로드 2 endpoint 에 일일 rate limit 게이트 (429) |
| `d71fc68` | docs(w2): Gemini 유료 키 전환 안내 + rate limit ENV 문서화 |

### 1.2 DB (Task 1 — `api/migrations/021_usage_counters.sql`)

- `usage_counters(user_key TEXT, metric TEXT, period_date DATE, count INTEGER, updated_at)` — PK `(user_key, metric, period_date)`. user_key 는 uuid/`ip:` 둘 다 수용하려 TEXT.
- `idx_usage_counters_period` — 날짜별 정리/조회 (W3-4 미터링 리포트용)
- RLS: **service_role 만** (anon/authenticated 정책 부재로 차단, 005 vision_usage_log 패턴)
- `increment_usage_counter(p_user_key TEXT, p_metric TEXT, p_period_date DATE) RETURNS INTEGER` — `SECURITY DEFINER`, UPSERT `ON CONFLICT DO UPDATE SET count = count + 1 RETURNING count` (동시성 안전). `REVOKE ALL FROM PUBLIC` + `GRANT EXECUTE ... TO service_role` (007 RPC 패턴)
- **멱등**: `IF NOT EXISTS` / `DROP POLICY IF EXISTS` / `CREATE OR REPLACE FUNCTION` — 재실행 안전
- production 적용: Supabase Studio(`mpmtydudhojpukuuadrd`) SQL Editor 에서 수동 Run 완료 (`Success. No rows returned`)

### 1.3 백엔드 (Task 2~5)

- **`api/app/config.py`**: `Settings` 에 `rate_limit_answers_per_day: int = 50` / `rate_limit_docs_per_day: int = 30` 필드 + `get_settings()` 에 `_parse_int("JETRAG_RATE_LIMIT_*_PER_DAY", …)` 파싱. 0/음수 clamp 안 함(무제한 토글, `vision_page_cap_per_doc` 패턴 계승)
- **`api/app/services/rate_limit.py`** (신규 — 핵심):
  - `_client_ip(request)` — XFF 첫 항목 우선, 없으면 `client.host`, 없으면 `"unknown"` (getattr 방어)
  - `build_user_key(current_user, request)` — authenticated→`user_id` / 익명→`ip:<주소>`
  - `_cap_for_metric(metric, settings)` — answers/docs cap 매핑, 알 수 없는 metric→0(무제한)
  - `enforce_rate_limit(metric, request, current_user, settings)` — auth_off/cap≤0 조기 return → RPC +1 → `count > cap` 이면 429. 예외는 fail-open
  - `check_rate_limit(metric)` — 라우터 레벨 의존성 팩토리
  - import 경로: `from app.db import get_supabase_client` (service_role, RLS 우회)
- **`GET /answer`** (`answer.py:432`): `dependencies=[Depends(check_rate_limit("answers"))]` 추가 (POST /answer* 피드백/eval 은 대상 아님)
- **`POST /documents`·`POST /documents/url`** (`documents.py`): `dependencies` 리스트에 `Depends(check_rate_limit("docs"))` 추가 — 기존 W1 `Depends(require_authenticated_user)` 보존. **순서 = auth → rate limit** (익명 쓰기는 401 이 먼저라 익명 카운터 오염 없음). reingest 라우트는 신규 생성 아님 → 제외(YAGNI)

### 1.4 문서 (Task 6 — 동작 변경 0)

- `_gemini_common.py`: `_MAX_ATTEMPTS` 주석에 유료 pay-as-you-go 전환 컨텍스트 추가 (`_MAX_ATTEMPTS = int(os.environ.get("JETRAG_GEMINI_RETRY", "1"))` 값 불변)
- `.env.example`: `GEMINI_API_KEY` 유료 권장 주석 + rate limit ENV 2개 (주석 처리)
- `README.md`: "현재 운영 모드" 섹션에 일일 rate limit bullet 추가

### 1.5 테스트

- `api/tests/test_config.py` — `RateLimitSettingsTest` (default/override/0=무제한)
- `api/tests/test_rate_limit.py` (신규) — 서비스 단위 10건 (client_ip 2 + build_user_key 2 + enforce 6). 외부 I/O 0, MagicMock RPC
- `api/tests/test_rate_limit_routes.py` (신규) — 429 게이트 통합 3건 (answer 1 + upload 2). 상한 초과가 핸들러 진입 전 short-circuit 하므로 검색/LLM I/O 0

### 1.6 리뷰 지적 (전부 non-blocking, 이연)

1. **[MEDIUM] usage_counters 무한 증가** — 익명 IP churn 으로 row 누적. 롤아웃 후 주기적 `DELETE ... WHERE period_date < CURRENT_DATE - 90` 크론 권장 (idx_usage_counters_period 가 지원). W3-4 미터링 때 자연 처리 가능
2. **[LOW] 알 수 없는 metric = 무제한** — 오타 시 게이트 조용히 disable. 리터럴만 넘기므로 저위험, warning 로그 추가로 개선 가능
3. **[LOW] fail-open 이 지속 DB 장애를 은폐** — blip 방어로는 옳으나 장기 장애 시 상한이 조용히 꺼짐(유료 Gemini 과금 중). `"rate_limit RPC 실패 — fail-open"` warning 로그 알림 권장
4. **[accept-as-designed] 익명 XFF 스푸핑** — 익명이 XFF 를 회전시켜 자기 익명 cap 우회 가능. 하지만 익명은 OWNER uid 공유라 IP 분리가 최선(대안은 데모 전체가 하나의 카운터로 뭉침). Railway 가 XFF 를 신뢰 가능하게 전달하는지 확인 필요

---

## 2. production 롤아웃 (Task 7 — 완료)

| 단계 | 상태 |
|---|---|
| 마이그 021 SQL Editor 적용 | ✅ (Success. No rows returned) |
| 검증 SQL (increment 2회 → count=2 → cleanup) | ✅ |
| `git push origin main` (9cf4170..d71fc68) | ✅ Railway 자동 재배포 |
| smoke: answer(anon) 200 / documents(anon POST) 401 / admin(anon) 403 | ✅ 회귀 0 |
| 카운터 row 실증 | ✅ `ip:121.131.211.110 / answers / count=1 / 2026-07-06` |

> **SQL Editor gotcha**: "Unable to find snippet with ID ..." 팝업은 SQL 오류가 아니라 깨진 스니펫 탭 참조 UI 버그. **New query 빈 탭**에서 붙여넣기·Run 하면 해결.

---

## 3. 남은 선택 항목 (지금 안 해도 rate limit 동작함)

1. **Gemini 유료 키 교체** — 아직 무료 tier 면 Railway `GEMINI_API_KEY` 교체 + Deploy. 유료 키 확보 후 진행
2. **카운터 정리 크론** (§1.6-1)
3. **fail-open 로그 알림** (§1.6-3)
4. **베타 유저 재초대** — 업로드가 로그인+rate limit 하에 안전 개방됨. `work-log/2026-05-28 베타테스터 안내문` 재발송
5. **rate limit 실동작 확인 (선택, 신중)** — 임시 `JETRAG_RATE_LIMIT_ANSWERS_PER_DAY=2` + Deploy → 익명 3회 호출 → 3번째 429 확인 → 원복. (현재는 카운터 증가만 실증, 429 실차단은 미검증)

---

## 4. 이후 로드맵 (수익화 sprint, spec `2026-07-05-monetization-sprint-design.md`)

| 주차 | 내용 | 상태 |
|---|---|---|
| W1 | 키 회전(스킵) + 멀티유저 쓰기 복원 | **완료·배포** |
| **W2** | Gemini 유료 키 준비 + per-user rate limit | **완료·배포** |
| W3-4 | 미터링 + 이메일 인제스트 (베타 피드백 1순위: 업로드 마찰 제거) | usage_counters 재사용 |
| W5-6 | 카카오페이 B2C 구독 | - |

---

## 5. 새 세션 진입 가이드

1. `git pull` — HEAD `d71fc68` 까지 push 됨
2. W2 동작 확인: 익명 `/answer` 호출 후 `SELECT * FROM usage_counters WHERE metric='answers' ORDER BY updated_at DESC LIMIT 5;` — `ip:` row 증가 확인
3. 테스트: `cd api && uv run python -m unittest discover tests` (baseline 1348, flaky 7)
4. 다음: 선택 항목(§3) 처리 또는 W3-4 플랜 (superpowers:writing-plans) — usage_counters 를 미터링/이메일 인제스트로 확장
5. 플랜 원본: `docs/superpowers/plans/2026-07-06-w2-gemini-paid-key-rate-limit.md`
