# 2026-09-06 — Phase 2 읽기 경로: `/stats` · `/me/*` Edge 전환

> **범위**: `/stats`, `/stats/trend`, `/me/*` 4개를 Supabase Edge Function 으로 이관하고
> Cloudflare 프록시 스위치를 열기까지. 선행 조건이던 `vision_usage` 의 프로세스 로컬 상태를
> DB 기준으로 옮기는 작업 포함.
> **다음 세션 재진입**: Phase 2 남은 경로 `/admin/*` 포팅부터. `/answer` 는 그 다음(가장 크고
> 스트리밍이라 별도 함수 검토 필요).

## 0. 한눈에 보기

| 항목 | 상태 |
|---|---|
| `vision_usage` DB 기준 전환 (Railway 선행) | ✅ 완료 (fa6d9e6) |
| `/stats` · `/stats/trend` Edge 포팅 + 전환 | ✅ 완료 (b0a5d58) |
| `/me/plan` `/me/subscription` `/me/email-ingest` `/me/email-ingest/rotate` | ✅ 완료 (c8368bd) |
| 프록시 `ROUTES` 에 `/stats`, `/me/` 개방 | ✅ 배포됨 |
| `/admin/*` | ⬜ 대기 |
| `/answer` | ⬜ 대기 (Phase 2 최대 항목) |

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
| 프록시 `routes.js` | 슬래시 없앰 / 줄 삭제 / 잘못된 대상 | 1 / 2 / 2 |

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

**테스트**: `deno test` 109건 + 프록시 14건 통과. Python 코드 변경 없음(검증 스크립트만 추가).

## 6. 커밋 이력

| 해시 | 메시지 |
|---|---|
| `c8368bd` | feat(edge): /me/* 4개 엔드포인트 Edge 이관 — 라우팅·인증 순서까지 대조 |
| `b0a5d58` | feat(edge): `/stats` · `/stats/trend` 를 Edge 로 전환 |
| `fa6d9e6` | fix(api): vision_usage 를 DB(오늘 KST) 기준으로 — /stats Edge 이관 선행 조건 |

## 7. 남은 작업 · 차단 요인

**Phase 2 남은 것**

- `/admin/*` — 권한 게이트(`require_admin`)가 붙어 있어 `/me` 와 같은 순서 문제를 다시 확인해야 한다.
- `/answer` — Phase 2 최대 항목. **스트리밍 응답**이라 별도 함수로 뺄지 판단이 필요하다.

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
| **A** | `/admin/*` 포팅 (권장) | Phase 2 에서 남은 것 중 작다. `/me` 에서 확립한 순서 계약을 권한 게이트에 그대로 적용하면 된다. `/answer` 앞에 한 번 더 리허설이 된다. |
| B | `/answer` 착수 | Phase 2 최대 항목이라 먼저 열면 남은 일정이 예측 가능해진다. 다만 스트리밍 설계 결정이 앞에 붙는다. |
| C | 원본 버그 2건 선(先)수정 | 사용자에게 500 이 나가는 상태를 오래 두지 않는다. 다만 골든셋 재측정이 필요해 이관 흐름이 한 번 끊긴다. |

**권고: A** — `/answer` 는 결정할 것(스트리밍)이 있어 별도 세션이 낫고, `/admin` 은 같은
패턴이라 이 세션의 문맥이 그대로 쓰인다.
