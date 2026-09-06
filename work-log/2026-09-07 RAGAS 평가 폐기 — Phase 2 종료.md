# 2026-09-07 — RAGAS 평가 폐기, Phase 2(읽기 경로) 종료

> **범위**: Edge 로 옮길 수 없던 4 라우트(`ragas` 의존)를 **폐기**해 Phase 6 의 차단 요인을
> 없애고, Phase 2 를 닫기까지.
> **다음 세션 재진입**: Phase 3 — `/documents/*` 9 라우트(쓰기 포함) 이관.

## 0. 한눈에 보기

| 항목 | 상태 |
|---|---|
| RAGAS 평가 엔드포인트 4개 제거 | ✅ (dd89d97) |
| 프론트 카드 2개 + API 함수 4개 + 안내 문구 제거 | ✅ |
| `ragas_eval.py` 서비스·의존성 | ⬜ **남김** (evals 오프라인 도구가 씀) |
| 내가 남긴 깨진 테스트 1건 수정 | ✅ |
| **Phase 2 (읽기 경로)** | ✅ **종료** |

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
