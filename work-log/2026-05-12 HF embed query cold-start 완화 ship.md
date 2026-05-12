# 2026-05-12 HF BGE-M3 embed query cold-start 완화 ship

> 프로젝트: Jet-Rag
> 작성: 2026-05-12 (`216086e` HEAD 기준 후속 — S3 D6 재측정에서 발견된 운영 이슈 대응, 남은작업 1순위)
> 범위: (O1) FastAPI lifespan startup warmup ping — 부팅 직후 background 로 BGE-M3 더미 embed 1회 → 첫 실 query 전에 모델 warm + (O4a) 어댑터 `_with_retry` 에 `Retry-After` 헤더 존중
> 흐름: senior-planner 명세(SPEC-coldstart-1) → 사용자 결정 8건 확정 → senior-developer 구현(초안 승인 → 구현) → senior-qa 검증(ship GO) → work-log
> 비용: paid $0 (BGE-M3 HF 무료). 의존성 추가 0 (`email.utils` stdlib).

---

## 0. 한 줄 요약

> S3 D6 측정에서 RRF-only 검색 P95 67.6s 발견 (HF Inference free-tier 가 BAAI/bge-m3 idle 시 scale-to-zero → 503 "model is loading" cold-load + 어댑터 3회 retry → worst ~95s. warm 시 p50 495ms). 대응: `api/app/main.py` 에 FastAPI `lifespan` 신규 추가 → 부팅 직후 `asyncio.create_task` 로 `_warmup_bgem3()` fire-and-forget (lazy import / 토큰 없으면 skip / `asyncio.to_thread` 로 동기 호출 위임 / graceful / shutdown 시 cancel / `app.state.bgem3_warmup_task` 강참조). embed/reranker 둘 다 동일 endpoint 라 한 번이면 검색·인제스트 둘 다 warm. + `bgem3_hf_embedding.py`·`bge_reranker_hf.py` 의 `_with_retry` 에 `Retry-After` 헤더 존중(`_parse_retry_after`, 클램프 60s, HTTP-date → backoff fallback, `_MAX_ATTEMPTS`·`_BASE_BACKOFF` 미변경). 단위 테스트 951 → **968** (+17, 회귀 0). 운영 코드 핫패스(search.py/answer.py) 무변경.

---

## 1. 왜

- S3 D6 재측정 (`evals/run_s3_d5_search_stack_eval.py --combo a`, golden_v2 183 row, n_eval 176, reranker OFF) 의 latency: p50 **495ms** / p90 24.4s / p95 **67.6s** / max 137.9s.
- long-tail 전수가 `bge-m3.embed_query transient` (read timeout / 500) 로그 — HF Inference free-tier 가 BAAI/bge-m3 모델을 idle 시 scale-to-zero → 첫 호출(또는 idle 후 호출) 시 503 "model is loading" + 긴 cold-load 대기. 어댑터의 3회 retry (5/10/20s backoff)와 결합 → 한 `embed_query` 호출 worst ~95s.
- warm 상태에선 p50 495ms 로 충분히 빠름 → **첫 query 의 cold-start 지연만 타깃**. 페르소나 A "월요일 아침 10분" 세션에서 첫 검색이 ~10~60s 걸리는 게 문제.
- 기획서 §13.1 KPI: 검색 P95 ≤ 3초.

## 2. 사용자 확정 결정 (senior-planner 미결 Q 중)

| ID | 결정 |
|---|---|
| Q-coldstart-1 keepalive (O3) | 도입 안 함 — free-tier quota/rate-limit 리스크. 필요 시 후일 ENV 플래그 |
| Q-coldstart-2 프론트 UX surfacing (O5) | 본 작업 범위 밖 — 별도 프론트 작업 |
| Q-coldstart-3 O4 범위 | (a) `Retry-After` 헤더 존중만 포함. 첫-attempt timeout 분리(b)는 안 함(인제스트 영향 검토 비용 큼 → follow-up). embedding + reranker 어댑터 양쪽 적용(코드 일관성) |
| Q-coldstart-4 reranker warmup | 포함 안 함 — reranker default OFF (S3 D6 결론) |
| Q-coldstart-5 lifespan vs startup event | `lifespan` async context manager (FastAPI `>=0.136.0`, `@app.on_event` deprecated) |
| Q-coldstart-6 wait_for 래핑 | 안 함 — 어댑터 retry 가 자연 상한 |
| Q-coldstart-7 더미 텍스트 | 한국어 짧은 구 `"문서 검색"` |
| Q-coldstart-8 worker 수 | 운영/dev 모두 `uvicorn app.main:app --reload` (--workers 미지정) = worker 1 (W8 D4 "단일 worker 전제" 명시). 멀티 시 worker 마다 warmup 1회 — 수용 |

## 3. 변경/신규 파일

### 변경 (4)
- **`api/app/main.py`** — `lifespan` async context manager + `_warmup_bgem3` async 함수 추가. `app = FastAPI(..., lifespan=lifespan)`. 기존 CORS/router include×5/`/`·`/health` route 등록 순서 무변경.
  - `_warmup_bgem3`: `get_settings().hf_api_token` 없으면 info 로그 후 return / `get_bgem3_provider()` try-except RuntimeError → skip / `await asyncio.to_thread(provider.embed_query, "문서 검색")` (동기 함수 `time.sleep` 포함 → 이벤트 루프 양보) / 성공 시 elapsed·dim info 로그 / 실패 시 warning (앱 정상 기동, best-effort) / `CancelledError` 는 재전파.
  - lifespan startup: `task = asyncio.create_task(_warmup_bgem3())`, `app.state.bgem3_warmup_task = task` (GC 방지 강참조). **yield 전 await 없음 — 부팅 비블로킹**.
  - lifespan shutdown: `task.done()` 아니면 `cancel()` → `await` → `except (asyncio.CancelledError, Exception): pass`/warning.
  - `app.config`/`bgem3_hf_embedding` 는 `_warmup_bgem3` 함수 안에서 lazy import (단, `app.main` 은 이미 `app.routers.search → bgem3_hf_embedding` 경로로 같은 모듈을 끌어옴 — lazy import 가 새 import-time 비용을 막지도 줄이지도 않음, cosmetic).
- **`api/app/adapters/impl/bgem3_hf_embedding.py`** — `import email.utils`, `_MAX_RETRY_AFTER_SECONDS=60.0` 상수, `_parse_retry_after(exc) -> float | None` 함수 (`httpx.HTTPStatusError` 아니면 None / `Retry-After` 헤더 없으면 None / 정수 초 → `min(v, 60.0)` 클램프, 음수·0 → None / HTTP-date → `parsedate_to_datetime` → 미래면 양수차 클램프, 과거·파싱실패(`ValueError`/`TypeError`) → None). `_with_retry` 의 backoff 산출: `attempt == _MAX_ATTEMPTS` 면 break(sleep 없음), 아니면 `_parse_retry_after` 우선(`+jitter`), 없으면 기존 `5*2^(n-1)+jitter`. `_MAX_ATTEMPTS=3`·`_BASE_BACKOFF_SECONDS=5.0`·`_REQUEST_TIMEOUT=60.0` 미변경.
- **`api/app/adapters/impl/bge_reranker_hf.py`** — 위와 동일 `_parse_retry_after` 복붙 + `_with_retry` 적용. 상수 미변경. reranker default OFF 라 무해 — 코드 일관성 + ENV ON 대비.
- **`api/tests/test_search_503_retry_after.py`** — `TestClient(app)` 사용 2곳에 `patch.object(main_module, "_warmup_bgem3", new=AsyncMock(return_value=None))` 추가 (SE-11: lifespan 생기면서 TestClient 가 warmup 트리거 → 레포 `.env` 의 실 `HF_API_TOKEN` 으로 실 HF 호출이 나갈 수 있음 → mock 으로 차단). `AsyncMock` import + docstring 1줄. **기존 assert 무변경** (이 파일은 W3 Day 2 보강분 — "search.py 라우터가 HF 영구실패 시 503 + Retry-After:60 응답 헤더 emit" 검증, 레이어가 다름: 라우터 응답 emit vs 어댑터 retry consume — 충돌·중복 없음).

### 신규 (2)
- **`api/tests/test_coldstart_warmup.py`** — warmup 토큰없음 skip / 토큰있음 embed_query 1회 호출 / embed_query 예외 graceful / provider 생성 예외 graceful / CancelledError 재전파 / lifespan 진입 시 `app.state.bgem3_warmup_task` 가 `asyncio.Task`. `IsolatedAsyncioTestCase` + `TestClient`, HF·Supabase mock, 실 호출 0.
- **`api/tests/test_bgem3_retry_after.py`** — `_parse_retry_after` 7건(정수 delta / strip / 비정수 / 60s 클램프 / 0·음수 / garbage·빈문자·헤더없음·비-HTTPStatusError → None / HTTP-date 미래·과거) + `_with_retry` 2건(Retry-After 값으로 sleep / 헤더없으면 지수백오프) + reranker 패리티 2건. `time.sleep` mock + `httpx.Response` 픽스처, 실 호출 0.

`git status`: M 4 + ?? 2. `pyproject.toml`/`uv.lock` 무변경. 의존성 추가 0.

## 4. 검증 (senior-qa, ship GO — 치명 0 / 중요 0 / 사소 3)

- **hot path 영향 없음**: 부팅 비블로킹(yield 전 await 없음) / router·CORS·route 순서 무변경 / `embed_query` 동기 호출 `asyncio.to_thread` 위임 → 이벤트 루프 안 막음 / search.py·answer.py `embed_query` 호출부 git diff 0 / shutdown cancel+suppress 예외 누수 없음.
- **SE-11 회귀 없음**: `TestClient(app)` 쓰는 파일은 `test_search_503_retry_after.py`(2곳) + `test_coldstart_warmup.py`(1곳) 단 2개 — 세 곳 전부 `_warmup_bgem3` mock 적용. 968 통과 이유 = mock 차단 (비결정성 없음). 향후 `TestClient(app)` 신규 테스트 추가 시 warmup mock 필요 — 사소 #2.
- **`_parse_retry_after` 정확성**: 정수 클램프/음수·0·garbage·None → None / HTTP-date tz 처리(`parsed.timestamp() - time.time()` 둘 다 float, datetime 끼리 안 빼서 TypeError 없음) / `attempt == _MAX_ATTEMPTS` 면 sleep·파싱 안 함 — 전부 PASS.
- **인제스트 영향 없음**: `embed_batch`/`doc_embed` 도 같은 `_with_retry` — `_MAX_ATTEMPTS` 미변경, Retry-After 헤더 올 때만 backoff 시간 조정. 인제스트 테스트 회귀 0.
- **reranker 변경 무해**: default OFF, 패리티 테스트 2건, `test_reranker`/`test_search_reranker_path` 회귀 0.
- **회귀 0**: `cd api && uv run python -m unittest discover tests` → 968 OK / skipped 0 (3회 반복 일관). 951 + 17.
- 정성 검증: `python -c "logging.basicConfig(INFO); asyncio.run(_warmup_bgem3())"` → `POST .../bge-m3/... 200 OK` + `BGE-M3 warmup 완료` 로그 확인. (uvicorn 으로 띄우면 안 보임 — §6 #A 로깅 갭.)
- 사소 3건: ①lazy import 가 import-time 격리를 실제로 달성 못 함(cosmetic, 무해) / ②향후 TestClient 신규 테스트 warmup mock 누락 위험(conftest autouse mock 권고) / ③lifespan shutdown(cancel+suppress)·"비블로킹" 직접 테스트 부재(수동 검증으로 갈음, 회귀 가드 보강 권고). `to_thread` 워커 스레드는 cancel 무시(최대 60s 잔존, 운영 무해).

## 5. 효과 — 측정 한계 + 정성 확인

- HF free-tier scale-to-zero 타이밍은 비결정적 → 단위 테스트로 재현 불가, p95 개선을 hard gate 로 삼지 않음. `eval_retrieval_metrics`/`run_s3_d5` 재실행해도 측정 noise 큼.
- 정성 확인: warmup 경로·HF 호출·로그 모두 정상 동작 (직접 실행 시 `200 OK` + 완료 로그).
- 기대 효과: 앱 콜드 부팅 → 사용자가 브라우저 열고 query 던지기까지 수 초~수십 초 사이에 warmup 이 모델을 데움 → 첫 query 가 warm 수준(<~3s)으로 응답. **한계: 부팅 후 사용자 query 전 모델이 다시 idle(HF 측 5분+ 추정)되면 무효** — 그 경우 첫 query 다시 cold (구조적, O3 keepalive 만이 해결하나 quota 트레이드오프로 미채택).

## 6. 남은 이슈 / 한계

- **#A uvicorn 로깅 갭 (기존 이슈, 이번 변경 무관)** — uvicorn 기본 로깅 config 가 `uvicorn`/`uvicorn.error`/`uvicorn.access` 로거만 핸들러 부착, root(`app.*`) 미설정 → `logging.getLogger("app.main")` 메시지가 콘솔에 안 보임. 전 `app.*` 모듈 공통. → warmup 이 "조용히 실패"하면 운영자가 모름. **사용자 사인오프 필요**: `app/main.py` 에 최소 logging 부트스트랩(`logging.getLogger("app").addHandler(...)` 또는 `--log-config`) 을 별도로 넣을지 — 본 작업 범위 밖이었음. (warmup 실패해도 첫 검색에서 정상 재시도되므로 ship 차단 아님.)
- **#B O1 구조적 한계** — 부팅 후 사용자 query 전 모델 재-idle 시 warmup 무효. O3 keepalive 만이 해결 (quota 트레이드오프 — Q-coldstart-1 미채택).
- **#C O4(b) 첫-attempt timeout 분리 미구현** — `_REQUEST_TIMEOUT=60.0` 단일 → warm 상태 네트워크 hiccup 시에도 첫 attempt 가 60s 까지 기다림. probe(짧게)/load(길게) 분리는 인제스트 `embed_batch` 영향 검토 비용 커서 follow-up 으로 분리 (Q-coldstart-3).
- **#D 향후 `TestClient(app)` 신규 테스트 warmup mock 누락 위험** — conftest autouse mock 또는 테스트 env `HF_API_TOKEN` 비움 권고 (별도 작업).
- **#E reranker `_parse_retry_after` 복붙 중복** — 명세 허용. 향후 공통 util 추출 여지 (과도한 추상화 지양 기조라 보류).

## 7. 다음 스코프 (2026-05-12+ 진입 시)

남은작업 우선순위 갱신:
1. ~~HF embed query cold-start 대응~~ → **본 문서로 완료** (O1 warmup + O4a Retry-After)
2. **(승격, 신규 1순위 후보) uvicorn 로깅 부트스트랩** — #A. `app.*` 로거가 콘솔에 안 보이는 기존 갭. warmup 로그 + 전반적 운영 가시성. 0.25일
3. **multimodal judge 17 row 확대** (~$0.03) — Phase 10 효과 일반화, paired t-test
4. **S4-B 본 PC 재검증 + entity_boost factor ablation** — cross_doc 약점(R@10 0.1273, top-1 0.0). origin 80% ship 정합성 확인 → entity_boost 1.0/1.5/2.0
5. acceptable judge 2차 라운드 (~$0.05) — caption_dependent huge-doc 7건 `[empty]` 재시도
6. O4(b) 첫-attempt timeout 분리 (인제스트 영향 검토 후) — #C
7. chunk text augmentation (S4-A D6 후보, 5~7일) / G-A-208 multimodal judge timeout 진단 / reranker degrade cap dev 관리(Q-D6-3)

새 미결 / 결정:
- ~~Q-coldstart-1~8~~ → 전부 확정 (§2). Q-coldstart-3(b) 첫-attempt timeout 분리만 follow-up 으로 잔존(#C)
- **Q-uvicorn-logging** #A 를 별도 작업으로 처리할지 + 방식 (`addHandler` vs `--log-config` vs `dictConfig`)

## 8. ship 메모

- main HEAD: 본 ship 전 `216086e`. 본 ship 후 커밋 1건 추가 예정 (변경 4 + 신규 2 = 6 파일).
- 산출물: 코드 + 단위 테스트 + 본 work-log. 의존성 변경 0.
- push 는 사용자 요청 시.
