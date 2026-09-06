# 2026-09-07 — RAGAS 평가 폐기, Phase 2(읽기 경로) 종료

> **범위**: Edge 로 옮길 수 없던 4 라우트(`ragas` 의존)를 **폐기**해 Phase 6 의 차단 요인을
> 없애고, Phase 2 를 닫기까지.
> **다음 세션 재진입**: **중간 산출물 저장 설계** → `extract` 핸들러. 그 다음 마이그 027
> (pg_cron 드레인 + 보관 개수 래퍼). 026 적용·워커 골격은 끝났다(§13, §14).

## 0. 한눈에 보기

| 항목 | 상태 |
|---|---|
| RAGAS 평가 엔드포인트 4개 제거 | ✅ (dd89d97) |
| 프론트 카드 2개 + API 함수 4개 + 안내 문구 제거 | ✅ |
| `ragas_eval.py` 서비스·의존성 | ⬜ **남김** (evals 오프라인 도구가 씀) |
| 내가 남긴 깨진 테스트 1건 수정 | ✅ |
| **Phase 2 (읽기 경로)** | ✅ **종료** |
| Phase 3 선행 — 인제스트 샌드박스 하네스 | ✅ (111d05f) |
| Phase 3 선행 — 현행 인제스트 기준선 3 포맷 | ✅ (0f365b8) |
| Phase 3 선행 — vision 래스터화 CPU 실측 | ✅ (ca6dae6) |
| Phase 3 — 마이그 026 (pgmq 큐 + public 래퍼) | ✅ **운영 적용** (13ed304) |
| Phase 3 — `ingest-worker` 드레인 골격 | ✅ 배포 (d3c74a6) — 핸들러는 아직 없음 |

## 1. 왜 폐기했나

`POST /answer/eval-ragas` 와 `POST /search/eval-precision` 은 `ragas` +
`langchain-google-genai` + `datasets` 를 쓴다. **전부 Python 전용**이라 Edge 로 옮길 수
없고, GET(캐시 조회)만 옮기려 해도 POST 와 경로가 같아 프록시 규칙으로 못 가른다.
4 라우트가 통째로 Railway 에 묶여 **Phase 6(Railway 제거)이 닫히지 않는 상태**였다.

선택지 A~D 를 놓고 사용자가 **A(폐기)** 를 골랐다. 근거는 실측이다:

| 항목 | 값 |
|---|---|
| `answer_ragas_evals` 행 수 | 4 |
| 최근 생성 | **2026-05-05** — 4개월간 신규 0 |
| 프론트 | 카드 2개가 살아 있었으나 그 기간 아무도 쓰지 않았다 |

## 2. 범위를 넓히지 않았다

지우기 전에 의존 관계를 봤더니 **`evals/` 오프라인 스크립트가 `api/.venv` 로
`app.services.ragas_eval` 을 직접 import** 하고 있었다
(`run_s1_d5_baseline.py:78`, `run_ragas_regression.py:490`).

| 대상 | 처리 |
|---|---|
| 라우트 4개 + 딸린 모델·헬퍼 (`answer.py` 356줄) | 제거 |
| 프론트 카드 2개·API 함수 4개·응답 타입 2개 | 제거 |
| 안내 문구 "…답변 + 출처 인용 + **RAGAS 정량 평가**" | 뒷부분 제거 |
| 라우터 대상 테스트 2개 클래스 | 제거 |
| `app/services/ragas_eval.py` | **남김** — evals 가 쓴다 |
| `pyproject.toml` 의 ragas·datasets·langchain | **남김** — 같은 이유 |
| `RagasEvalProviderEnvTest` (서비스 테스트) | 남김 |
| `answer_ragas_evals` 테이블·데이터 4행 | 남김 (읽는 코드만 없앴다) |

의존성까지 지웠으면 평가 도구가 통째로 깨졌을 것이다. 이들은 로컬 실행이라
**Railway 제거와 무관**하다.

## 3. 곁다리로 고친 것 — 내가 남긴 깨진 테스트

`test_vision_metrics.test_db_path_aggregates` 가 어제까지 통과하다 오늘 실패했다.
샘플에 `2026-09-06T05:00:00+00:00` 같은 **고정 날짜**를 박았는데 집계 창이 "오늘(KST)"
이라, 날짜가 바뀌자 전부 창 밖으로 나가 0 건이 됐다.

`/admin` 검증기에서 **같은 실수를 이미 한 번 고쳤으면서**(합성 행을 상대 날짜로) 이건 그
전에 만든 거라 남아 있었다. KST 자정 기준 상대 시각으로 바꿨다.

> 교훈: 시간 창을 쓰는 검사에 고정 날짜를 박으면 **만든 날만 통과**한다.

## 4. 판정기 오류 1건 (또)

Phase 2 커버리지를 재려고 `routes.js` 를 정규식으로 긁었더니 `/documents`·`/payments` 가
"이미 Edge" 로 나왔다. **주석 처리된 규칙까지 잡은 것**이다.
숫자가 이상해서 원본을 열어 알아챘고, 실제 `resolveTarget()` 을 부르는 방식으로 다시 쟀다.

> `k_rrf` 때(0 건 = 실행 실패)와 같은 계열이다 — **판정이 이상하면 자[尺]를 먼저 의심한다.**

## 5. 검증

| 항목 | 결과 |
|---|---|
| 라우트 전수 | 38 → **34개** (fixture 갱신) |
| 프록시 가드 테스트 | 19건 통과 |
| `tsc --noEmit` · `eslint` · `next build` | 통과 |
| Python | 1,465건, 실패 4건 — 전부 기존 `test_embed_cache`. **HEAD 에서도 동일** |
| 운영 HTTP (Railway ↔ 프록시) | 16건 전건 일치 |

**운영 확인** — 제거된 4 라우트가 전부 404, 남은 경로 무회귀:

```
GET/POST /answer/eval-ragas        404/404
GET/POST /search/eval-precision    404/404
GET  /answer (answer 텍스트 제외)    200/200 일치
POST /answer/feedback              401·422 일치
/health /stats /search /me/plan /admin/* /documents   전건 일치
```

**프론트 배포 확인** — 한 방향 신호("없음")만으로는 배포 전과 구분이 안 되므로
**두 방향**을 봤다. HTML + 청크 14개(1,124,586자) 검사:

| 검사 | 결과 |
|---|---|
| 새 문구 `출처 인용` 이 **있다** | 예 |
| 옛 문구 `RAGAS 정량 평가` 가 **없다** | 예 |
| `eval-ragas` · `eval-precision` 참조 없다 | 예 |
| (대조군) `/search` · `answer/feedback` 은 남아 있다 | 예 |

## 6. Phase 2 종료 — 현재 이관 상태

**Edge 15 라우트** — `/auth/me` `/health` `/search` `/stats` `/stats/trend`
`/me/*`(4) `/admin/queries/stats` `/admin/feedback/stats` `/admin/subscriptions`(GET·POST)
`/answer` `/answer/feedback`

**Railway 잔존 19** = FastAPI 기본 5(`/` `/docs` `/redoc` `/openapi.json`
`/docs/oauth2-redirect`) + **업무 14**:

| 대상 | 개수 | Phase |
|---|---|---|
| `/documents/*` | 9 | 3 |
| `/payments/subscribe/*` | 3 | 4 |
| `/billing/run` | 1 | 4 |
| `/ingest/email` | 1 | 5 |

**읽기 경로는 전부 넘어갔다.** 남은 건 전부 쓰기이거나 웹훅이다.

## 7. 커밋 이력

| 해시 | 메시지 |
|---|---|
| `dd89d97` | feat: RAGAS 평가 엔드포인트·UI 폐기 — Railway 제거의 마지막 차단 요인 해소 |

## 8. 남은 작업 · 차단 요인

**Phase 3~6**

- Phase 3 — `/documents/*` 9 라우트. **업로드·삭제라 되돌리기가 어렵다.**
  파일 업로드(multipart)와 Storage 연동이 새 요소다.
- Phase 4 — `/payments/subscribe/*` 3 + `/billing/run`. 결제라 실패 대조가 까다롭다.
- Phase 5 — `/ingest/email` 웹훅.
- Phase 6 — Railway 종료 + 프록시 제거(도메인을 Supabase 로 직결).
  **차단 요인은 이제 없다.**

**이관 후로 미룬 원본 버그 2건** (변동 없음)

1. MMR 이 도달 불가능 — `search.py:1286` 변수 가림. 실측 `mmr.rerank` 호출 0회.
2. `9999-12-31` / `9999년 12월` 질의가 500 — `try` 밖 OverflowError.

둘 다 Railway·Edge 를 같이 고치고 골든셋을 다시 재야 한다.

**기타**

- `usage_counters` 익명 상한 50/일. `/answer` HTTP 대조가 이걸 소비한다.
- 기존 실패 `test_embed_cache` 4건 — 전체 실행에서만 실패, 단독은 통과. 원인 미조사.
- 익명 rate limit 키가 프록시 뒤에서 Cloudflare PoP IP 로 뭉친다(2026-09-06 §3-E).
  이관과 무관한 기존 사안. 고치려면 Worker 가 `CF-Connecting-IP` 를 넘겨야 한다.

## 9. 다음 후보

| | 후보 | 근거 |
|---|---|---|
| **A** | Phase 3 — `/documents/*` 착수 | 남은 것 중 가장 크고(9 라우트) 뒤 Phase 가 여기에 의존하지 않는다. 다만 **업로드·삭제라 대조 설계가 먼저** 필요하다 |
| B | 원본 버그 2건 선수정 | `9999-12-31` 500 이 아직 사용자에게 노출된다. 골든셋 재측정이 필요해 한 세션을 쓴다 |
| C | Phase 4 — `/payments` + `/billing` | 결제. 카카오페이 심사 상태에 따라 손대기 곤란할 수 있다 |

**권고: A** — 다만 `/documents` 는 **쓰기가 대부분이라 지금까지의 "요청 모양만 대조"
방식으로는 부족하다.** 착수 전에 ① 업로드 대조를 어떻게 할지(테스트 문서? 실제 업로드 후
삭제?) ② Storage 이관 범위를 정해야 한다. 그 설계를 먼저 하고 구현에 들어가는 게 낫다.


## 10. Phase 3 선행 작업 — 인제스트 샌드박스 격리 하네스

Phase 3 착수 전에 **가장 우선 문제**를 먼저 풀었다.

> 문제: Phase 3 은 인제스트 *재작성*(~5,600줄)이고 완료 조건이 "결과가 현행과 동등"인데,
> 인제스트는 **부수효과가 본질**이라 지금까지의 "요청 모양만 대조"가 통하지 않는다.
> 게다가 Supabase 프로젝트가 하나뿐이라 대조가 곧 운영 DB 쓰기가 된다.

### 세 방안 비교

| | 방안 | 대가 |
|---|---|---|
| 1 | 별도 스테이징 프로젝트 | 요금, 마이그·확장·시크릿 재현, **설정이 조금만 달라도 대조가 신뢰를 잃는다** |
| **2** | **샌드박스 `user_id` + 정리 하네스** | 운영과 동일 환경, 비용 0. **정리가 필수** |
| 3 | 격리 없이 모양만 대조 | 동등성 판정 불가 |

로컬 Postgres(Docker 미설치)·트랜잭션 롤백(PostgREST 불가)·shadow 실행(2 위에서 가능)은
검토 후 탈락. **2 를 골랐다.**

### 실측 근거

| 사실 | 확인 |
|---|---|
| dedup 이 `user_id` 단위 | `documents.py:474` |
| Storage 가 `user/<uid>/` prefix | 마이그 020 |
| `documents.user_id` 에 `auth.users` FK 없음 | `001_init.sql:20` + insert 실측 → **계정 불필요** |
| `ingest_jobs`·`vision_usage_log` 는 사용자로 안 걸러짐 | `stats/sources.ts:40` → **정리가 필수** |

### 음성 대조가 하네스 결함 2건을 잡았다

1. **고아 행을 0 으로 세고 있었다.** 정리 검증이 `collect()` 재호출이었는데 그건
   `documents` 에서 출발해 자식을 찾는다 — `documents` 를 지우면 남은 자식을 **0 으로 보고**.
   → 삭제 전 ID 를 보관해 **독립 경로로 재조회**.
2. **`vision_usage_log` 만 `ON DELETE SET NULL`** (나머지는 CASCADE). documents 를 지우면
   doc_id 가 NULL 이 되어 **doc_id 로는 영원히 못 찾는다** — 운영에 그렇게 고아가 된 행이
   **이미 1,929 개**. → `call_id` 기반으로 전환.

②의 효과를 직접 쟀다: 옛 방식으로 돌리면 하네스가 `vision_usage_log: 0` 이라고
**보고하는데 DB 에는 고아가 2 개 남아 있었다.** 고친 하네스는 3 건으로 잡는다.

### 사용법

```
api/scripts/ingest_sandbox.py guard              # 보호 UUID 차단 확인
api/scripts/ingest_sandbox.py status             # 흔적 + 운영 총계
api/scripts/ingest_sandbox.py clean --apply      # 정리 (기본은 dry-run)
api/scripts/ingest_sandbox.py selftest --apply   # 심고 → 세고 → 지우고 → 0 확인
```

`--slot` 으로 여러 샌드박스를 쓴다(현행/신규 병렬 대조용).

### 남은 주의

- `chunks`/`ingest_jobs`/`ingest_logs` 삭제 누락이 음성 대조에서 0 건인 것은
  **DB CASCADE 가 대신 지우기 때문**이지 검사기 약점이 아니다(마이그 001 확인).
- 하네스는 **`documents.user_id` 를 출발점**으로 삼는다. 인제스트가 그 밖에 흔적을 남기는
  경로가 생기면 수집기에 추가해야 한다.


## 11. Phase 3 선행 (2) — 현행 인제스트 기준선

샌드박스 위에서 **운영을 건드리지 않고** 현행이 무엇을 만드는지 고정했다.
`api/scripts/ingest_baseline.py`.

### 원본 핸들러를 그대로 태운다

`run_full_ingest` 가 아니라 **`upload_document`** 를 부른다 — dedup·검증·행 생성까지
원본 경로를 지나가야 기준선이 의미가 있다. `BackgroundTasks` 는 수동 실행.

### 결정적/비결정적을 나눴고, 그 분류를 검증했다

| 성격 | 단계 | 기준선에 담는 것 |
|---|---|---|
| 결정적 | extract · chunk · chunk_filter · dedup | 청크별 **sha256 + 길이 + 위치**, 개수, stages |
| 비결정적 | embed · doc_embed · tag_summarize · vision | **존재·개수·차원**만 |

`law_sample1.hwp` 를 두 번 인제스트 → 결정적 필드 차이 **0 건**, digest 동일.
**이 검증이 없었으면 기준선 자체가 무의미했을 수 있다.**

### 실측

| 파일 | 청크 | digest | 응답 / BG | vision |
|---|---|---|---|---|
| `law_sample1.hwp` (49KB) | 11 | `942f1b2e98ae666e` | 117ms / 6.2s | 0 |
| `직제_규정.hwpx` (78KB) | 171 | `2f388bd6a08310dd` | 145ms / 35.5s | 0 |
| `law_sample2.pdf` (169KB) | 9 | `be79531d0f13653e` | 96ms / 34.3s | 2/2 |

세 번 다 정리 완료 + 운영 총계 불변. 기준선 JSON 3 개 전부 **본문 유출 0 건**(해시만).

### 계획 문서와 다른 점 — 9 단계이고 순서도 다르다

```
extract → chunk → chunk_filter → content_gate → tag_summarize → load → embed → doc_embed → dedup
```

계획서는 "8-stage" 라고 쓰지만 실제로는 9 개다. `content_gate` 가 `chunk_filter` 뒤,
**`load` 가 `embed` 앞**이다. 재작성 시 이 순서를 따라야 한다.

### `--compare` — Phase 3 대조용

신규 구현 결과를 기준선과 대조한다. 결정적 부분은 완전 일치를 요구하고 비결정적은
개수·차원만 본다.

첫 검증에서 **버그가 나왔다**: `zip()` 이 짧은 쪽에 맞춰 도는 탓에 한쪽 청크가 0 개일 때
"다른 청크 **0 개**" 라는 말이 안 되는 보고를 냈다. 길이를 먼저 비교하도록 고쳤다.

그 실행에서 인제스트가 중간에 죽었는데(청크 0) **재현되지 않아 원인은 확정하지 못했다.**
다만 하네스는 초록으로 넘기지 않고 FAIL 8 로 잡았다. 실패를 더 빨리 짚도록
`job_status != completed` 경고를 추가했다. 재실행은 **대조 FAIL 0**.

### 남은 미검증 — CPU

BG 6~35 초는 **wall clock** 이다. Edge 의 진짜 제약인 **CPU 2 초**는 아직 안 쟀다.
pgmq 분할 단위(1 페이지? 1 청크배치?)를 정하려면 Phase 0 의 CPU 계측 하네스로 재야 한다.
큰 문서(`sample-report.pdf` 9MB)도 아직 안 돌렸다.


## 12. Phase 3 선행 (3) — vision 래스터화 CPU

### 먼저 정정

§9 에서 "CPU 를 안 쟀다"고 썼는데 **틀렸다.** Phase 0 이 파싱 CPU 는 이미 다 쟀다:

| 대상 | CPU |
|---|---|
| HWP (`@rhwp/core`) | 총 72ms |
| HWPX / HWPML | 1.5~27ms |
| PDF 텍스트+span (`mupdf` 1.27.0) | **페이지당 최대 100.8ms** |
| DOCX / PPTX | 10~72ms |
| 메모리 | 상한 240MB, PDF 경로 24MB |

전부 2s 예산 안이고 판정표까지 있다(7de38cf). **다시 잴 필요가 없었다.**

### 그런데 안 잰 구간이 하나 있었다

Phase 0 S2 는 **텍스트+span 추출만** 쟀다. 현행은 vision 대상 페이지를
`page.get_pixmap(dpi=150)` → `pix.tobytes("png")` 로 **이미지로 굽는다**
(`extract.py:440`, `:685`). 래스터화는 CPU 집약적인데 재 본 적이 없었다.

`spike` 하네스에 `kind=pdf-render` 를 추가했다. **API 를 짐작하지 않는다** —
`methodsOf()` 로 실제 메서드를 확인하고, 없으면 `rendered:false` 로 드러나게 했다.

### 실측 — 4 개 문서 8 페이지 (150dpi, 약 1240×1755)

| 문서 | p | 렌더 | PNG | 총 CPU | PNG 크기 |
|---|---|---|---|---|---|
| law_sample2 | 0 / 1 | 106 / 59ms | 145 / 121ms | 259 / 188ms | 173 / 44KB |
| law sample3 | 0 / 1 | 93 / 84ms | 160 / 147ms | 260 / 238ms | 368 / 215KB |
| 데이터센터 안내서 | 0 / 5 | 68 / **254**ms | 162 / 161ms | 239 / **431ms** | 140 / 857KB |
| sample-report(9MB) | 0 / 5 | **213** / 72ms | 173 / 103ms | **443** / 234ms | 529 / 10KB |

### 짐작으로는 몰랐을 것 — **PNG 인코딩이 렌더만큼 비싸다**

103~173ms 로, 페이지에 따라 래스터화보다 더 든다. 래스터화만 보고 예산을 짰으면
**2 배 틀렸을 것**이다.

### 분할 단위 결론

| 경로 | 페이지당 CPU | 2s 예산 | 안전마진 적용 |
|---|---|---|---|
| 텍스트만 | ~100.8ms | ~19p | 10p |
| **vision 렌더 포함** | **최악 443ms** | 4.5p | **1~2p** |

계획서의 "작업 단위 = 1 페이지 추출" 이 실측으로 뒷받침됐다.

### 남은 변동성

같은 페이지 재측정에서 371.6ms → 258.8ms 로 흔들렸다(콜드/워ם 추정). 표본이 적어
**최악값 기준으로 설계**한다. 분포를 좁히는 건 큐 구현 후에 해도 늦지 않다.


## 13. Phase 3 — 마이그 026 운영 적용 (pgmq 큐)

사용자 승인 후 **직접 적용**했다. 로컬에 SQL 경로가 없어(PostgREST DDL 불가 · PAT 없음 ·
psycopg 미설치) **일회용 Edge 함수**로 실행하고 **끝나고 삭제**했다(HTTP 404 확인).
그 함수는 `SUPABASE_DB_URL` 로 DB 에 직결해 **service_role key 보다 강한 권한**이라
오래 두면 안 된다.

### STEP 0 을 먼저 돌리길 잘했다 — 가정 2 개가 틀렸다

| 항목 | 내 가정 | 실제 |
|---|---|---|
| `pgmq.send` | `bigint` | **`SETOF bigint`** |
| `pgmq.message_record` | 5 컬럼 | **6 컬럼** (`headers` 추가) |
| `pgmq.read` 4번째 인자 | 없음 | `conditional jsonb DEFAULT '{}'` (기본값 있어 3인자 OK) |

그대로 갔으면 STEP 3 래퍼가 깨졌다.

### 🔴 보안 결함 1건 — `REVOKE FROM PUBLIC` 만으로는 안 막힌다

`SET LOCAL ROLE anon` 상태에서 `ingest_queue_send` 가 **성공했다**(msg_id 반환).
Supabase 는 `public` 스키마 새 함수에 `anon`/`authenticated` 로 **직접** EXECUTE 를
부여하는 default privileges 를 둔다 — PUBLIC 회수로는 그 부여분이 남는다.

```sql
REVOKE ALL ON FUNCTION ... FROM PUBLIC, anon, authenticated;   -- 롤을 명시해야 한다
```

**앞으로 `public` 에 SECURITY DEFINER 함수를 만들 때마다 이 함정이 있다.**

### 그 밖에 잡은 것 3건

- 실행기가 BigInt 를 직렬화 못 해 던진 예외가 **"SQL 실패" 로 보여** 원인 파악이 꼬였다.
  `msg_id` 가 bigint 라 실제로 밟았다 — SQL 은 성공했는데 실패로 읽었다.
- **`SET LOCAL` 은 트랜잭션 안에서만 유효하다.** BEGIN 없이 쓰면 조용히 무시돼
  첫 권한 테스트가 무의미했다.
- `read` 는 FIFO 다. 큐를 비우지 않으면 "방금 넣은 것" 이 아니라 가장 오래된 게 읽힌다.

### 최종 검증 — 실패 0

| 항목 | 결과 |
|---|---|
| 확장 3종 | `pg_cron 1.6.4` · `pg_net 0.20.0` · `pgmq 1.5.1` |
| 큐 테이블 | `pgmq.q_ingest_tasks` · `pgmq.a_ingest_tasks` |
| `ingest_jobs` 신규 컬럼 | `pending_tasks` · `last_heartbeat_at` |
| 래퍼 왕복 | send(5) → read(일치) → delete(true) → depth(0) |
| 권한 | anon 차단 · authenticated 차단 · **service_role 허용(대조군)** |
| 최종 큐 깊이 | 0 |

### 027 로 남긴 것

pg_cron 드레인 + pg_net Edge 호출, Vault 키, 고아 잡 sweep.
**워커가 아직 없다** — 없는 엔드포인트를 부르는 cron 을 먼저 만들면 실패 로그만 쌓인다.


## 14. Phase 3 — `ingest-worker` 드레인 골격

### 설계에 결정적인 사실을 먼저 찾았다

`run_chunk_stage` 는 문서 전체 `sections` 를 받아 `_split_long_sections` →
**`_merge_short_sections`(인접 섹션 병합)** 을 한다(`chunk.py:96`).

> 페이지별로 쪼개서 청킹하면 **경계에서 병합이 안 일어나 청크가 달라진다.**

즉 "페이지 추출 → 전부 모아 청킹" 이어야 하고, **중간 산출물을 둘 자리가 필요하다.**
그 설계 전에 `extract` 핸들러를 쓰면 되돌리게 되므로 이번엔 골격만 만들었다.

### 드레인 계약 — 분기마다 결과가 다르다

| 상황 | 처리 |
|---|---|
| 성공 | `delete` |
| 핸들러가 던짐 | **지우지 않는다** — vt 후 재배달, `read_ct` 증가 |
| 모르는 stage | 즉시 `archive` + 잡 failed (독약 메시지 방지) |
| `read_ct > MAX_ATTEMPTS` | 핸들러 **부르지 않고** archive + 잡 failed |

하나라도 뒤집히면 메시지가 영원히 돌거나 조용히 사라진다.

**잠정값 2개**: `MAX_ATTEMPTS=3`(현행 `attempts` 가 전부 1 이라 근거가 약하다),
`vt=600s`(Edge 백그라운드 wall clock 400s 보다 크게).

### 검증

- 단위 7건 통과. **음성 대조 6종 발화** — 실패 시 delete / 모르는 stage skip /
  한도 경계 `>`→`>=` / 성공 시 delete 누락 / 한도 초과인데 핸들러 실행 / 첫 실패에 중단.
  한 주입은 **타입 체크에서 걸려 런타임 검출이 아니었다** — 위치를 옮겨 다시 쟀다.
- 실제 큐 왕복: send(msg_id=7) → drain → `{read:1, archived:1, ok:0}` → 깊이 0.
- 인증 비인증 401 · anon 403 · service_role 200. 경로 GET 405 · `/bogus` 404.

### 미검증 1건 (기록)

`archived:1` 은 **워커의 자기 보고**다. `pgmq.a_ingest_tasks` 가 PostgREST 에 안 보여
"정말 보관됐는지" 를 독립 신호로 확인하지 못했다.
→ 027 에서 `ingest_queue_archived_count` 래퍼를 추가해 닫는다.
