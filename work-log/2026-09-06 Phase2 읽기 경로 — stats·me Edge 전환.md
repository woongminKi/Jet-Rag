# 2026-09-06 — Phase 2 읽기 경로: `/stats` · `/me/*` · `/admin/*` Edge 전환

> **범위**: `/stats`, `/stats/trend`, `/me/*` 4개, `/admin/*` 4개를 Supabase Edge Function
> 으로 이관하고 Cloudflare 프록시 스위치를 열기까지. 선행 조건이던 `vision_usage` 의
> 프로세스 로컬 상태를 DB 기준으로 옮기는 작업 포함.
> **다음 세션 재진입**: `/answer` **HTTP shell + 배포 + 프록시 개방**부터.
> 모듈 포팅과 대조는 끝났고(d4029da) 라우팅·배포만 남았다.
> (정정 — 앞선 기록에 "스트리밍이라 별도 함수 검토 필요"라고 썼는데 **틀렸다**.
> `answer.py` 헤더의 설계 결정 Q6 이 "동기 호출, streaming 은 v1.5 이후"이고
> `StreamingResponse`·`yield` 가 하나도 없다. 원본을 안 읽고 쓴 가정이었다.)

## 0. 한눈에 보기

| 항목 | 상태 |
|---|---|
| `vision_usage` DB 기준 전환 (Railway 선행) | ✅ 완료 (fa6d9e6) |
| `/stats` · `/stats/trend` Edge 포팅 + 전환 | ✅ 완료 (b0a5d58) |
| `/me/plan` `/me/subscription` `/me/email-ingest` `/me/email-ingest/rotate` | ✅ 완료 (c8368bd) |
| 프록시 `ROUTES` 에 `/stats`, `/me/` 개방 | ✅ 배포됨 |
| `/admin/queries/stats` · `/admin/feedback/stats` 포팅 + 전환 | ✅ 완료 (d6bc9e6) |
| `/admin/subscriptions` (GET+POST) 포팅 + 대조 | ✅ 코드 완료 — **프록시는 미개방**(POST 가 쓰기) |
| `/answer` 본체 모듈 포팅 + 대조 | ✅ 완료 (d4029da) — **HTTP shell·배포·프록시는 미완** |
| `/answer/feedback` · `/answer/eval-ragas` · `/search/eval-precision` | ⬜ 대기 (5개 라우트) |
| `/search/eval-precision` 프록시 회귀 | ✅ 수정·배포 (5a74ea6) |

## 1. `/stats` — 프로세스 로컬 상태를 먼저 걷어냈다

`/stats` 응답의 `vision_usage` 는 `vision_metrics.py` 의 **모듈 전역 카운터**를 읽고 있었다.
Edge 는 요청마다 다른 아이솔레이트일 수 있어 이 값이 유지되지 않는다 — 그대로 옮기면 이관
직후 `vision_usage` 가 조용히 0 이 된다. `search_slo` 의 링 버퍼도 같은 문제였고 그건
직전 세션에 처리했다(3c36178).

그래서 **Railway 쪽을 먼저 DB 기준으로 바꾸고**(fa6d9e6), 두 경로가 같은 값을 내는지 확인한
다음에 포팅했다. 순서를 바꿨으면 "이관했더니 지표가 0" 을 이관 탓인지 원래 그런지 구분할 수
없었다.

- `_today_start_kst()` + `count="exact"` 질의로 오늘(KST) 사용량을 센다.
- 응답에 `source` 필드를 넣어 링 버퍼/DB 중 어느 쪽을 읽었는지 드러낸다.

**검증**: `verify_vision_usage_parity.py`, `verify_stats_parity.py`, `verify_stats_aggregate_parity.py`
전부 FAIL 0. 전환 전 HTTP 13건 일치, 전환 후 운영 경로 9건 일치.

## 2. `/me/*` — 라우팅과 인증의 **순서**가 계약이었다

처음엔 `/me/` 로 들어오면 즉시 인증을 걸었다. 그랬더니 없는 경로·틀린 메서드가 전부 401 이
됐다. FastAPI 는 경로·메서드 매칭이 dependency 보다 앞이라 원본은 404·405 를 낸다.

| 요청 | 원본 | 초기 구현 | 수정 후 |
|---|---|---|---|
| `GET /me/email-ingest/rotate` | 405 | **401** | 405 |
| `POST /me/plan` | 405 | **401** | 405 |
| `GET /me/bogus` | 404 | **401** | 404 |

**배포 후 HTTP 대조에서만 드러났다.** in-process 대조는 이 계층을 안 거친다. 라우팅을 먼저
하고 매칭된 경우에만 인증하도록 고쳤다.

### 그 밖에 대조로 잡힌 것

- **토큰 길이** — 16 으로 짐작했는데 원본 `_TOKEN_LEN = 8`.
- **`rotated_at` 형식** — Python `isoformat()` 은 마이크로초 0 이면 소수부를 통째로 생략한다.
  `toISOString()` 은 늘 `.000` 을 붙인다. `_shared/pytime.ts` 로 뽑고 `stats/pipeline.ts` 의
  중복 구현도 여기로 합쳤다.
- **`past_due` 는 플랜을 유지한다** — 유예 기간이라 `active` 와 같이 취급한다.
- **`usage_counters` 는 UTC 날짜** — 같은 응답의 `/stats` 쪽 집계는 KST 다. 원본이 그런 상태라
  통일하지 않았다.

### `rotate` 는 실행하지 않고 대조한다

`POST /me/email-ingest/rotate` 는 토큰을 재발급해 **구 주소를 즉시 무효화**한다. 대조하겠다고
부르면 사용자의 수신 주소가 바뀐다. 토큰·시각을 주입할 수 있게 만들어 두고 **요청 행 + 질의
URL 만** 대조했다. HTTP 검증에서도 비인증 401 까지만 확인하고 인증 호출은 하지 않았다.

## 3. 검사기가 초록인데 아무것도 안 태우던 문제

`verify_me_quota_parity.py` 의 합성 케이스에서 `subscriptions` · `documents` 행에 `user_id` 를
빼먹었다. `eq("user_id", ...)` 에 전부 탈락해 **양쪽 다 "구독 없음 → free"** 로 떨어졌고, 값이
같으니 초록이었다 — `past_due` 분기가 통째로 안 태워진 채로.

음성 대조(`past_due` 를 유효 상태에서 빼기)가 0 건을 잡아서 드러났다. 고친 방식:

1. 행을 만드는 헬퍼(`sub()` / `doc()` / `usage()`)가 필터 키를 **항상** 넣는다.
2. 케이스마다 기대 `plan_code` 를 적어 두고, **검사기가 그 분기를 실제로 태웠는지 스스로 확인**한다.

이번 세션에 같은 계열 실수를 한 번 더 했다: 음성 대조 주입 스크립트의 앵커 문자열이 실제
파일과 달라 `sed` 치환이 실패했는데, 실패 출력이 "0건"으로 보였다. **0 건은 "안 잡혔다" 가
아니라 "안 돌았다" 였다.** 주입 실패를 조용히 넘기지 않도록 고쳐서 다시 쟀다.

## 3-B. `/admin/*` — 4개 전부 포팅, 프록시는 읽기 2개만 열었다

`api/app/routers/admin.py` 는 4개 엔드포인트를 갖는데 그중 **`POST /admin/subscriptions`
는 쓰기**다(구독 수동 부여·회수). Phase 2 는 읽기 경로가 범위라 여기서 열지 않았다.
경로가 `GET /admin/subscriptions` 와 같아 **프록시 규칙으로는 메서드를 못 가른다** —
그래서 GET 도 같이 Railway 에 남겼다. 코드와 대조는 끝났으므로 Phase 3 에서 프록시에
한 줄 더하면 된다.

열린 것: `/^\/admin\/queries\//`, `/^\/admin\/feedback\//`

### 새로 포팅한 것

- `_shared/query_classifier.ts` — 9 라벨 룰 분류기. `/admin/queries/stats` 가 질의마다 부른다.
- `_shared/admin/aggregate.ts` — KST 일별 집계·분포·실패 샘플·코멘트 분류.
- `_shared/admin/pipeline.ts` · `_shared/admin/body.ts` · `_shared/pydantic_errors.ts`.

`literal_error` 문구 규칙(`'a', 'b' or 'c'`)은 `/stats/trend` 와 중복이라 공용 모듈로 뽑았다.
두 곳에 복제하면 한쪽만 고쳤을 때 조용히 갈린다.

### 정규식을 그대로 옮기면 틀린다

| 원본 | Python | JS 기본 | 대응 |
|---|---|---|---|
| `\d` | 유니코드 Nd 전부(680자) — `３개월`, `٣년` | ASCII 만 | `\p{Nd}` + `u` |
| `\s` | `\x1c-\x1f`·`\x85` 포함, U+FEFF 제외 | 반대 | `PY_SP` 문자 클래스 |
| `strip()` | 위 공백 집합 | `trim()` 은 U+FEFF 를 뗀다 | `pyStrip()` |

음성 대조로 ①②는 각각 3건·2건 잡혔다.

### 422 는 짐작하지 않고 운영에서 쟀다

`POST /admin/subscriptions` 의 본문 오류 9종을 **운영 Railway 에 실제로 보내** 응답을
수집했다. 전부 검증 단계에서 멈추는 무효 본문이라 DB 에는 아무것도 쓰이지 않았다.

| 본문 | type | loc |
|---|---|---|
| `{}` | `missing` × 2 | `["body","user_id"]`, `["body","plan_code"]` |
| `[1,2]` · `"hello"` | `model_attributes_type` | `["body"]` |
| `null` · 빈 본문 | `missing` | `["body"]` |
| 깨진 JSON | `json_invalid` | `["body", <문자 오프셋>]` |

배열 본문의 `model_attributes_type` 은 문서만 봐서는 안 나온다.
`api/scripts/fixtures/admin_422_measured.json` 에 남겨 재측정 없이 회귀를 잡는다.

**한 곳은 근사임을 명시한다** — `json_invalid` 의 `ctx.error` 와 오프셋은 Python `json`
모듈의 것이라 `JSON.parse` 로 완전히 재현할 수 없다. 실측한 두 형태에는 맞췄지만 다른
형태로 깨진 JSON 에서는 문구가 갈릴 수 있다.

### 원본의 1,000행 상한을 그대로 뒀다

원본은 `search_metrics_log` 를 `limit` 없이 읽어 PostgREST 상한에 걸린다. 페이지네이션을
넣으면 더 정확해지지만 **원본과 갈린다** — 이관 중에는 재현이 우선이다.
실측: 30일치 627행이라 아직 상한 밖. 정렬이 `desc` 라 잘려도 최근 것이 남는 것까지 같다.
데이터가 늘어 상한에 닿으면 양쪽이 같이 잘린다.

## 3-C. 운영 회귀 1건 — `/search/eval-precision` 이 404 였다

`/answer` 포팅을 시작하며 `answer.py` 를 읽다가 **이 라우터에 `/search/eval-precision` 이
들어 있는 걸** 발견했다. `/search` 전환 때 넣은 `[/^\/search/, "api-search"]` 규칙이 이
경로까지 삼켜 `api-search` 로 보냈고, 그 함수에는 해당 라우트가 없어 404 가 나가고 있었다.

| 요청 | Railway | 프록시(수정 전) |
|---|---|---|
| `GET /search/eval-precision` | 422 | **404** |
| `GET ?query=test` | 200 | **404** |
| `POST` | 401 | **404** |
| `DELETE` | 405 | **404** |

**왜 못 잡았나** — 전환 당시 접두어 오매칭을 `/searchfoo` 로만 확인하고 "원본에도 없으니
404 로 같다"고 넘겼다. `/searchfoo` 는 맞았지만 **원본 라우트 목록을 대조하지 않았다.**
`/health` 는 `$` 를 붙였고 `/me/`·`/admin/queries/` 는 슬래시를 요구했는데, `/search` 만
접두어로 열어 둔 채였다.

**고친 것**

- 규칙을 `[/^\/search\/?$/, "api-search"]` 로 좁혔다.
- `api/scripts/fixtures/fastapi_routes.json` — FastAPI 앱에서 뽑은 라우트 전수 38개.
- 프록시 테스트 3종 — 손으로 적은 `MIGRATED_PATHS` 와 라우트 전수를 대조해
  ① Edge 로 가는데 이관 안 된 경로 ② 원본에 없는 이관 선언 ③ 이관 선언했는데 안 보내는
  경로를 막는다. **목록을 규칙에서 자동으로 뽑지 않는 게 핵심**이다 — 규칙이 틀리면 같이 틀린다.

**가드가 사고를 재현하면 잡는다**: 규칙 되돌리기 2건, `/admin` 통째 열기 2건,
`/me`→`/answer` 확장 1건, `/documents` 선개방 5건.
운영 HTTP 15건 전건 일치로 해소 확인.

## 3-D. `/answer` 범위 실측 (착수 전 조사)

| 라우트 | 메서드 | 성격 |
|---|---|---|
| `/answer` | GET | 본체 — RPC + Gemini LLM 호출 |
| `/answer/feedback` | POST | 쓰기 |
| `/answer/eval-ragas` | GET·POST | 조회 + 쓰기 |
| `/search/eval-precision` | GET·POST | 조회 + 쓰기 |

- **스트리밍 아니다** (위 정정 참조).
- 의존: `intent_router`(292) · `query_decomposer`(523) · `multi_query_search`(66) ·
  `rate_limit`(159) · `factory`(209) · Gemini 어댑터. 라우터 자체가 1,000 줄.
- **재사용 가능**: `_shared/search/` 의 `intent.ts` · `rpc.ts` · `embed.ts` 가 이미 있다.
  `_gather_chunks` 는 `/search` 와 같은 RPC 를 쓴다.
- **운영에서 decomposition 은 꺼져 있다** — 실측 `decomposed_subqueries: []`,
  `decomposition_cost_usd: 0.0`. `JETRAG_PAID_DECOMPOSITION_ENABLED` 기본 OFF 라
  `_gather_chunks_with_decomposition` 분기는 현재 도달 불가.
- 운영 응답 실측: `model=gemini-2.5-flash`, `router_confidence=1.0`,
  `query_parsed={dense_hits:28, sparse_hits:31, fused:50}`.

**한 세션에 안 끝난다** — 아래 후보표의 분해안 참조.

## 3-E. `/answer` 본체 — LLM 직전까지 이관

라우터가 1,000 줄이라 **본체(GET) 모듈 → HTTP shell·배포 → 나머지 5 라우트** 로 쪼갰다.
이번 묶음은 첫 단계다.

### Gemini 요청 모양은 캡처해서 맞췄다

Python 은 `google-genai` SDK 를 쓰고 Edge 에는 없다. REST 를 직접 부르는데, 문서를 보고
짐작하는 대신 **SDK 의 `BaseApiClient.request` 를 가로채 실제 바디를 떴다.**

```
POST .../v1beta/models/<model>:generateContent      헤더 x-goog-api-key
{"contents":[{"parts":[{"text":"..."}],"role":"user"}],
 "systemInstruction":{"parts":[{"text":"..."}],"role":"user"},
 "generationConfig":{"temperature":0.2}}
```

짐작했으면 틀렸을 것 — **`systemInstruction.role` 이 `"user"`** 이고, system 은
`contents` 안이 아니라 별도 필드다. 응답 `text` 조립도 SDK 소스를 읽고 옮겼다:
첫 candidate 만, `part.thought === true` 는 건너뛰고, 텍스트 파트가 없으면 `null`.

### 재사용하면 안 되는 지점

`search/rpc.ts` 의 `runSearchRpc` 는 dense 실패 시 `search_sparse_only` 를 부르는데
`/answer` 원본은 **`search_sparse_only_pgroonga`** 를 부른다. 인자도 다르다(`k_rrf` 없음).
그대로 썼으면 **임베딩이 죽었을 때만** 조용히 갈렸을 것이다.

### 순서 계약이 `/me` 와 다르다

무효 요청 10 건을 보내니 `usage_counters` 가 정확히 10 올랐다 —
`라우팅 → rate_limit dependency → 파라미터 검증(422) → 핸들러` 순이라
**검증 실패도 카운터를 올린다.** `/me` 의 "라우팅이 인증보다 먼저"와는 층이 다르다.

### 실측한 파라미터 계약

| 입력 | 결과 |
|---|---|
| `q` 없음 / `q=` / 201자 | 422 `missing` / `string_too_short` / `string_too_long` |
| `q=   `(공백만) | **400** `질문이 비어있습니다.` (422 아님 — 핸들러 안) |
| `top_k=0` / `11` / `abc` / `` | 422 `greater_than_equal` / `less_than_equal` / `int_parsing` ×2 |
| `top_k=" 5 "` · `"5.0"` · `"+5"` | 전부 5 로 통과 |
| `top_k="5.5"` · `"1e1"` | 422 `int_parsing` (`int_from_float` 이 **아니다**) |

`"5.5"` 를 `int_from_float` 으로 짐작했다가 대조에서 잡혔다. 그건 실수 **타입**이 들어올
때 나는 오류라 쿼리 파라미터 경로에는 안 나온다.

### 검증 — LLM 은 부르지 않는다

같은 프롬프트에도 Gemini 출력은 매번 다르다. **LLM 직전까지**를 대조했다. 임베딩은 한 번만
뜨고 **양쪽에 같은 벡터를 주입**해 RPC 입력을 고정했다(DeepInfra 는 같은 입력에도 벡터가
미세하게 흔들린다).

`verify_answer_parity.py` FAIL 0 — 파라미터 422 8건(운영 실측 fixture), pydantic int
10건, astral 길이 3건, 프롬프트 문자열, PGroonga 6건, `_gather_chunks` 6건,
sparse-only 2건, NFD 정규화, 응답 조립 6건, Gemini 바디, 응답 텍스트 7건, quota 7건,
rate key 6건, sources 5건.

### 검사기 결함 2건을 또 잡았다

- `LEGACY_DEFAULT_USER`(FastAPI Depends 객체)를 핸들러에 그대로 넘겨 `user_id` 가 깨졌다.
  RPC 가 0 행을 돌려줘 **응답 조립 6건이 전부 "검색 0건"으로 통과**하고 있었다.
- 음성 대조 4 종이 0 건이었는데 전부 **케이스 부족**이었다 — sparse-only 경로 미태움,
  NFD 질의 없음, 모델명을 fallback 과 같은 값으로 둠, astral 문자 없음. 고치니 전부 발화.

### 판정 방식 오류 1건 (같은 실수 2회째)

여분 인자 `k_rrf` 를 sparse RPC 에 넣는 음성 대조가 "0 건"으로 보였는데, 실제로는 RPC 가
오류를 내 **스크립트가 죽은 것**이었다. `grep -c` 만 세느라 종료 코드를 안 봤다.
pytime 때와 같은 유형이다 — **0 건은 "안 잡혔다"가 아니라 "안 돌았다"일 수 있다.**
판정에 종료 코드를 넣어 다시 재니 검출됐다.

### 이관과 무관한 기존 사안 (기록만, 조치 안 함)

익명 rate limit 키가 프록시 뒤에서 **Cloudflare PoP IP** 로 뭉친다.
실측: 직접 호출 `ip:121.131.211.110` / 프록시 경유 `ip:104.23.251.88`.
같은 PoP 을 쓰는 익명 사용자들이 하루 50 회를 나눠 쓴다. **프록시를 세운 시점부터 그랬고
Edge 이관이 만드는 문제가 아니다** — 로직을 원본과 같게 두는 한 이관 전후 동작이 같다.
고치려면 Worker 가 `CF-Connecting-IP` 를 `X-Forwarded-For` 로 넘겨야 한다(사용자 결정 필요).

## 4. 음성 대조 발화 기록

| 대상 | 주입 | 잡힘 |
|---|---|---|
| `quota.ts` | `past_due` 제외 | 1 |
| | 구독 사용자 필터 제거 | 1 |
| | usage 날짜를 KST 로 | 1 |
| | metric 필터 제거 | 1 |
| | 문서 카운트 사용자 필터 제거 | 1 |
| `pipeline.ts` | plans 없을 때 503 안 냄 | 1 |
| `pytime.ts` | 항상 소수부 붙임(옛 구현) | 3 |
| | 늘 생략 / 3자리로 / `Z` 안 뗌 | 3 / 3 / 6 |
| 프록시 `routes.js` (`/me`) | 슬래시 없앰 / 줄 삭제 / 잘못된 대상 | 1 / 2 / 2 |
| `query_classifier.ts` | `\p{Nd}` → ASCII `\d` | 3 |
| | `PY_SP` → JS `\s` | 2 |
| | vision/table 우선순위 뒤집기 | 50 |
| | numeric/summary 순서 뒤집기 | 1 |
| | 키워드 1개 제거 / 라벨 순서 | 49 / 1 |
| `admin/aggregate.ts` | KST 오프셋 제거 | 5 |
| | zero-fill 순서 뒤집기 | 7 |
| | `fused` null 을 성공으로 | 4 |
| | 실패샘플 10건 컷 제거 | 4 |
| | rating null → up | 2 |
| | `pyRound` → `Math.round` | 1 |
| | `trunc` → `round` (지연) | 1 |
| | 코멘트 500자 / query 200자 컷 제거 | 2 / 2 |
| | helpful null 을 down 으로 | 2 |
| | 빈 코멘트도 카운트 | 2 |
| `admin/pipeline.ts` | `total_feedback` = 전체 행 수 | 1 |
| `admin/body.ts` | `missing` 의 input 을 null 로 / status 기본값 변경 | 1 / 2 |
| 프록시 `routes.js` (`/admin`) | subscriptions 까지 열기 / 줄 삭제 / 슬래시 없앰 | 1 / 2 / 1 |

### 0건이 나온 2건 — 검사기 약점이 아니라 **동작이 안 바뀌는 코드**였다

`query_classifier` 에서 `strip()` 제거와 단위 alternation 순서 뒤집기가 0건이었다.
검사기를 의심하기 전에 **Python 원본을 직접 고쳐** 같은 입력 17,706건에 돌렸다:

| 변경 | 달라진 건수 |
|---|---|
| `q = query.strip()` 제거 | **0** / 17,706 |
| 단위 순서 `개월|시간` → `개|개월|시간` | **0** / 17,706 |
| (대조군) 키워드 `도표` 제거 | 49 / 17,706 |

전부 무앵커 substring 매칭이라 앞뒤 공백과 alternation 순서가 판정을 바꾸지 않는다.
원본 주석의 "긴 것 먼저"는 이 함수에서는 효과가 없다.

## 5. 실측 수치

**응답 대조** — 전건 일치.

| 대조 | 건수 | 불일치 |
|---|---|---|
| `verify_me_parity.py` (응답 3 + 주소 4 + isoformat 5 + rotate 2) | 14 | 0 |
| `verify_me_quota_parity.py` (합성 DB 분기) | 10 | 0 |
| 비인증 HTTP (401·405·404·후행 슬래시) | 13 | 0 |
| 프록시 경유 HTTP | 13 | 0 |
| **인증된 200 경로** (`/me/plan` `/me/subscription` `/me/email-ingest` + 후행 슬래시) | 4 | 0 |
| 잘못된 토큰 401 | 1 | 0 |

인증 토큰은 admin `generate_link`(메일 발송 없음) → `verify_otp` 로 발급해 실제 200 본문까지
대조했다. 401 게이트만 보면 인증 경로의 버그는 못 잡는다.

**지연** — `/me` 는 성능 목적이 아니어서 개선폭이 작다. 비교를 공정하게 두려면 프록시
오버헤드를 빼야 한다(사용자는 프록시로만 접근한다).

| 경로 | Railway 직접 | 프록시→Edge |
|---|---|---|
| `/mefoo` (404, 양쪽 다 Railway) | 217ms | 482ms → **프록시 오버헤드 266ms** |
| `GET /me/plan` (인증, 중앙값 아닌 2회차) | 818ms | 979ms |

즉 프록시 오버헤드를 감안하면 Edge 가 약 100ms 빠르지만, 표본이 적어 **유의한 차이라고
주장하지 않는다**. `/search` 처럼 4.2배 같은 개선은 없다.

**`/admin` 대조**

| 대조 | 건수 | 불일치 |
|---|---|---|
| `verify_query_classifier_parity.py` (운영 query_text 17,663 + 합성 43) | 17,706 | 0 |
| `verify_admin_parity.py` (순수 집계·반올림 경계·실 DB 응답·422 12건) | — | 0 |
| Edge 직접 HTTP (403 게이트 / 405·404 순서 / 200 / 422) | 19 | 0 |
| 프록시 경유 HTTP (+ 미개방 경로가 Railway 로 가는지 + 기존 경로 회귀) | 15 | 0 |

**`/admin` 지연** — 프록시 오버헤드 273ms 를 뺀 값이 실제 비교 대상이다.

| 경로 | Railway 직접 | 프록시→Edge | 오버헤드 제외 |
|---|---|---|---|
| `/admin/queries/stats` | 569ms | 928ms | 655ms (86ms 느림) |
| `?range=30d` | 781ms | 873ms | 600ms (181ms 빠름) |
| `/admin/feedback/stats` | 374ms | 951ms | 678ms (304ms 느림) |

표본 5회 중앙값이고 방향이 엇갈린다 — **개선도 악화도 유의하다고 말할 수 없다.**
Edge CPU 2초 제약 대비로는 전부 여유가 있다(대부분이 네트워크 시간).

**테스트**: `deno test` 109건 + 프록시 15건 통과. Python 코드 변경 없음(검증 스크립트만 추가).
`deno lint` 4건 수정 — 주석에 U+FEFF 를 리터럴로 넣은 것. 이전 세션들에서 `tail -1` 만
보느라 놓치고 있었다(기존 2건 포함).

## 6. 커밋 이력

| 해시 | 메시지 |
|---|---|
| `d4029da` | feat(edge): /answer 본체 포팅 — Gemini REST 계약을 SDK 캡처로 확정 |
| `5a74ea6` | fix(proxy): /search 접두어 규칙이 /search/eval-precision 을 삼켜 404 를 내던 것 수정 |
| `d6bc9e6` | feat(edge): /admin/* 4개 엔드포인트 Edge 이관 — 422 본문은 운영 실측으로 고정 |
| `c8368bd` | feat(edge): /me/* 4개 엔드포인트 Edge 이관 — 라우팅·인증 순서까지 대조 |
| `b0a5d58` | feat(edge): `/stats` · `/stats/trend` 를 Edge 로 전환 |
| `fa6d9e6` | fix(api): vision_usage 를 DB(오늘 KST) 기준으로 — /stats Edge 이관 선행 조건 |

## 7. 남은 작업 · 차단 요인

**Phase 2 남은 것**

- `/answer` — Phase 2 최대 항목이자 유일하게 남은 것. **스트리밍 응답**이라 별도 함수로
  뺄지 판단이 앞에 붙는다.
- `/admin/subscriptions` 프록시 개방 — 코드·대조는 끝났고 POST 가 쓰기라 Phase 3 으로 미뤘다.
  `workers/api-proxy/src/routes.js` 에 `[/^\/admin\/subscriptions/, "api-account"]` 한 줄이면 된다.

**Phase 3~6**: `/documents`(쓰기) → `/payments`+`/billing` → `/email` → Railway 종료·프록시 제거.

**이관 후로 미룬 원본 버그 2건** (사용자 결정: 이관 중에는 운영 동작을 그대로 유지)

1. **MMR 이 도달 불가능** — `search.py:1286` 의 `for r in rpc_rows: doc_id = r["doc_id"]` 가
   함수 인자를 가려서, 1458 행의 `doc_id is None` 게이트가 절대 참이 되지 않는다.
   실측: T1 발화 7개 질의에서 `mmr.rerank` 호출 **0회**. Edge 도 그대로 재현해 뒀다.
2. **사용자가 500 을 낼 수 있다** — 질의에 `9999-12-31` / `9999년 12월` 이 있으면
   `meta_filter_fast_path` 의 `try` 밖에서 OverflowError/ValueError. 실측 확인
   (`9999-12-30` → 200, `9999-12-31` → 500).

둘 다 **Railway·Edge 를 같이 고치고 골든셋을 다시 재야** 한다.

**기타**

- Cloudflare 봇 보호가 `Python-urllib/*` 를 막는다. 완화 여부는 사용자 결정 대기.
- 기존 실패: `test_embed_cache` 4건이 `unittest discover` 전체 실행에서만 실패(단독 실행은 통과).
  이관 작업 전후 동일 — 이번 변경과 무관하다.

## 8. 다음 후보

| | 후보 | 근거 |
|---|---|---|
| **A** | `/answer` 본체(GET) 먼저 (권장) | Phase 2 의 마지막. 라우터가 1,000줄이라 한 세션에 다 못 하므로 **본체 → 나머지 5개** 로 쪼갠다. 본체는 `intent.ts`·`rpc.ts`·`embed.ts` 재사용이 가능하고, 새로 드는 것은 Gemini 어댑터 + prompt 조립 + rate_limit 이다. |
| B | 원본 버그 2건 선(先)수정 | 사용자에게 500 이 나가는 상태(`9999-12-31`)를 오래 두지 않는다. MMR 도 같이 고치고 골든셋을 다시 잰다. 이관 흐름이 한 번 끊긴다. |
| C | Phase 3(`/documents` 쓰기) 착수 | `/answer` 를 뒤로 미루고 쓰기 경로를 먼저 연다. 업로드·삭제라 되돌리기가 어려워 준비가 더 필요하다. |

**권고: A** — 포팅한 코드가 배포되지 않은 채로 다른 일을 시작하면 검증 결과가 낡는다.
`/answer` 는 LLM 비용과 `usage_counters` 증가가 있어 HTTP 대조를 아껴야 하므로
(익명 상한 50 회/일 중 오늘 이미 22 회 소비) 대조 횟수를 미리 정하고 들어간다.
