# 2026-05-21 TODO — Phase 4 노출 API 키 **반드시** 회전

> ⚠️ **이 문서는 잊으면 안 되는 보안 작업이다.** Phase 4 (D1 ship 데이터 이관 + OWNER_USER_ID 등록) 진행 중 본 conversation 에 두 개의 고권한 API 키가 노출됐다. 작업이 종료되면 즉시 회전(rotate)해야 한다. 두 키 모두 작업 종료 후 더 이상 필요 없다.

---

## 0. 왜 회전해야 하는가

| 항목 | 노출된 키 | 위험 |
|---|---|---|
| Supabase **service_role JWT** | `eyJhbGciOiJIUzI1...AKu38` (HS256, exp 2092-08-13) | **모든 RLS bypass.** 모든 user 의 모든 데이터 read/write/delete. Storage 객체 임의 조작. admin REST API 전권. **이게 유출되면 production 즉시 게임 오버.** |
| Railway **account token** | `19e1ac78-b146-4534-a50e-863b66c50bc6` (`woongmin projects token`) | woongminki's Projects scope. 전 프로젝트의 ENV 변수 변경, redeploy, 서비스 중단 가능. Jet-Rag 외 다른 프로젝트(`slack-md-viewer`, `akgui-detector` 등) 도 영향. |

본 conversation 의 메시지 본문에 두 키가 평문 노출됐다. Claude Code 가 메모리에 저장하지 않더라도 대화 로그 / 백업 / 캐시 등 부수 채널에 남을 수 있어 회전 외에는 안전 보장 불가.

---

## 1. Supabase service_role key 회전

### 1a. 현재 키 무효화 + 신규 발급
- **위치**: <https://supabase.com/dashboard/project/mpmtydudhojpukuuadrd/settings/api>
- **JWT Settings** 섹션 → `Service Role Secret` 행 우측 ⋮ → **Generate new secret** 클릭
- ⚠️ 클릭 시 기존 키 즉시 무효화. backend (Railway) 가 `SUPABASE_SERVICE_ROLE_KEY` 를 ENV 로 들고 있으면 다음 1c 까지 backend 503/401 가능.

### 1b. 신규 키 복사 → 사용처 갱신
- 신규 key 한 번 노출됨 → **즉시 복사**.
- 사용처 1곳: **Railway backend ENV `SUPABASE_SERVICE_ROLE_KEY`**
  - 위치: <https://railway.com> → Jet-Rag 프로젝트 → backend service → Variables
  - `SUPABASE_SERVICE_ROLE_KEY` 값 → 신규 키로 교체 → 좌상단 보라색 **Deploy** 버튼 클릭 (TS-8 함정 주의)
- (선택) 로컬 개발 `.env` 파일에 있으면 동일 갱신 — 본 repo 의 `api/.env` 또는 환경변수.

### 1c. 검증
```bash
curl -s -o /dev/null -w "/search no-token: %{http_code}\n" "https://jetrag-api.woong-s.com/search?q=test"
curl -s -o /dev/null -w "/health: %{http_code}\n" "https://jetrag-api.woong-s.com/health"
# 기대: /search 401 (auth_enabled=true), /health 200.
# 만약 /health 가 503/500 이면 backend 가 신규 service role key 로 Supabase connect 실패 — Railway 로그 확인.
```
- 브라우저 본인 로그인 → 문서 12건 보임 → 정상.

### 1d. (선택) JWT Settings 자체 변경 — 이미 ECC 라 추가 회전 불필요
- 현재 `CURRENT=ECC(P-256), PREVIOUS=Legacy HS256(verify-only)`. service role key 발급은 ECC signing key 와 별개의 HS256 long-lived JWT. 1a 의 generate new secret 만으로 충분.

---

## 2. Railway account token 회전

### 2a. 현재 토큰 즉시 삭제
- **위치**: <https://railway.com/account/tokens>
- `woongmin projects token` 행 우측 **🗑 휴지통 아이콘** 클릭 → 확인 → 삭제.

### 2b. (선택) 새 토큰 발급 — 일회성 작업용
- 본 conversation 외에 Railway API 를 호출할 일이 다시 있으면 새로 발급. 한 번 작업 끝나면 즉시 삭제하는 패턴이 안전.

### 2c. 검증
- Railway dashboard 의 Tokens 페이지에서 `woongmin projects token` 사라졌는지 확인.

---

## 3. 사후 점검

### 3a. ENV 갱신 누락 검증
```bash
# Supabase service role key 가 일치하는지 backend log 에서 확인
# Railway dashboard → backend service → Deployments → 최신 → Logs
# 검색: "supabase" 또는 "401" 또는 "PGRST301"
```

### 3b. 노출된 키로 마지막 호출 시도 → 403/401 기대
```bash
OLD_SBKEY='eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1wbXR5ZHVkaG9qcHVrdXVhZHJkIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NjgzNzM4MCwiZXhwIjoyMDkyNDEzMzgwfQ.hHQ1Nbo9kD00bXyDVxTjt60DXfKAKZxaVnVaVhAKu38'
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://mpmtydudhojpukuuadrd.supabase.co/rest/v1/documents?limit=1" \
  -H "apikey: $OLD_SBKEY" -H "Authorization: Bearer $OLD_SBKEY"
# 기대: 401 (서비스 키 무효화됨)
```

### 3c. 본 문서 삭제 또는 archive
- 회전 완료 후 본 문서의 노출 키 fingerprint 도 함께 redact 권장. 또는 작업 완료 후 본 파일 `archive/` 이동.

---

## 4. 진행 체크리스트

```
□ 1a Supabase Service Role Secret → Generate new secret 클릭 (기존 무효화)
□ 1b 신규 키 → Railway backend Variables 의 SUPABASE_SERVICE_ROLE_KEY 갱신 + Deploy 클릭
□ 1c /search no-token 401 / /health 200 / 본인 로그인 → 문서 보임 검증
□ 2a Railway 'woongmin projects token' 삭제
□ 2b (선택) 새 토큰 발급 — 일회성 작업 시
□ 3a backend log 에 service role key 관련 401/PGRST 오류 없는지
□ 3b 노출된 OLD_SBKEY 로 호출 → 401 확인
□ 3c 본 문서 노출 키 fingerprint redact 또는 archive
```

---

## 5. 회전 직후 production 무중단 보장

| 단계 | risk | 완충 |
|---|---|---|
| 1a Generate new secret | 기존 key 즉시 무효화 — backend 가 일시적으로 401 (Supabase REST call 실패) | 1b Railway ENV 갱신까지 1~2분 내 완료 권장. 본인 단독 운영이라 사용자 영향 사실상 0. |
| 1b Railway Deploy 클릭 | 신규 deploy ~1~2분 대기 | 그 동안 본인이 사용 안 하면 됨. |
| 2a Railway token 삭제 | 본 conversation 의 추가 API 호출 즉시 차단 — Phase 5 이후 작업이 필요하면 새 토큰 발급 후 진행. | Phase 5 작업 종료 직전에 회전 권장. |

**권고 회전 시점**: Phase 5 (D2 deploy) 완전 종료 직후 즉시. Phase 5 진행 중에는 두 키가 다시 필요할 수 있어 회전 보류.

---

## 6. 핵심 한 줄

> **Phase 5 끝나는 즉시 본 문서 §4 체크리스트 정직하게 돌려라. 키 회전은 미루면 잊는다.**
