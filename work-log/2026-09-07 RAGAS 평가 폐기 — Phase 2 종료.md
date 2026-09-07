# 2026-09-07 — RAGAS 평가 폐기, Phase 2(읽기 경로) 종료

> **범위**: Edge 로 옮길 수 없던 4 라우트(`ragas` 의존)를 **폐기**해 Phase 6 의 차단 요인을
> 없애고, Phase 2 를 닫기까지.
> **다음 세션 재진입**: `chunk` **조각 c** — 먼저 **Python `\w` 전수 대조**(§21 에서
> `\b` 차이 6건 실측). 그다음 entity 정규식 8개 재작성 → `_to_chunk_records` 조립 →
> **기준선 digest `942f1b2e98ae666e` 직접 대조**. a·b 는 끝났다(§19, §20).

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
| Phase 3 — 마이그 027 (중간 산출물 테이블) | ✅ **운영 적용** (86c7eaa) |
| Phase 3 — HWP extract 이식 + 대조 | ✅ (5815c4d) |
| Phase 3 — extract 핸들러 결선 (큐→워커→산출물) | ✅ **E2E 성립** (afe0d82) |
| Phase 3 — Python 문자 판정 3종 (chunk 기반) | ✅ (2689641) |
| Phase 3 — chunk 조각 a (분할·마스킹·overlap) | ✅ (ff61062) |
| Phase 3 — chunk 조각 b (섹션 병합·표 셀 판정) | ✅ (743ce46) |
| Phase 3 — chunk 조각 c | 🟡 착수 — `\b` 차이 실측·fixture 만 (05c4999) |

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


## 15. Phase 3 — 마이그 027 (중간 산출물 테이블)

### 저장 위치를 셋 중에서 골랐다

| | 방안 | 판단 |
|---|---|---|
| **1** | **새 테이블 `ingest_artifacts`** | **채택** — `doc_id` CASCADE 로 정리 자동, 트랜잭션 일관성, 나중에 payload 를 Storage 참조로 바꿔도 스키마 유지 |
| 2 | Storage 경로 | 크기 무제한이나 정리가 수동 |
| 3 | `ingest_jobs.stage_progress` | **부적절** — 실시간 진행 표시용이고 Realtime push 가 걸려 있어(마이그 009) 큰 페이로드마다 프론트로 밀린다 |

### 설계 요점

- `UNIQUE(job_id, stage, seq)` — vt 만료로 같은 작업이 두 번 배달돼도 행이 둘이 되면 안 된다.
  **워커 멱등성의 근거가 이 제약이다.**
- RLS 를 켜고 **정책을 하나도 만들지 않았다.** 파이프라인 내부 산출물이라 클라이언트가
  볼 이유가 없다. service_role 은 RLS 를 우회하므로 워커는 그대로 쓴다.
- 026 의 교훈 적용: `REVOKE ... FROM PUBLIC, anon, authenticated`.

### 026 미검증 항목을 닫았다

`ingest_queue_archived_count()` 래퍼를 추가했다. 실측 **`archived_count = 1`** —
§14 에서 워커가 보관했다던 msg_id=7 이 실제 archive 테이블에 있음을 **독립 신호로** 확인.

### 검증 — 실패 0

테이블·UNIQUE 제약 · RLS on/정책 0 · anon 차단 · service_role 허용(대조군) ·
보관 래퍼 값과 anon 차단.

### 샌드박스 하네스 갱신

`ingest_artifacts` 를 수집·검증·정리·운영총계에 추가하고 selftest 가 그 분기를 태우게 했다.
**CASCADE 가 지워 주더라도 세지 않으면 누락을 못 잡는다**(026 에서 vision 이 그랬다).

음성 대조: 수집 제거 2건 발화. 검증 무력화는 0 건인데, artifacts 는 `job_id`·`doc_id`
**두 경로 모두 CASCADE** 라 둘 중 하나만 살아 있어도 지워진다 — DB 직접 조회로 실제 행이
0 임을 확인했다. 검사기 약점이 아니라 이중 보호다.


## 16. Phase 3 — HWP extract 이식

대조는 **섹션 단위**로 했다. 기준선(청크)은 extract→chunk→chunk_filter 를 거친 결과라
extract 만으로는 판정할 수 없다.

### 🔴 Phase 0 이 놓친 것 — 출력이 JSON 인코딩 문자열이다

`@rhwp/core` 의 `getTextFileText()` 는 평문이 아니라 `"\r\n문서번호\r\n…"` 처럼
**따옴표로 감싸이고 개행이 `\r` `\n` 두 글자로 이스케이프된** 문자열을 준다.
그대로 쓰면 단락 분할이 통째로 어긋난다 — 실측 **섹션 py 36 개 vs ts 1 개**.

**왜 Phase 0 이 못 잡았나**: 채점(`spike_hwp_similarity.py`)이 `strip_ws()` 로
**공백을 전부 지우고** 비교했다. work-log 의 "유사도 1.0000" 은 그 조건 아래 값이고,
"필수 후처리 1건(엔티티 디코딩)" 만 적혀 있어 그걸 믿고 갔다가 잡혔다.

> 교훈: **"유사도 1.0" 은 무엇을 무시하고 잰 값인지까지 적어야 한다.**

### 이식한 규칙 (원본 `hwp_parser.py:57`)

1. `getTextFileText()` → **JSON 언랩** → 숫자 엔티티 디코딩(`&#65378;` = `｢`)
2. 빈 텍스트면 sections=[] + warning 1
3. `\n\n` 분할 → `pyStrip` → 빈 것 제거. **1 개 이하면 `\n` 로 재분할**
4. `page`·`section_title`·`bbox` 는 HWP 경로에서 전부 null

명명 엔티티(`&amp;`)는 **건드리지 않았다** — 실측된 건 숫자 엔티티뿐이고, 넓히면 원문에
진짜 `&amp;` 가 있을 때 갈린다.

### 검증 — FAIL 0

| 항목 | 결과 |
|---|---|
| 숫자 엔티티 8 · 단락 분할 7 · JSON 언랩 8 | OK |
| `buildHwpResult` 8건 (Python 파서에 텍스트 주입) | OK |
| law_sample1.hwp **섹션 36 개 내용·메타 완전일치** | OK |
| raw_text 공백 제외 **722자 완전일치** | OK |

음성 대조 7종 발화. **처음 2종이 0 건이었다**(pyStrip→trim, 빈 텍스트 분기) — 실제 파일
하나로는 안 태워지는 분기라 `buildHwpResult` 직접 대조를 추가했다(U+001C·U+FEFF·NBSP·빈 값).

### 의도적으로 남긴 차이 1건

`raw_text` 가 **공백만 +8자** 다르다(빈 문단 처리: `\r\n` py 35 vs ts 39).
임의로 개행을 축약해 맞추면 다른 문서에서 오히려 갈리므로 안 했다.

소비처를 확인했다 — `tag_summarize`(LLM 입력) · `doc_embed`(요약 NULL 시 fallback) ·
`chunk`(ENV OFF 면 미사용) 뿐이고 `extract.py:308` 스캔 판정은 PDF 전용이다.
**결정적 산출물인 chunks 는 sections 에서 나오므로 영향이 없다.**
검사기는 "공백 제외 완전일치 + 공백 차이 50자 임계" 로 고정했다.

### 미검증 1건

로컬 Python 이 `hwp5txt CLI 실패 → olefile fallback` 을 탔다(경고 확인).
**운영 Railway 에서 CLI 가 성공하면 텍스트가 다를 수 있다.** 기준선도 같은 로컬 경로로
떴으므로 기준선↔Edge 일관성은 유지되지만, 운영 CLI 경로와의 대조는 아직 못 했다.


## 17. Phase 3 — extract 핸들러 결선

Storage 다운로드 → `extractHwp` → `ingest_artifacts` upsert. **큐가 처음으로 실제 일을 한다.**

### 설계 선택 2가지

- **다음 단계를 큐에 넣지 않는다.** `chunk` 핸들러가 없어 넣는 순간 "모르는 stage" 로
  archive + 잡 failed 가 된다(§14 계약). 산출물 저장까지만.
- **잡을 completed 로 만들지 않는다.** 청킹이 안 끝났으므로 완료가 아니다.
  `running` + `current_stage='extract'` 로 사실대로 둔다.

### 못 하는 건 못 한다고 던진다

이식 안 된 포맷 · `pending/` 경로 · 빈 `storage_path` · 문서 없음 — 전부 예외.
조용히 건너뛰면 잡이 영원히 running 으로 남고 어디서 멈췄는지도 안 보인다.

### 멱등성 — 실제로 확인했다

`ingest_artifacts` 의 `UNIQUE(job_id, stage, seq)` 위에 upsert 한다.
E2E 에서 같은 작업을 다시 배달해도 **행이 1개**였다.

### 검증

단위 6건(문서 없음 / 미지원 포맷 / pending / 빈 경로 / 정상 upsert+onConflict /
`task.from`→seq).

**E2E 실패 0** — 샌드박스 + 실제 Storage + 실제 큐 + 배포된 워커:

```
Storage 업로드 → 큐 msg_id=8 → drain {read:1, ok:1, archived:0}
ingest_artifacts 1행 · 섹션 36개 Python 과 완전일치 · raw_text 내용 일치(722자)
재배달 멱등성 ok=1 행=1 · 잡 running/extract · 정리 완료 · 운영 총계 불변
```

`deno test` 122건 통과.

### 현재 Phase 3 진행도

| 단계 | 상태 |
|---|---|
| 큐 인프라(pgmq·래퍼·워커) | ✅ |
| 중간 산출물 자리 | ✅ |
| **extract** | ✅ HWP 만 (hwpx·pdf·docx·pptx 남음) |
| chunk · chunk_filter · content_gate · tag_summarize · load · embed · doc_embed · dedup | ⬜ |
| `api-documents` HTTP 경로(큐에 넣는 쪽) | ⬜ |
| pg_cron 드레인 | ⬜ |


## 18. Phase 3 — Python 문자 판정 3종 (chunk 이식의 기반)

`chunk` 착수 중 발견: `_looks_like_table_cell` 이 Python 문자 메서드 **3종**을 쓴다.

```python
non_ws      = sum(1 for c in stripped if not c.isspace())
digit_punct = sum(1 for c in stripped
                  if c.isdigit() or (not c.isalnum() and not c.isspace()))
```

셋 중 하나만 어긋나면 표 셀 판정이 뒤집히고 → 섹션 병합이 달라지고 →
**청크 경계가 통째로 바뀐다.** JS 기본 문자 클래스로는 못 맞춘다.

### 전수 대조 (0x0~0x10FFFF)

| Python | JS 후보 | 차이 |
|---|---|---|
| `isspace` | `/\s/u` | Python 만 `\x1C-\x1F`·`\x85` 5 자 / JS 만 `U+FEFF` 1 자 |
| `isdigit` | `/\p{Nd}/u` | Python 만 128 자(`²³¹` 등) / JS 만 80 자 |
| `isalnum` | `/[\p{L}\p{N}]/u` | **JS 만 5,004 자** (Python 이 부분집합) |

`isalnum` 은 Python 집합을 통째로 박으면 747 범위인데 `[\p{L}\p{N}]` 에서 **27 범위만
빼면** 같아진다 — 그쪽을 골랐다. `isspace` 는 `PY_SP` 와 정확히 같아 재사용.

### 이 차이는 유니코드 버전에서 온다

Deno 가 Python 보다 새 유니코드를 쓴다. **런타임이 올라가면 집합이 달라질 수 있다.**
테스트가 전 코드포인트를 fixture 와 대조하므로 조용히 갈리면 먼저 깨진다.

`isalnum` fixture 에는 예외 목록이 아니라 **Python 집합 747 범위를 통째로** 넣었다 —
예외 목록으로 검증하면 구현과 같은 경로를 두 번 도는 **자기 대조**가 된다.

### 검증

전수 3종 + 함정 값 + 빈 문자열 = 5건. 음성 대조 4종 발화
(예외 제거 2 · `\p{Nd}` 교체 2 · JS `\s` 교체 2 · 이진탐색 경계 3).
`deno test` 127건 통과.

### chunk 이식 — 남은 조각

`chunk.py` 543 줄 + 의존 모듈 2 개(`entity_extract`, `synonym_inject`). 한 번에 못 옮긴다.

| 조각 | 내용 | 함정 |
|---|---|---|
| a | 문장 분할 · 법령 날짜 마스킹 · overlap | lookbehind 정규식, `\d`·`\s` 차이, 코드포인트 길이 |
| b | 짧은 섹션 병합 (`_looks_like_table_cell` 포함) | **§18 이 그 기반** |
| c | 레코드 변환 (`_to_chunk_records`) | NFC 정규화, `entity_extract` 의존 |


## 19. Phase 3 — chunk 조각 a (문장 분할·날짜 마스킹·overlap)

### 정규식 3개가 전부 함정이었다

| 원본 | 문제 | 대응 |
|---|---|---|
| `(?<=[가-힣\)\]][.!?])\s+` | Python `\s` ≠ JS `\s` | `PY_SP` 문자 클래스 |
| `\n\s*\n` | 〃 | 〃 |
| `(\d{2,4})\.\s+…` | Python `\d` 는 유니코드 Nd 전부 | `\p{Nd}` |

길이·슬라이스는 전부 코드포인트로. `.length` 를 쓰면 이모지·한자 확장에서 800/1000
임계와 overlap 100 자가 어긋난다.

### 검증 — FAIL 0

날짜 마스킹+복원 10건 · 따옴표·괄호 균형 15건 · overlap 9건 · 문장 분할 20건.
음성 대조 **7종 발화**.

### 🔴 케이스가 약해서 3종이 처음 0 건이었다

- **`_KO` 를 20자로 잘못 세어** 케이스가 510자밖에 안 됐다(임계 800).
  **분할이 아예 안 일어나** 흡수 분기·overlap 이 통째로 미발화였다. 실측 11자로 고쳐
  1,100자로 키우니 3건 발화.
- `pyStrip`/`trim` 차이는 조합 결과의 **양끝**에 와야 드러난다.
  `U+FEFF`(JS 만 공백)·`U+001C`(Python 만 공백)로 시작·끝나는 케이스를 추가해 1건 발화.
- 셸 `grep -c` 가 한글 패턴에서 빈 값을 내 "0 건" 처럼 보인 적도 있다 —
  전체 출력을 직접 열어 실제로는 검출된다는 걸 확인했다.

> 교훈: **음성 대조가 0 건이면 세 가지를 순서대로 의심한다** —
> ① 케이스가 그 분기를 안 태우는가 ② 그 변경이 동작을 안 바꾸는가(CASCADE·무앵커 매칭 등)
> ③ 측정 자체가 실패했는가(실행 실패·grep 오판).

### chunk 남은 조각

| 조각 | 내용 | 상태 |
|---|---|---|
| a | 문장 분할 · 날짜 마스킹 · overlap | ✅ |
| b | 짧은 섹션 병합 (`_looks_like_table_cell` 포함) | ⬜ — 기반(§18)은 준비됨 |
| c | 레코드 변환 (NFC, `entity_extract` 의존) | ⬜ |


## 20. Phase 3 — chunk 조각 b (짧은 섹션 병합·표 셀 판정)

표 셀 판정 하나가 뒤집히면 **병합 여부가 달라지고 청크 경계가 통째로 바뀐다.**
그래서 §18(문자 판정 3종 전수 대조)을 먼저 깔았고, 여기서 그걸 쓴다.

### 이식한 규칙

- `looksLikeTableCell` — ` | ` 포함이면 무조건 참. 아니면 strip 후 **30자 미만**이고
  `digit_punct / non_ws >= 0.5` 일 때 참.
- `mergeShortSections` — 앞 조각 200자 미만 + **같은 page** + 합계(구분자 2자 포함)
  1000 이하일 때만 병합. 표 셀 의심은 어느 쪽이든 걸리면 끊는다.
  병합 시 `section_title` 은 **뒤쪽 우선**(빈 문자열은 falsy), `metadata` 도 뒤쪽이 이긴다.

### 검증 — FAIL 0

표 셀 18건(True 11 / False 7) · 병합 18건(병합 발생 11).
경계를 직접 태웠다 — 30자 딱, 비율 0.5 딱, 199/200, 합계 1000/1001, `page None==None`,
`²`(Python 만 digit), `U+001C`(Python 만 공백), `U+FEFF`(JS 만 공백), 새 유니코드,
이모지 코드포인트.

음성 대조 **12종 전부 발화** — ` | ` 검사 제거 3 · 30자 경계 1 · 비율 경계 1 ·
`pyIsDigit`→`\p{Nd}` 1 · `pyIsSpace`→JS `\s` 1 · MIN_MERGE 경계 1 · page 비교 제거 1 ·
`+2` 누락 1 · title 우선순위 1 · metadata 순서 1 · 표 셀 차단 제거 2 · `cpLen`→`.length` 2.

### chunk 남은 조각

| 조각 | 내용 | 상태 |
|---|---|---|
| a | 문장 분할 · 날짜 마스킹 · overlap | ✅ |
| b | 짧은 섹션 병합 · 표 셀 판정 | ✅ |
| c | 레코드 변환 (NFC, `entity_extract` 의존) | ⬜ — **여기까지 가면 기준선 digest 직접 대조** |


## 21. Phase 3 — chunk 조각 c 착수: `\b` 경계 차이 발견

조각 c(`_to_chunk_records`)는 `entity_extract` 를 부른다. 그 정규식 8 개에 `\b` 가 널려
있는데 **Python `\b` 와 JS `\b` 는 단어 문자 정의가 다르다** — Python 은 유니코드 `\w`,
JS 는 ASCII `\w`. 한국어 문서에서 정면으로 갈린다.

### 실측 — 17 입력 × 5 패턴 중 6 건 차이

| 입력 | Python | JS | 원인 |
|---|---|---|---|
| `50,000원` | 매칭 | **안 함** | `원\b` — Python 은 `원` 이 `\w` |
| `100만원` | 매칭 | **안 함** | 〃 |
| `$100달러` | **안 함** | 매칭 | `\b` 뒤 — Python 은 `달` 이 `\w` |
| `약25%` | **안 함** | 매칭 | `\b` 앞 — Python 은 `약` 이 `\w` |
| `２５%` · `５０,０００원` | 매칭 | 안 함 | `\d` 가 유니코드 Nd |

**`50,000원` 이 안 잡히면 한국어 금액 추출이 통째로 죽는다.** 그대로 옮겼으면
`chunks.metadata.entities` 가 조용히 비었을 것이다.

### 다음 세션이 할 일

1. **Python `\w` 전수 대조** — `\b` 를 `(?<![\w])`/`(?![\w])` 로 풀려면 `\w` 집합이
   정확해야 한다. §18 에서 `isalnum` 이 5,004 자 달랐던 전례가 있어 짐작 금지.
2. 정규식 8 개 재작성 (`\b` 치환 + `\d`→`\p{Nd}` + `(?<!\d)` lookbehind)
3. `_compose_vision_text`(HWP 는 no-op 이지만 계약) · `synonym_inject` ENV 처리
4. `_to_chunk_records` 조립 → **기준선 digest 직접 대조**

`api/scripts/fixtures/entity_regex_baseline.json` 에 Python 정답을 떠 뒀다 —
재측정 없이 바로 대조할 수 있다.


## 22. Phase 3 — chunk 조각 c 완료: **기준선 digest 직접 대조 성공**

§21 이 남긴 과제를 전부 닫고, 이번 이식의 최종 관문인 **운영 기준선 digest 대조**까지
통과했다.

```
chunks_digest  기준선 942f1b2e98ae666e
               python 942f1b2e98ae666e  일치
               deno   942f1b2e98ae666e  일치
```

### 22.1 Python `\w` 전수 대조 — 새 자산이 필요 없었다

§18 의 `isalnum` 이 5,004 자 달랐던 전례 때문에 짐작하지 않고 0x0~0x10FFFF 를 다 셌다.

```
Python \w        : 137,936자, 748개 범위
JS [\p{L}\p{N}_] : 142,940자
  Python 만 :     0자,  0개 범위
  JS 만     : 5,004자, 27개 범위
```

Python `\w` = `isalnum`(137,935) + `_` = 137,936 이고, **JS 와의 차이 27 범위가 §18 에서
만든 `ALNUM_EXCESS` 와 완전히 동일**했다. 새 전수 테이블 없이 재사용했다.

`v` 플래그 집합 뺄셈(`[[\p{L}\p{N}_]--[...]]`)이 Deno 에서 동작하는 것을 확인하고
`PY_WORD_CLASS` 로 노출했다. 0x0~0x10FFFF 전수 테스트 2 개(`pyIsWord`, 정규식 형태)
추가 — `pychar_test.ts` 7 passed.

### 22.2 `\b` 축약형은 틀린다 — 음성 대조가 3 건 잡았다

`\b` 를 `(?<!W)`(앞) / `(?!W)`(뒤) 로 줄여 쓰려 했다. **패턴 양끝 문자가 항상 word 일
때만 같다.** ISBN 패턴 `[\p{Nd}\-Xx]{10,17}` 은 `-` 로 끝날 수 있어 갈린다.

| 입력 | Python | 축약형 |
|---|---|---|
| `ISBN 1234567890-a` | `1234567890-` | `1234567890` |
| `ISBN 1234567890-` | `1234567890` | `1234567890-` |

정확히 **정반대**다. 정의 그대로 `(?:(?<=W)(?!W)|(?<!W)(?=W))` 로 구현했다.
쓰기 전에 의심하고 케이스를 넣어 뒀기 때문에 음성 대조가 잡았다.

### 22.3 대조 결과

| 대상 | 케이스 | 결과 |
|---|---|---|
| `entity_extract` | 49 건 (§21 fixture 17 + 신규 32) | 전부 일치 |
| `chunk_records` | 23 건 × caption ON/OFF | 전부 일치 |
| HWP 파이프라인 전체 | 기준선 11 청크 | 3 방향 전부 일치 |

**음성 대조 19 종 중 18 종이 실제로 깨졌다.**

- entity: JS 기본 `\b` 14 / 축약형 `\b` 3 / `\d` 4 / JS `\s` 2 / dedup 제거 1
- records: 코드포인트 len 4 / NFC 2 / 제목 NFC 2 / caption 잘림 2 / caption 우선순위 1 /
  `is not None`→truthy 2 / vision 진입 18 / 2차분할 임계 2 / metadata 승계 2 /
  overlap 메타 6 / entities 34 / merge 2 / cpSlice 1

0 건이 나온 2 건은 원인을 각각 규명했다:

- **`pyStrip`→`trim` 0 건** — 케이스 부족이 아니라 **구조적 no-op**. 패턴 8 개가 전부
  숫자·`$`·`₩`·`제` 로 시작해 숫자·`원`·`%`·`호`·`[Nd Xx]` 로 끝나므로 매칭 결과가
  공백으로 시작·끝날 수 없다. 무작위 200,000 입력 × 30,587 매칭에서 `strip()` 이 값을
  바꾼 경우 **0 건**으로 확인했다.
- **2차분할 임계 `<=`→`<` 0 건** — 정확히 1000 자 케이스가 없었다. 그런데 1000 자를
  그냥 넣어도 안 된다 — 문장 경계가 없으면 분할해도 1 조각이라 임계를 바꿔도 결과가
  같다. `"끝났다. "` 200 회(정확히 1000 자)로 바꿔 태웠다 → 2 건 차이.

### 22.4 ENV 는 추측하지 않고 DB 결과물로 판정했다

Railway CLI 가 없어 ENV 를 못 읽는다. **ENV 대신 운영 chunk 에 뭐가 박혔는지**를 봤다 —
ENV 가 켜져 있어도 인제스트가 안 됐으면 chunk 엔 안 박히므로 이쪽이 더 강한 증거다.

| metadata / text | 행수 | 판정 |
|---|---|---|
| `entities` | 7,067 | 상시 ON |
| `overlap_with_prev_chunk_idx` | 37,067 | 상시 |
| `synonym_candidates` | 199 | **2026-05-13 하루치, 문서 8 개** |
| `[표 p.N: ` prefix | 101 | **2026-05-13 하루치, 문서 3 개** |
| `[그림 p.N: ` prefix | 37 | 〃 |
| `vision_incremental` | 0 | 코드 경로만 존재 |

2026-05-13 에 ablation 으로 하루 켰다 껐다. 이후 2026-05-14 대량 인제스트(34,000 행
이상)와 2026-07-07 최신 건은 전부 0 → **현재 둘 다 OFF**.

그래도 caption 은 양쪽 경로를 다 옮기고 ON/OFF 양쪽으로 대조했다. synonym 은 훅만 두고
**ENV ON 이면 던진다** — 조용히 다른 청크를 만드는 것보다 낫다.

### 22.5 `raw_text` 는 일치하지 않는다 (숨기지 않고 고정)

```
raw_text   py 985자  ts 993자   공백 제외 동일? True   (+8자)
```

- 원인은 **추출기가 다른 것**. Python 은 `hwp5txt` CLI 가 이 파일에서 죽어
  (`msoleprops.py` `KeyError: 2`) olefile fallback 을 타고, TS 는 `@rhwp/core` 다.
  CLI 실패는 파일의 SummaryInformation 스트림 문제라 **환경 무관 — Railway 에서도 같다.**
- 차이는 **빈 줄 3 곳뿐**이고 공백을 모두 제거하면 동일. 섹션 36 개·청크 11 개는 완전 일치.
- `raw_text` 사용처는 `tag_summarize` LLM 입력과 `doc_embed` 의 summary-없음 fallback
  둘뿐이고 모두 비결정 단계(기준선도 `nondeterministic` 분류) → **결정적 산출물 영향 0**.
- 문자 내용이 갈리면 잡히도록 "공백 제외 동일" 을 회귀 검사에 넣었다.

### 22.6 실측이 다음 우선순위를 바꿨다 — PDF 가 99.3%

이식 순서를 Phase 0 스파이크 기준으로 잡았는데, 운영 사용량을 재 보니 달랐다.

| doc_type | 문서 | chunk | 비중 |
|---|---|---|---|
| **pdf** | 9 | **36,818** | **99.3%** |
| hwpx | 2 | 227 | 0.6% |
| hwp | 1 | 30 | 0.08% |
| pptx | 1 | 5 | 0.01% |

게다가 운영의 유일한 `hwp` 문서(`law sample2`)는 **HWPML(XML)** 이라 방금 이식한
`@rhwp/core` 경로를 아예 안 탄다. **지금 Edge 로 옮긴 HWP 파서를 타는 운영 문서는 0 건.**

### 22.7 검증

| 항목 | 결과 |
|---|---|
| Deno `_shared/` 전체 | **129 passed / 0 failed** |
| Python `test_chunk*` | 63 OK |
| Python `test_entity*` | 26 OK |
| Python 전체 discover | 1,465 tests, 4 failed (전부 기존 `test_embed_cache` — 단독 실행 시 통과, 이번 작업 무관) |

### 22.8 커밋

| 해시 | 내용 |
|---|---|
| `6d5944e` | HWP 파이프라인 기준선 직접 대조 |
| `eb4add4` | chunk 레코드 조립 이식 (조각 c1) |
| `27f2881` | entity_extract 이식 (`\b`·`\d` 경계) |

### 22.9 남은 것

| 항목 | 상태 |
|---|---|
| chunk 조각 c2 — `synonym_inject` | ⬜ (ENV OFF 라 급하지 않음, 훅은 던지게 해 둠) |
| **PDF extract 이식** | ⬜ — **운영 99.3%. 다음 순위 1 순위** |
| HWPML extract 이식 | ⬜ — 운영 hwp 문서가 실제로 타는 경로 |
| hwpx / docx / pptx extract | ⬜ |
| chunk 핸들러 결선 | ⬜ |
| chunk_filter / content_gate / tag_summarize / load / embed / doc_embed / dedup | ⬜ |
| `api-documents` HTTP 경로 · pg_cron 드레인(마이그 028) | ⬜ |


## 23. Phase 3 — PDF extract 이식 (운영 99.3%). **Phase 0 결론 2건이 뒤집혔다**

§22.6 에서 실측한 대로 PDF 가 운영 chunk 의 99.3% 라 다음 순위로 잡았다. 파서 자체는
하루 만에 옮겼지만, 넓은 범위로 대조하자 **Phase 0 이 7 페이지로 내린 판정 2 건이
깨졌다.** 이번 절의 대부분은 그걸 찾아 고친 기록이다.

### 23.1 CPU 는 제약이 아니었다 — 제약은 순차 의존성이다

착수 전에 "대형 PDF 를 Edge CPU 2s 안에 처리 가능한가" 를 미검증 가정으로 잡았는데,
**Phase 0 이 이미 쟀다**(400p = 1,421ms). 확인 없이 미검증이라 말한 건 부정확했다.

새로 잰 것은 **문서 열기 비용**이다. 워커는 상태가 없어 태스크마다 문서를 새로 여는데,
그게 비싸면 페이지 분할 설계가 통째로 바뀐다.

| 파일 | 페이지 | 문서 열기 | 페이지당(로컬) |
|---|---|---|---|
| law sample3 | 4 | 1.4ms | 11.7ms |
| 삼성 사업보고서 | 573 | **0.7ms** | 2.1ms |
| SK 사업보고서 | 1,513 | **2.0ms** | 1.7ms |

mupdf 는 lazy loading 이라 **열기가 사실상 공짜**다. 페이지 분할은 자유롭다.

진짜 제약은 따로 있었다. `current_title` 이 **문서 전체 sticky** 다 —

```python
current_title = None
for page_num, page in enumerate(doc, start=1):
    ...  # heading 을 만날 때까지 직전 제목을 상속
```

페이지 범위를 **병렬로 돌리면 제목이 어긋난다.** 음성 대조에서 sticky 전파를 끊자
실자산 145 페이지가 깨져 실증됐다. 순차 처리 + 태스크 경계에서 title 인계가 필요하다
(`extractDictBlocks` 가 `currentTitle` 을 받고 `nextTitle` 을 돌려주는 이유).

### 23.2 **뒤집힌 Phase 0 판정 ① — `STEXT_OPTS` 에 `preserve-ligatures` 누락**

Phase 0 이 정한 값은 `preserve-whitespace,preserve-images` 둘뿐이었다.
PyMuPDF 의 실제 기본값을 확인하니 다섯 개다:

```
fitz.TEXTFLAGS_DICT == 199
  = 1 PRESERVE_LIGATURES | 2 PRESERVE_WHITESPACE | 4 PRESERVE_IMAGES
  | 64 MEDIABOX_CLIP     | 128 CID_FOR_UNKNOWN_UNICODE
```

`preserve-ligatures` 가 빠져서 mupdf 가 `ﬀ`(U+FB00)를 `ff` 로 풀었다. arXiv 문서
텍스트가 491 자 길어지고 800 자 분할 경계가 밀려 **청크 749 → 803 개(+7.2%)**.
옵션을 맞추자 blocks 10 / lines 25 / chars 1,682 / 리거처 4 로 완전 일치.

> **라이브러리 기본값을 짐작하지 말 것** — 누락 점검 §2 그대로다. 옵션 이름도
> 상상하지 않고 WASM 바이너리에서 문자열을 뽑아 확인했다.

버전도 다시 채점했다(Phase 0 이 "올릴 때 채점기 재실행" 을 지시해 뒀다):

| | 블록목록 완전일치 |
|---|---|
| mupdf@1.27.0 | **301/309p (97.4%)** |
| mupdf@1.28.1 | 160/232p (69.0%) |

1.27.0 유지가 옳다. `preserve-spans` 도 재평가했는데 텍스트 불일치가 5 → 138 페이지로
훨씬 나빠져 기각을 유지했다.

### 23.3 **뒤집힌 Phase 0 판정 ② — "합성 공백 영향 0" 은 heading 경로를 안 봤다**

Phase 0 은 span 분할 잔차를 알고 있었고 "섹션 텍스트·bbox·needs_vision·triggers
7/7 동일" 이라 영향 0 으로 적었다. **`pageMedianSize` 를 통한 heading 판정은 보지
않았다.**

PyMuPDF 는 MuPDF 가 간격 때문에 끼워 넣은 공백을 독립 span 으로 둔다. 같은 페이지인데
span 수가 3 배 차이난다(arXiv 47,479 vs 16,522). **size 값 자체는 편차 0** 이었다 —
문제는 `_page_median_size` 가 **span 하나당 1표**로 중앙값을 낸다는 것이다. 표 수가
달라지면 중앙값이 이동하고 `_HEADING_FONT_RATIO`(1.15) 임계에서 판정이 뒤집힌다.

| `pageMedianSize` 방식 | py≠ts |
|---|---|
| span 가중 (기존 원본) | 14/249p |
| **글자 수 가중** | **0/249p** |
| 알고리즘 변경이 Python 결과를 바꾸는 양 | 39/249p (15.7%) |

**사용자 결정: 글자 수 가중으로 변경**(Python·TS 양쪽). 텍스트 내용에만 의존해 span
분할과 무관해지고, 구하려는 값이 "페이지 본문의 대표 글자 크기" 이므로 의미에도 맞다.
span 가중은 애초에 파서 버전에 흔들리는 불안정한 지표였다.

대가: 해당 39 페이지를 재인제스트하면 `section_title` 이 바뀐다. 검색 품질이 더 낫다는
증거는 **없다**(RAGAS 폐기로 측정 수단이 없음) — 안정성 논거로만 택했다.

### 23.4 내 대조에도 구멍이 있었다

`has_section_title`(bool) 만 비교하고 **title 문자열을 비교하지 않았다.** arXiv 가
그래서 "일치" 로 통과했는데 실제로는 105 행이 달랐다. `title_sha16` 을 추가해 메웠다.

> 누락 점검 §8 계열이다 — 판정기가 무엇을 **안 보는지**를 확인하지 않았다.

### 23.5 대조 결과

| 대상 | 규모 | 결과 |
|---|---|---|
| median / isHeadingBlock / extractDictBlocks | 10 / 53 / 19 건 | 전부 일치 |
| 실자산 (PyMuPDF dict 를 양쪽에 먹임) | 7 문서 **5,619 섹션** | 전부 일치 |
| 전체 파이프라인 (mupdf 직접) | 8 문서 249p | 5 문서 완전 일치 |
| 음성 대조 | **19 종 전부** 깨짐 확인 | |

음성 대조 0 건이 처음 6 종 나왔고 전부 원인을 규명해 해소했다. 마지막 UTF-16 케이스는
**판정 대상 블록의 글자도 median 에 들어간다**는 걸 빼고 계산한 내 실수였다. 표를 전부
세서 다시 잡으니(20pt×10 / 10pt×15 / 12pt×4) 판정이 갈린다.

### 23.6 맞출 수 없는 차이 — 스냅샷으로 고정

3 개 문서가 여전히 갈린다. **MuPDF 1.27.0 vs 1.27.2 블록 분할 패치 차이**인데
npm 에 1.27.2 가 없다(1.27.0 다음이 1.28.0).

| 문서 | 청크 py/ts | 섹션 py/ts |
|---|---|---|
| 데이터센터 안내서 41p | 384 / 386 | 1,202 / 1,214 |
| sample-report 60p | 525 / 526 | 1,040 / 1,049 |
| 보건의료 26p | 148 / 149 | 613 / 614 |

**텍스트 손실은 0 이다.** 5 개 자산에서 공백을 모두 제거한 전체 텍스트가 동일했다 —
블록 경계만 이동한다.

늘 FAIL 을 내면 검사는 곧 무시된다. `fixtures/pdf_known_divergence.json` 에 값을
고정하고 **"알려진 값과 다르면 실패"** 로 바꿔 악화를 잡는다(`UPDATE_KNOWN=1` 로 갱신).

### 23.7 검증

| 항목 | 결과 |
|---|---|
| Deno `_shared/` 전체 | **129 passed / 0 failed** |
| Python `test_*pdf*` | 48 OK |
| Python `test_*pars*` | 30 OK |
| Python 전체 discover | 1,465 tests, 4 failed (전부 기존 `test_embed_cache`) |

### 23.8 커밋

| 해시 | 내용 |
|---|---|
| `336eda2` | pageMedianSize 글자 수 가중 (Python·TS) |
| `ed8ce7c` | STEXT_OPTS preserve-ligatures 누락 수정 |
| `1b3e085` | PDF extract 이식 |

### 23.9 남은 것

| 항목 | 상태 |
|---|---|
| PDF extract 핸들러 결선 (`deno.json` 에 `mupdf@1.27.0` 추가 필요) | ⬜ |
| 페이지 분할 워커 — **순차 + title 인계** | ⬜ |
| chunk 핸들러 결선 | ⬜ |
| HWPML / hwpx / docx / pptx extract | ⬜ |
| chunk 조각 c2 (`synonym_inject`) | ⬜ |
| chunk_filter / content_gate / tag_summarize / load / embed / doc_embed / dedup | ⬜ |
| `api-documents` HTTP 경로 · pg_cron 드레인(마이그 028) | ⬜ |


## 24. Phase 3 — extract→chunk 결선. **실제 DB E2E 로만 잡히는 버그 2건**

§23 까지는 함수를 옮기고 대조만 했다. 여기서 처음으로 **큐·Storage·DB 를 붙여 끝까지**
돌렸다. 그러자 대조로는 절대 안 나올 버그가 두 개 나왔다.

### 24.1 구조

| 단계 | 단위 | 다음 작업 |
|---|---|---|
| `extract` (PDF) | 페이지 **10 개**씩 | 남았으면 다음 범위, 끝났으면 `chunk` |
| `extract` (HWP) | 문서 전체 1 회 | 바로 `chunk` |
| `chunk` | extract 아티팩트 전부 | 없음(다음 핸들러 미이식) |

**PDF 는 순차여야 한다.** `current_title` 이 문서 전체 sticky 라 범위를 병렬로 돌리면
제목이 어긋난다(§23.1). 그래서 범위를 한꺼번에 큐에 넣지 않고 **직전 범위가 끝날 때
다음 하나만** 넣는다. `carryTitle` 은 큐 메시지가 아니라 **직전 아티팩트의
`next_title`** 에서 읽는다 — 메시지에 실으면 재시도 때 낡은 값이 따라온다.

저장 → enqueue **순서**도 계약이다. 반대면 다음 태스크가 아직 없는 아티팩트에서
`carryTitle` 을 찾다가 던진다. 단위 테스트로 고정했다.

### 24.2 실제 Supabase E2E

`api/scripts/e2e_ingest_worker.ts` — `documents` 는 읽기만 하고, 만든 job 1 행과 그
job_id 의 아티팩트만 쓰고 끝나면 지운다.

| 문서 | 페이지 | 태스크 | 섹션 | 청크 | 시간 |
|---|---|---|---|---|---|
| law sample3 | 4 | 2 | 34 | 26 | 1.7s |
| 보건의료 | 26 | 3 | 614 | 149 | 2.4s |
| arXiv | 56 | 6 | 1,239 | 749 | 3.1s |
| 삼성 사업보고서 | 573 | 58 | 29,787 | 8,477 | 27.7s |
| **SK 사업보고서** | **1,513** | **152** | **72,588** | **25,831** | **85.7s** |

전부 페이지 분할 대조·파이프라인 대조와 같은 청크 수다. 큐 잔여 0, 아티팩트 잔여 0 확인.

### 24.3 **버그 ① — Postgres jsonb 는 NUL 을 못 받는다**

arXiv 를 돌리자 `ingest_artifacts 저장 실패: unsupported Unicode escape sequence` 로
죽었다. LaTeX PDF 가 `U+0000` 을 뱉는데 Postgres 는 TEXT/JSONB 어디에도 못 담는다.

원본은 이미 알고 있었다 — `SupabasePgVectorStore._strip_null_bytes` 가 **`chunks` 저장
직전**에 재귀 제거한다(주석: "arXiv 같은 LaTeX PDF 추출 보호"). 운영 chunks 에 NUL 이
0 건인 이유다.

Edge 는 중간 산출물을 jsonb 에 넣어야 해서 **더 일찍** 지워야 한다. 그러면 청킹 입력의
길이가 줄어 800 자 분할 경계가 밀릴 수 있다. 쓰기 전에 쟀다:

| 자산 | NUL | 늦게 지움(현행 Python) | 일찍 지움(Edge) |
|---|---|---|---|
| arXiv 56p | 96 개 | 749 청크 | 749 청크 — **동일** |
| sample-report 60p | 0 | 525 | 525 |
| SK 60p | 0 | 758 | 758 |

이 자산들에서 같다는 뜻이지 일반 보장은 아니다. 갈리기 시작하면 파이프라인 대조가 잡는다.

### 24.4 **버그 ② — `mediabox-clip` 은 deprecated 였다**

E2E 로그에 `The 'mediabox-clip' option has been deprecated. Use 'clip' instead.` 가
찍혔다. §23.2 에서 PyMuPDF `TEXTFLAGS_DICT` 를 재현하려고 넣은 옵션이다. 하위호환으로
동작은 했지만(양쪽 결과 동일 확인) 경고를 남기면 다음 사람이 "무시되는 옵션" 으로
오해한다 — `clip` 으로 바꿨다(`TEXT_CLIP == TEXT_MEDIABOX_CLIP == 64`).

### 24.5 페이지 분할이 결과를 바꾸지 않는다

분할 설계의 성립 조건이라 따로 고정했다(`verify_pdf_page_split.py`).
**분할 크기를 4 종(1/3/10/7 페이지) 써서 경계 위치를 옮겨 가며** 통합 처리와 대조한다 —
한 가지 크기로만 재면 우연히 맞을 수 있다.

6 문서 × 4 크기 **전부 digest 동일**. 음성 대조로 `carryTitle` 인계를 끊으면 **5/6
문서가 깨진다** — 검사가 유효하다는 증거다(2 페이지짜리 `law_sample2` 만 안 깨지는데,
sticky title 이 경계를 넘을 일이 없어서다).

### 24.6 E2E 스크립트 자체의 결함도 하나 고쳤다

첫 arXiv 실패 뒤 두 번째 실행이 **FK 위반**으로 죽었다. 실패한 큐 메시지는 `vt` 동안
안 보이는데, 정리 루프가 그때 읽으려 해서 못 지웠다. 그 사이 job 행은 지워졌으니
다음 실행이 없는 job_id 를 참조한 것이다. `drainOnce` 가 돌려주는 `errors[].msg_id` 로
바로 보관하게 고쳤다.

### 24.7 검증

| 항목 | 결과 |
|---|---|
| Deno `_shared/` 전체 | **147 passed / 0 failed** (신규 23) |
| 페이지 분할 대조 | 6 문서 × 4 분할 크기 FAIL 0 |
| 파이프라인 대조 | FAIL 0 (알려진 차이 8 건 그대로) |
| 실제 DB E2E | 5 문서 성공, 큐·아티팩트 잔여 0 |

### 24.8 커밋

| 해시 | 내용 |
|---|---|
| `c257011` | extract→chunk 결선 + 페이지 분할 워커 + NUL 방어 |

### 24.9 다음 단계가 알아야 할 것

**chunk 아티팩트가 크다** — 삼성 4.4MB, SK 는 약 13MB 로 추정된다. `load` 단계가 이걸
통째로 읽으면 Edge 메모리 상한 240MB(Phase 0 실측)에 부담이 된다. JSON 파싱 후 힙은
몇 배가 되므로 **분할 읽기**가 필요하다. chunk 핸들러가 레코드를 seq 로 나눠 저장하도록
바꾸는 편이 나을 수 있다.

| 항목 | 상태 |
|---|---|
| `load` 단계 — 분할 읽기 설계 필요 | ⬜ |
| pg_cron 드레인(마이그 028) · Edge 배포 | ⬜ |
| HWPML / hwpx / docx / pptx extract | ⬜ |
| chunk 조각 c2 (`synonym_inject`) | ⬜ |
| chunk_filter / content_gate / tag_summarize / embed / doc_embed / dedup | ⬜ |
| `api-documents` HTTP 경로 | ⬜ |


## 25. Phase 3 — `load` 이식. **chunks 테이블 실적재까지 E2E**

§24.9 가 남긴 숙제(chunk 아티팩트가 SK 약 13MB)를 닫고 `load` 를 붙였다. 이제
인제스트 사슬이 `extract → chunk → load` 로 이어져 **실제 `chunks` 테이블에 행이 들어간다.**

### 25.1 분할 저장 — 13MB → 0.6MB

`chunk` 가 레코드를 `CHUNKS_PER_ARTIFACT`(1,000) 개씩 나눠 `seq 0,1,2…` 로 저장하고
각 행에 `total_parts` 를 적는다. `load` 는 **part 하나만** 읽어 upsert 하고 남았으면
다음을 큐에 넣는다.

| 문서 | 청크 | part | part 당 payload |
|---|---|---|---|
| 삼성 | 8,477 | 9 | 4.4MB → **0.7MB** |
| SK | 25,831 | 26 | 약 13MB → **0.6MB** |

1,000 개로 잡은 근거는 실측 청크당 약 500B 다. Edge 메모리 상한 240MB 대비 여유가 크다.

### 25.2 `_serialize_chunk` — 키를 넣느냐 마느냐가 계약이다

이 함수가 틀리면 DB 에 잘못된 행이 들어가는데 **대개 조용하다.**

- `bbox` · `dense_vec` · `char_range` · `id` 는 **값이 있을 때만** 넣는다.
  `dense_vec` 을 `null` 로 명시하면 `embed` 가 채워 둔 벡터를 `load` 재실행이 **지운다.**
- `sparse_json` · `metadata` · `flags` 는 **빈 값이라도 반드시** 넣는다.
  원본 주석 그대로 "직전 레코드 flags 가 잔존하지 않도록" 이다.
- `char_range` 는 `INT4RANGE` 라 `"[start,end)"` **문자열**이다. 끝이 열린 구간이라
  `]` 가 아니라 `)` 다.

대조 20 건 전부 일치. 음성 대조 12 종 전부 깨짐 확인 — 특히 `id` 를 truthy 대신
`!= null` 로 바꾸면 **빈 문자열 `chunk_id`** 케이스가 갈린다.

`load` 는 upsert 를 다시 `chunk_upsert_batch_size`(기본 50)로 쪼갠다. 이유도 원본 주석
그대로 Supabase `statement_timeout`(약 30~60s) 안에 들어가야 해서다.

### 25.3 E2E — 실제 `chunks` 에 들어갔는지 본다

| 문서 | 페이지 | 드레인 | 청크 | part | **적재** | 시간 |
|---|---|---|---|---|---|---|
| law sample3 | 4 | 4 | 26 | 1 | 26/26 | 2.0s |
| 삼성 사업보고서 | 573 | 68 | 8,477 | 9 | 8,477/8,477 | 42.5s |
| **SK 사업보고서** | **1,513** | **179** | **25,831** | **26** | **25,831/25,831** | **116.0s** |

개수만 보지 않는다 — 첫 행을 실제로 열어 `char_range`("[0,311)") · `page` ·
`section_title` · `text` 를 산출물과 대조한다. 개수만 맞고 내용이 비면 소용없다.

### 25.4 E2E 격리를 다시 짰다 — 운영 청크를 덮어쓸 뻔했다

`load` 가 붙으면서 `chunks` **쓰기**가 생겼다. upsert 키가 `doc_id,chunk_idx` 라
**원본 문서 id 로 돌리면 운영 청크를 통째로 덮어쓴다.**

그래서 대상 문서를 그대로 쓰지 않는다:

- 샌드박스 `user_id`(`ingest_sandbox.py` 와 같은 uuid5 네임스페이스)로 **복제
  `documents` 행**을 만들고 그 doc_id 로 돌린다.
- 원본 `documents` 행과 Storage 파일은 **읽기만** 한다(경로만 재사용).
- 끝나면 복제 문서를 지운다 → `chunks` 가 `ON DELETE CASCADE` 로 함께 사라진다.
- 지우기 직전에 `user_id` 가 샌드박스인지 **한 번 더 확인**한다. 원본을 지우는 사고를
  막는 마지막 관문이다.

실행 전후 운영 무손상 확인:

```
chunks 37,080 → 37,080   documents 13 → 13   큐 0   artifacts 0
```

### 25.5 검증

| 항목 | 결과 |
|---|---|
| Deno `_shared/` 전체 | **157 passed / 0 failed** (load 7 · chunk 분할 3 신규) |
| `chunkRecordToRow` 대조 | 20 건 일치, 음성 대조 12 종 |
| 파이프라인 대조 | FAIL 0 (알려진 차이 8 건 그대로) |
| E2E | 3 문서, 최대 SK 25,831 청크 적재 |

### 25.6 커밋

| 해시 | 내용 |
|---|---|
| `822cb67` | load 이식 + chunk 분할 저장 + E2E 격리 재설계 |

### 25.7 남은 것

인제스트 사슬은 이제 `extract → chunk → load` 까지 돈다. 다만 **`chunks.dense_vec` 이
NULL 이라 검색은 아직 안 된다** — `embed` 가 없다. 그래서 잡을 `completed` 로 만들지
않고 사실대로 running 에 둔다.

| 항목 | 상태 |
|---|---|
| `embed` — BGE-M3 임베딩 (검색이 되려면 필수) | ⬜ |
| pg_cron 드레인(마이그 028) · Edge 배포 | ⬜ |
| HWPML / hwpx / docx / pptx extract | ⬜ |
| chunk 조각 c2 (`synonym_inject`) | ⬜ |
| chunk_filter / content_gate / tag_summarize / doc_embed / dedup | ⬜ |
| `api-documents` HTTP 경로 | ⬜ |


## 26. Phase 3 — Edge 배포 + pg_cron 자동 드레인. **사슬이 스스로 돈다**

§25 까지 만든 4 개 핸들러는 **한 번도 Edge 에서 안 돌았다.** Phase 0 이 남긴 교훈이
"로컬 Deno 통과는 Edge 통과의 근거가 아니다" 였고(그때 `@ohah/hwpjs` 가 로컬은 되고
Edge 에서 죽었다), 그 관문을 여기서 통과했다. 그리고 pg_cron 을 붙여 자동화했다.

### 26.1 mupdf WASM 이 Edge 에서 돈다

E2E 스크립트에 `--edge` 를 넣어 **드레인만** 배포된 함수에 HTTP 로 시켰다. 준비·검증·
정리는 그대로 로컬이다.

| 문서 | 페이지 | 요청 | 청크 | 적재 | 시간 |
|---|---|---|---|---|---|
| law sample3 | 4 | 3 | 26 | 26/26 | 4.3s |
| sample-report (9MB, 이미지 다수) | 93 | 12 → **6** | 899 | 899/899 | 19.9s |

CPU 초과 없음. 콜드 스타트 4.3s.

### 26.2 예산 기반 반복 드레인 — 12 요청이 6 요청이 됐다

작업이 **순차 의존**이라 `batch` 를 키워도 소용이 없다(다음 작업은 직전 작업이 끝나야
큐에 들어간다). 그래서 한 요청에서 예산이 남는 동안 여러 판을 돈다.

예산은 **wall clock 1,500ms** 다. Supabase 가 재는 건 CPU 시간(I/O 제외)인데 Edge 에
CPU 시계 API 가 없다 — Phase 0 스파이크도 `performance.now()` 근사를 썼다. wall clock 은
DB 왕복을 포함해 실제 CPU 보다 **크므로** 예산 초과 판정이 보수적이다.

**판 도중에는 끊지 않는다.** 핸들러를 중간에 자르면 산출물이 반만 남는다. 테스트로
고정했다(예산 0 이어도 첫 판은 끝까지).

### 26.3 STEP 0 이 또 가정을 잡았다

026 에서 `pgmq.send` 가 `SETOF bigint` 인 걸 STEP 0 이 잡아냈다. 이번에도 하나 틀렸다.

| 항목 | 내 가정 | 실제 |
|---|---|---|
| Edge 안의 `SUPABASE_SERVICE_ROLE_KEY` | 로컬과 같은 219 자 legacy JWT | **41 자 `sb_secret_…`** |

cron 은 **Edge 안의 키**를 쓰게 된다. 내가 로컬에서 통과시킨 건 legacy JWT 였으므로
`verify_jwt=true` 게이트웨이가 새 형식을 받는지는 **따로 확인해야 했다** — 일회용
함수가 자기 키로 `/drain` 을 직접 불러 **HTTP 200** 을 확인했다.

`pg_cron 1.6.4` 의 `'10 seconds'` 문법도 시그니처로는 알 수 없어 더미 잡으로 실측했다
(`ok: true`). `net.http_post` 인자 · `vault.create_secret` 시그니처 ·
`vault.decrypted_secrets` 컬럼도 조회로 확정했다.

### 26.4 설계 — 큐가 비면 부르지 않는다

10 초마다 무조건 부르면 하루 8,640 번 Edge 인보케이션을 태운다. 대부분은 빈 큐다.
`ingest_drain_tick()` 이 먼저 `pgmq.q_ingest_tasks` 를 세고 **0 이면 `NULL` 로 끝낸다.**

키 취급:

- service_role 키를 마이그 파일에 적지 않는다. `supabase_vault`(0.3.1)에 이름으로 둔다.
- **저장도 사람이 붙여넣지 않았다.** 일회용 함수가 자기 `Deno.env` 값을 그대로 넣어서
  키가 대화·로그·커밋 어디에도 남지 않는다. 응답에는 **길이만** 실었다(41 자 / 77 자).
- `ingest_drain_tick()` 은 `postgres`·`service_role` 만 EXECUTE. `anon`/`authenticated`
  회수 — 026 의 교훈(`REVOKE FROM PUBLIC` 만으로는 안 막힌다)을 따랐다.

### 26.5 적용 경로 — 일회용 함수, 쓰고 버린다

026·027 과 같다. 로컬에 SQL 실행 경로가 없어(PostgREST DDL 불가 · PAT 없음 · psycopg
미설치) `SUPABASE_DB_URL` 직결 함수를 잠깐 띄워 STEP 0 → 0.5 → 1 → 2 → 3 을 돌리고
**삭제했다**(HTTP 404 확인, 함수 목록에서도 사라짐). 그 함수는 service_role key 보다
강한 권한이라 오래 두면 안 된다.

### 26.6 검증 — cron 이 실제로 처리한다

`--cron` 모드를 넣어 스크립트가 **큐에만 넣고 기다리게** 했다.

```
보건의료(26p)  →  18.4초  extract 3 + chunk + load  →  chunks 149/149 적재
```

`net._http_response` 증거:

```
200 {"read":3,"ok":3,…,"rounds":3,"elapsedMs":2124}
200 {"read":2,"ok":2,…,"rounds":2,"elapsedMs":1936}
```

예산 반복이 cron 경로에서도 동작한다 — **5 태스크를 2 요청으로** 처리했다.
`elapsedMs` 가 예산 1,500ms 를 넘긴 것은 "판 도중에는 끊지 않는다" 규칙대로다.

`cron.job_run_details` 5 건 전부 `succeeded`(2ms), 빈 큐에서는 http 호출 **0**.

### 26.7 지금부터 달라지는 것

**큐에 작업이 들어가면 자동으로 처리된다.** 앞으로 E2E 를 로컬/`--edge` 모드로 돌리면
cron 과 경합할 수 있다(pgmq visibility timeout 이 중복 처리는 막지만, 어느 쪽이 처리했는지
헷갈릴 수 있다). `--cron` 모드가 기본 확인 경로다.

아직 **`chunks.dense_vec` 이 NULL 이라 검색은 안 된다** — `embed` 가 없다. 그래서 잡을
`completed` 로 만들지 않고 running 에 둔다.

### 26.8 커밋

| 해시 | 내용 |
|---|---|
| `6492599` | 마이그 028 운영 적용 (pg_cron 자동 드레인) |
| `4a21ddf` | Edge 배포 + 예산 기반 반복 드레인 |

### 26.9 남은 것

| 항목 | 상태 |
|---|---|
| `embed` — BGE-M3 임베딩 (**검색이 되려면 필수**) | ⬜ |
| HWPML / hwpx / docx / pptx extract | ⬜ |
| chunk 조각 c2 (`synonym_inject`) | ⬜ |
| chunk_filter / content_gate / tag_summarize / doc_embed / dedup | ⬜ |
| `api-documents` HTTP 경로 (업로드 → 큐 투입) | ⬜ |
| `/payments` · `/billing` · `/email` (Phase 4~5) | ⬜ |


## 27. Phase 3 — `embed` 이식. **사슬이 `dense_vec` 까지 채운다**

`extract → chunk → load → embed` 가 pg_cron 으로 자동으로 돈다. 이제 인제스트된 문서가
dense 검색 대상이 된다.

### 27.1 어댑터 — 재시도 정책이 핵심이다

운영 ENV 가 `JETRAG_EMBED_PROVIDER=deepinfra` 라 그쪽만 옮겼다(HF 경로는 v1.5 W-1 에
always-warm 인 DeepInfra 로 갈아탔다). 원본 정책을 그대로 가져왔다:

- 3 회, transient(네트워크·429·5xx)만, backoff `5s × 2^(n-1) + jitter`
- `Retry-After` 헤더가 있으면 그걸 쓰되 **60s 로 자른다**(악의적 헤더 방어)
- **401·파싱 실패는 재시도하지 않는다** — 다시 불러도 같고 쿼터만 태운다

배치 응답은 `index` 로 정렬한다. 순서가 틀리면 **엉뚱한 청크에 벡터가 박히고** 검색이
조용히 망가진다.

### 27.2 핸들러 — offset 을 쓰지 않는다

`dense_vec IS NULL` 은 **처리하면서 사라지는 조건**이다. offset 을 들고 다니면
건너뛰는 청크가 생긴다. 그래서 매번 **NULL 인 앞쪽 64 개**를 집는다 — 자연히 멱등이고
재시도해도 이미 채운 것을 다시 부르지 않는다.

`upsert` 가 아니라 **단건 UPDATE** 다. 원본 주석 그대로 — upsert 는 보내지 않은 컬럼을
NULL 로 처리해 `chunks.doc_id` NOT NULL 위반이 관찰된 적이 있다.

### 27.3 **BGE-M3 는 비결정적이다** — 대조 기준을 두 번 고쳤다

실 API 대조를 짰는데 "불일치 3 건" 이 나왔다. 두 번 다 **대상이 아니라 자[尺]가**
문제였다.

**① 로컬 `.env` 에 `JETRAG_EMBED_PROVIDER` 가 없었다.** 사용자가 알려준 `deepinfra` 는
운영(Railway·Edge) 값이고 로컬엔 없다. 그래서 Python 이 기본값 `hf` 로 가서 **HF vs
DeepInfra** 를 비교했다. 서로 다른 서비스니 다른 게 당연하다.

**② provider 를 맞춰도 편차가 남았다.** Python 을 두 번 불러 봤더니 **같은 provider 로도
1.494e-04** 가 흔들린다. 프로젝트가 이미 겪은 성질이다 — work-log 2026-05-12:

> HF BGE-M3 embed query API 비결정성 (모델 서버 인스턴스·배치·fp 정밀도 차이 →
> dense query 벡터 미세 변동 → dense_rank → RRF 재정렬 전파). **회귀 아님.**
> 같은 세션 내 baseline↔f110 은 byte-identical — **세션 내 결정적, 세션 간 비결정적.**

그래서 절대 일치가 아니라 **서비스 흔들림 폭 대비**로 판정하도록 바꿨다:

| | 문장 0 | 문장 1 | 문장 2 |
|---|---|---|---|
| py↔py 기준선 (서비스 비결정성) | 1.42e-04 | 1.56e-04 | 1.53e-04 |
| **py↔ts** | 1.44e-04 | 1.53e-04 | 9.52e-05 |
| 한도 (기준선 × 3) | 4.26e-04 | 4.67e-04 | 4.58e-04 |
| 코사인 유사도 | 0.99999936 | 0.99999935 | 0.99999947 |

py↔ts 편차가 **기준선과 같은 수준**이다. 같은 경로를 탄다.

> 편차만 보면 방향이 틀어져도 통과할 수 있어 코사인 유사도를 같이 본다.

### 27.4 검증

| 항목 | 결과 |
|---|---|
| Deno `_shared/` 전체 | **189 passed / 0 failed** (embed_provider 19 · embed 핸들러 8 신규) |
| 순수 함수 대조 | `parseRetryAfter` 20 건 · `parseBatchResponse` 9 건 |
| 실 API 대조 | 3 문장 (비용 최소 — **문장을 늘리지 말 것**) |
| E2E (pg_cron 자동) | law sample3 → chunks 26/26, **dense_vec 26/26**, 33.6s |

### 27.5 커밋

| 해시 | 내용 |
|---|---|
| `50d69dd` | embed 이식 (어댑터 + 핸들러 + 대조 2종) |

### 27.6 남은 것

| 항목 | 상태 |
|---|---|
| `api-documents` HTTP 경로 (업로드 → 큐 투입) | ⬜ — **지금은 사람이 큐에 넣어야 시작된다** |
| `tag_summarize` · `doc_embed` (문서 제목·요약·문서 벡터) | ⬜ |
| `chunk_filter` · `content_gate` · `dedup` | ⬜ |
| HWPML / hwpx / docx / pptx extract | ⬜ |
| chunk 조각 c2 (`synonym_inject`) | ⬜ |
| `/payments` · `/billing` · `/email` (Phase 4~5) | ⬜ |


## 28. Phase 4 — `POST /documents` 업로드. **사슬의 입구를 열었다**

§27 까지 만든 사슬은 **입구가 막혀 있었다** — 큐에 작업을 사람이 넣어야 시작됐다.
이제 파일을 올리면 `업로드 → extract → chunk → load → embed` 가 끝까지 자동으로 돈다.

### 28.1 Railway 와 흐름이 다르다 — BackgroundTasks 가 없다

| | 원본(Railway) | Edge |
|---|---|---|
| `documents.storage_path` | `pending/<uuid><ext>` placeholder | **처음부터 final path** |
| Storage 업로드 | 응답 후 BG task | **응답 전에 끝낸다** |
| 파이프라인 | BG task 가 8 단계 직접 | 큐에 `extract` 투입 → pg_cron |

`pending/` 을 안 쓰는 게 오히려 낫다. extract 핸들러가 "storage_path 가 아직 pending"
이면 던지고 재시도하는데(§24), **그 경합 자체가 사라진다.**

응답 시간은 실측 **1,293ms**(0.26MB)로 원본 SLO(수신 ≤2초) 안에 들어온다.

### 28.2 프록시가 메서드를 보게 했다

`/documents` 는 **같은 경로에 GET(목록)과 POST(업로드)** 가 있다. 경로만 보고 열면
아직 Railway 인 목록이 405 로 깨진다. `/admin/subscriptions` 때 미뤄 둔 문제다.

`ROUTES` 항목에 선택적 메서드 집합을 더하고 `resolveTarget(pathname, method)` 로 확장했다:

```js
[/^\/documents\/?$/, "api-documents", new Set(["POST"])],
```

**메서드를 모르면 넘기지 않는다.** 모른 채 넘겨서 GET 이 405 를 받는 쪽이 더 나쁘다.
기존 규칙(메서드 제한 없음)은 그대로 동작하고, 이관 선언 가드도 메서드까지 보도록 넓혔다.

### 28.3 입력 게이트 — 대조가 결함 1건을 잡았다

목적은 **"exe 가 .docx 로 위장" 차단**이다. `filetype` 라이브러리 전체를 옮기지 않았다 —
판정이 "허용 목록에 드는가" 뿐이라 **허용되는 것만 정확히 인식하고 나머지는 `null` 로
두면 결과가 같다.** 실측으로 확인했다(exe as .pdf: py `application/x-msdownload` /
ts `null` — **둘 다 거절**).

그런데 `mif1`/`msf1` 이면 `image/heif` 라는 분기를 **추론으로** 넣었다가 대조가 잡았다:

```
HEIC major=mif1 brand 없음   py={"ok": false, "status": 400}  ts={"ok": true}
```

`filetype` 1.2.0 에는 **`image/heif` 타입이 아예 없다.** `Heic` 매처 하나뿐이고
`mif1`/`msf1` 은 compatible_brands 에 `heic` 가 있을 때만 매치한다.
`_EXT_TO_MIMES[".heic"]` 의 `"image/heif"` 는 **도달 불가 값**이었다.
짐작으로 넓혔으면 위장 파일이 통과했다.

### 28.4 검증

| 항목 | 결과 |
|---|---|
| Deno `_shared/` 전체 | **202 passed / 0 failed** (upload 13 신규) |
| `validateMagic` 대조 | 40 건 (통과 21 / 거절 19) — 실자산 + 위장 + 교차 + HWPML + HEIC 경계 |
| 프록시 | 20 passed — `POST /documents` 만 Edge, GET·하위경로는 Railway |
| HTTP 층 | 인증없음 **401** · GET **405** · 없는경로 **404** · service_role **401**(사용자가 아니다) |
| **사슬 E2E** | 업로드 202 **1,293ms** → Storage 264,473B 크기 일치 → pg_cron 자동 → 청크 26 · **dense_vec 26/26** |
| 운영 무손상 | documents 13 · chunks 37,080 · 큐 0 · artifacts 0 |

### 28.5 한계 — 못 잰 것

- **완전한 HTTP E2E(로그인 → 업로드)는 못 했다.** 사용자 자격증명이 필요하다. HTTP 층은
  curl 로, 그 아래는 `handleUpload` 직접 호출로 나눠 검증했다. 즉 **실제 브라우저 토큰
  경로와 rate limit 발화는 미검증**이다(코드는 `/answer` 와 같은 `_shared` 를 쓴다).
- 업로드 SLO 는 **0.26MB 에서만** 쟀다. 큰 파일은 임베딩 비용 때문에 안 했다.

### 28.6 커밋

| 해시 | 내용 |
|---|---|
| `ee94432` | `POST /documents` 업로드 + 프록시 메서드 인지 |
| `21fd38a` | 입력 게이트(확장자·매직바이트) |

### 28.7 프록시 배포 — 실측으로 확인

사용자 승인 후 배포했다. 헤더로 백엔드를 구분해 전수 확인했다
(`x-served-by: supabase-edge-runtime` vs `x-railway-request-id`).

| 요청 | 결과 |
|---|---|
| `POST /documents` | 401 **Edge** |
| `POST /documents/` | 401 **Edge** (trailing slash) |
| `GET /documents` | 200 Railway (목록 — 아직 이관 안 함) |
| `PUT /documents` | 405 Railway |
| `POST /documents/url` | 401 Railway |
| `GET /documents/active` | 200 Railway |
| `GET /documents/batch-status` | 422 Railway |
| `POST /documents/{id}/reingest` | 401 Railway |
| `POST /documentsfoo` | 404 Railway |

기존 전환분 회귀 없음 — `/health` 200 Edge, `/answer/feedback` 401 Edge.
`/me/usage` 404 와 `/search/eval-precision` 404 는 **원본에 없는 경로**라 정상이다
(후자는 §1~9 의 RAGAS 폐기로 제거됐다).

> 배포 직후 첫 확인에서 `POST` 가 Railway 로 갔다. **전파 지연**이었고 20 초 뒤 정상이다.
> 배포 직후 한 번만 보고 판단했으면 "라우팅이 안 걸린다" 고 잘못 결론 냈을 것이다.

### 28.8 배포 중 발견 — `GET /documents` 가 인증 없이 목록을 준다

이번 작업과 무관한 **기존 Railway 동작**이다. 내가 만든 것도, 악화시킨 것도 아니지만
확인했으니 적어 둔다.

```
$ curl -s "https://jetrag-api.woong-s.com/documents?limit=3"   # 토큰 없음
{"total":13,"items":[{"title":"25년케이터링제이(한국은행)…"},
                     {"title":"[SK]사업보고서(2026.03.18)"}, …]}
```

실측:
- 운영 `documents` 13 건이 **전부 한 user_id**(`2af8fca5…`) 소유다.
- 인증 없이 부르면 그 **13 건 전부**가 제목·태그까지 나온다.
- `list_documents` 에는 `require_authenticated_user` 가 걸려 있지 않다
  (`POST` 에는 걸려 있다 — "쓰기 = 로그인 필수(수익화 W1)").

**확인 결과 — 의도된 설계다(수익화 W1 "데모 병행").** 위 미검증 항목을 `get_current_user`
를 읽어 확정했다.

```python
# app/auth/dependencies.py — get_current_user
"""호출자 식별 — 데모 병행 3-way 분기 (수익화 W1).
- auth_enabled=false: 로컬 dev / single-user
- 토큰 없음: **익명 데모 — owner 문서 read-only** (쓰기는 require_authenticated_user 가 차단)
- 토큰 있음: JWT 검증 → 본인 격리 컨텍스트
"""
if token is None:
    return CurrentUser(
        user_id=settings.owner_user_id or settings.default_user_id,
        is_authenticated=False,          # ← 쓰기 게이트가 이 값을 본다
    )
```

즉 로그인 없이 서비스를 체험하도록 **owner 문서를 읽기 전용으로 열어 둔 기능**이다.
확인한 것:

| 항목 | 결과 |
|---|---|
| 목록이 사용자별로 격리되는가 | `list_documents` 가 `.eq("user_id", current_user.user_id)` — **격리된다** |
| 익명이 보는 것 | owner 문서 **뿐**. 다른 사용자 문서는 안 보인다 |
| 익명 쓰기 | `POST /documents` **401** (실측) |
| Edge 이식본도 같은가 | `_shared/current_user.ts` 가 동일 — `ownerUserId || defaultUserId`, `isAuthenticated: false`, `requireAuthenticatedUser` 가 쓰기 차단 |

**보안 결함이 아니다.** §28.8 을 처음 적을 때 "판단이 필요하다" 고 한 것은 코드를 안 읽고
현상만 봤기 때문이다 — 설계 의도가 docstring 에 적혀 있었다. 이슈로 올리기 전에 그걸
먼저 읽었어야 했다.

이 확인의 실질 소득: **`/documents` 나머지 라우트를 이식할 때 인증을 그대로 옮기면 된다.**
바꿀 이유가 없다.

### 28.9 남은 것

| 항목 | 상태 |
|---|---|
| ~~`GET /documents` 무인증 노출 판단~~ | ✅ 의도된 데모 병행 설계 — 28.8 |
| `/documents` 나머지 8 라우트 (목록·상세·삭제·재인제스트·URL) | ⬜ |
| `tag_summarize` · `doc_embed` | ⬜ |
| `chunk_filter` · `content_gate` · `dedup` | ⬜ |
| HWPML / hwpx / docx / pptx extract | ⬜ |
| chunk 조각 c2 (`synonym_inject`) | ⬜ |
| `/payments` · `/billing` · `/email` (Phase 4~5) | ⬜ |


## 29. Phase 4 — `/documents` 읽기 3종. **응답 대조가 결함 3건을 잡았다**

`GET /documents`(목록) · `GET /documents/{id}`(상세) · `GET /documents/{id}/status`.

### 29.1 읽기에는 인증 게이트를 걸지 않는다

§28.8 에서 확인한 대로 토큰이 없으면 `getCurrentUser` 가 owner 컨텍스트
(`isAuthenticated: false`)를 준다 — 수익화 W1 "데모 병행" 설계다.
쓰기(`POST`)에만 `requireAuthenticatedUser` 를 건다. **원본을 그대로 옮겼다.**

### 29.2 함수 대조가 아니라 **응답 대조**를 했다

이 라우트들은 DB 조회와 조립이 전부다. 함수 단위로 맞춰 봐야 "내가 고른 입력에서 같다"
는 증거뿐이고, 진짜 위험은 **응답 스키마의 미세한 차이**다. 그래서 Railway 와 Edge 를
같은 요청으로 두들겨 **JSON 을 통째로** 비교했다. 결함 3 건이 나왔다.

**① `JobStatus` 에 필드 2 개가 빠졌다**

```
railway …"error_msg": null, "estimated_remaining_ms": null, "finished_at": …
edge    …"error_msg": null,                                 "finished_at": …
```

원본 `JobStatus` 에는 `estimated_remaining_ms` 와 `stage_progress` 가 있고, 이
라우트들은 그 둘을 안 넘겨 pydantic 기본값 `None` 이 들어간다(실제 값은 `/active` 만
채운다). **키가 없는 것과 `null` 인 것은 다르다** — 프런트가 구조분해하면 갈린다.

**② 422 본문이 FastAPI(pydantic v2) 형식이어야 한다**

```
railway {"detail":[{"type":"greater_than_equal","loc":["query","limit"],
                    "msg":"Input should be greater than or equal to 1",
                    "input":"0","ctx":{"ge":1}}]}
edge    {"detail":"limit 는 1~100 범위여야 합니다."}     ← 내가 지어낸 문구
```

프런트가 `detail[].loc` 이나 `type` 으로 분기할 수 있어 지어내면 안 된다. 실측해
`fixtures/documents_422_measured.json` 에 떠 뒀다. **오류가 여러 개면 전부 담는다** —
`?limit=0&offset=-1` 은 항목이 2 개다(실측).

**③ bool 쿼리를 느슨하게 파싱했다**

`include_failed=maybe` 는 원본이 **422** 인데 나는 `false` 로 넘겼다. 유효값을
실측했다:

| 200 (유효) | 422 (무효) |
|---|---|
| `true/True/TRUE · false/False · 1/0 · yes/no · on/off · y/n · t/f` | `""` · `2` · `maybe` |

`include_logs` 도 같은 bool 이라 같이 고쳤다. 처음엔 오류를 버리고 있었다.

### 29.3 프록시 — `{doc_id}` 가 `/active` 를 삼키지 않게

```js
[/^\/documents\/(?!active$|batch-status$)[^/]+$/, "api-documents", new Set(["GET"])],
```

부정 전방탐색으로 제외했다. `/search` 를 접두어로 열었다가 `/search/eval-precision` 을
삼킨 사고(5a74ea6)와 같은 실수를 막는다. 프록시 테스트가 이걸 고정한다.

> "미이관 경로는 기존 백엔드로" 테스트 3 건이 깨졌다. 예시로 쓰던 `/documents` 가
> 이관돼서다 — 주석에 "옮기고 나면 깨지므로 예시를 바꾸면 된다"고 적혀 있던 그대로다.
> 아직 Railway 인 `/documents/active` 로 바꿨다.

### 29.4 검증

| 항목 | 결과 |
|---|---|
| 응답 대조 | **25 건 전부 일치** (200 ×12 · 404 ×2 · 422 ×10 · 500 ×1) |
| Deno `_shared/` | 202 passed / 0 failed |
| 프록시 | 20 passed / 0 failed |
| 배포 후 실측 | 이관 3 종 **Edge**, `/active`·`batch-status`·`url`·`reingest`·`DELETE` 는 **Railway** |

`/documents/not-a-uuid` 가 양쪽 다 **500** 이다. 잘못된 UUID 로 DB 를 조회해서 나는
원본 동작이라 그대로 재현됐다 — 고치려면 원본을 고쳐야 한다.

### 29.5 커밋

| 해시 | 내용 |
|---|---|
| `37365ee` | `/documents` 읽기 3종 + 프록시 규칙 |

### 29.6 남은 것

Railway 에 남은 `/documents` 라우트는 **5 개**다(9 개 중 4 개 이관).

| 항목 | 상태 |
|---|---|
| `GET /documents/active` · `batch-status` | ⬜ — `stage_progress` 동적 컬럼 + 잔여시간 추정이 얽혀 있다 |
| `POST /documents/url` | ⬜ — URL 파서 필요 |
| `POST /documents/{id}/reingest` · `reingest-missing` | ⬜ |
| `tag_summarize` · `doc_embed` | ⬜ |
| `chunk_filter` · `content_gate` · `dedup` | ⬜ |
| HWPML / hwpx / docx / pptx extract | ⬜ |
| chunk 조각 c2 (`synonym_inject`) | ⬜ |
| `/payments` · `/billing` · `/email` (Phase 4~5) | ⬜ |


## 30. Phase 4 — `/documents/active` · `batch-status`. **`/documents` 의 GET 이 전부 넘어갔다**

프런트 폴러가 주기적으로 부르는 두 라우트다. `/ingest` 페이지의 진행 카드와 문서 목록
상태 갱신이 여기에 달려 있다. 이걸로 `/documents` 의 **GET 5 개가 전부** Edge 다.

### 30.1 ETA 를 통째로 옮겼다 (`eta.py` 325 줄)

`estimated_remaining_ms` 를 `null` 로 고정하면 프런트의 "약 N분 N초 남음" 이 죽는다 —
**기능 손실**이라 넘길 수 없었다. `null` 자체도 의미 있는 값이다(cold start 시 web 이
"처음에는 시간 추정이 부정확합니다" 카피로 분기한다).

Python 함정 3 개를 맞췄다:

| 함정 | 대응 |
|---|---|
| `statistics.median` — 짝수면 두 중간값 평균, `sort()` 는 문자열 정렬 | 직접 구현 + 비교 함수 (§23 에서 겪은 것과 같다) |
| `int()` 는 truncate, **`round()` 는 은행가 반올림** | `Math.trunc` / `bankersRound` (percentile 랭크) |
| `isinstance(duration, int)` | `Number.isInteger` — 실수 duration 은 표본에서 빠진다 |

캐시는 Edge 인스턴스 수명만큼만 산다. 원본은 프로세스 전역 90 초 TTL 이라 적중률이
낮아지지만 **결과는 같고 DB 조회가 잦을 뿐**이다.

### 30.2 옮기지 않은 것 — `stage_progress` fallback

원본에는 그 컬럼이 없는 환경(마이그 010 미적용)을 위한 1 회 재시도 fallback 이 있다.
**운영 DB 에 컬럼이 있는 것을 실측했다.** 죽은 경로를 옮기면 그게 도는지 아무도 확인
못 한다 — 컬럼이 사라지면 조용히 넘기지 말고 500 으로 드러나는 편이 낫다. 근거를 코드
주석에 남겼다.

### 30.3 `status` 를 SQL 에서 거르지 않는다

원본 주석 그대로다. 거르면 같은 doc 에 "어제 failed + 오늘 completed" 가 있을 때
`failed` 만 뽑혀 **완료된 문서가 계속 진행 중으로 보인다.** 전부 가져와 `doc_id` 별
latest 만 남기고, 그 status 가 active 인 것만 응답한다.

### 30.4 응답 대조로는 ETA 를 검증할 수 없었다

응답 대조 39 건이 전부 통과했는데, **`/active` 가 `{"items":[]}` 였다.** 진행 중인
잡이 없어서다. 즉 방금 옮긴 325 줄이 **한 번도 안 돌았다.** 그 상태로 "이식 완료" 라고
하면 안 된다.

그래서 같은 DB·같은 입력으로 `compute_remaining_ms` 를 직접 대조했다:

| 대상 | 케이스 | 결과 |
|---|---|---|
| `median` | 8 | 일치 |
| `percentile` | 8 (은행가 반올림 경계 포함) | 일치 |
| `computeRemainingMs` | 30 | 일치 |

결과 분포가 **None 3 / 값 27, 서로 다른 값 10 가지** — 분기가 실제로 태워졌다는 증거다
(전부 `None` 이거나 값이 다 같으면 케이스 무효로 처리하도록 검사도 넣었다).

케이스는 queued/running/completed, 각 stage, vision 분해(`unit='pages'`), 일반
sub-progress 비율, 경계(`total<=0` · `current>total` · 문자열 값 · 모르는 stage)를 태운다.

### 30.5 검증

| 항목 | 결과 |
|---|---|
| 응답 대조 | **39 건 전부 일치** (200 ×20 · 400 ×2 · 404 ×2 · 422 ×14 · 500 ×1) |
| ETA 대조 | median 8 · percentile 8 · `computeRemainingMs` 30 |
| Deno `_shared/` | 202 passed / 0 failed |
| 프록시 | 20 passed / 0 failed |
| 배포 후 실측 | `active`·`batch-status` **Edge**, `url`·`reingest` **Railway** |

> 프록시 "미이관 예시" 테스트가 또 깨졌다(§29 에 이어 두 번째). `/documents/url` 로
> 바꿨는데 `req()` 기본이 GET 이라 새 `GET /documents/{doc_id}` 규칙에 걸렸다 —
> **메서드를 명시**해야 했다. 예시 경로를 고를 때 메서드까지 봐야 한다.

### 30.6 커밋

| 해시 | 내용 |
|---|---|
| `ccdde7f` | `/documents/active` + `batch-status` + ETA 이식 |

### 30.7 남은 것

`/documents` 9 개 중 **6 개 이관**. 남은 3 개는 전부 쓰기다.

| 항목 | 상태 |
|---|---|
| `POST /documents/url` | ⬜ — URL 파서 이식 필요 |
| `POST /documents/{id}/reingest` · `reingest-missing` | ⬜ |
| `tag_summarize` · `doc_embed` | ⬜ |
| `chunk_filter` · `content_gate` · `dedup` | ⬜ |
| HWPML / hwpx / docx / pptx extract | ⬜ |
| chunk 조각 c2 (`synonym_inject`) | ⬜ |
| `/payments` · `/billing` · `/email` (Phase 4~5) | ⬜ |

---

## 31. Phase 4 — vision 이식 (1) 판정·캡셔너·입력 이미지

`reingest` 를 열려다 막혔다. 재인제스트는 vision 유래 청크 213 개(6 문서)를 지우고
다시 만드는데, vision 이 안 옮겨져 있으면 **그게 그냥 사라진다.** 그래서 vision 을 먼저 했다.

운영 ENV 를 확인했다 — `JETRAG_PDF_VISION_ENRICH=true`. **켜져 있는 기능이다.**

### 31.1 `vision_need_score` (`5f9ee89`)

어느 페이지에 vision 을 태울지 정하는 OR 규칙 5 종. `entity` 는 가중치 0 으로 규칙에서
**일부러 빠져 있다**(deprecated). 합성 53 건 + 실자산 154 페이지 전부 일치, 트리거 5 종
모두 발화.

### 31.2 `vision_caption` (`3184e70`)

Gemini 응답 파싱 + 단가. 파싱 36 건 + 거부 6 건 + 단가 9 건 일치.

단가를 기억으로 적었다가 `gemini-2.5-pro`(표에 없는 모델)를 지어내고 2.0 계열 3 종을
빠뜨렸다. 그대로 뒀으면 2.0 모델에서 fallback 단가가 적용돼 `estimated_cost` 가 3~6 배
부풀었다. **원본 `factory._GEMINI_PRICING` 을 보고 옮겼다.**

### 31.3 입력 이미지 생성 (`66105d8`) — 여기가 제일 까다로웠다

vision 은 150 DPI PNG 를 **그대로 보내지 않는다.** Pillow 로 단변 1024px LANCZOS
축소 후 JPEG q85 로 다시 구운 바이트를 넘긴다. Gemini 는 비결정적이라 "이미지가 좀
달라도 캡션이 같은가" 를 **사후에 측정할 수 없다.** 그래서 모델 입력을 원본과 같게
만들어 질문 자체를 없앴다.

후보 3 개를 실측으로 비교했다.

| 후보 | 크기 | 원본 대비 | 판정 |
|---|---|---|---|
| `mupdf.Image#toPixmap(w,h)` | 요청 무시, 원본 크기 반환 | — | 탈락 |
| 목표 배율로 직접 렌더 | 높이 ±1 | PSNR 26dB | 탈락 |
| **150dpi 렌더 + Pillow LANCZOS 포팅** | 정확 일치 | **바이트 완전 일치** | 채택 |

`Resample.c` 를 그대로 옮겼다 — 2 패스, **8 비트 중간값**(float 로 쭉 계산하면 갈린다),
22 비트 고정소수점 반올림. 12/12 Pillow 와 바이트 완전 일치, 64~159ms.
음성 대조 12/12 검출 — **440 만 바이트 중 1 바이트 변조도 잡는다.**

래스터화 자체는 8 페이지 중 7 페이지 바이트 완전 일치. 나머지 1 페이지는 표 괘선
1 줄(y=468, 그 영역 텍스트 0 자)만 델타 10/255 — MuPDF 1.27.0 vs 1.27.2 안티앨리어싱.

**알려진 차이 — 크로마 서브샘플링.** Pillow 4:2:0 vs mupdf 4:4:4. `asJPEG(quality,
invertCMYK)` 에 이걸 바꿀 인자가 없다. 다만 수치가 방향을 분명히 말한다:

| | py↔ts 차이 | py↔무손실 | ts↔무손실 |
|---|---|---|---|
| 최소 | 42.9dB | 39.5dB | 40.5dB |
| 최대 | ∞ | 41.9dB | 45.2dB |

인코더 차이가 **양쪽이 공유하는 JPEG q85 손실보다 작고**, 모든 케이스에서 포팅 쪽이
실제 페이지에 더 가깝다. 크기가 같으므로 Gemini 토큰 비용도 동일하다. 맞추려면 JPEG
인코더를 직접 포팅해야 하는데 결과가 더 나쁜 이미지다 — 안 한다.

---

## 32. Phase 4 — vision 이식 (2) 가드·캐시·메트릭

### 32.1 `budget_guard` (`3365248`)

doc / daily / 24h_sliding / page_cap 4 종. DB 조회가 깨지면 통과시키는 원본의 graceful
정책도 그대로. 337 → **343 건 대조 0 불일치**(SUM 만 스텁, 판정·분기·한국어 메시지는
실제 코드).

`pynum.ts` 에 헬퍼 2 개를 추가했다. 둘 다 JS 기본 동작이 **조용히 틀리는** 자리다.

- `pyFloat` — `Number("")` 은 0, `Number("0x10")` 은 16 인데 Python 은 예외다.
  비용 SUM 에 들어가는 값이라 한 건만 어긋나도 한도 판정이 뒤집힌다.
- `pyFormatF` — `toFixed` 는 절반에서 올리고 Python `.4f` 는 짝수 쪽으로 간다
  (`f"{0.00015:.4f}"` = `"0.0001"`). 한도 메시지가 `warnings[]` 로 문서에 남는다.

**대조가 실제 차이 2 건을 잡았다** — `float("٣")` = 3.0(유니코드 십진 숫자),
`float("\x851")` = 1.0(U+0085 NEL 을 JS `\s` 가 공백으로 안 본다). 둘 다 고쳤다.

읽다가 `budget_guard` 의 ISO 생성이 "마이크로초 0 이면 소수부 생략" 규칙을 빠뜨린 것도
발견해 이미 있던 `pyIsoUtc` 로 교체하고 그 분기를 대조에 추가했다.

### 32.2 캐시·메트릭·결과 합성 (`a362c9e`)

`vision_page_cache` 3 튜플 캐시, `vision_usage_log` 적재, `_compose_result`.
174 건 대조 0 불일치.

in-memory 카운터는 **안 옮겼다** — stats 이관 때 이미 DB 기반으로 정리됐고 Edge 는
상주 프로세스가 아니다. fire-and-forget 도 불가(BackgroundTasks 없음)라 await 하지만,
vision 호출 1~3 초 I/O 뒤 수십 ms 고 I/O 는 CPU 예산에 안 잡힌다.

막은 함정: `raw.get("ocr_text") or ""`(빈 배열·빈 dict 가 Python 은 falsy, JS 는 truthy),
`str(v)` vs `String(v)`(컨테이너 표기 `[1, 2]` vs `1,2`).

**검증 도구 결함도 1 건 고쳤다** — 테스트 데이터의 U+0085 가 출력에 실리면 Python
`splitlines()` 가 그걸 줄바꿈으로 쪼갠다. stdout 대신 파일로 주고받게 바꿨다.

---

## 33. **`toPageDict` 의 span 분할이 틀렸다** (`059d945`)

결선 대조를 돌렸더니 Python 이 sample-report 12 페이지를 건너뛰는데 포팅은 안 건너뛰었다.
`needs_vision` 이 갈렸다.

원본을 열어 봤다. 같은 블록 수·줄 수인데 **span 분할이 달랐다** — `< 요약 6/8 > ` 가
PyMuPDF 1 개 / 포팅 4 개. mupdf.js **원시 stext 는 PyMuPDF 와 똑같이 1 개를 준다.**
쪼갠 건 내 `toPageDict` 였다.

글자별로 재 보니 폰트 **포인터**가 달랐다 — `BCDLEE+MalgunGothic` 과
`BCDEEE+MalgunGothic`, 같은 글꼴의 다른 서브셋 인스턴스다. PyMuPDF `JM_make_spanlist`
를 읽어 규칙 전체를 옮겼다: **(정규화 폰트명, size, argb, flags)**. 정규화는
`JM_font_name` 그대로 6 자 + `+` 접두사만 뗀다.

### 33.1 실측 — 151 페이지 표본

| | 수정 전 | 수정 후 |
|---|---|---|
| vision 판정 뒤집힘 | 7/151 | **1/151** |
| span 불일치 | 3,625 | 2,029 |

### 33.2 남은 1 건은 재현 불가다

PyMuPDF 는 `char_flags` 의 **`FZ_STEXT_SYNTHETIC_LARGE`(512, MuPDF 가 넓은 간격을
공백으로 채워 넣었다는 표시)** 와 `bidi` 로도 span 을 가른다. mupdf.js 1.27.0 walk 의
`onChar` 인자는 `(c, origin, font, size, quad, color)` **6 개뿐**이라 그 값이 없다.

문자 폭으로 대신 판정할 수 있는지 공백 18,780 개를 쟀다 — SYNTHETIC_LARGE 는 폭/size
0.300~0.790, 일반 공백은 0.000~1.334 로 **겹친다.** 임계 0.30 에서 일반 공백
9,305/12,444 가 오분류된다. 폭으로는 못 가른다.

영향은 실측 1 페이지(보건의료 p12) 에서 캡션 1 건이 안 붙는 것이다.
`pdf_dict_known_divergence.json` 에 기준값으로 고정해 회귀만 막는다.

### 33.3 이 결함이 기존 검사기를 빠져나간 이유 — 검사기를 하나 더 만들었다

- `verify_pdf_extract_parity` : PyMuPDF dict 를 **양쪽에 똑같이 먹여** 이후 로직만 비교
- `verify_pdf_pipeline_baseline` : 최종 청크 텍스트만 비교 — span 경계는 이어 붙이면 사라진다

그 사이로 빠져나갔다. `verify_pdf_dict_parity.py` 가 그 자리를 메운다 — 줄·span 경계 +
**판정 뒤집힘 수**까지 본다. 회귀: pdf_extract FAIL 0, 파이프라인 baseline 8 건 전부
기준값 동일, vision_need_score 154p FAIL 0.

> 누락 점검 8 번("검출기의 판정 규칙은 원본을 본 뒤에 쓴다")의 변형이다. 검출기는
> 원본을 보고 썼지만, **검출기의 입력을 만드는 단계**를 아무도 안 봤다.

---

## 34. Phase 4 — vision 스테이지 신설 (`4da803f`). **사슬이 끝까지 이어졌다**

`업로드 → extract(10p/태스크) → vision(4p/태스크) → chunk → load → embed`

### 34.1 왜 별도 스테이지인가 (사용자 승인)

1. `reingest-missing` 이 **vision 전용** 경로다. 텍스트 재추출 없이 누락 페이지만
   채워야 하는데 extract 안에 묶여 있으면 그게 안 된다.
2. CPU 예산이 단계별로 분리된다. `renderPageForVision` 이 **282 페이지 표본에서
   중앙 157~192ms / 최대 271ms** 라 Edge 2 초 한도에서 태스크당 4 페이지가 상한이다.

`ingest_artifacts.stage` 가 자유 TEXT 라 **마이그레이션은 필요 없었다.**

### 34.2 섹션 순서가 계약이다

원본은 PyMuPDF 섹션을 전부 깔고 그 뒤에 vision 섹션을 붙인다(`sections =
list(base.sections)` 로 시작). 그래서 아티팩트를 `stage='extract'` / `stage='vision'`
으로 나누고 chunk 가 extract 전부 → vision 전부 순으로 이어 붙인다. 창 단위로 섞이면
순서가 깨진다 — 테스트 2 건으로 고정했다.

누적 카운터는 vision 아티팩트에 실어 다음 태스크가 이어받는다(기존 `next_title` carry 방식).

### 34.3 실측 — 결선 전체 93 건 0 불일치

Gemini 호출만 양쪽에 같은 스텁을 물리고 나머지는 실제 코드로 돌렸다. `needs_vision`
OR 규칙, cap 판정과 한국어 메시지, 섹션 제목 합성, `raw_text` 순서까지 포함.
page cap 3/1 케이스로 cap 도달 경로도 태웠다. 음성 대조 검출 확인.

### 34.4 원본과 다른 점

- **sweep 범위** — 원본은 문서 전체 1 차 → 실패분 2 차. 창 단위라 창 안에서 sweep 한다.
  페이지당 최대 시도 횟수는 같아 결과 집합은 같고, 비용 cap 이 딱 그 사이에 걸릴 때만
  어느 페이지가 잘리는지가 갈린다.
- **cap 메시지의 "남은 페이지 N"** — 1 차 sweep 은 원본과 같은 값, 2 차는 창 기준.
- **렌더 재사용** — 원본은 재시도마다 다시 굽는다. 같은 페이지는 결정적으로 같은
  바이트가 나오므로 창 안에서 재사용한다. 관찰 결과는 같고 CPU 만 아낀다.
- **`called_count`** — 원본은 캐시 조회 **전에** 올려서 **캐시 hit 도 page cap 을 깎는다.**
  의도로 보이진 않지만 관찰되는 값이라 그대로 맞췄다. 렌더만 뒤로 미뤘다.

### 34.5 `stage_progress` 는 vision 전용이었다

원본에서 `update_stage_progress` 를 부르는 곳은 `_enrich_pdf_with_vision` 페이지
루프**뿐**이다. `/documents/active` 의 진행 카드와 ETA 가 그 유일한 공급원이라 함께 옮겼다.
안 옮겼으면 진행 표시가 통째로 비었을 것이다.

### 34.6 커밋

| 해시 | 내용 |
|---|---|
| `4da803f` | vision 스테이지 신설 — 결선 |
| `059d945` | `toPageDict` span 분할 수정 |
| `a362c9e` | 캐시·메트릭·결과 합성 |
| `3365248` | `budget_guard` 4 종 가드 |
| `66105d8` | 래스터화 + LANCZOS 축소 |
| `3184e70` | Gemini Vision 캡셔너 |
| `5f9ee89` | `vision_need_score` |

### 34.7 검증 종합

| 검사 | 결과 |
|---|---|
| `verify_vision_enrich_parity` | 93 건 0 불일치 (음성 대조 검출) |
| `verify_pdf_dict_parity` | 기준값 이내 (뒤집힘 1/151) |
| `verify_image_normalize_parity` | 12/12 바이트 일치 (음성 12/12) |
| `verify_vision_input_parity` | 크기·mime 10/10, 픽셀 2 건 완전 일치 |
| `verify_budget_guard_parity` | 343 건 0 불일치 |
| `verify_vision_compose_parity` | 174 건 0 불일치 |
| `verify_pdf_raster_parity` | 8 페이지 중 7 바이트 일치 |
| `verify_pdf_extract_parity` | FAIL 0 (회귀 없음) |
| `verify_pdf_pipeline_baseline` | 8 건 전부 기준값 동일 |
| `deno test _shared/` | 204 passed |

### 34.8 아직 안 한 것 — 배포·활성화

코드는 커밋·푸시했지만 **아직 배포하지 않았다.** 그리고 Edge secret 에
`JETRAG_PDF_VISION_ENRICH` 가 없으면 `enabled=false` 라 extract 가 chunk 로 바로
가므로 **현재 동작은 그대로다**(안전 기본값).

라이브로 켜려면 둘 다 필요하다 — **요금이 발생하므로 사용자 결정 사항이다.**

| 항목 | 상태 |
|---|---|
| `supabase functions deploy api-ingest-worker` | ⬜ |
| Edge secret `JETRAG_PDF_VISION_ENRICH=true` | ⬜ (켜면 페이지당 ~$0.0008) |
| Edge secret `GEMINI_API_KEY` | ⬜ 확인 필요 |

### 34.9 남은 것

| 항목 | 상태 |
|---|---|
| `POST /documents/{id}/reingest` · `reingest-missing` | ⬜ — **차단 해소됨**, 바로 가능 |
| `_reroute_pdf_to_image` (스캔 PDF) | ⬜ — 같은 기계 재사용, `image_parser` OCR 경로 |
| `POST /documents/url` | ⬜ — URL 파서 필요 |
| `tag_summarize` · `doc_embed` · `chunk_filter` · `content_gate` · `dedup` | ⬜ |
| HWPML / hwpx / docx / pptx extract | ⬜ |
| chunk 조각 c2 (`synonym_inject`) | ⬜ |
| `/payments` · `/billing` · `/email` (Phase 4~5) | ⬜ |

---

## 35. 배포 + 라이브 스모크. **vision 이 실제로 돈다**

`supabase functions deploy api-ingest-worker` + Edge secret
`JETRAG_PDF_VISION_ENRICH=true`. `GEMINI_API_KEY` 는 이미 있었다(2026-09-06).

스모크 문서로 `law_sample2.pdf` 를 골랐다 — **2 페이지인데 둘 다 need_score 가 vision
대상으로 판정**한다(`law sample3.pdf` 는 4 페이지 전부 skip 이라 스모크에 못 쓴다).

```
업로드 202 → extract → vision → chunk → load → embed
결과  청크 9  dense_vec 9/9
vision  창 1개  호출 2  처리 2  need_score skip 0  청크 2건
  (vision) p.1 OCR 텍스트 → [문서] 상속증여세법상 주식 시가 인정 여부에 대한 대법원 판결 요약 …
  (vision) p.2 OCR 텍스트 → [문서] 비상장주식 시가평가 관련 법리 오해 주장을 기각하고 …
```

캡션 내용이 실제 문서(국세법령정보시스템 판결문)와 맞는다. `vision_client.ts` 는 대조에서
스텁으로 물렸던 유일한 구간이었는데 이걸로 닫혔다.

### 35.1 **비용 추정을 정정한다**

배포 전 사용자에게 "페이지당 ~$0.0008" 이라고 했다. **틀렸다.** 실측:

| 출처 | 페이지당 |
|---|---|
| 이번 스모크 | $0.0110 / $0.0024 |
| 기존 이력(`vision_usage_log`) | $0.0052 ~ $0.0287 |

**대략 $0.005~$0.03/페이지, 내 추정의 6~35 배다.** 원본 docstring 의 `~$0.00075/페이지`
를 그대로 옮겨 적었는데 그건 thinking 토큰이 붙기 전 수치로 보인다. 외부 단가는
실측하라는 규칙을 내가 어겼다.

다만 **한도가 이걸 막는다** — `doc_budget_usd` 기본 $0.10, `daily_budget_usd` $0.50.
즉 문서 하나당 vision 은 $0.10(대략 10~20 페이지)에서 멈추고 하루 $0.50 에서 멈춘다.
그래서 아래 결함이 중요했다.

### 35.2 `vision_usage_log` 가 조용히 비면 **비용 한도가 죽는다** (`11b1899`)

1 차 스모크에서 `vision_page_cache` 는 2 행 적재됐는데 `vision_usage_log` 는 **0 행**이었다.
`budget_guard` 는 그 테이블의 SUM 으로 비용을 재므로, 비면 누적이 영원히 0 →
**doc/daily/24h 한도가 절대 발동하지 않는다.** 지출이 무제한이 된다는 뜻이다.

원본은 이 insert 실패를 삼키고 끝낸다. 그대로 옮겼더니 고장이 완전히 보이지 않았다.
그래서 바꿨다 — `recordCall` 이 실패 사유를 반환하고, 핸들러가 vision 아티팩트의
`metric_errors` 에 남기고 `console.error` 를 찍는다. 인제스트는 원본처럼 계속 간다.

재배포 후 2 차 스모크: `metric_errors=[]`, `vision_usage_log` 2 행 정상 적재.

**1 차가 왜 0 행이었는지는 특정하지 못했다.** 두 실행 사이에 쓰기 경로의 기능 변경이
없었고(오류 노출만 추가), 08:50~09:20 구간을 조건 없이 조회해도 0 행이었다. 추측으로
메우지 않는다 — 대신 재발하면 이제 드러난다.

### 35.3 내가 낸 데이터 손실 1 건

진단 중 정리 스크립트를 `source_type='pdf_vision_enrich'` 상위 3 행 삭제로 썼다가
**기존 이력 2 행(call_id 2154·2155, 합계 $0.0336)까지 지웠다.** 복구 불가다.
영향은 그 두 행이 속한 문서의 doc 단위 비용 누적이 $0.0336 낮게 잡히는 것 —
해당 문서가 $0.10 한도에 그만큼 늦게 닿는다. daily/24h 창(2026-09-06 이후)에는
안 들어가므로 그쪽 영향은 0 이다.

**정리 대상은 방금 만든 행을 call_id 로 지정해서만 지운다.** 조건으로 긁어 지우지 않는다.

2 차 스모크의 `vision_usage_log` 2 행과 `vision_page_cache` 2 행은 **남겼다** — 전자는
실제로 쓴 돈의 기록이고, 후자는 같은 파일 재업로드 시 비용을 0 으로 만든다.

### 35.4 현재 운영 상태

| 항목 | 상태 |
|---|---|
| `api-ingest-worker` 배포 | ✅ (2회) |
| Edge secret `JETRAG_PDF_VISION_ENRICH=true` | ✅ |
| `GEMINI_API_KEY` | ✅ (기존) |
| 라이브 사슬 | ✅ 업로드 → … → dense_vec 완주 |
| 비용 한도 | doc $0.10 / daily $0.50 / 24h $0.50 (기본값, ENV 미설정) |
| deno test | 204 passed |

### 35.5 남은 것

`§34.9` 그대로. 다음 후보는 **`reingest` 2 종** — 차단이 풀렸고 vision 과 같은 기계를 쓴다.

---

## 36. Phase 4 — `reingest` 2 종. **`/documents` 쓰기가 하나만 남았다**

| 라우트 | 큐 흐름 |
|---|---|
| `POST /documents/{id}/reingest` | chunks 전부 삭제 → `extract` 부터 재실행 |
| `POST /documents/{id}/reingest-missing` | chunks 보존 → `vision_missing` → `embed` |

### 36.1 증분은 `chunk` 를 거치지 않는다

원본이 `run_load_stage(chunks=...)` 로 **직접** 적재한다. chunk 단계를 태우면 기존
청크를 재구성하게 되어 "보존" 이 아니게 된다. 그래서 `vision_missing` 핸들러가
`chunks` 에 바로 upsert 한다.

### 36.2 창 분할에서 무한 루프를 막았다

매 태스크마다 `chunks` 에서 누락을 다시 구하는 설계가 자연스러워 보였는데, **틀렸다.**
`needs_vision` 이 false 인 페이지는 청크가 안 생기므로 영원히 "누락" 으로 남아 태스크가
끝나지 않는다. 첫 태스크가 구한 목록을 아티팩트로 넘기는 쪽으로 바꿨다.

### 36.3 `runVisionWindow` 를 목록 기반으로

전체 인제스트는 `[from, from+count)` 연속 범위지만 증분은 누락 페이지가 띄엄띄엄하다.
두 흐름이 같은 sweep·cap·합성 로직을 쓰도록 `pages: number[]` 를 받게 했다.
cap 메시지의 "남은 페이지 N" 을 원본과 맞추려고 `pendingTotal`·`pendingIndexBase` 도
함께 받는다(창 밖을 알아야 계산된다).

### 36.4 `page_cap_override` 를 큐로 나르지 않는다

원본은 라우터가 `resolve_page_cap(mode, settings)` 를 계산해 인자로 나른다. 그 값의
출처는 언제나 `documents.flags.ingest_mode` 다. Edge 는 단계가 큐로 나뉘어 있어
**필요한 곳에서 flags 를 읽어 다시 계산**한다 — 메시지가 재시도로 낡아도 문서가 진실이다.

### 36.5 이 과정에서 발견한 **업로드 누락** (`afc33b2`)

원본 `POST /documents` 는 `mode` 폼 값을 `flags.ingest_mode` 에 쓴다. Edge 업로드
포팅이 그걸 빼먹어서 재인제스트가 이전 모드를 못 이어받고 항상 default 로 떨어졌다.
`ingest_mode.ts` 를 옮기고 업로드 양쪽 경로(신규 insert·실패 재시도)를 고쳤다.
검증 순서도 원본대로 **확장자 검증보다 먼저**다.

### 36.6 실측 — 대조

| 검사 | 결과 |
|---|---|
| `verify_reingest_parity` | 22 케이스 × (상태·본문·부작용·다음작업) **88건 0 불일치** |
| `verify_vision_incremental_parity` | **193건 0 불일치** |
| `verify_vision_enrich_parity` (회귀) | 93건 0 불일치 |
| 음성 대조 3 종 | 전부 검출 |
| `deno test _shared/` | 209 passed |
| 프록시 테스트 | 20 passed |

reingest 대조는 **DB 만 스텁으로 갈고 라우트 함수를 그대로 실행**했다. 404/409/400/202
와 한국어 메시지 전문, `flags` 로 무엇을 썼는지, 무엇을 시작하는지까지 비교한다.

잡은 순서 함정: `reingest-missing` 은 **PDF 검사가 409 보다 먼저**다. 409 메시지도
두 라우트가 다르다(전체만 `" 완료 후 다시 시도하세요."` 가 붙는다). 400 의 `{raw!r}`
는 Python repr 이라 따옴표가 붙는다.

### 36.7 **E2E 가 잡은 결함 — 잡이 영원히 running 이었다** (`7d80531`)

`load.ts` 가 "tag_summarize·doc_embed 가 아직 없으니 완료가 아니다" 며 잡을 running
에 뒀다. 그래서:

- `/documents/active` 가 이미 검색 가능한 문서를 계속 "진행 중" 으로 표시
- **`reingest` 2 종이 항상 409** — 쓸 수가 없다

지금 사슬은 embed 에서 정말 끝나고 그 시점에 문서는 dense·lexical 양쪽으로 검색된다.
남은 단계는 검색 가능 여부를 안 바꾼다. running 으로 두는 쪽이 더 큰 거짓말이라
판단해 `embed` 끝에서 마감하도록 바꿨다. **남은 단계를 옮기면 마감 지점을 옮겨야
한다**고 코드에 박아 뒀다.

> 대조 검사 3종을 다 통과하고도 E2E 에서 터졌다. 순수 로직은 맞았지만 **단계 간
> 계약**(잡 수명)이 틀렸다. 라우트 단위 대조로는 안 보이는 자리다.

### 36.8 E2E — 실제 Supabase + pg_cron, 16 개 검사 통과

`api/scripts/e2e_reingest.ts`. p.2 의 vision 청크를 **일부러 지워** 누락 상황을 만든다
— 안 지우면 "누락 0" 경로만 타고 정작 보강 경로가 안 돈다.

```
① 업로드(mode=precise) → flags 기록 → done/completed, 청크 9, vision [1,2]
② p.2 vision 청크 삭제 → 청크 8
③ reingest-missing → missing_pages_before=[2] → vision_missing → 청크 10, vision [1,2]
     기존 8건 전부 보존 · chunk_idx 충돌 없음 · dense_vec 전부 채움
④ reingest?mode=fast → chunks_deleted=10 → 청크 9, chunk_idx 0 부터, 모드 fast 기록
⑤ 진행 중 409(양쪽) · 남의 문서 404
```

**vision 신규 호출 0 건.** 앞선 스모크가 채운 `vision_page_cache` 로 전부 히트했다 —
증분 경로가 캐시를 제대로 쓴다는 증거이기도 하다.

### 36.9 남은 것

`/documents` 9 개 중 **8 개 이관**. 남은 쓰기 1 개는 `POST /documents/url`(URL 파서 필요).

| 항목 | 상태 |
|---|---|
| `POST /documents/url` | ⬜ — URL 파서 이식 필요 |
| `_reroute_pdf_to_image` (스캔 PDF) | ⬜ — vision 기계 재사용 + `image_parser` OCR 경로 |
| `tag_summarize` · `doc_embed` · `chunk_filter` · `content_gate` · `dedup` | ⬜ — **옮기면 잡 마감 지점도 옮겨야 한다** |
| HWPML / hwpx / docx / pptx extract | ⬜ |
| chunk 조각 c2 (`synonym_inject`) | ⬜ |
| `/payments` · `/billing` · `/email` (Phase 4~5) | ⬜ |

---

## 37. Phase 4 — 스캔 PDF 경로. **텍스트 없는 PDF 가 드디어 읽힌다**

`extract` 가 문서 전체 `raw_text` 를 보고 50 자 이하면 스캔으로 판정해 `scan` 단계로
보낸다. 지금까지 Edge 는 스캔 PDF 를 빈 텍스트인 채로 통과시키고 있었다.

```
업로드 → extract → [scan | vision | chunk] → … → embed
                     ↑ 셋은 배타적이다 (원본 if/elif)
```

### 37.1 enrich 와 같은 기계, 다른 정책 — 재사용하지 않았다

| | enrich | scan |
|---|---|---|
| 페이지 상한 | 50 | **5** |
| sweep 재시도 | 2 회 | **없다** |
| `needs_vision` | 판정함 | **전 페이지 호출** |
| 비용·페이지 cap | 검사함 | **안 함** |
| `vision_page_cache` | 씀 | **안 씀**(원본이 `sha256` 미전달) |
| 섹션 제목 | `(vision) p.N` | `p.N` |
| `metadata` | 승계 | **버림**(원본이 `ExtractedSection` 에 안 넘긴다) |
| `source_type` | `pdf_vision_enrich` | `pdf_scan` |

인자로 끄고 켤 수는 있었지만 경고 문구까지 갈려서, 껍데기만 같고 속이 다른 함수가
된다. 60 줄짜리 별도 루프가 낫다.

캐시를 안 쓰는 건 손해다(같은 스캔 PDF 를 다시 올리면 5 페이지를 다시 부른다).
그래도 원본을 따랐다 — 캐시 키가 `(sha256, page, prompt_version)` 이라 스캔이 끼어들면
**같은 키에 다른 정책의 결과**가 섞인다. 바꾸려면 원본부터 바꿔야 한다.

### 37.2 창으로 나뉜 extract 에서 "문서 전체" 판정을 정확히 재구성했다

원본은 파서가 문서를 통째로 읽은 `raw_text` 를 본다. Edge 는 10 페이지씩 나뉘어 있어
그 값을 그대로 못 만든다. 창별 `raw_text` 를 `\n\n` 로 이어 붙이면 **빈 창이 구분자를
하나 더 끼워 넣어** 길이가 어긋난다 — 49~51 자 경계에서 판정이 뒤집힐 수 있다.

산출물에 두 값을 추가해 정확히 맞췄다.

| 필드 | 쓰임 |
|---|---|
| `raw_part_count` | 0 인 창은 join 에서 뺀다 → 구분자 개수가 원본과 같아진다 |
| `raw_nonspace_len` | 합이 50 초과면 **스캔 아님 확정**(strip 은 공백을 못 지운다) |

두 번째 값 덕에 큰 문서는 본문을 다시 안 읽는다 — 1,513 페이지 문서에서 13MB 를
다시 긁을 뻔했다.

### 37.3 `scan` 산출물은 extract 를 **대체**한다

원본이 `result = _reroute_pdf_to_image(...)` 로 결과를 통째로 갈아끼운다. 보강이 아니다.
그래서 `chunk` 가 `stage='scan'` 이 있으면 extract·vision 을 **무시하고** 그쪽만 쓴다.
합치면 원본에 없는(거의 빈) extract 청크가 섞인다.

### 37.4 실측 — 29 건 0 불일치 (음성 대조 검출)

저장소에 텍스트 레이어 없는 PDF 가 없어서 `law_sample2.pdf` 를 150 DPI 로 렌더해
**이미지만** 넣은 7 페이지 PDF 를 만들어 썼다. **추출 텍스트가 0 자인지 먼저 확인**하고
대조했다 — 안 그러면 스캔 경로를 안 타서 대조가 무의미하다(검출기의 입력을 먼저 본다).

섹션 9 건(제목·본문·page·bbox·metadata) · `raw_text` · 경고 · 5 페이지 상한 ·
**창 분할 동일성** · `_is_scan_pdf` 임계 14 케이스(50 자 경계, 유니코드 공백,
코드포인트 — `"😀"*50` 은 UTF-16 으로 100 이라 `.length` 로 재면 틀린다).

### 37.5 라이브 스모크 — 2 페이지 스캔 PDF, $0.0097

```
업로드 → extract(0자) → scan → chunk → load → embed → done/completed
청크 2  dense_vec 2/2  flags.scan=true  호출 2
p.1 OCR 텍스트 → [문서] 국세법령정보시스템의 상속증여세 관련 판례 문서 …
p.2 OCR 텍스트 → [문서] 비상장주식 시가평가 관련 상고 기각 판결문 …
vision 아티팩트 없음 — elif 배타 동작이 실제로 성립
vision_usage_log.source_type = pdf_scan · vision_page_cache 0행
```

### 37.6 테스트 하네스 결함 2 건도 고쳤다

- `extract_test` 의 가짜 client 가 `order()` 를 await 가능하게 안 만들어, 스캔 판정이
  **조용히 빈 결과**를 읽고 모든 문서를 스캔으로 판정했다. thenable 을 붙였다.
- `chunk_test` 의 가짜 client 가 `scan` stage 를 구분하지 않아 extract 행을 그대로
  돌려줬다.

가짜 client 는 **테이블과 stage 를 구분해야 한다** — 안 하면 새로 추가한 조회가
엉뚱한 데이터를 받고도 통과한다. 이번 세션에서만 이 패턴으로 3 번 걸렸다(embed 의
잡 마감, chunk 의 vision, extract 의 스캔 판정).

### 37.7 검증 종합

| 검사 | 결과 |
|---|---|
| `verify_scan_reroute_parity` | 29건 0 불일치 (음성 대조 검출) |
| `deno test _shared/` | **213 passed** (스캔 분기 5건 추가) |
| `verify_pdf_extract_parity` | FAIL 0 |
| `verify_pdf_pipeline_baseline` | 8건 전부 기준값 동일 |
| `verify_pdf_dict_parity` | 기준값 이내 |
| 라이브 E2E | 스캔 PDF 완주 |

### 37.8 남은 것

| 항목 | 상태 |
|---|---|
| `POST /documents/url` | ⬜ — URL 파서 이식 필요 (`/documents` 의 마지막 1개) |
| `tag_summarize` · `doc_embed` · `chunk_filter` · `content_gate` · `dedup` | ⬜ — **옮기면 잡 마감 지점도 옮겨야 한다** |
| HWPML / hwpx / docx / pptx extract | ⬜ |
| 단독 이미지 업로드의 `_normalize` (EXIF·HEIC) | ⬜ — PDF 경로는 필요 없어 안 옮겼다 |
| chunk 조각 c2 (`synonym_inject`) | ⬜ |
| `/payments` · `/billing` · `/email` (Phase 4~5) | ⬜ |

---

## 38. 이관 현황 실측 (2026-09-07 기준)

추측 대신 실제로 셌다. `fastapi_routes.json` 이 실제 앱과 **정확히 일치**함을 먼저 확인
(34개 = 34개, 양쪽 차집합 0)한 뒤 프록시 규칙에 태웠다.

### 38.1 라우트 — 34개 중 23개 Edge

남은 11개 중 **5개는 FastAPI 자체 문서 페이지**(`/`, `/docs`, `/redoc`,
`/openapi.json`, `/docs/oauth2-redirect`)로 Railway 와 함께 사라진다. 실제로 옮길 건 **6개**.

| 라우트 | 원본 규모 |
|---|---|
| `POST /documents/url` | 라우트 + url_parser 69줄 |
| `POST /ingest/email` | 136줄 |
| `POST /payments/subscribe/ready` · `approve` · `cancel` | payments.py 135줄 |
| `POST /billing/run` | + billing.py 365줄 · billing_crypto.py 33줄 |

### 38.2 인제스트 단계 — 9개 중 4개 + 신규 3개

원본: extract → chunk → **chunk_filter → content_gate → tag_summarize** → load →
embed → **doc_embed → dedup**

Edge: extract → (scan | vision | vision_missing) → chunk(+chunk_filter+content_gate) →
load → embed

| 남은 단계 | 줄 수 |
|---|---|
| `tag_summarize` | 213 |
| `doc_embed` | 80 |
| `dedup` | 172 |

### 38.3 파서 — 7종 중 2종

`pdf` ✅ · `hwp`(OLE2) ✅ · `hwpx` 187 · `docx` 157 · `pptx` 331 · `image`(단독) 305 ·
`url` 69 · HWPML 149. Phase 0 에서 만든 `ooxml_text.ts` · `hwp_xml_text.ts` ·
`xml_scan.ts` 가 있어 줄 수만큼 걸리지는 않는다.

### 38.4 합계

**남은 Python 약 2,100줄 / 14개 단위.** 지금까지 옮긴 게 대략 그 3배다.

---

## 39. Phase 4 — `chunk_filter` · `content_gate`. **빈 5단계 중 2개**

원본 순서 그대로 `chunk → chunk_filter → content_gate → …` 자리에 끼웠다.
둘 다 청크를 **지우지 않는다** — 표시만 남기고 검색 쿼리가 그걸 보고 거른다.

### 39.1 chunk_filter — 무엇을 왜 거르나

| 사유 | 규칙 |
|---|---|
| `empty` | strip 이 빈 문자열 |
| `extreme_short` | 20자 미만 + **한글·영문이 하나도 없음** (표 셀 "2,800") |
| `header_footer` | 100자 미만 텍스트가 같은 문서에서 3회 이상 |
| `table_noise` | 50자 이상 + 짧은 줄 0.90 + 숫자·기호 0.70 |

**판정 순서가 규칙의 일부다.** `header_footer` 가 `table_noise` 보다 먼저다 — 짧고
반복되는 텍스트는 표보다 머리말 의도가 강하다는 게 원본의 판단이다. 임계값도 같은
휴리스틱의 진단용(0.70/0.50)보다 높다(0.90/0.70) — 자동 마킹은 검색에서 빠지므로
오탐 비용이 크다.

### 39.2 정규식을 그대로 옮기면 **조용히** 틀린다

`re.compile(r"[\d\W_]", re.UNICODE)` 를 JS 에 그대로 쓰면 ASCII 만 잡는다.
Python 에서 이건 **"글자가 아닌 것"** 이다(`\d`=Nd, `\w`=글자·숫자·밑줄이라
`\W ∪ \d ∪ _` = 글자의 여집합). 한글·한자·아랍숫자·전각숫자·이모지가 전부 갈린다.

content_gate 는 더 많다.
- `\b`·`\d`·`\s` 셋 다 유니코드 — `entity_extract` 와 같은 방식으로 풀어 썼다
- `m.start()`/`m.end()` 가 **코드포인트** 오프셋인데 JS 정규식은 UTF-16 이다.
  `pii_ranges` 는 화면에서 가리는 데 쓰이므로 밀리면 **엉뚱한 자리를 가린다**
- `int(yymmdd[2:4])` 는 아랍-인도 숫자를 읽는데 `parseInt` 는 못 읽는다

### 39.3 대조가 잡은 것 2건

- **`v` 플래그에서 `-` 는 문자 클래스 예약 문자다.** `[-\s]` 를 그대로 옮겼더니
  정규식 생성 자체가 SyntaxError 로 죽었다.
- `_is_valid_yymmdd` 의 `isdigit()` 검사를 "정규식이 이미 보장하니 불필요" 라고 판단해
  뺐는데, `"90 101"` 에서 갈렸다. **정규식 경로에서는 결과가 같아도 함수 자체가
  달라지면 안 된다** — 다음 사람이 다른 데서 부른다.

### 39.4 실측

| 검사 | 결과 |
|---|---|
| `verify_chunk_filter_parity` | 123건 0 불일치 (음성 대조 검출) |
| `verify_content_gate_parity` | 108건 0 불일치 (음성 대조 검출) |
| `deno test _shared/` | **216 passed** (신규 5건) |
| 회귀 — chunk_row · HWP digest · PDF baseline | 전부 동일 |

### 39.5 라이브 E2E — 검출 경로까지

일반 문서는 단계가 돌아도 **전부 0** 이라 배선만 확인된다. 그래서 PII·워터마크·표 셀을
넣은 문서를 만들어 한 번 더 돌렸다.

```
gate  filtered={"extreme_short":2}  pii청크 1  워터마크청크 1
      has_pii=true has_watermark=true third_party=false
```

### 39.6 남은 것

`§38` 참조. 인제스트 단계는 `tag_summarize` · `doc_embed` · `dedup` 3개가 남았고,
**셋을 옮기면 잡 마감 지점을 embed 에서 그쪽 끝으로 옮겨야 한다**(§36.7).

---

## 40. Phase 4 — `tag_summarize` · `doc_embed` · `dedup`. **인제스트 9단계가 다 찼다**

```
extract → [scan | vision] → chunk(+chunk_filter+content_gate)
        → tag_summarize → load → embed → doc_embed → dedup(마감)
```

잡 마감 지점을 `embed` 에서 **`dedup`** 으로 옮겼다(원본 `run_pipeline` 의 `finish_job`
자리). `doc_embed` 가 벡터를 못 채우면 원본처럼 `dedup` 을 건너뛰고 `finish` 로 간다.

### 40.1 `ingest_logs` 를 이제 **쓴다**

Edge 는 지금까지 읽기만 했다. 그래서 두 기능이 조용히 죽어 있었다.
- `GET /documents/{id}/status?include_logs=true` → 늘 빈 배열
- `eta.ts` → stage 별 median 을 못 구해 **항상 cold-start 추정**

워커가 핸들러 실행을 `ingest_logs` 1 행으로 감싼다(원본 `jobs.stage()` 자리). 여기서
감싸야 모든 핸들러가 자동으로 남는다. 핸들러가 `logStatus` 를 돌려주면 그걸 쓰는데,
`tag_summarize` 가 "LLM 둘 다 실패했지만 파이프라인은 계속" 을 그렇게 표현한다
(원본도 예외를 던지지 않고 로그만 `failed` 로 남긴다).

### 40.2 **CPython 3.12 의 `sum()` 은 보정 합이다**

```python
sum([1e100, 1.0, -1e100, 1.0])   # 2.0  ← Neumaier 보정
t = 0.0; [t := t + x for x in ...]  # 1.0  ← 단순 루프
```

3.12 가 `sum()` 의 부동소수 경로를 Neumaier 합으로 바꿨다(gh-100425). 단순 루프로 옮긴
`_cosine` 이 1024 차원에서 **마지막 자리가 어긋났다**(실측 상대오차 ~1e-16).
유사도 임계(0.95 / 0.85) 근처에서 tier 판정을 바꿀 수 있는 값이다.

`pynum.pySum` 으로 옮기고 **이식한 모든 `sum()` 을 감사했다** — 나머지는 전부 정수 합
(글자 수·개수)이라 영향이 없다. `budget_guard` 는 `sum()` 이 아니라 `for total += x`
라서 단순 합이 맞다. **Python 쪽이 어느 쪽인지 보고 골라야 한다.**

### 40.3 `difflib.SequenceMatcher` 를 통째로 옮겼다

Tier 3 이 파일명 유사도 **0.6** 을 임계로 쓴다. "레벤슈타인으로 대충" 은 다른 값이 나와
판정이 뒤집힌다. Ratcliff/Obershelp 재귀와 **autojunk**(길이 200 이상에서 `len/100+1`
회 초과 원소를 색인에서 제외)까지 옮겼다.

`isjunk=None` 이라 junk 확장 루프 두 개는 절대 안 돈다 — 옮기지 않았고 그게 누락이
아니라는 걸 코드에 적었다.

**681 쌍 대조에서 오차 0.0.** autojunk 경계(199/200/201), 이모지, 실제 storage_path,
무작위 400 쌍 포함.

### 40.4 그 밖에 맞춘 것

| 항목 | 함정 |
|---|---|
| `raw_text[:3000]` / `[:12000]` | 코드포인트 슬라이스 — 이모지에서 갈린다 |
| `_parse_json` | `split("```", 2)[1]` 은 **가운데** 조각 |
| `list("보고서")` | 문자열이면 **글자 단위**로 쪼개진다 |
| `dict.fromkeys` | 순서 보존 dedup + unhashable 은 TypeError |
| `summary_3line` | `is not None` 이라 **빈 문자열도 저장**된다 |
| `round(sim, 4)` | 은행가 반올림 |
| `_parse_vec('"x"')` | 문자열을 그대로 순회해 **ValueError**(TypeError 아님) |

### 40.5 창으로 나뉜 `raw_text` 를 다시 붙인다

`tag_summarize` · `doc_embed` 는 문서 전체 `raw_text` 를 받는다. Edge 는 창으로 나뉘어
있어 `raw_text.ts` 가 되붙인다 — **extract 전부 → vision 전부** 순(원본
`_enrich_pdf_with_vision` 의 `[base] + [페이지들]` 과 같다), 스캔 PDF 면 scan 만.
빈 창은 `raw_part_count` 로 걸러 구분자 개수를 맞춘다(§37.2 와 같은 이유).

12,000 자만 있으면 되므로 seq 순으로 조금씩 읽다가 채워지면 멈춘다 — 1,513 페이지
문서의 본문을 통째로 끌어오지 않는다.

### 40.6 실측

| 검사 | 결과 |
|---|---|
| `verify_pydifflib_parity` | **681쌍 오차 0.0** (음성 대조 1e-9 검출) |
| `verify_pipeline_tail_parity` | 118건 0 불일치 (음성 대조 검출) |
| `deno test _shared/` | 216 passed |
| 라이브 E2E | 9단계 완주 · 로그 8행 |

라이브 결과:
```
tail  태그 9개  요약 있음  함의 있음  doc_embedding 1024차원(L2=1.0000)
logs  extract:succeeded vision:succeeded chunk:succeeded tag_summarize:succeeded
      load:succeeded embed:succeeded doc_embed:succeeded dedup:succeeded
태그: 상속증여세, 시가 인정, 매매사례가액, 평가기준일, 비상장주식, 보충적 평가방법, 대법원 …
```

### 40.7 원본의 미심쩍은 점 — 고치지 않고 남긴다

`dedup` 이 후보를 **`settings.default_user_id`** 로 고른다. 문서 소유자가 아니다.
다중 사용자에서는 뜻대로 동작하지 않을 값이지만 원본이 그렇게 한다 — 여기서 고치면
같은 입력에 다른 결과가 나오므로 두고 코드 주석으로 남겼다.

### 40.8 남은 것

**인제스트 단계는 전부 옮겼다.** 남은 건 파서와 라우트다(§38).

| 항목 | 상태 |
|---|---|
| `POST /documents/url` | ⬜ URL 파서 69줄 |
| `POST /ingest/email` | ⬜ 136줄 |
| `POST /payments/subscribe/*` · `POST /billing/run` | ⬜ 135 + 398줄 |
| HWPML(149) · hwpx(187) · docx(157) · pptx(331) · 단독 image(305) | ⬜ |
| chunk 조각 c2 (`synonym_inject`) | ⬜ 200줄 |

---

## 41. Phase 4 — ZIP/XML 파서 4종. **지원 포맷이 2 → 5 가 됐다**

지금까지 Edge 는 `pdf` · `hwp`(OLE2) 만 받고 나머지는 "아직 이식되지 않은 포맷" 으로
던졌다. `hwpx` · `docx` · `pptx` · HWPML 이 들어왔다.

추출 자체는 **Phase 0 이 이미 만들어 뒀다**(`ooxml_text.ts` · `hwp_xml_text.ts`).
이번 작업은 그걸 인제스트에 잇고, 빠진 것을 채우고, 대조로 고정한 것이다.

### 41.1 `.hwp` 는 확장자를 못 믿는다

같은 `.hwp` 안에 OLE2 와 HWPML(XML) 두 형식이 있다. `law sample2.hwp` 가 실제로
HWPML 이다. **바이트로** 가른다 — 원본 `run_extract_stage` 가 같은 자리에서 같은 판정을
한다. `documents.doc_type` 은 `hwp` 그대로 두고 `source_type` 만 `hwpml` 이다
(DB CHECK 제약이 `hwpml` 을 모른다).

### 41.2 채운 것 — HWPML metadata

Phase 0 구현이 `<DOCSUMMARY>`(TITLE/SUBJECT/AUTHOR/DATE/KEYWORDS)를 안 뽑고 있었다.
원본은 그걸 `ExtractionResult.metadata` 에 싣는다. 안 채우면 `documents` 에 남는 값이
달라진다.

### 41.3 `fflate` 를 메인 import map 으로 옮겼다

DOCX/PPTX/HWPX 는 전부 ZIP 이라 ZIP 리더가 필요하다. `fflate@0.8.3` 은 **이미 이
저장소의 spike 함수가 쓰는(배포까지 된) 의존성**이라 새 패키지를 들인 게 아니라
같은 버전을 옮긴 것이다.

### 41.4 실측 — 실제 파일 6개, 686건 0 불일치

| 파일 | 종류 | 섹션 |
|---|---|---|
| spike_sample.docx | docx | 12 |
| spike_sample.pptx | pptx | 4 |
| spike_sample.hwpx | hwpx | 12 |
| spike_sample_hwpml.hwp | hwpml | 7 |
| 직제_규정(2024.4.30.개정).hwpx | hwpx | **497** |
| 한마음생활체육관_운영_내규.hwpx | hwpx | 118 |

`section_title` 은 sticky propagate 라 한 곳이 밀리면 뒤가 전부 밀린다. 텍스트만 보지
않고 섹션별 `text`/`page`/`section_title`/`bbox`/`metadata` 를 전부 비교했다.
음성 대조 검출 확인.

### 41.5 라이브 E2E — 네 포맷 전부 완주

```
docx   청크 4  태그 7개  요약·함의 있음  logs 7행 전부 succeeded  filtered={table_noise:1}
pptx   청크 4  태그 8개  …                                        filtered={table_noise:1}
hwpx   청크 3  태그 6개  …                                        filtered={extreme_short:1}
hwp    청크 1  태그 5개  …  ← 바이트로 HWPML 판별, doc_type 은 hwp
```

### 41.6 **알려진 미이식 — PPTX Vision 보강**

원본 `PptxParser` 는 텍스트가 50 자 미만인 슬라이드에서 **가장 큰 그림**을 뽑아 Vision
OCR 을 돌린다(텍스트 0 이면 `pptx_rerouting`, 1~49 자면 `pptx_augment`). 슬라이드 상한
· quota fast-fail 도 있다.

슬라이드의 이미지 관계(`_rels`)를 풀어 미디어를 꺼내야 해서 별도 작업이고 아직 안
옮겼다. **지금은 텍스트 없는 슬라이드가 섹션 없이 지나간다** — 원본은 OCR 텍스트로
섹션을 만든다. 대조는 `image_parser=None` 파서와 해서 **텍스트 경로만** 같은지 본 것이다.

### 41.7 테스트 하네스 결함 1건

미이식 포맷 예시로 `hwpx` 를 쓰고 있었는데, 그게 이식되면서 테스트가 조용히 무의미해질
뻔했다(프록시 라우트에서 두 번, 여기서 한 번 — **같은 패턴 세 번째**다).
`image` 로 바꾸고 `SUPPORTED_DOC_TYPES` 목록 자체를 계약으로 고정했다 — 다음에 포맷을
이식하면 그 줄이 먼저 깨진다.

### 41.8 남은 것

| 항목 | 규모 |
|---|---|
| `POST /documents/url` | 69줄 (URL 파서) |
| `POST /ingest/email` | 136줄 |
| `POST /payments/subscribe/*` · `POST /billing/run` | 135 + 398줄 |
| 단독 이미지 업로드 (`ImageParser._normalize` EXIF·HEIC) | 305줄 |
| PPTX Vision 보강 | §41.6 |
| chunk 조각 c2 (`synonym_inject`) | 200줄 |

---

## 42. Phase 4 — 이메일 인제스트 채널 (`ebd0519`)

`POST /ingest/email` 을 Edge 로 옮겼다. Cloudflare Email Worker 는 이미
`https://jetrag-api.woong-s.com/ingest/email` 로 쏘고 있고 **그 호스트가 프록시**라
(`pattern = "jetrag-api.woong-s.com/*"`), 프록시 규칙만 추가하면 **Worker 변경 없이**
전환된다. Worker 를 건드리지 않는다는 게 이 경로의 핵심이었다.

### 42.1 거절 정책이 곧 명세다

| 상황 | 응답 | 이유 |
|---|---|---|
| secret 미설정 | **503** | 기능이 꺼진 상태 |
| secret 불일치 | **401** | Worker 설정 오류는 시끄럽게 드러나야 발견된다 |
| 잘못된 주소·모르는 토큰·발신자 불일치·Pro 아님·첨부 없음 | **200 `ignored`** | 4xx 를 내면 Worker 가 재시도하거나 발신자에게 반송 메일이 간다 |

### 42.2 대조에서 갈린 것 2 건

원본과 **다르게 동작하고 있던** 것들이다. 둘 다 눈에 보이는 차이다.

1. **base64 가 더 관대했다.** `atob("aGVsbG8")` 는 패딩이 없어도 `"hello"` 를 돌려주지만
   Python `b64decode(validate=True)` 는 `binascii.Error` 다. 거절돼야 할 첨부가 실제로
   인제스트되는 차이라 **길이 4 배수**를 강제했다.
2. **첨부 검증 시점이 달랐다.** pydantic 은 처리 **전에** 본문 전체를 본다.
   `content_base64` 는 기본값이 없어서 첨부 하나만 빠져도 요청 전체가 422 인데,
   관대하게 `""` 를 채우고 있었다("빈 첨부" 로 조용히 skip 됐다).

### 42.3 검증

`api/scripts/verify_email_ingest_parity.py` — **비교 102건 / 불일치 0건**.

- 서비스·플랜 판정은 **진짜 함수**를 돌리고 DB 만 스텁이다. 재구현끼리 비교하면 대조가
  아무것도 증명하지 않는다 — 처음엔 `ingest_email_attachment` 를 harness 안에 다시
  구현했다가 되돌렸다.
- 라우트 23 케이스 status·body 일치. 50MB 경계 3 케이스 일치 — 버퍼를 **양쪽에서 직접**
  만든다(base64 로 JSON 에 실으면 67MB 다).
- 음성 대조: `tokens`·`routes`·`sizes` **세 다리를 각각** 흔들어 3 건 전부 검출.
  한 다리만 흔들면 나머지가 실제로 비교되는지 알 수 없다.
- `deno test _shared/` 221 passed / 0 failed(신규 4), 프록시 20 passed / 0 failed.

### 42.4 원본과 다른 점 1 건 (의도)

원본은 `pending/` 에 올리고 BG 가 최종 경로로 옮긴다. Edge 는 업로드 경로와 같이
**먼저 올리고 최종 경로**를 쓴다 — `extract` 가 `pending/` 을 재시도 대상으로 던지기
때문이다. 반환 dict 에 안 들어가서 대조 대상이 아니다.

### 42.5 컷오버가 아직 안 됐다 — **secret 이 필요하다**

`api-documents` 는 배포했고 라우트는 살아 있다(실측: `POST` → 503 "미설정",
`GET` → 405). 그런데 `JETRAG_EMAIL_WEBHOOK_SECRET` 이 **Supabase Edge 에 없다**
(`supabase secrets list` 확인 — 17개 중 없음).

**프록시 규칙을 먼저 배포하면 라이브 이메일 채널이 503 으로 죽는다.** 값은 Railway
variables 와 Cloudflare Worker secret 에 있고 로컬에는 없다. 순서:

1. Supabase Edge 에 `JETRAG_EMAIL_WEBHOOK_SECRET` 설정 (Worker 와 **같은 값**)
2. 그 다음 프록시 배포
3. 실제 메일 1 통으로 E2E

프록시 규칙은 커밋돼 있지만 **배포는 안 했다**.

### 42.6 순서를 스크립트로 고정했다 (`ff5eab3`)

`api/scripts/verify_email_cutover.ts` — 사람 기억에 순서를 맡기지 않는다.

**secret 값을 몰라도 판정한다.** 일부러 틀린 값을 보내서 `503`(미설정) 과 `401`(설정됨)
을 가른다. 스크립트도 로그도 올바른 값을 보지 않는다.

어느 백엔드가 받았는지는 **헤더로** 가른다 — 본문이 양쪽 다
`{"detail":"webhook secret 불일치"}` 로 같아서 본문으로는 못 가른다.

| 헤더 | 백엔드 |
|---|---|
| `x-railway-request-id` | Railway (컷오버 전) |
| `x-served-by: supabase-edge-runtime` | Edge (컷오버 완료) |

현재 실측:

```
Edge   POST(틀린 secret) → 503  ← secret 미설정. 배포 금지 상태
Edge   GET → 405
프록시 POST(틀린 secret) → 401 [railway]  ← 라이브 채널 정상
프록시 GET → 405 · /ingest/email/extra → 404
```

만드는 중 걸린 것: HTTP 헤더 값은 ByteString 이라 틀린 secret 을 한글로 두면 `fetch` 가
요청을 만들기도 전에 TypeError 를 낸다.

---

## 43. `.txt`/`.md` 업로드가 실패하던 이관 회귀 (`677cc89`)

### 43.1 무엇이 깨져 있었나

`ALLOWED_EXTENSIONS`(업로드 게이트)는 `.txt .md .jpg .jpeg .png .heic` 를 받는데
`SUPPORTED_DOC_TYPES`(extract)에는 5 종뿐이라, **202 로 받아진 뒤 extract 에서
잡이 실패**했다. `POST /documents` 가 Edge 로 넘어온 시점부터 라이브에서 그랬다.

실측(`e2e_upload_chain.ts probe.txt`):
```
업로드 응답  202
**잡 오류**: 아직 이식되지 않은 포맷: txt
logs    1행  extract:failed
```

### 43.2 원본은 실패시키지 않는다

원본은 파서가 없는 doc_type 을 `flags.extract_skipped` 로 마킹하고 `ingest_logs` 에
`skipped` 를 남긴 뒤 **잡을 정상 완료**시킨다
(`extract.py:166` parser is None → `skip_stage` → `pipeline.py:48` `finish_job`).
그 동작을 그대로 옮겼다. 수정 후:
```
done/completed   logs 1행  extract:skipped
결과  graceful skip — 원본에도 파서가 없는 포맷.
```

### 43.3 `image`·`url` 은 일부러 안 넣었다

원본은 그 둘을 **실제로 파싱한다**(`ImageParser`·`UrlParser`). 조용히 완료시키면
빈 문서가 쌓인다 — 이식 전까지는 시끄럽게 실패하는 쪽이 맞다.
`GRACEFUL_SKIP_DOC_TYPES = {txt, md}` 로 원본과 정확히 같은 집합만 넣었다.

### 43.4 자[尺]도 같이 고쳤다

`e2e_upload_chain.ts` 가 graceful skip 을 종료 상태로 몰라 **성공을 실패로 읽었다**
(`사슬이 끝까지 안 갔다` + content_gate 경고 3건). 하네스가 틀리면 다음에 또 오판하므로
같이 고쳤다. 대조군으로 `sample-report.pdf` 를 같은 하네스로 돌려 **항상 통과하게
바뀐 게 아님**을 확인했다(청크 998 · 태그 17개 · vision 창 13개로 정상 진행).

### 43.5 새 회귀 방지선

이번 회귀가 안 보였던 이유는 **업로드 화이트리스트와 extract 목록을 대조하는 곳이
없었기** 때문이다. 그 대조를 테스트로 만들었다 — 업로드가 받는 doc_type 이 전부
`SUPPORTED` / `GRACEFUL_SKIP` 중 하나로 분류돼 있어야 하고, 남는 것은
`["image"]` 뿐이어야 한다. 새 값이 나타나면 그게 곧 조용한 회귀다.

> §41.7 의 "미이식 예시가 이식되면 테스트가 무의미해진다" 와 **다른 종류**다.
> 그건 예시가 낡는 문제였고, 이건 **두 목록이 따로 자라는** 문제다.

### 43.6 비용 실수 1건

대조군으로 93페이지짜리 `sample-report.pdf` 를 골라서 vision 40 회가 돌았다
(§35.1 실측 단가 기준 **$0.20~1.2**). 하네스가 항상 통과하지 않는지만 보면 됐으므로
작은 문서로 충분했다. 대조군은 **가장 싼 것**으로 고른다.

---

## 44. 입력 채널 3종 — 나머지 2개는 결정이 필요하다

`GET /me/email-ingest` · `POST /me/email-ingest/rotate` 는 이미 Edge 다
(`/me/*` 전체가 `api-account`). 실측: 비인증 401. 즉 주소 발급·회전은 이미 넘어가 있고
webhook 하나만 남아 있었다.

### 44.1 채널 사용량 실측 (2026-09-07)

```
총 문서 13
source_channel  {'drag-drop': 9, 'api': 3, 'email': 1}
doc_type        {'pdf': 9, 'hwpx': 2, 'pptx': 1, 'hwp': 1}
url 채널 0 · image doc_type 0 (삭제 포함)
```

### 44.2 `POST /documents/url` — trafilatura 가 막는다

본문 추출이 `trafilatura` 다. SSRF 검증·fetch·DNS rebinding 재검증은 전부 옮길 수 있는데
**추출 알고리즘만 JS 등가물이 없다**(Readability.js 계열은 출력이 다르다).

다만 **출력 대조의 보호 대상이 없다** — url 채널로 만들어진 문서가 **0건**이고,
웹 UI 에도 이 엔드포인트를 부르는 코드가 **없다**(`web/` 전체 검색 0건).

### 44.3 단독 이미지 업로드 — **막혀 있지 않다. 이전 보고를 정정한다**

직전에 "EXIF/HEIC 디코드가 Pillow 없이 필요하다"고 했는데 **HEIC 부분이 틀렸다.**
원본 `image_parser.py:101` 은 HEIC/HEIF 를 **디코드하지 않고 raw bytes 그대로**
Gemini 에 넘긴다(주석: "pillow-heif 등 추가 의존성 회피"). 필요한 건:

| 조각 | 상태 |
|---|---|
| LANCZOS 다운스케일 | **이미 있다** — `image_normalize.ts`, Pillow 와 12/12 바이트 일치 |
| png/jpg/webp 디코드·JPEG 인코드 | mupdf `Image → Pixmap → asJPEG` — vision 경로에서 이미 쓰는 방식 |
| EXIF orientation | 미구현. JPEG APP1 에서 Orientation 태그 읽고 8 변환 중 하나 적용 (~80줄) |
| PNG alpha 분기 | `optimize=True` PNG 인코딩은 바이트 일치가 어렵다 (LLM 입력이라 무해) |

`web/src/app/share/route.ts` 가 **Web Share Target 으로 이미지를 받는다** — 폰에서
사진을 공유하면 들어오는 경로다. 지금은 §43 의 판단대로 **시끄럽게 실패**한다.

### 44.4 다음 후보

| 후보 | 내용 | 근거 |
|---|---|---|
| **A** | secret 설정 → `/ingest/email` 컷오버 + 실제 메일 E2E | §42.5. 이미 만든 것을 라이브로 만드는 마지막 한 걸음 |
| **B** | 단독 이미지 업로드 이식 | §44.3. 막혀 있지 않다. Share Target 이 실제로 이미지를 받는다 |
| **C** | `POST /documents/url` 처리 방향 결정 | §44.2. 이식/폐기/보류 중 선택 — 사용 0건이라 폐기가 RAGAS 패턴과 같다 |
| **D** | 결제 3종 + `POST /billing/run` (135 + 398줄) | Railway 제거의 마지막 큰 덩어리 |

---

## 45. Phase 4 — 단독 이미지 업로드 (`33c9431`). **지원 포맷 5 → 6**

`web/src/app/share/route.ts` 의 Web Share Target 이 폰에서 사진을 받는데, 그 경로가
지금까지 §43 의 판단대로 **시끄럽게 실패**하고 있었다. 그걸 막은 게 무엇인지 다시 보니
내가 직전에 보고한 이유가 틀렸다.

### 45.1 정정 — HEIC 는 막고 있지 않았다

직전 보고: "EXIF/HEIC 디코드를 Pillow 없이 해야 한다."
실제: 원본 `image_parser.py:101` 은 HEIC/HEIF 를 **디코드하지 않는다.** raw bytes 를
그대로 Gemini 에 넘긴다(주석: "pillow-heif 등 추가 의존성 회피").
필요한 건 **EXIF orientation 하나**였다. 원본을 끝까지 안 읽고 요약한 결과다.

### 45.2 mupdf 를 상상하지 않고 전부 재 봤다

| 확인한 것 | 결과 |
|---|---|
| png·jpeg·gray 디코드 | 된다 |
| `getNumberOfComponents()` | 알파를 **포함**한다 (RGBA → 4) |
| EXIF 적용 | **안 한다** — 방향 6 인 1600×1200 을 그대로 준다 → 이중 적용 위험 없음 |
| 알파 | **premultiplied** (alpha=0 픽셀의 RGB 가 0,0,0) |
| `convertToColorSpace` 로 알파 떼기 | **못 한다** — "cannot drop alpha when converting pixmap" |
| `asJPEG` + 알파 픽스맵 | **던진다** — "pixmap may not have alpha to save as JPEG" |

→ 알파를 뗄 때는 `alpha=false` 픽스맵을 새로 만들어 색 채널만 옮긴다.

**자를 먼저 의심한 건 여기서도 맞았다.** 첫 프로브에서 `n+1` 로 알파를 읽어
`alpha_opaque` 를 `minAlpha=0` 으로 오판했다. 값이 이상해서 대상이 아니라 프로브를
고쳤고, 그제야 `alpha_real=0 / alpha_opaque=255` 라는 맞는 값이 나왔다.

### 45.3 대조 — 인코더를 방정식에서 뺐다

`api/scripts/verify_image_decode_parity.py` — **비교 163건 / 불일치 0건**.

처음엔 최종 JPEG 바이트로 PSNR 을 재고 "포팅 차이가 기준선보다 크다"며 10건을
실패로 찍었다. **그 판정이 틀렸다.** 서로 다른 인코더 둘을 비교하면 각자의 오차가
독립이라 PSNR 이 기준선보다 ~3dB 낮게 나오는 게 정상이다. 임계값을 완화하는 대신
**인코딩 직전 픽셀**을 비교하도록 바꿨다 — 디코드·회전·축소·RGB 변환까지가 내가
포팅한 전부이고, 거기까지는 완전 일치를 요구할 수 있다.

| 검사 대상 | 기준 | 결과 |
|---|---|---|
| 무손실(PNG) 입력 | **바이트 완전 일치** | `big_rgb.png`·`big_gray.png` 일치 (3·1채널 축소 증명) |
| 무손실 + 알파 | 불투명 픽셀 완전 일치 | `big_rgba.png` 일치 (4채널 축소 증명) |
| EXIF 8 방향 | 크기·픽셀 일치 | 8/8 일치 |
| JPEG 입력 | **축소가 증폭하지 않을 것** | 3.18→2.87 · 2.82→2.68 · 0.12→0.12 (전부 감소) |
| premultiplied 알파 | 투명 픽셀에만 | RGB 차이 100% 가 `alpha<255`, 불투명 0건, 알파 채널 완전 일치, 흰배경 합성 PSNR ∞ |

**JPEG 에 절대 임계값을 두지 않은 이유**: 그 값은 그림 내용에 따라 0.12~2.9 로 움직인다.
임계값을 정하면 픽스처를 바꿀 때마다 따라 올려야 하고, 그건 검사가 아니라 요식이다.
실제로 픽스처를 다시 만들자 2.5 기준이 바로 깨졌다. 그래서 **축소 직전 픽셀로 디코더
차이를 직접 재고 축소 후와 비교**하는 자기교정 방식으로 바꿨다.

음성 대조: 픽셀 **1바이트** 변조를 검출.

### 45.4 라이브 E2E

900×600 PNG 1장(**$0.006** — 대조군은 가장 싼 것으로, §43.6 의 교훈):

```
extract → chunk → tag_summarize → load → embed → doc_embed → dedup  전부 succeeded
청크 1 / dense_vec 1
태그: 인제스트, 이관, 현황, 업로드, 첨부, 수집, 김우민   ← 이미지 안의 글자
vision_usage_log  call_id 2197  source_type='image'  page=null  $0.006152
```

`source_type='image'` · `page=null` 은 원본의 단독 이미지 호출 의미와 같다.

### 45.5 같이 한 것

- `resizeRgbLanczos` → 채널 수를 받는 `resizeLanczosN` 으로 일반화. **구현이 하나여야**
  PDF 경로와 이미지 경로가 갈리지 않는다. PDF 회귀 확인: `verify_image_normalize_parity.py`
  **12/12 바이트 일치 유지**.
- 대조 픽스처(2.1MB)를 커밋하지 않고 스크립트가 매번 만든다. 생성이 결정적이라 같은
  바이트가 나오고 픽스처가 낡지 않는다. 진짜 카메라 사진을 보고 싶으면
  `api/scripts/fixtures/images/` 에 넣으면 자동 포함된다(gitignore).
- **미이식 예시를 하드코딩하지 않는다.** 예시로 쓴 포맷이 이식되면 테스트가 조용히
  무의미해지는 일이 `extract_test.ts` 에서만 **네 번** 반복됐다(hwpx → image → …).
  이제 집합에서 유도해서, 마지막 하나까지 이식되면 `undefined` 가 되어 즉시 드러난다.

### 45.6 남은 것

| 항목 | 상태 |
|---|---|
| `POST /ingest/email` 컷오버 | **secret 대기** (§42.5) — 코드·배포 완료 |
| `POST /documents/url` | trafilatura 차단. 사용 0건 · 웹 UI 호출 0건 → 이식/폐기 결정 필요 |
| `POST /payments/subscribe/*` · `POST /billing/run` | 135 + 398줄 — Railway 제거의 마지막 큰 덩어리 |
| PPTX Vision 보강 (`_vision_ocr_largest_picture`) | §41.6 |
| chunk 조각 c2 (`synonym_inject`) | 200줄 |

---

## 46. Phase 4 — 카카오페이 정기결제 (`35a1ed4`). **앱 라우트가 1개 남았다**

Railway 제거의 마지막 큰 덩어리. `/payments/subscribe/{ready,approve,cancel}` +
`POST /billing/run` 을 옮겼다. **프록시는 아직 배포하지 않았다** — §46.6 참조.

### 46.1 시작 전에 실측한 것 — 위험이 생각보다 낮았다

```
subscriptions 1행 — plan=pro status=active, billing_key **없음**, period_end=None
payment_history 0행
plans: free(0원) / pro(6900원)
```

암호문이 **아직 하나도 없다**(카카오페이 심사 대기). 즉 "기존 Fernet 암호문을 Edge 가
못 풀어 자동결제가 멈추는" 최악 시나리오는 지금 존재하지 않는다. 그래도 Fernet 은
정확히 맞춰야 한다 — 앞으로 쌓일 값을 양쪽이 다 읽어야 하기 때문이다.

### 46.2 승부처는 Fernet 이었다 — 먼저 증명하고 나머지를 썼다

`subscriptions.billing_key` = SID(빌링키)의 Fernet 암호문. Python `cryptography` 가
만든 걸 Edge 가 못 풀면 **자동결제가 통째로 멈춘다.** 그래서 다른 걸 쓰기 전에 이것부터
했다. WebCrypto(AES-128-CBC + HMAC-SHA256)로 명세대로 구현했다.

`verify_fernet_parity.py` — **비교 40건 / 불일치 0건**:

| 검사 | 결과 |
|---|---|
| Python 토큰 9종 → TS 가 푼다 (DB 의 기존 값) | 9/9 |
| TS 토큰 9종 → Python 이 푼다 (이관 중 역방향) | 9/9 |
| 손상 8패턴 (MAC·암호문·IV·버전·timestamp 변조, 절단, base64 아님, 빈 문자열) | 양쪽 다 거절 |
| 다른 키 9건 | 전부 거절 |
| 잘못된 키 4종 (빈 값·비 base64·16B·64B) | 양쪽 다 거절 |

**바이트 일치는 애초에 불가능하다** — IV 가 난수고 timestamp 가 현재 시각이다.
그래서 교차 복호화로 증명했다. 거절 동작까지 맞춘 건, 한쪽만 받아 주면 그게 곧 구멍이라서다.

### 46.3 돈이 걸린 코드라 대조 기준을 올렸다

검색 결과가 조금 달라지는 것과 **이중 청구**는 무게가 다르다. 응답만 보지 않고
**DB write 가 어떤 순서로 나갔는지**까지 비교했다. 양쪽 DB·KakaoPay 를 같은 스텁으로
갈고 서비스 함수는 진짜를 돌린다(재구현끼리 비교하면 아무것도 증명하지 않는다).

`verify_billing_parity.py` — **비교 69건 / 불일치 0건**. 시나리오 20건 중 핵심:

| 시나리오 | 지켜야 하는 것 |
|---|---|
| `billing_key` 없음 | `past_due` — 조용한 무한 skip 방지 |
| SID 복호화 실패 | **`past_due` 아님** — 설정 오류라 grace clock 을 건드리면 안 된다 |
| 이미 `past_due` | `past_due_since` 를 **덮어쓰지 않는다** — 덮으면 7일이 영원히 안 온다 |
| 이번 주기 이미 결제됨 | 결제 호출 **0회** (멱등 마커) |
| 결제 성공 후 갱신 실패 | `charged` 로 세고, 다음 배치가 멱등 처리 |
| 재클릭 | `pending_tid` 만 갱신 — Pro 접근이 끊기면 안 된다 |

날짜 9건도 같이: 말일 clamp(1/31→2/28, 윤년 2/29), 연도 넘김, 마이크로초 표기.

### 46.4 대조가 잡은 것 2건

**① `payment_history.detail` 에 `"Error: "` 가 붙고 있었다.**
JS `String(err)` 는 클래스 이름을 앞에 붙이는데 Python `str(exc)` 는 메시지만 준다.
관리자 화면에 보이는 값이라 실제 차이다.

같은 패턴이 **vision 경로 3곳**에도 있었다(`vision_usage_log.error_msg` —
`image_parser.ts` · `vision_enrich.ts` · `vision_scan.ts`). 전부 이번 세션에 내가 이식한
것들이다. `_shared/pyerror.ts` 로 빼고 4곳을 고친 뒤 vision 대조 4종을 재실행해
회귀 없음을 확인했다.

> 교훈: **DB 컬럼으로 나가는 예외 문자열**은 로그와 다르게 취급해야 한다.
> 결제 대조를 안 했으면 vision 쪽 3건은 계속 몰랐을 것이다.

**② `parseIso` 가 불가능한 날짜를 통과시켰다.**
`2026-02-29`(2026 은 윤년이 아니다)를 정규식이 받아 조용히 틀린 값을 계산했다.
Python `fromisoformat` 은 거부한다. 날짜 유효성 검사를 넣었다.
— 이건 **내 테스트 데이터의 오류**가 먼저 드러낸 것이다. 데이터가 틀렸다고 고치고
넘어갈 뻔했는데, 구현도 같이 틀려 있었다.

### 46.5 배포 중 걸린 것

`config.toml` 에 `[functions.api-payments]` 항목이 없어 **첫 배포가 실패**했다
(`Relative import path "jose" not prefixed` — import_map 을 못 찾는다).
함수 디렉터리만 만들면 되는 게 아니라 항목을 같이 넣어야 한다.

배포 후 실측:
```
POST /payments/subscribe/ready (비인증) → 401 {"detail":"로그인이 필요합니다."}
POST /billing/run (틀린 secret)        → 503 "billing cron 이 비활성 상태입니다"
GET  /billing/run                      → 405
프록시                                  → 아직 railway
```

### 46.6 컷오버가 막혀 있다 — **secret 3개**

| ENV | 없으면 |
|---|---|
| `JETRAG_KAKAOPAY_SECRET_KEY` | 결제 3종 **503** |
| `JETRAG_BILLING_KEY_ENCRYPTION_KEY` | 결제 3종 **503** |
| `JETRAG_BILLING_CRON_SECRET` | `/billing/run` **503** |

`verify_email_cutover.ts` → **`verify_cutover.ts`** 로 확장했다. 이제 이메일·결제 두
경로의 준비 상태를 함께 보고, 하나라도 미완료면 "워커를 배포하면 준비 안 된 쪽이
죽는다" 로 막는다. 이식은 됐는데 프록시를 안 돌린 경로가 **둘**이 되면서 배포 순서
사고 위험이 커졌기 때문이다.

### 46.7 cron 은 아직 안 만들었다 (사용자 결정 필요)

현재 주 경로는 Railway cron 의 `scripts/billing_charge.py` 다. Railway 를 없애면
누군가 매일 `/billing/run` 을 불러야 한다. Supabase 에서는 pg_cron 인데 그건
**신규 마이그레이션**(029)이라 가드레일상 사용자 확인이 필요하다.
마이그 028(인제스트 drain)이 Vault + `net.http_post` 패턴을 이미 만들어 뒀으므로
그걸 그대로 따르면 된다.

### 46.8 이관 현황 실측 (2026-09-07 기준)

프록시 `resolveTarget` 으로 FastAPI 라우트 전수를 판정한 결과:

```
총 라우트(메서드 단위) 34  ·  Edge 28  ·  Railway 6
```

남은 6개:

| 라우트 | 성격 |
|---|---|
| `GET /` · `/docs` · `/docs/oauth2-redirect` · `/openapi.json` · `/redoc` | FastAPI 자체 문서 — **Railway 와 함께 사라진다** |
| `POST /documents/url` | **유일하게 남은 앱 라우트** — trafilatura 차단 |

### 46.9 다음 후보

| 후보 | 내용 |
|---|---|
| **A** | secret 설정 → 이메일 + 결제 **동시 컷오버** (`verify_cutover.ts` 가 순서를 지킨다) |
| **B** | `POST /documents/url` 처리 — 사용 0건 · 웹 UI 호출 0건이라 폐기가 RAGAS 패턴과 같다 |
| **C** | billing cron 마이그 029 (pg_cron + Vault, 마이그 028 패턴) |
| **D** | PPTX Vision 보강 · `synonym_inject` 등 잔여 이식 |

---

## 47. `POST /documents/url` 폐기 (`9361661`). **앱 라우트 이관 완료**

본문 추출이 `trafilatura`(Python 전용)에 묶여 Edge 로 옮길 수 없었다. RAGAS 때와 같은
판단으로 닫았다 — 다만 이번엔 **근거를 전부 실측한 뒤** 사용자 확인을 받고 진행했다.

### 47.1 폐기 근거 (전부 측정)

| 항목 | 값 |
|---|---|
| url 채널로 만들어진 문서 | **0건** (`documents` 전수 조회) |
| 웹 UI 호출 | **0건** (`web/` 전체 검색) |
| iOS Shortcuts 가이드 | `/documents`(파일 업로드)를 쓴다 — `/documents/url` 아님 |
| Web Share Target | manifest 가 **files 전용** |
| `trafilatura` 사용처 | `upload_url` 제목 추정 + `url_parser.py` 본문 추출 — **둘뿐** |

마지막 항목이 결정적이었다. 라우트를 닫으면 **의존성이 통째로 빠진다.**

"iOS Shortcuts 호환성" 이 신설 근거였다는 기록(2026-04-24 명세)이 있어 외부 소비자를
의심했지만, 실제 가이드 문서는 `/documents` 를 쓰고 있었다. 근거 문장만 보고
"외부 사용자가 있다" 고 단정하지 않은 게 맞았다.

### 47.2 지운 것

```
upload_url (204줄) · UrlUploadRequest · _URL_FETCH_* 상수
url_parser.py (69줄) · _url_gate.py (189줄, SSRF) · test_url_gate.py (234줄, 12건)
extract.py 의 url 디스패처 · pyproject 의 trafilatura
총 763 줄 삭제 / 53 줄 추가
```

**SSRF 게이트도 같이 지웠다.** 호출자가 없어지면 죽은 코드인데, 살아 있는 것처럼 보이는
죽은 코드가 더 나쁘다. 필요해지면 이 커밋 이전에서 꺼내면 된다.

### 47.3 검증 — 기준선 대비

| 시점 | 결과 |
|---|---|
| 삭제 전 | 1465 tests / **4 failures** / 12 skipped |
| 삭제 후 (1차) | 1449 tests / **5 failures** ← 늘었다 |
| 원인 수정 후 | 1449 tests / **4 failures** / 12 skipped |

4 건은 전부 알려진 `test_embed_cache` 선재 실패다(단독 실행 시 통과). **회귀 0.**

늘어난 1 건의 원인: `test_nfc_normalize` 가 `documents.py` 에 NFC 정규화 호출이
**2 곳**(파일 + URL) 있어야 한다고 검사하고 있었다. URL 경로가 사라져 1 곳이 된 것이라
기대값을 고쳤다 — 남은 파일 업로드 경로가 실제로 정규화하는지 먼저 확인했다.

`fastapi_routes.json` 을 **실제 앱에서 다시 뽑았다**: 34 → 33, 사라진 것은
`POST /documents/url` 하나뿐.

### 47.4 프록시 테스트의 "미이관 예시" 를 데이터에서 유도하게 바꿨다

"Railway 로 가는 미이관 경로" 예시를 `/documents/url` 로 **하드코딩**하고 있었다.
그게 사라지면서 테스트가 뜻을 잃었다. 같은 패턴이 `/stats/overview`(전환),
`/documents` 읽기(전환)에 이어 **세 번째**다.

이제 라우트 전수에서 유도한다 — 마지막 하나까지 넘어가면 `undefined` 가 되어 즉시
드러난다. (`extract_test.ts` 의 미이식 포맷 예시도 §45.5 에서 같은 이유로 바꿨다.)

### 47.5 라이브 확인

```
POST /documents/url  →  401 → (Railway 재배포) → 405
```

405 인 이유는 `GET /documents/{doc_id}` 가 같은 경로를 잡기 때문이다 — FastAPI 의
정상 동작이고, 라우트가 사라졌다는 뜻이다.

### 47.6 덤으로 드러난 **원본 버그** (고치지 않음)

확인하다가 `GET /documents/url` 이 **500** 인 걸 발견했다. 비-UUID doc_id 전부
그렇다(`not-a-uuid`, `12345`, `abc-def`).

**원본 Railway 도 똑같이 500 이다.** 즉 이식 결함이 아니라 원본 버그를 그대로 옮긴
것이다. 유효하지만 없는 UUID 는 양쪽 다 `404 {"detail":"문서를 찾을 수 없습니다."}` 로
일치한다.

이관 중에는 고치지 않는다 — 고치면 그게 곧 원본과의 차이가 된다.
기존 이월 목록(MMR 도달 불가 `search.py:1286`, `9999-12-31` 질의 → 500)에 추가한다.

### 47.7 이관 현황 — **앱 라우트가 0 개 남았다**

```
총 33  ·  Edge 28  ·  Railway 5
```

Railway 에 남은 5 개는 **전부 FastAPI 자체 문서 페이지**다:
`GET /` · `/docs` · `/docs/oauth2-redirect` · `/openapi.json` · `/redoc`.
Railway 를 끄면 함께 사라진다 — 옮길 대상이 아니다.

### 47.8 Railway 를 끄기 전에 남은 것

| # | 항목 | 성격 |
|---|---|---|
| 1 | **이메일 + 결제 컷오버** | secret 4 개(§42.5 · §46.6). 사용자 실행 필요 |
| 2 | **billing cron** | 마이그 029 (pg_cron + Vault). 신규 마이그레이션 = 사용자 확인 필요 |
| 3 | 프런트의 API base URL | 프록시를 보고 있으므로 변경 불요 (확인 필요) |
| 4 | PPTX Vision 보강 · `synonym_inject` | 기능 잔여 — Railway 제거의 차단 요인은 아님 |

### 47.9 다음 후보

| 후보 | 내용 |
|---|---|
| **A** | secret 설정 → 이메일 + 결제 컷오버 (`verify_cutover.ts` 가 순서를 지킨다) |
| **B** | billing cron 마이그 029 |
| **C** | Railway 종료 리허설 — `LEGACY_ORIGIN=""` 로 두고 무엇이 깨지는지 실측 |
| **D** | PPTX Vision 보강 · `synonym_inject` 등 잔여 이식 |

---

## 48. Railway 종료 리허설 (`c337e8a`). **드리프트 3건을 잡았다**

`LEGACY_ORIGIN` 을 비우면 Railway 의존이 끝난다. 진짜로 비우면 그 순간 트래픽이 죽으므로
**오프라인에서 재현**했다. `api/scripts/rehearse_railway_shutdown.ts`.

### 48.1 표만 보지 않고 워커를 그대로 돌린다

`resolveTarget` 만 부르면 라우팅 표만 보는 것이고 `LEGACY_ORIGIN` 분기·자기참조 검사·
헤더 주입을 안 거친다. 워커의 `fetch` 핸들러를 그대로 부르고 **네트워크만 가로챘다.**

### 48.2 저장소 기준 결과

```
라우트 33개 · 메서드 단위 33건
  그대로 도는 것  28건 — 전부 Edge
  끄면 404       5건 — 전부 FastAPI 자체 문서
                       (/ · /docs · /docs/oauth2-redirect · /openapi.json · /redoc)
앱 라우트: 0건
```

라우트 표 밖도 확인했다:

| 경로 | 지금 | 끈 뒤 |
|---|---|---|
| `GET /search/` · `/documents/` | Edge 200 | Edge 200 (후행 슬래시를 떼고 받는다) |
| `GET /me` · `/favicon.ico` · 없는 경로 | Railway | **404** |
| `POST /documents/url` (§47 폐기) | Railway | **404** |

### 48.3 **`--live` 가 핵심이었다 — 자를 의심한 게 맞았다**

리허설이 "끄면 문서 페이지만 사라진다" 고 말했다. 그런데 이 스크립트는 `routes.js` 를
import 해서 도는 것이라 **저장소를 재는 것**이지 프로덕션이 아니다. 이메일·결제 규칙은
커밋만 돼 있고 **배포 전**이다.

그래서 배포된 워커를 실제로 찔러 대조하는 `--live` 를 붙였다. 헤더로 판정한다
(`x-served-by` vs `x-railway-request-id`) — 오류 본문은 양쪽이 비슷할 수 있다.

```
POST /ingest/email               저장소 Edge / 배포 Railway   ← 드리프트
POST /billing/run                저장소 Edge / 배포 Railway   ← 드리프트
POST /payments/subscribe/ready   저장소 Edge / 배포 Railway   ← 드리프트
GET  /health                     저장소 Edge / 배포 Edge      일치
GET  /documents/active           저장소 Edge / 배포 Edge      일치
```

드리프트가 있으면 **exit 1**. 이게 없었으면 리허설이 "안전하다" 고 말하는데 프로덕션은
아닌 상태로 남았을 것이다.

> 이번 세션에서 자[尺]를 의심해 건진 게 이걸로 네 번째다
> (mupdf 알파 오프셋 §45.2 · PSNR 판정 기준 §45.3 · e2e graceful skip §43.4 · 여기).

### 48.4 비-HTTP 의존 — 자동으로 못 잰다

| # | 항목 | 끄면 |
|---|---|---|
| 1 | Railway cron `billing_charge.py` | **월 자동결제가 멈춘다.** 마이그 029 미작성 |
| 2 | `.github/workflows/monitor-search-slo.yml` | 매일 02:00 UTC `JET_RAG_API_BASE` 로 `/stats` 를 친다. 그 secret 이 Railway 면 실패 → 프록시 도메인으로 교체 |
| 3 | `verify_documents_read_parity.py` | 비교 대상이 사라져 못 돈다 — 이관이 끝나면 역할도 끝나므로 정상 |
| 4 | FastAPI 자체 문서 | 함께 사라진다. 대체물을 둘지는 결정 사항 |
| 5 | 프런트(Vercel) | **의존 아님** — §48.5 |

`gh` 가 미인증이라 워크플로 실행 이력은 확인 못 했다(미검증).

### 48.5 §47.8 의 "확인 필요" 항목을 닫는다 — 프런트는 의존이 아니다

Vercel `NEXT_PUBLIC_API_BASE_URL` = `https://jetrag-api.woong-s.com` (**프록시 도메인**).
2026-05-19 도메인 부착 때 그렇게 설정한 기록이 있다.

배포된 번들에서 직접 확인하려 했지만 랜딩 청크 9 개에는 값이 없었다(인증 페이지 청크에
inlining 된다). **기록 기반 확인**이고 라이브 재확인은 Vercel 대시보드에서 1 분이면 된다.

### 48.6 끄기 전 순서

```
1. secret 4개 설정 → deno run --allow-net api/scripts/verify_cutover.ts
2. 프록시 배포 (이메일·결제가 Edge 로) → 리허설 --live 가 드리프트 0 이 된다
3. billing cron 대체 (마이그 029)
4. monitor-search-slo 의 JET_RAG_API_BASE 를 프록시 도메인으로
5. 그 다음에 LEGACY_ORIGIN 비우기
```

### 48.7 다음 후보

| 후보 | 내용 |
|---|---|
| **A** | secret 4개 설정 → 이메일 + 결제 컷오버 (1·2단계) |
| **B** | billing cron 마이그 029 (3단계) — 신규 마이그레이션이라 확인 필요 |
| **C** | `monitor-search-slo` 의 API base 교체 (4단계) — 저장소 안에서 할 수 있는 유일한 잔여 |
| **D** | PPTX Vision 보강 · `synonym_inject` 등 기능 잔여 |

---

## 49. `monitor-search-slo` 의 Railway 의존 제거 — 그리고 **깨져 있던 CI 전부 복구** (`2f5e92f`)

### 49.1 원래 하려던 것

`JET_RAG_API_BASE` secret 이 Railway 주소를 담고 있어 Railway 를 끄면 이 workflow 가
조용히 실패한다(§48.4-2). API base 는 **공개 URL 이라 secret 이 아니므로** 프록시
도메인을 기본값으로 박고 secret 을 더 이상 읽지 않게 했다. `if:` 가드도 뗐다.

실측(스크립트를 프록시로 돌림): 표본 출처 `search_metrics_log (최근 500행)` ·
p50 167ms · p95 1290ms · cache hit 0.990. `monitor_search_slo.py` 의 "여전히
in-memory ring buffer 기반" docstring 은 낡아서 고쳤다 — 렌더러는 이미
`search_slo.source` 를 읽고 있었다(2026-09-05 Edge 이관).

**남은 확인 1건**: repo *variable* `JET_RAG_API_BASE` 가 Railway 주소로 설정돼 있으면
여전히 그쪽이 이긴다. 저장소에서는 볼 수 없다 — Settings → Variables 확인 필요.

### 49.2 그 과정에서 CI 가 깨져 있는 걸 발견했다 — **상당수가 내 탓이다**

세션 시작 커밋(`5134b74`)을 꺼내 같은 검사를 돌려 대조했다:

| 항목 | 세션 시작 | 발견 시점 | 원인 |
|---|---|---|---|
| edge fmt | **통과** | 29건 실패 | **내가 깨뜨림** |
| edge lint | 2건 | 5건 | 3건 **내가 추가** |
| proxy test | 17/2 실패 | 더 악화 | 선재 + **내가 악화** |
| proxy lint | 1건 | 1건 | 선재 |
| py unittest | 4건 실패 | 4건 실패 | 선재 |
| py `uv sync --frozen` | **통과** | 실패 | **내가 깨뜨림** (§47) |

### 49.3 앞선 판단이 틀렸다

세션 중 `deno fmt --check` 가 18/53 실패하는 걸 보고 **"이 저장소는 fmt 를 강제하지
않는다"** 고 결론내고 재포맷을 안 했다. 그때 이미 내가 만든 미포맷 파일을 보고 원래
그런 줄 오독한 것이다. 실제로는 **CI 가 fmt 를 검사하고 세션 시작 시점엔 통과**했다.

> "선재 결함" 은 편한 결론이라 검증 없이 채택하기 쉽다. 그러면 내가 만든 회귀가 그
> 라벨 아래 숨는다. **선재라고 부르려면 대조 근거를 갖는다.**
> (`git archive <시작커밋> | tar -x -C /tmp/base` 로 1 분이면 된다.)

### 49.4 고친 것

| 대상 | 내용 |
|---|---|
| edge fmt | `deno fmt` (29파일) |
| edge lint 3건(내 것) | `pdf_extract.ts` 주석의 **U+FEFF 리터럴** 2건을 `U+FEFF` 표기로 · `worker_test.ts` 미사용 `calls` 제거 |
| edge lint 2건(선재) | 선언 앞의 미사용 `deno-lint-ignore` 제거 |
| **proxy test** | `Deno.readTextFile` 이 `--allow-read` 를 요구하는데 CI 는 `deno test --allow-net` 이다. **정적 JSON import** 로 바꿔 권한 없이 읽게 했다 — 선재 2건 + §47 에 내가 추가한 것이 함께 해소 |
| proxy lint(선재) | `async fetch` 에 사유를 적은 ignore |
| **uv.lock** | §47 에서 `pyproject` 의 trafilatura 만 지우고 락을 안 잡아 `uv sync --frozen` 이 깨졌다. `uv lock` 재생성 = **삭제 133줄뿐**(전이 의존 10개, 버전 상승 0) |
| **py unittest 4건(선재)** | §49.5 |

### 49.5 오래된 4건의 진짜 원인 — 2단 캐시

`test_embed_cache` 는 in-process LRU 만 가정하는데 `embed_query` 는 **2단 캐시**다:
① in-process LRU ② DB `embed_query_cache`. 다른 테스트가 Supabase 자격증명을 올려놓으면
②가 hit 을 내서 `_last_cache_hit` 가 True 가 된다 — 그래서 **단독은 통과하고 전체는
실패**했다. `setUp` 에서 ②를 끊었다(`lookup`→None, `upsert`→no-op).

이 4건은 이전 세션부터 "알려진 선재 실패" 로 넘겨 왔다. 원인을 한 번 파니 5줄이었다.

### 49.6 검증 — CI 9개 항목을 로컬에서 그대로 재현

```
py    : uv sync --frozen 통과 · unittest **0 failures** · 대조 스크립트 14/14
edge  : fmt 통과 · lint 통과 · test 237 passed
proxy : fmt 통과 · lint 통과 · test 20 passed
종합  : 전부 통과
```

`unittest` 가 0 failures 로 끝난 건 이 프로젝트에서 처음이다.

### 49.7 §48.6 체크리스트 갱신

```
1. secret 4개 설정 → verify_cutover.ts                  ⬜ 사용자 실행
2. 프록시 배포 → 리허설 --live 드리프트 0                  ⬜ 1 이후
3. billing cron 대체 (마이그 029)                        ⬜ 사용자 확인 필요
4. monitor-search-slo API base 교체                     ✅ **이 커밋**
   (남은 것: repo variable 이 Railway 를 가리키지 않는지 확인)
5. LEGACY_ORIGIN 비우기                                 ⬜ 1~4 이후
```

### 49.8 다음 후보

| 후보 | 내용 |
|---|---|
| **A** | secret 4개 설정 → 이메일 + 결제 컷오버 (1·2단계) |
| **B** | billing cron 마이그 029 (3단계) — 신규 마이그레이션이라 확인 필요 |
| **C** | PPTX Vision 보강 (`_vision_ocr_largest_picture`, §41.6) |
| **D** | chunk 조각 c2 `synonym_inject` (200줄) |

---

## 50. 마이그 029 — pg_cron 정기결제 배치 (`77f6bda`). **작성만, 미적용**

Railway 를 끄면 `billing_charge.py` 를 돌리던 cron 이 사라진다. 마이그 028(인제스트
drain)의 Vault + `net.http_post` 패턴을 그대로 따라 그 자리를 채웠다.

**적용하지 않았다.** 신규 마이그레이션이고 돈을 움직이는 경로라 SQL Editor 실행은
사용자 몫이다.

### 50.1 §48.4-1 을 정정한다 — **지금 Railway cron 은 돌고 있지 않다**

리허설에 "끄면 월 자동결제가 멈춘다" 고 적었는데 **틀렸다.** W5-6 에서 `0 18 * * *` 로
걸기로 해 놓고 카카오페이 SECRET_KEY 대기 때문에 **켜지 않았다**
(work-log 2026-07-08 §3 · 메모리 `jetrag_w5_6_kakaopay_ship`).

즉 끈다고 멈출 결제가 없다 — 처음부터 대기 상태였다. 이 마이그는 **돌던 것을 옮기는
게 아니라 처음부터 대기 중이던 것을 Edge 쪽에 세우는 것**이다. 리허설 문구도 고쳤다.

원본 cron 은 `charge_due_subscriptions()` → `sweep_past_due()` 를 그대로 부른다.
`POST /billing/run` 이 같은 순서로 같은 일을 한다 — 등가다.

### 50.2 028 과 다르게 한 것 둘 — 둘 다 근거가 있다

**① 큐 가드를 안 넣었다.**
028 은 10 초마다 돌아 "큐가 비면 호출 안 함" 이 필요했다(하루 8,640 회).
여기는 **하루 1 회**라 아낄 게 없다. 반대로 가드 조건을 서비스의 선택 조건과 어긋나게
쓰면 **청구가 조용히 안 도는** 실패가 생긴다. 절약(1회/일)보다 그 위험이 크다.

**② Authorization 헤더를 안 붙였다.**
실측: 헤더 없이 POST 해도 함수가 실행된다(503 은 게이트웨이가 아니라 **우리 게이트**의
응답이다. `Bearer invalid` 로도 동일). 보안 경계는 `X-Billing-Cron-Secret` 이므로
필요 없는 `service_role` 키를 한 곳 더 복사해 두지 않는다.

### 50.3 실행 없이 할 수 있는 검증을 다 했다

로컬 Postgres·Docker 가 없어 SQL 실행 검증은 **불가**하다. 대신:

| 검증 | 결과 |
|---|---|
| **이미 적용돼 돌고 있는 028 과 구문 대조** | `net.http_post` 인자 이름(`url`/`headers`/`body`/`timeout_milliseconds`) 동일 · `LANGUAGE plpgsql` · `SECURITY DEFINER` · `vault.decrypted_secrets` 조회 · `REVOKE` 대상 · `cron.unschedule ... WHERE EXISTS` 모두 동일 |
| **헤더 casing** | pg_cron 이 보낼 `X-Billing-Cron-Secret` 을 Deno `Headers.get("x-billing-cron-secret")` 이 읽는다 (HTTP 헤더는 대소문자 무관) |
| **pg_cron 이 보낼 형태 그대로 라이브 호출** | `503 "billing cron 이 비활성 상태입니다"` — 우리 게이트에 도달. 마이그 STEP 3 상태코드 표의 예상값 그대로다 |

> 중간에 자를 두 번 고쳤다: `net.http_post` 인자 정규식이 빈값을 내서 다시 짰고,
> CI 재현 스크립트가 zsh 의 미인용 변수 비분할 때문에 `deno "fmt --check"` 를
> 한 덩어리로 넘겨 거짓 실패를 냈다.

### 50.4 Edge 가 Python 스크립트보다 안전하다

`billing_charge.py` 는 결제 키가 없으면 `get_payment_provider()` 에서 **RuntimeError** 다
(그래서 켜지 못했다). `/billing/run` 은 provider 를 만들기 **전에** 503 으로 끊는다
(`routes.ts` 의 `ensureEnabled`). 키를 넣기 전에 스케줄을 걸어 둬도 **매일 503 한 번**일
뿐이다. 이 순서는 `billing_test.ts` 가 이미 고정하고 있다.

### 50.5 사람이 할 일

```sql
-- 1) Vault (SQL Editor, 1회)
select vault.create_secret('<JETRAG_BILLING_CRON_SECRET 과 같은 값>',
  'billing_cron_secret', 'POST /billing/run 게이트. 마이그 029.');
select vault.create_secret(
  'https://mpmtydudhojpukuuadrd.supabase.co/functions/v1/api-payments/billing/run',
  'billing_run_url', 'pg_cron 이 부를 결제 배치 엔드포인트. 마이그 029.');
-- 2) api/migrations/029_billing_cron.sql 실행
-- 3) STEP 3 검증 쿼리 (특히 net._http_response 의 status_code)
```

`Vault 값 ≠ Edge secret` 이면 매일 401 이 쌓인다 — STEP 3-3 의 상태코드 표로 바로 구분된다.

### 50.6 Railway 종료 체크리스트

```
1. secret 4개 설정 → verify_cutover.ts        ⬜ 사용자 실행
2. 프록시 배포 → 리허설 --live 드리프트 0       ⬜ 1 이후
3. billing cron 마이그 029                    🟡 **작성 완료 · 적용 대기**
4. monitor-search-slo API base 교체           ✅ §49
5. LEGACY_ORIGIN 비우기                       ⬜ 1~4 이후
```

### 50.7 다음 후보

| 후보 | 내용 |
|---|---|
| **A** | secret 4개 설정 → 이메일 + 결제 컷오버 (1·2단계) — 남은 차단 요인의 전부 |
| **B** | PPTX Vision 보강 (`_vision_ocr_largest_picture`, §41.6) |
| **C** | chunk 조각 c2 `synonym_inject` (200줄) |
| **D** | 원본 이월 버그 정리 (MMR 도달 불가 · `9999-12-31` 500 · 비-UUID doc_id 500) |

---

## 51. 원본 이월 버그 3건 정리 (`91c13b1`)

2026-09-05 에 "이관 후 고친다"로 미뤄 둔 것들이다. `/search` 와 `/documents/*` 가
**100% Edge** 가 되어(프록시가 Railway 로 안 보낸다) 두 백엔드가 갈릴 구간이 없어졌다.
셋 다 원본에도 있는 버그이므로 **의도된 divergence** 다.

### 51.1 비-UUID doc_id → 500 (사용자가 만들 수 있다)

`documents.id` 가 uuid 컬럼이라 `eq("id","url")` 이 Postgres 오류를 내고 그대로 샜다.
**원본 Railway 도 동일**(실측):

| 경로 | 고치기 전 | 고친 뒤 | 유효하지만 없는 UUID |
|---|---|---|---|
| `GET /documents/{id}` | 500 | **404** | 404 |
| `GET /documents/{id}/status` | 500 | **404** | 404 |
| `GET /documents/batch-status?ids=` | 500 | **200 `{"items":[]}`** | 200 `{"items":[]}` |

`isUuid` 가드를 DB 호출 **전에** 둔다. 존재할 수 없는 id 이므로 **이미 있는 404 로
합류**시킨다 — 새 응답을 만든 게 아니다. reingest 2 종은 `fetchOwnedDoc` 한 곳에 넣어
함께 해결했다.

§47 에서 `POST /documents/url` 을 폐기한 직후 `GET /documents/url` 을 확인하다 발견했다.

### 51.2 `9999-12-31` / `9999년 12월` → 500

Python `datetime` 상한이 9999 라 하루 더하기·다음 달 1 일 계산이 `try` 밖에서 샜다.

**고침은 특별한 처리가 아니라 인위적으로 넣었던 `throw` 를 뺀 것**이다:
- JS `Date` 는 275760 년까지 된다
- `utcMidnightIso` 가 수동 포맷이라 `10000-01-01T00:00:00+00:00` 문자열이 나온다
- **Postgres 가 그 값을 받는다** — timestamptz 상한은 294276 년(실측으로 확인하고 진행)

라이브: `9999-12-31 자료` · `9999년 12월 자료` · `9999-12-30 자료` 전부 **200**.

### 51.3 MMR 이 도달 불가능하던 것 — **영향 범위를 재고 켰다**

원본은 `for r in rpc_rows: doc_id = r["doc_id"]`(`search.py:1286`)가 파라미터를 덮어써
게이트의 `doc_id is None` 이 항상 False 다. 파라미터를 그대로 쓰도록 되돌렸다.

게이트의 실질 조건은 `isCrossDocQuery`(T1 단독)다. 켜기 전에 그걸 쟀다:

| 측정 | 결과 |
|---|---|
| 골든 20 건 중 T1 발화 | **0 건** |
| 배포 전후 골든 top doc | **20/20 완전 동일** (직접 대조) |
| T1 패턴 질의("A 와 B 문서", "문서들에서") | 8/8 발화 |
| 라이브 `이력서와 포트폴리오 비교` | 2 위 rel=**0.5169** 가 3·4 위(0.5847·0.5752)보다 **위** — 다양성 재정렬이 실제로 걸렸다 |

되돌리려면 배포 없이 **`JETRAG_MMR_DISABLE=1`** 하나면 된다(킬스위치가 원래 있다).

### 51.4 대조 스크립트를 조용히 실패하게 두지 않았다

`verify_meta_fast_path_parity` · `verify_search_pipeline_parity` 에
`INTENTIONAL_DIVERGENCE` 목록을 두고 **사유를 매번 출력**한다.

핵심은 반대 방향 가드다 — **적어 놓고 실제로는 일치하면 실패**시킨다. 죽은 예외 목록이
쌓이면 진짜 회귀를 덮기 때문이다.

### 51.5 하려다 못 한 것 — **골든셋이 낡았다**

사용자 결정(2026-09-05)에 "MMR 은 골든셋 재측정이 따라붙는다" 고 적혀 있었다.
그런데 실측하니:

```
현재 문서 id(앞8): 0ed5a024 2303ef36 2e083cb0 430e9fbe 4783ea68 51dd2cc0 613a4c6b
                   80b48fa9 a5f3cfae b218e8a1 bc7b4591 c9d397fd d1259dfe
골든 기대 id      : 3970feab 49ef8d01 6004fd65 b758eec4 dd8c1fb0
교집합            : **없음**
```

문서가 재업로드되며 id 가 전부 바뀌었고, **쏘나타 문서는 아예 없다**. 그래서
`top-1 hit 0/20` 이 나오는데 이건 **검색 품질이 아니라 골든셋이 낡은 것**이다.

다만 MMR 이 골든 질의에 발화하지 않으므로(§51.3) 이 재측정은 **애초에 MMR 판정에
쓸 수 없었다.** 대신 배포 전후 순위 동일성을 직접 확인하는 것으로 대체했다.

> 골든셋 갱신은 별도 작업이다. 기대값을 "지금 1 위인 문서" 로 채우면 **순환 논증**이라
> 항상 100% 가 된다 — 제목 기준 ground truth 로 다시 만들고, 없어진 문서는 다시 올리거나
> 질의를 빼야 한다. 사용자 결정이 필요하다.

### 51.6 도구 수정 — 같은 것을 두 이름으로 부르고 있었다

`golden_batch_smoke.py` 는 `JETRAG_API_BASE_URL`, `monitor_search_slo.py` 는
`JET_RAG_API_BASE` 를 읽는다. 한쪽 이름으로 넘기면 **조용히 localhost 로 떨어져 20 건
전부 err** 이 된다 — 실제로 한 번 헛돌았고 "API 가 죽었나" 로 오해할 뻔했다.
둘 다 받게 했다.

### 51.7 이번 세션의 자[尺] 오판 다섯 번째·여섯 번째

- `isCrossDocQuery(q, null)` 로 재서 T1 발화 0 건이 나왔다. `decision` 을 **명시적으로
  null 로 주면 항상 false** 다 — 인자를 빼야 `route(query)` 가 돈다.
- CI 재현 스크립트가 zsh 의 미인용 변수 비분할 때문에 `deno "fmt --check"` 를 한 덩어리로
  넘겨 거짓 실패를 냈다(§50.3 에 이어 반복).

### 51.8 다음 후보

| 후보 | 내용 |
|---|---|
| **A** | secret 4개 설정 → 이메일 + 결제 컷오버 — Railway 종료의 남은 차단 요인 전부 |
| **B** | 골든셋 갱신 (§51.5) — 제목 기준 ground truth 재작성 + 없어진 문서 처리 |
| **C** | PPTX Vision 보강 (`_vision_ocr_largest_picture`, §41.6) |
| **D** | chunk 조각 c2 `synonym_inject` (200줄) |
