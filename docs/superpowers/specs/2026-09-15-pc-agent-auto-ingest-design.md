# PC 폴더 감시 에이전트 + 서버 근본 수정 — 설계 (2026-09-15)

> **상태**: 사용자 확정(2026-09-15). 다음 단계는 구현 플랜 작성.
> **자리**: 자동 수집 로드맵 ① → ③ → ② 중 **①**. 서버 수정 4건(S1~S4)은 ③아이폰 단축어·②안드로이드가 그대로 재사용한다.
> **상위 맥락**: `work-log/2026-09-14 목적 대비 점검 + 이관 마무리 착수.md` §1 — 목적 3요소 중 "자동 수집"과 "다건 브리핑"이 비어 있었다. 이 스펙은 자동 수집의 첫 조각이다.

---

## 0. 확정된 결정 (사용자)

| # | 질문 | 결정 |
|---|---|---|
| D1 | 문서 도착 지점 | 카톡, 이메일, 폰 스크린샷, PC 다운로드 폴더 |
| D2 | 폰 OS | 안드로이드·아이폰 둘 다 |
| D3 | 하위 프로젝트 순서 | ① PC 에이전트 → ③ 아이폰 단축어 → ② 안드로이드 앱 |
| D4 | PC OS | 본인은 맥, 대다수 유저는 윈도우 → **둘 다** |
| D5 | 필터 통과 문서 처리 | **전부 자동 업로드** (제외 규칙·대기함 없음) |
| D6 | 기존 계약 재활용 여부 | 재점검 후 **재활용하되 근본부터 수정** (§2) |
| D7 | 한도 모델 | **비용 기반 계량**(저장 용량 + 월 Vision 페이지)으로 교체. 문서 수·일일 상한 폐지 |
| D8 | 에이전트 인증 | **기기 토큰** (설정에서 발급, 해시 저장, 기기별 폐기) |
| D9 | 설치 시 기존 파일 | **최근 90일 백로그** (기본, 조정·해제 가능), 최신순·후순위 처리 |

---

## 1. 목표와 범위

**목표**: 사용자가 손대지 않아도 PC(맥·윈도우)의 지정 폴더에 도착한 문서·이미지가 Jet-Rag에 올라가 읽히고 요약·태그·검색 대상이 된다.

**범위 안**
- 서버 근본 수정 S1~S4 (§3)
- PC 에이전트 `agent/` — 폴더 감시, 로컬 원장, 90일 백로그, CLI, 자동 시작 등록, 맥·윈도우 바이너리
- 웹 `/settings` 의 "연결된 기기" 섹션과 계량 표시 변경

**범위 밖**
- ③ 아이폰 단축어, ② 안드로이드 앱 (별도 스펙)
- 삭제 동기화 — 로컬에서 지워도 Jet-Rag 문서는 남는다
- 파일 내용·이름 기반 제외 규칙, 트레이 UI, 코드서명·공증
- 다건 브리핑(기획서 US-10) — 이 뒤

---

## 2. 기존 업로드 계약 재점검 (2026-09-15 실측)

"기존 `POST /documents`를 그대로 쓰면 된다"는 판단을 냉정하게 재검토했다. 운영 API에 소유자 토큰으로 호출했고, 중복 파일과 매직바이트 실패 파일만 써서 DB·비용 부작용은 0이다.

| 측정 | 값 | 설계 반영 |
|---|---|---|
| Cloudflare가 `Python-urllib` UA 차단 | 403 (error 1010). `JetRag-Agent/0.1`·`curl`은 통과 | 에이전트 고유 UA 고정 |
| 중복 파일 3.2MB → `duplicated:true` | 3.0~4.4초 | 중복 판정에 파일 전체 전송이 필요 → **S2 precheck** |
| 51MB 본문(매직 실패 경로) | Edge 수용, 21초 | 50MB 상한 유지. Edge가 통째로 메모리에 올림 |
| 로컬 Deno CPU — 50MB multipart+sha256 | 317ms | Edge 2초 상한 안일 가능성 높음. **Edge 실측은 미검증** (§8) |
| 한도 | 일 30건, Free 10건 · Pro 200건 | 수동 업로드 전제 → **S4 계량 교체** |
| Storage RLS (마이그 020) | 본인 `user/<uid>/` INSERT 이미 허용 | 직접 업로드(안 C)가 정책 변경 없이 가능 — 지금은 안 씀 |
| 이메일 경로 `email_ingest.ts` | dedup·insert·enqueue를 **별도 재구현** | 저장 로직이 2벌 → **S1 통합** |
| `max_documents` 강제 | ~~이메일 경로만~~ → **정정(9/15 플랜 작성 중)**: `rate_limit.ts`의 `METRIC_DOCS` 분기가 웹 업로드에도 걸고 있었다. 7/19 기록의 갭은 이미 닫혀 있었음 | S4에서 용량 검사로 대체 |

**비교한 안**

| | A. 그대로 | **B. 재활용 + precheck + 공통화 (채택)** | C. Storage 직접 업로드 + 등록 API |
|---|---|---|---|
| 중복 낭비 | 전체 전송 후 판정 | 0 | 0 |
| 50MB·Edge CPU | 그대로 | 그대로 | 해제·이어올리기 가능 |
| 서버 변경 | 채널 값 1개 | precheck 1개 + 저장 로직 통합 | 등록 API + 서버가 Storage에서 헤더 읽어 매직 검사 |
| 위험 | 첫 동기화 재전송 | 낮음 | 2단계라 "올렸는데 미등록" 상태 처리 필요 |

C를 지금 안 고르는 이유: 다운로드·카톡 파일은 대부분 10MB 미만이라 50MB 상한과 CPU에 여유가 있다. C의 이점은 필요해질 때 B 위에 얹을 수 있고 precheck는 그때도 그대로 쓴다. "PC에서 텍스트 추출 후 전송"은 파서 6종을 클라이언트에 복제하게 되어 제외했다.

---

## 3. 전체 구조

```
PC (맥·윈도우)                                      서버 (Supabase)
┌───────────────────────────┐   Bearer 기기토큰    ┌────────────────────────────────┐
│ jetrag-agent (Deno 바이너리)  │ ─────────────────▶ │ api-documents                   │
│  · 폴더 감시 (Deno.watchFs)   │ POST /documents/precheck│  · 기기 토큰 인증 분기 (S3)       │
│  · 로컬 원장 (SQLite)         │ POST /documents        │  · persistDocument() 1벌 (S1)    │
│  · 백로그 90일 스캔            │ GET  /documents/{id}/status│  · 계량: 용량·월 Vision 페이지 (S4)│
│  · 우선순위 큐·백오프          │ GET  /documents/batch-status│ api-account                     │
│  · CLI + 로그 (트레이 없음)     │                      │  · /me/devices 발급·목록·폐기 (S3) │
└───────────────────────────┘                      └────────────────────────────────┘
                                                     웹 /settings: 연결된 기기 · 계량 표시
```

런타임: Deno 2.8 (로컬 확인 — `node:sqlite`, `Deno.watchFs`, `deno compile --target` 맥 arm64·x86_64·윈도우 x86_64 지원). 서버 함수와 같은 언어라 확장자 표·계약 타입을 나눠 쓴다.

---

## 4. 서버 상세

### S1 저장 공통화 — `supabase/functions/_shared/documents/persist.ts`

```ts
persistDocument({ client, bucket, userId, bytes, fileName, sourceChannel, ingestMode, title? })
  → { outcome: "created" | "duplicated" | "retried", docId, jobId: string | null }
```

순서는 현행 `upload.ts`와 같다: 확장자 표 → 크기(50MB) → 매직바이트 → sha256 → 3갈래 dedup(정상 중복 / 실패 흔적 재시도 / 신규) → Storage `user/<uid>/<sha256><ext>` → `documents` → `ingest_jobs` → 큐 `extract`.

달라지는 점
1. 확장자 표는 `ALLOWED_EXTENSIONS` 하나. `EMAIL_ALLOWED_EXTENSIONS`는 제거한다.
2. `UNIQUE(user_id, sha256)` 충돌(에이전트가 같은 파일을 두 번 보내는 경합)을 500이 아니라 `duplicated`로 돌려준다.
3. 저장 전에 S4 용량 한도를 검사한다 → 초과 시 402.
4. 호출자: `upload.ts`(multipart 파싱만 남김), `email_ingest.ts`(토큰·발신자 검사만 남김), 그리고 에이전트 경로(같은 `POST /documents`).
5. `source_channel`에 `pc-agent` 추가, `ios-shortcut`·`android-agent` 예약. DB CHECK 제약 확장은 마이그 031에 포함.

### S2 사전 확인 — `POST /documents/precheck`

| 항목 | 내용 |
|---|---|
| 인증 | 세션 또는 기기 토큰(`ingest` 스코프) |
| 요청 | `{ "hashes": ["<sha256 hex>", …] }` 1~500개. 초과 시 422 |
| 응답 | `{ "results": { "<sha256>": { "state": "existing" \| "failed" \| "new", "doc_id"?: string } } }` |
| 규칙 | 본인 문서(`deleted_at IS NULL`)만. `failed`는 `flags.failed`가 있는 행 = 재업로드 대상 |

### S3 기기 토큰

마이그 `030_device_tokens.sql`

```sql
CREATE TABLE device_tokens (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  name          text NOT NULL,
  token_hash    text NOT NULL UNIQUE,   -- sha256(token) hex
  token_prefix  text NOT NULL,          -- 표시용 앞 8자 (jrd_xxxx)
  scopes        text[] NOT NULL DEFAULT '{ingest}',
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_used_at  timestamptz,
  revoked_at    timestamptz
);
CREATE INDEX ON device_tokens (user_id);
-- RLS: 본인 행만 SELECT. INSERT/UPDATE/DELETE 는 service role(Edge) 만.
```

- 토큰 형식 `jrd_` + 43자(32바이트 base64url, 256비트). 발급 응답에 한 번만 실린다. 서버는 SHA-256 해시만 저장한다(고엔트로피라 느린 해시 불필요).
- 인증 분기(`_shared/current_user.ts`): `Authorization: Bearer jrd_…` → `token_hash` 조회 → `{ userId, authKind: "device", scopes, deviceId }`. `revoked_at IS NOT NULL` 이면 401.
- `ingest` 스코프 허용 라우트 **4개**: `POST /documents`, `POST /documents/precheck`, `GET /documents/{id}/status`, `GET /documents/batch-status`. 그 외는 403 `{detail:"device token scope"}`.
- 기기 관리는 세션 인증만: `POST /me/devices {name}` → `{ id, name, token, token_prefix, created_at }` (token 은 이 응답에만), `GET /me/devices` → 목록(토큰 없음), `DELETE /me/devices/{id}` → `revoked_at` 기록.
- `last_used_at`은 분당 1회만 갱신(요청마다 쓰지 않는다).
- 웹 `/settings` "연결된 기기": 이름·앞 8자·마지막 사용·폐기. 발급 직후 토큰 1회 표시 + 복사.

### S4 계량 교체 — 마이그 `031_metering_v2.sql`

| 축 | 측정 | 강제 지점 | 초과 시 |
|---|---|---|---|
| 저장 용량 | `SUM(documents.size_bytes) WHERE user_id=? AND deleted_at IS NULL` | `persistDocument` 저장 전 (`used + size > limit`) | 402 `{reason:"storage_limit", used, limit}` |
| 월 Vision 페이지 | 기존 `vision_usage_log` 당월 합계 | vision 단계 진입 시 | 잡 상태 `deferred_quota`(실패 아님). 월초 pg_cron이 재투입 |

- `plans`에 `storage_bytes_limit bigint`, `vision_pages_per_month int` 추가. `max_documents` 컬럼과 그 참조, 일일 문서 30건(`JETRAG_RATE_LIMIT_DOCS_PER_DAY`, `METRIC_DOCS`), `usage_counters`의 `docs` 지표는 제거. `answers_per_day`는 LLM 비용 기반이라 유지.
- 남용 방지: 사용자당 분당 업로드 60회(`usage_counters` 에 분 단위 period 로 기록).
- `ingest_jobs.status`에 `deferred_quota` 추가. 월초 cron(`0 0 1 * *` KST 기준 환산)이 `deferred_quota` 잡을 큐에 재투입.
- `/me/plan` 응답: `{ plan, storage: {used_bytes, limit_bytes}, vision_pages: {used, limit, period}, answers: {…기존} }`. 설정 화면 표시 변경.
- **플랜 값은 잠정** — Free 1GB · 100페이지, Pro 10GB · 1,000페이지. `plans` 행에만 적고 코드에 상수로 두지 않는다. Vision 단가 실측이 페이지당 $0.005~0.03이라 Pro 6,900원에 1,000페이지 전부 Vision이면 손실 구간. 출시 전 재산정 항목.

---

## 5. PC 에이전트 상세 — `agent/`

### 5.1 모듈

| 모듈 | 역할 | 의존 |
|---|---|---|
| `config` | `~/.jetrag/config.json` — `api_base`, `device_token`, `watch_dirs[]`, `backlog_days`(기본 90). 권한 0600(윈도우는 사용자 ACL) | 없음 |
| `ledger` | SQLite `~/.jetrag/ledger.sqlite`. 표 `files(sha256 pk, path, size, mtime, state, doc_id, job_id, attempts, last_error, updated_at)`, `events(ts, level, msg)` | `node:sqlite` |
| `watcher` | `Deno.watchFs(dirs, {recursive:true})` → 디바운스 → 완성 판정 → scheduler | 없음 |
| `scanner` | 백로그: `watch_dirs` 재귀 스캔 → mtime ≤ N일 → 최신순 | 없음 |
| `gate` | 확장자 11종(서버 표 import, 불가 시 복사본 + 동일성 테스트), 50MB, 임시 파일 패턴 `.crdownload` `.part` `.tmp` `.download` `~$*` `.DS_Store` | 서버 확장자 표 |
| `scheduler` | 큐 2개(실시간 우선, 백로그 후순위). 동시성 실시간 2 · 백로그 1(5단계 실측 후 조정). 백오프 | ledger, client |
| `client` | precheck / upload / batch-status. UA `JetRag-Agent/<ver> (<os>)`, Bearer 기기 토큰, 타임아웃 업로드 120s·그 외 15s | 없음 |
| `cli` | `init` `run` `install` `uninstall` `status` `logs` | 위 전부 |

### 5.2 파일 상태 기계

```
발견 → 대기 → precheck → existing → 완료(중복)
                        → new | failed → 업로드중
업로드중 → 202 created/retried → 등록됨 → batch-status → 처리완료 | 처리실패
       → 400/413/422           → 제외 (사유 저장, 재시도 없음)
       → 402                   → 한도대기 (6시간 간격 재시도)
       → 429/5xx/네트워크        → 대기 (지수 백오프 5s→5m, attempts+1)
```

- **완성 판정**: 이벤트 후 3초 간격 2회 크기·mtime 동일 시에만 진행.
- **정체성은 sha256**: 이름 변경·이동은 경로만 갱신, 재업로드 없음. 해시는 50MB 이하만 계산.
- **제외·실패는 조용히 사라지지 않는다**: 원장에 사유가 남고 `status`에서 보인다.
- **원장 손상**: 열기 실패 시 `.bak` 으로 재생성, 없으면 빈 원장. precheck 덕에 중복 업로드는 생기지 않는다.

### 5.3 백로그

1. 스캔 → N일 이내 → gate 통과분 집계.
2. `대상 137개, 412MB. 시작할까요? [y/N]` (`--yes` 생략).
3. 해시 → 200개 단위 precheck → `new`·`failed`만 후순위 큐.
4. 실시간 큐가 비어 있을 때만 진행. 중단해도 원장에서 이어감.

### 5.4 설치·상주

| OS | 자동 시작 | 기본 감시 폴더 |
|---|---|---|
| 맥 | `~/Library/LaunchAgents/com.jetrag.agent.plist` — RunAtLoad + KeepAlive | `~/Downloads`, 카톡 저장 폴더 |
| 윈도우 | 작업 스케줄러 "로그온 시" (`schtasks /create /sc onlogon`) | `%USERPROFILE%\Downloads`, 카톡 저장 폴더 |

카톡 기본 저장 경로는 문서값이 아니라 **양쪽 OS에서 실측**해 확정한다(7단계). 맥 launchd 프로세스의 `~/Downloads` 접근 권한 프롬프트(TCC)가 실제로 뜨는지·승인이 유지되는지 8단계에서 확인한다.

### 5.5 배포

CI에서 `deno compile --target` 3종 → GitHub Releases. 코드서명·공증 없음 → 맥 Gatekeeper·윈도우 SmartScreen 경고(우회 안내로 감). 외부 배포 전 서명은 별도 작업.

### 5.6 로그·관측

`~/.jetrag/agent.log` 회전(5MB×3). `status`: 상태별 개수, 최근 실패 5건과 사유, 마지막 서버 통신 시각. 서버는 `source_channel=pc-agent`와 `device_tokens.last_used_at`으로 기기·시각이 남는다.

---

## 6. 오류 처리 (서버)

| 상황 | 처리 |
|---|---|
| 같은 sha256 동시 업로드 | UNIQUE 충돌 → `duplicated` 응답 |
| 용량 한도 초과 | 402 + 사유. 파일 저장 안 함 |
| 월 Vision 페이지 초과 | `deferred_quota` 보류, 월초 재투입. 문서 상태 "한도 대기" |
| 기기 토큰 폐기·형식 오류 | 401. 실패는 로그만 |
| 기기 토큰으로 허용 외 라우트 | 403 + 스코프 사유 |
| precheck 500개 초과 | 422 |

---

## 7. 테스트

| 층 | 방법 |
|---|---|
| 서버 단위 | `persist.ts`: 3갈래 dedup, UNIQUE 충돌→duplicated, 용량 402. precheck 분류. 기기 토큰: 정상·폐기 401·스코프 403. 계량: 용량 합계, `deferred_quota` 보류·재투입. 이메일 경로가 persist 경유. 기존 `deno test` 246건에 추가 |
| 대조 하네스 | 의도적으로 원본과 달라지는 부분(UNIQUE 충돌, 한도)은 해당 `verify_*_parity.py` 갱신 또는 폐기. 착수 시 CI 14종 중 영향 범위 목록화 |
| 에이전트 단위 | 임시 폴더로 watcher·scanner·gate, 인메모리 SQLite로 ledger, `Deno.serve` 목 서버로 client·scheduler(429·5xx·402·네트워크 단절) |
| E2E | 본인 계정 테스트 기기 토큰 → 임시 폴더에 샘플 11종 투입 → 서버 문서 상태까지. 백로그 90일 시나리오 1회 |
| 실측 | 큐 처리량(재인제스트 배치, 시간당 건수), 맥 launchd TCC, 카톡 기본 폴더(맥·윈도우), Edge 50MB CPU 1회 |

---

## 8. 롤아웃 순서 (각 단계 = 커밋·검증·ship 단위)

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| 1 | S1 persist 통합 (동작 보존 리팩터) | 기존 테스트·대조 통과, 배포 후 웹·이메일 업로드 스모크 |
| 2 | S3 기기 토큰 — 마이그 030, 인증 분기, `/me/devices`, 설정 UI | 발급→업로드→폐기→401 E2E |
| 3 | S2 precheck | 단위 + 라이브 호출 |
| 4 | S4 계량 — 마이그 031, 상한 제거, Vision 보류, `/me/plan`·설정 | 402·deferred_quota 시나리오 통과 |
| 5 | 큐 처리량 실측 | 시간당 건수 숫자 → 백로그 동시성 기본값 |
| 6 | 에이전트 코어 (config·ledger·gate·client·scheduler) | 단위 테스트 + 큰 파일 1회 Edge CPU 실측 |
| 7 | 감시·백로그·CLI (`run` `status`) | E2E 샘플 11종 |
| 8 | 설치·배포 (launchd·schtasks, compile CI, Releases) | 맥 TCC 확인, 바이너리 3종 |
| 9 | 도그푸딩 — 본인 맥 다운로드 폴더 90일 백로그 | 건수·시간·실패 사유를 work-log에 숫자로 |

1~4가 서버 근본 수정이라 ③·②는 이 위에 바로 올라간다.

**미검증 가정 → 확인 시점**
- Edge 50MB CPU (로컬 317ms) → 6단계
- 큐 처리량 → 5단계
- 맥 launchd TCC 프롬프트 → 8단계
- 카톡 기본 저장 경로 → 7단계
- **윈도우 검증 환경** — 사용자는 맥만 쓴다. 실기기·VM이 없으면 8~9단계를 "맥 검증 완료, 윈도우 미검증"으로 명시하고 넘어간다
- 플랜 값(1GB/100p, 10GB/1,000p) → 출시 전 재산정

---

## 9. 참고

- 재점검 실측값은 §2 표가 원본이다. 측정 방법: 소유자 세션으로 `POST /documents`에 중복 파일(3.2MB pptx)과 매직 실패 파일(10·30·51MB)을 보내 wall time 을 쟀고, CPU 는 로컬 Deno 2.8 에서 multipart+sha256 을 5·20·50MB 로 쟀다.
- 업로드 계약 현행: `supabase/functions/_shared/documents/upload.ts`, 이메일: `_shared/ingest/email_ingest.ts`, Storage RLS: `api/migrations/020_storage_per_user_prefix.sql`, 플랜: `api/migrations/022_plans_subscriptions.sql`.
- Vision 단가 실측: 메모리 `jetrag_vision_cost_measured` (페이지당 $0.005~0.03).
- 보안 페르소나 합의(전체 스캔 금지, 지정 폴더만): 메모리 `jetrag_beta_feedback_auto_ingest`.

---

## 10. 구현 드리프트 (2026-09-15 서버 편 구현 후 정정)

서버 S1~S4 구현(플랜 Task 1~4, 커밋 `10b6cdf`…`3846c50`)에서 위 본문과 달라진 것. **이 절이 본문보다 우선**한다.

| 본문 | 실제 구현 | 이유 |
|---|---|---|
| S4 남용 방지 "`usage_counters` 에 분 단위 period" | 별도 표 `upload_burst` + RPC `increment_upload_burst` + 매시 `upload-burst-sweep` cron | `usage_counters.period_date` 가 DATE 라 분 단위 불가 |
| S4 월초 cron `0 0 1 * *` | **매일** `0 15 * * *`(00:00 KST) + KST 월 경계 가드 + `p_force` 인자 | 월초 하루만 실제로 풀고, 결제 훅에서 즉시 해제 가능 |
| S4 용량 검사 "저장 전" | dedup **뒤** (신규·재시도만) | 중복 파일은 용량을 안 쓰고, 한도 찬 사용자도 "있다"는 답을 들어야 원장이 정리됨 |
| S4 402 본문 `{reason, used, limit}` | + `detail` 문구, + `code:"storage_limit"` | 모든 4xx 가 `code` 를 실음(아래) |
| (없음) | 업로드 4xx 는 `code ∈ ext·empty·too_large·magic·storage_limit` 을 항상 실음. `channel`(DB CHECK 미적용)은 **503** — 재시도 대상 | 에이전트가 한국어 문구를 매칭하지 않게 |
| S3 403 `{detail:"device token scope"}` | `{detail:"기기 토큰의 권한 범위를 벗어난 요청입니다."}` | 한국어 통일 |
| S3 `/me/devices` POST 응답 | `{id,name,token,token_prefix,scopes,created_at,last_used_at,revoked_at}` (201). GET 은 `{devices:[…]}` 로 감쌈 | |
| S3 인덱스 `(user_id)` | `(user_id, created_at DESC)` | 목록 정렬 |
| S4 `/me/plan.vision_pages.period` | `period_start` (ISO) | |
| (없음) | `ingest_jobs.deferred_task JSONB` — 보류 시 태스크 페이로드 보존, 재투입 시 사용. `deferred_quota` 는 active 목록·웹("한도 대기 중")에 노출 | 회복 가능성 |
| (없음) | `GET /documents/precheck` 는 프록시에서 404(Edge 단독은 405) | 삽입 위치 |
| (없음) | CORS `ALLOW_METHODS` 에 DELETE — Python 원본과 의도적 이탈(원본에도 반영해 대조 유지) | 기기 폐기 버튼 |
| (없음) | 마이그 031 의 `documents` CHECK 는 `NOT VALID` + **별도 트랜잭션 VALIDATE**. `supabase db query --linked -f` 로 적용 | 락 회피 형식 |
| (없음) | 500 은 `text/plain "Internal Server Error"` (FastAPI 동등) — 에이전트 클라이언트는 content-type 을 보고 파싱 | |
| (없음) | 분당 상한 키는 **user_id** — 한 사용자의 모든 기기와 웹이 60건/분을 나눠 씀 | 에이전트 백로그 동시성 설계 입력 |
| 업로드 4xx 만 `code` | **401·403·429·422 도 `code` 를 싣는다** — 401 `auth` · 403 `scope`(기기 토큰 스코프 밖·세션 전용) · 403 `admin`(운영자 전용) · 429 `rate_limited` · 402 `answers_quota`(답변 한도) · 422 `form`. `detail` 문구는 그대로 두고 덧붙인 것이라 기존 프론트·에이전트 동작은 안 바뀜. 5xx 는 여전히 `text/plain` | iOS 단축어의 `Get Contents of URL` 은 **상태코드를 노출하지 않아** 본문 없이는 401/403/429 를 구분할 수 없다 (2026-09-16 단축어 설계 §5) |

**에이전트가 다뤄야 할 상태코드(계약)**: 202 `{doc_id, job_id|null, duplicated}` · 400 `code ext|empty|magic` · 413 `code too_large` · 402 `code storage_limit, used, limit` · 422 `code form`(폼 필드·채널 값 오류) · 503 `code channel`(재시도) · 429 `code rate_limited`(분당 60, 백오프) · 401 `code auth`(토큰 폐기·무효) · 403 `code scope|admin`(스코프 밖·운영자 전용) · 5xx text/plain(백오프).
