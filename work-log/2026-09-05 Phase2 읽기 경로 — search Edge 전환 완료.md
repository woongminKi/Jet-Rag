# Phase 2 읽기 경로 — `/search` Edge 전환 완료

> **범위**: Phase 2 Task 2.1~2.9 전체 + `/stats.search_slo` DB 전환.
> `search.py` 1,020줄을 Deno 모듈 18개로 옮기고 운영 트래픽을 Supabase Edge 로 넘겼다.
> (2026-09-04 16:18 플랜 작성 → 2026-09-05 15:05 전환 완료)
>
> **다음 세션 재진입**: `/search` 는 이미 Edge 에서 돈다. 다음 후보는 §8 참조 —
> 권고는 **C(이관 후 수정 묶음)** 가 아니라 **B(Phase 2 나머지 엔드포인트)** 다. 이유는 §8.

---

## 0. 한눈에 보기

| 항목 | 상태 | 근거 |
|---|---|---|
| Task 2.1 `params.ts` + `pgroonga.ts` + `iso_datetime.ts` | ✅ | 패리티 80건 + fuzz 46,616건 |
| Task 2.2 `guards.ts` + `filters.ts` | ✅ | 78건 + 실제 청크 12,197건 |
| Task 2.3 `rrf.ts` | ✅ | 합성 30건 + live RPC 11질의 |
| Task 2.4 `snippet.ts` + `assemble.ts` | ✅ | 22,121건 (live 청크 600 포함) |
| Task 2.5 `intent.ts` + `mmr.ts` | ✅ | 497건 (live 문서 임베딩 13) |
| Task 2.6 `meta_fast_path.ts` | ✅ | 156건 (plan 84 · URL 6 · live 66) |
| Task 2.7 `embed.ts` + `rpc.ts` + `metrics.ts` | ✅ | 80건 + 실제 임베딩 API 2종 |
| Task 2.8 `pipeline.ts` + `api-search` + `unsupported.ts` | ✅ | 응답 대조 28건 |
| Task 2.9 골든셋 → 배포 → 전환 | ✅ | 골든셋 123행 불일치 0 · HTTP 26건 일치 |
| `/stats.search_slo` DB 전환 | ✅ | 같은 표본 500행 두 경로 완전 일치 |
| **운영 `/search` 트래픽** | ✅ **Edge** | `x-served-by: supabase-edge-runtime` |

**산출물**: Deno 모듈 18개 3,306줄 · 패리티 검사기 10개 + 골든셋 비교기 1개 · CI 잡 13개 ·
`deno test` 109(함수) + 13(프록시) passed.

---

## 1. 결과 — 전환 실측

| 구간 | wall 중앙 | 서버 `took_ms` 중앙 |
|---|---|---|
| 전환 **전** (프록시→Railway) | 1,191ms | 619ms |
| 전환 **후** (프록시→Edge) | 1,250ms | **147ms** |
| 참고 — Edge **직접** (프록시 없음) | **384ms** | 165ms |

**서버 처리가 4.2배 빨라졌다.** DB(서울)와 함수(서울)가 같은 지역이라 왕복이 짧아진 결과다.

wall 이 +59ms 인 건 Cloudflare Worker 홉 때문이다. 이 존은 **한국에서 접속해도 SJC PoP** 로
들어간다(`/cdn-cgi/trace`: `colo=SJC, loc=KR`. 같은 클라이언트가 `cloudflare.com` 에는 ICN).
경로가 `KR → SJC → 서울` 이 되어 태평양을 한 번 더 건넌다. Worker 는 **이관 기간용 도구**라
Phase 6 에서 사라지면 `KR → 서울` 이 되고 그게 위 표의 384ms 다.

`/stats.search_slo` 는 전환 후에도 `source='db'` 로 표본 500건을 낸다 — 지표 단절 없음.

---

## 2. 플랜 대비 변경

| 플랜 | 실제 | 이유 |
|---|---|---|
| 모듈 14개 | **18개** | `iso_datetime.ts`·`chunks.ts`·`pystr.ts`·`pipeline.ts` 가 늘었다 |
| Task 2.6 을 **최하 우선순위** | **2.9 의 하드 선행 조건**으로 승격 | ENV 토글이 아니라 **항상 켜진 분기**라 `unsupported.ts` 로 못 막는다. 미이식이면 fast path 질의가 Edge 에서 조용히 RAG 경로를 탄다 |
| 러너에 `--endpoint` 추가해 HTTP 로 비교 | **양쪽 in-process 비교**로 변경 | 운영 `/search` 는 비인증이면 문서 0건이라 비교가 성립하지 않고, 토큰을 만들면 인증·네트워크 변수가 섞인다. 그래서 `api-search` 를 HTTP 껍데기(`index.ts`)와 파이프라인(`pipeline.ts`)으로 나눴다 |
| 회귀 판정 = "지표 차이 0" | **응답 동일성**을 1차 기준으로 | 지표는 여러 응답 차이가 상쇄돼 같은 값이 나올 수 있다(A·B 순위가 맞바뀌어도 둘 다 정답이면 R@10 그대로). 응답이 같으면 지표는 자동으로 같다 |
| — | `/stats.search_slo` DB 전환 **추가** | 전환 자체가 만드는 회귀라 2.9 앞에 끼워 넣었다 (§4) |

---

## 3. 원본에서 찾은 버그 2건 — **둘 다 재현해 두었다**

이관은 동작을 맞추는 일이라 **고치지 않고 그대로 옮겼다.** 조용히 고치면 Edge 만 달라진다.

### (1) MMR 은 운영에서 도달 불가능한 코드다

`search.py` 3) 단계의 `for r in rpc_rows: doc_id = r["doc_id"]`(1286행)가 **함수 파라미터
`doc_id` 를 덮어쓴다.** 5) 단계 MMR 게이트가 `doc_id is None`(1458행)을 보므로 `rpc_rows`
가 비지 않는 한 항상 False 다. 두 행 사이에 재대입 없음(전 구간 확인).

**실측**: T1 이 발화하고 문서가 5~8건인 질의 **7개 전부에서 `mmr.rerank` 호출 0회**.

내 포팅은 처음에 파라미터 `docId` 를 그대로 써서 **MMR 을 되살렸고**, cross-doc 질의 순위가
갈렸다 — 파이프라인 패리티가 잡았다. 지금은 덮어쓰기까지 재현해 뒀다.

> 원본에도 이 shadowing 을 아는 주석이 있는데(1510~1513행), 그건 청크 cap 판정을
> `rpc_top_k` 로 재구성하는 대목이고 **MMR 게이트는 고쳐지지 않았다.**

### (2) `9999-12-31` / `9999년 12월` 이 든 질의는 500 이다

Python `datetime` 의 최대 연도가 9999 라, fast path 날짜 범위 계산의
`start + timedelta(days=1)`(OverflowError)과 다음 달 1일 계산(ValueError)이 **`try` 밖**이라
그대로 샌다. **운영 실측**:

```
/search?q=9999-12-31 자료   → 500 Internal Server Error
/search?q=9999년 12월 자료   → 500 Internal Server Error
/search?q=9999-12-30 자료   → 200
```

사용자가 만들 수 있는 500 이다. Edge 도 같이 던지도록 재현했다.

**결정 (사용자, 2026-09-05)**: 둘 다 **이관 후** Railway·Edge 양쪽을 함께 고친다.
이관 중 순위 변화는 0 이 된다. 그 작업에는 골든셋 재측정이 따라붙는다(MMR 이 살아나면
cross-doc 순위가 바뀐다).

---

## 4. 전환 자체가 만드는 회귀를 먼저 막았다 — `/stats.search_slo`

`search_metrics.py` 는 DB write-through 와 **별도로** 프로세스 안 ring buffer(최근 500건)를
두고, `/stats` 의 `search_slo` 가 그걸 읽는다(`stats.py:246`). Edge 는 isolate 가 휘발성이라
그 구조가 성립하지 않는다. `/search` 만 Edge 로 넘기면 **Railway 프로세스의 ring 에는
아무것도 안 쌓여서** `/stats.search_slo` 가 계속 0 을 보고한다 — 매일 02:00 UTC cron
(`monitor-search-slo.yml`)이 보는 값이 바로 그것이다.

이식 품질과 무관한 회귀라 전환 **전에** 처리했다.

- 표본 출처를 `search_metrics_log` 최근 500행으로 (ring 과 같은 창)
- `JETRAG_SLO_SOURCE=ring` 으로 회귀 가능. 미설정 시 write-through 여부로 결정 —
  꺼져 있으면 DB 에 쌓일 리가 없으므로 ring (기존 단위 테스트가 그 경우다)
- 응답에 `source` 필드 추가 (`db` / `ring` / `ring_fallback`)
- 같은 표본 500행에서 두 경로 **완전 일치** (p50=183 p95=1828, `by_mode`·fallback 분포 포함)

---

## 5. 언어 차이로 갈린 것들 — 전부 실측

포팅에서 실제로 결과를 바꾼 차이들. 상상이 아니라 재서 확인한 것만 적는다.

| # | 차이 | 실측 |
|---|---|---|
| 1 | Python `re` 의 `\s` ≠ JS `\s` | Python 만 `U+001C`~`U+001F`·`U+0085`, JS 만 `U+FEFF`. 가드 정규식을 기본 `\s` 로 되돌리면 6건 어긋남 |
| 2 | `len`·슬라이스가 코드포인트 vs UTF-16 | **운영 청크 37,080건 중 20건**이 astral 문자 포함(수식 이탤릭 `𝑖`·`𝜎`). UTF-16 으로 되돌리면 live 15건 어긋남 |
| 3 | `round()` 는 **은행가 반올림** | 소수 4자리에서 정확히 절반인 값은 `m/32`(m 홀수) 16개뿐, 그중 8개가 갈린다. BigInt 로 이진 표현을 꺼내 맞춤 — 22,065건 대조 |
| 4 | `new Date()` 가 원본과 **양방향**으로 어긋남 | `2026-02-30`→3월 2일 롤오버, `24:00:00`→익일, 반대로 `2026-W14-1`·`20260401` 거부. `fromisoformat` 호환 파서를 직접 씀 |
| 5 | `float()` 이 **전각 숫자**를 받음 | `float("０.５")` = 0.5. Nd 문자 **680개 전량** 그렇다. `re` 의 `\d` 도 마찬가지 |
| 6 | `str.strip()` ≠ JS `trim()` | Python 은 `U+FEFF` 를 안 버린다 |
| 7 | `.lower()` 매핑 | 코드포인트 110만개 중 **27개**만 갈림 (전부 Python 쪽 유니코드 버전이 낮아 매핑이 없는 신규 문자) |
| 8 | `x ** 0.5` vs `Math.sqrt` | macOS 에서 20만건 중 261건이 1 ulp. **libm 을 묻지 않는 실험**으로 닫음 — Python 안에서 두 방식으로 MMR 을 돌려 순서가 갈리는지 봄: **0/64건**, 결정 마진 최솟값 8.65e-05 (1 ulp ≈ 1e-17) |
| 9 | JSON 직렬화 | pydantic `"relevance":1.0` vs JS `"relevance":1`. **1위 문서는 항상 1.0** 이라 모든 응답에 나타난다. 맞추지 않고 **대조를 파싱된 값으로** 함 |
| 10 | 처리되지 않은 예외의 500 본문 | FastAPI 는 JSON 이 아니라 **평문**(`text/plain`, `Internal Server Error`). Edge 는 `{"detail":...}` 이었다 → `errors.ts` 에서 맞춤 (모든 Edge 함수 영향) |
| 11 | DeepInfra 임베딩이 **비결정적** | 같은 텍스트 4회 호출 중 일부는 1024성분 전부 다름, 자기 코사인 0.999999. HF 는 3회 완전 일치 |

---

## 6. 검사기가 스스로 틀린 3회 — **패턴으로 남긴다**

이번 세션에서 가장 값싸게 배운 것. **"통과"가 근거가 아니라 "틀린 걸 잡는가"가 근거다.**

### ⓐ 자기 자신과 비교 (2회)

- **SLO 검사기**: `_fetch_recent_from_db()` 결과를 그대로 ring 에 넣어 비교했다.
  그 함수에 버그가 있으면 **양쪽이 똑같이 틀려서 항상 통과**한다. 음성 대조 5종에서
  **한 건도 못 잡았다** → 스크립트가 직접 질의하도록 독립시켜 4종을 잡게 됨.
- **meta fast path 검사기**: URL 대조 러너가 `buildFastPathQuery` 를 부르지 않고
  조립을 복붙했다. 같은 실수 → 빌더를 분리해 러너가 그걸 부르게 고침. 재측정 후
  `lt→lte`(2) · `contains→overlaps`(3) · `limit`(6) · 정렬(6) · ILIKE(3) · `deleted_at`(6) 발화.

### ⓑ 데이터가 없어서 못 잡는 걸 통과로 읽음

meta fast path 의 `lt→lte` / `contains→overlaps` / `limit 20→5` 를 심었더니 **셋 다 0건**.
운영 데이터에 경계 시각 문서가 없고, 태그 질의가 단일 태그라 AND/OR 이 같고, 결과가 5건을
넘는 질의가 없어서였다 → **요청 URL 대조**를 층으로 추가(데이터 무관).

### ⓒ 케이스가 그 코드 경로를 안 태움

- 가드 skip 음성 대조가 0건 — 가드 대상 청크가 후보에 없었고 `목차` 질의는 의도 감지로
  penalty 가 이미 skip 되고 있었다 → 실측으로 실제 발동 질의 3종을 찾아 넣음.
- MMR tie-break 음성 대조가 1건만 — 동점 케이스가 죄다 `doc_id` 오름차순이라 알파벳
  2차 키를 넣어도 결과가 같았다. 실제 `doc_id` 는 UUID → 역순 케이스 4건 보강 후 5건.
- T7 가드 제거가 0건 — T1 과 조사 2개가 동시에 성립하는 질의가 없었다 → 4건 보강.

### ⓓ 본문만 보고 헤더를 안 봄

파이프라인 패리티가 본문만 비교해서, **배포 후 HTTP 로 재고 나서야** fast path 응답의
`X-Reranker-Path` 차이를 발견했다(원본은 그 헤더를 2-c 단계에서 붙이는데 fast path 는
그 전에 반환하므로 **헤더가 없다**) → 검사기에 헤더 비교 추가, 음성 대조 6건 발화.

### ⓔ 불공정 비교로 결론을 뒤집을 뻔함

전환 후 "Edge 가 600ms 느리다"로 읽었는데, **Edge 는 프록시 경유로 재고 Railway 는 직접
재는** 비교였다. 프록시 오버헤드를 따로 재고, 그 값을 Supabase 대상에 그대로 못 쓴다는 걸
확인한 뒤(두 번째 홉 거리가 다르다) **라우트를 잠깐 되돌려 전환 전 값을 직접 쟀다.**

---

## 7. 전환하면서 잡은 운영 이슈 3건

1. **함수 실행 지역** — Supabase 는 **호출자에 가까운 지역**에서 함수를 띄우는데, 호출자가
   사용자가 아니라 Worker 다. Worker 가 SJC 에 있으니 함수가 `us-west-1` 에서 떴고
   DB(서울) 왕복마다 태평양을 건넜다. 같은 함수·같은 질의로 **서울 132ms vs 미국서부 828ms
   (6.3배)**. 프록시가 `x-region` 을 붙이도록 고침(`SUPABASE_FUNCTION_REGION`).
   → **함수는 사용자 근처가 아니라 DB 옆에서 돌아야 한다.**
2. **후행 슬래시** — 원본은 `/search/` 에 307 을 내는데 `Location` 이
   `http://jet-rag-production.up.railway.app/...` 라 **제거 대상 호스트를 직접 가리킨다.**
   재현이 역행이라 Edge 는 슬래시를 떼고 200 을 준다. `/health/`·`/auth/me/` 에도 같은
   차이가 Phase 1 부터 있었다 — 함께 고침.
3. **Cloudflare 가 `Python-urllib/*` 를 403 으로 막는다** (Task 1.7 오렌지 구름 전환 부작용).
   매일 02:00 UTC cron 이 부르는 `monitor_search_slo.py` 가 다음 실행부터 실패할 상태였다 →
   명시적 User-Agent 로 해소. **Cloudflare 봇 설정을 완화할지는 미결(사용자 결정).**

---

## 8. 남은 작업 · 차단 요인

### 차단 없음
외부 대기 항목 없음. `JETRAG_EMBED_PROVIDER=deepinfra` 는 사용자 확인 완료(2026-09-05).

### 남은 것

| 구분 | 항목 |
|---|---|
| Phase 2 나머지 | `/answer` · `/stats` · `/me/*` · `/admin/*` |
| Phase 3~5 | `/documents`(쓰기) · `/payments`·`/billing` · `/email` |
| Phase 6 | Railway 종료 + 프록시 제거(도메인을 Supabase 로 직결) |
| 이관 후 수정 | MMR shadowing · `9999-12-31` 500 (§3) |
| 미결(사용자) | Cloudflare 봇 보호 완화 여부 |
| 기존 결함 | `unittest discover` 시 `test_embed_cache` 4건 실패(단독 실행은 통과). **이관 전부터 동일** — 변경 전 1452 tests failures=4 / 후 1460 failures=4. CI 의 `api · unittest` 잡도 같은 명령이라 이미 빨간 상태일 가능성 |

### 다음 후보

- **A. 이관 후 수정 묶음** — MMR + 9999. 골든셋 재측정이 따라붙는다.
- **B. Phase 2 나머지 엔드포인트** — `/answer`·`/stats`·`/me/*`·`/admin/*`.
  `/stats` 는 이미 절반 준비됨(SLO 가 DB 기준이라 Edge 로 옮겨도 그대로 동작).
- **C. `test_embed_cache` 4건 오염 수정** — CI 를 초록으로. 작지만 이관과 무관.

> **권고: B.** Railway 제거가 목표인데 A·C 는 그 목표를 진전시키지 않는다. A 는 MMR 이
> 살아나면 순위가 바뀌므로 **이관이 끝난 뒤** 한 번에 재측정하는 게 싸다(지금 하면 남은
> 엔드포인트 이관마다 기준선이 흔들린다). C 는 이관 전부터 있던 결함이라 급하지 않다.
> B 안에서는 **`/stats` 부터** — SLO 를 이미 DB 기준으로 바꿔 뒀고, `/answer` 보다 훨씬 작다.

---

## 9. 커밋 이력 (최신순)

| 해시 | 메시지 |
|---|---|
| `acf9177` | feat(edge): `/search` 트래픽을 Edge 로 전환 — 서버 처리 4.2배 단축 |
| `adbf3fe` | feat(edge): api-search 배포 + 실제 HTTP 대조 18/18 일치 |
| `c5c6d31` | feat(evals): 골든셋 Railway↔Edge 비교 — 123행 응답 불일치 0 |
| `3c63132` | feat(edge): Task 2.6 — meta_fast_path.ts 이식 |
| `f507272` | feat(edge): Task 2.8 — api-search 조립 + unsupported.ts |
| `3c36178` | fix(api): search_slo 표본을 DB 로 옮김 |
| `da05bfd` | feat(edge): Task 2.7 — embed.ts + rpc.ts + metrics.ts |
| `ab4aad3` | feat(edge): Task 2.5 — intent.ts + mmr.ts |
| `f727aea` | feat(edge): Task 2.4 — snippet.ts + assemble.ts |
| `ccb0a2c` `c3e5e6c` `c4f1f51` | Task 2.3 — rrf.ts + CI + 플랜 |
| `fe1e19f` `e9e153b` | Task 2.2 — guards.ts + filters.ts + 플랜 |
| `dbb4f09` | feat(edge): 날짜 파싱을 fromisoformat 호환 파서로 교체 |
| `4c3c0c9` | fix(edge): params 검증에 doc_type 화이트리스트 누락 정정 |
| `b618105` | feat(edge-p2): params.ts + pgroonga.ts |
| `d5ba4cc` | fix(evals): 골든셋 러너가 전 지표 0 을 내던 문제 |
| `08a94d9` | docs(plan): Phase 2 읽기 경로 플랜 |
| `b69c874` | measure(edge-p2): 검색 경로 CPU 몫 실측 |

---

## 10. 롤백 절차

`workers/api-proxy/src/routes.js` 에서 `[/^\/search/, "api-search"],` 한 줄을 지우고
`cd workers/api-proxy && npx wrangler deploy`. 즉시 Railway 로 되돌아간다.
Edge 함수는 그대로 둬도 트래픽이 안 간다. **이번 세션에서 실제로 한 번 왕복해 확인했다.**
