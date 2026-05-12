# 2026-05-12 — HF BGE-M3 embed query → dense 벡터 영구 캐시 (마이그 016)

## 배경

S4-B ablation (2026-05-12) 에서 발견: HF BGE-M3 Inference API 가 **같은 query 텍스트에 미세하게 다른 dense 벡터를 반환** → `dense_rank` → RRF 재정렬 → 세션 간 ~92 row(of 183) top-10 순위 churn. 집계 점수는 ±0.005 상쇄(회귀 아님)이지만 **평가셋 측정 재현성 결함**. + D6 의 HF Inference free-tier scale-to-zero cold-start (수백 ms ~ 67s).

→ query 텍스트(sha256) → dense 벡터 **영구 캐시** = 첫 fetch 벡터를 canonical 로 freeze → 워밍 후 측정 결정적 + HF 호출 0회(cold-start 부수 완화). 기존 in-process LRU 512 는 프로세스 종료 시 소실 → eval 매 실행마다 HF 재호출 → 비결정. 영구 캐시는 LRU 의 **아래 계층** 으로 보강.

선례: `vision_page_cache` (마이그 015) — sha256 키, JSONB result, RLS service_role, ENV 토글, `_warn_first` graceful 패턴 그대로 복제.

## 사용자 확정 결정

| Q | 결정 |
|---|---|
| 저장소 | DB 테이블 `embed_query_cache` (마이그 016) — vision_page_cache 선례 |
| 마이그 적용 | `supabase-jetrag` MCP `apply_migration` (015 동일) — **본 세션에서 미적용** (아래 ⚠️) |
| 벡터 컬럼 타입 | JSONB float array — 이 테이블에선 벡터 연산 안 함 (pgvector 의존 최소) |
| query 텍스트 저장 | sha256 만 (PII — 페르소나 A query 에 개인·업무 정보) |
| ENV 토글 | `JETRAG_EMBED_QUERY_CACHE` default "1", "0" 으로 off |

## 변경/신규 파일

| 파일 | 내용 |
|---|---|
| `api/migrations/016_embed_query_cache.sql` | **신규** — `embed_query_cache` 테이블 (`text_sha256`, `model_id`, `dim`, `vector JSONB`, `UNIQUE(text_sha256, model_id)`) + 보조 인덱스 + RLS service_role FOR ALL + COMMENT. 헤더 주석 015 스타일. 검증 SQL 주석 포함. |
| `api/app/services/embed_query_cache.py` | **신규** — `is_enabled()` / `lookup(text_sha256, model_id)` / `upsert(text_sha256, model_id, dim, vector)` / `model_id()` / `_warn_first` / `_reset_first_warn_for_test`. lazy import `from app.db import get_supabase_client`. 상수 `_MODEL_ID="BAAI/bge-m3"`, `_DENSE_DIM=1024`. `vision_cache.py` 패턴 복제. |
| `api/app/adapters/impl/bgem3_hf_embedding.py` | **수정** — `embed_query` 에 2단 캐시: ① in-process LRU(기존, key=text) → ② 영구 캐시(DB, key=sha256(NFC(text.strip())), model_id). `_cache_key(text)` static 헬퍼. `_last_cache_source`("lru"/"persistent"/"miss") 진단 플래그 추가. 영구 캐시 read/write 는 belt-and-suspenders try/except 로 graceful (검색 가용성 > 결정성). 모델 슬러그 `_MODEL_SLUG` 상수로 분리 (URL 조립). **`embed_passages`/`embed_batch`/`embed` 무변경 (인제스트 경로).** |
| `api/tests/__init__.py` | **수정** — 단위 테스트는 `JETRAG_EMBED_QUERY_CACHE="0"` 강제 (로컬 `.env` 에 실 Supabase 자격이 있어 mock 안 한 테스트가 실 DB 에 가비지 벡터 write 하는 것 방지). `JET_RAG_METRICS_PERSIST_ENABLED` 패턴과 동일. |
| `api/tests/test_embed_query_cache.py` | **신규** — 16 테스트: helper(env toggle / lookup hit·miss·dim 불일치 / db 실패 graceful + 첫 1회 warn / upsert row 형식·on_conflict·ignore_duplicates / db 실패 no-raise / empty vector skip), provider(miss→HF→upsert / persistent hit→HF 0+LRU 채움 / 재현성 c-1 LRU / 재현성 영구 캐시 after LRU clear / graceful lookup·upsert 예외→HF fallback / ENV off 우회 / NFC·NFD 같은 키). |

## 캐시 조회 순서 (`embed_query(text)`)

1. in-process LRU hit (key=text) → `list(cached)` 반환, `hit=True`, source="lru". [기존]
2. ★ 영구 캐시 `lookup(sha256(NFC(text.strip())), "BAAI/bge-m3")` hit → `len==1024` 검증 → 통과 시 LRU 에 put + 반환, `hit=True`, source="persistent".
3. miss → `_embed_query_uncached(text)` (HF + retry, 기존) → 성공 시 ★ `upsert(...)` best-effort + LRU put → `hit=False`, source="miss" → 반환.

- 영구 캐시 read/write 실패 → graceful (debug 로그, HF 직접). write 는 best-effort (실패해도 벡터 반환).
- 영구 캐시 hit 도 메트릭상 `_last_cache_hit=True` (= "HF 호출 안 함" 통일).
- import lazy — provider 가 supabase 의존 강제 안 함. **embed 입력 text 자체는 안 건드림** (HF 에는 호출부가 준 text 그대로 — `_cache_key` 의 NFC strip 은 키 계산에만).

## 단위 테스트 결과

`cd api && uv run python -m unittest discover tests` → **997 OK / skipped 0 / 회귀 0** (직전 981 + 신규 16). 신규 `test_embed_query_cache.py` 단독 16/16 OK.

## 재현성 검증

- **c-1 (필수, 완료)** — 단위 테스트 `test_reproducibility_second_hf_value_change_ignored`: 1회차 HF mock=0.11 캐시 후 2회차 HF mock=0.99 로 바꿔도 `embed_query(같은 text)` 가 0.11 반환 + 2회차 HF mock call count = 0. 추가로 `test_reproducibility_via_persistent_after_lru_cleared`: LRU 비우고(프로세스 재시작 모사) 영구 캐시 hit → 1회차 canonical(0.42) 반환, 2회차 HF mock=0.99 미호출. ✅
- **c-2 (실 DB eval, 미수행)** — `evals/run_s3_d5_search_stack_eval.py --combo a --goldenset v2` 2~3회 byte-identical 검증은 **마이그 016 미적용 + 실 eval 환경 미구성**으로 본 세션 미수행. 마이그 016 적용 후 별도 실행 권장. c-1 단위 테스트로 갈음.

## ⚠️ 미해결 — 마이그 016 적용

**본 세션(senior-developer) 환경에 `supabase-jetrag` MCP 툴(`apply_migration`/`list_migrations`/`list_tables`)이 노출되지 않음** (`.mcp.json` 에 서버 설정은 있으나 서브에이전트 툴셋에 미포함). 로컬에 `supabase` CLI / `psql` / `DATABASE_URL` 도 없음.

→ **마이그 016 은 미적용 상태.** `api/migrations/016_embed_query_cache.sql` 파일만 repo 에 작성됨. 다음 중 하나로 적용 필요:
1. MCP 접근 가능한 컨텍스트에서 `list_migrations` (다음 번호 016 확인) → `apply_migration(name="016_embed_query_cache", query=<016 파일 내용>)` → 검증 SQL (파일 헤더 주석 참조: `INSERT ... ON CONFLICT DO NOTHING; SELECT jsonb_array_length(vector); DELETE;`).
2. Supabase Studio → SQL Editor → 016 파일 paste → Run.

**미적용 상태에서도 안전** — `lookup` 이 PostgREST 404(`relation "embed_query_cache" does not exist`) 를 받으면 `_warn_first` 가 1회 warn 후 graceful None → HF 직호출 (현 동작 그대로 보존). 적용하면 자동 회복.

## 운영 메모

- **캐시 비우는 법** (새 측정 baseline 필요 시): `DELETE FROM embed_query_cache;` (또는 `WHERE model_id = 'BAAI/bge-m3'`). + 일시 우회는 ENV `JETRAG_EMBED_QUERY_CACHE=0` (eval ablation 에서 비결정성 의도적 측정 시).
- **HyDE on 시 hit 률 낮음** — `JETRAG_HYDE_ENABLED=true` 면 `embed_input = f"{clean_q}\n{hypothetical}"` 로 매 호출 hypothetical 이 달라져 캐시 키가 매번 새로움 → hit 못 함. 범위 밖 (production default RRF-only, HyDE off). 동작은 기존과 동일 (HF 직호출).
- **stale 벡터** — `model_id` 키로 방어. BGE-M3 고정이라 우려 0. 모델 교체 시 새 `model_id` row 로 재진입, 구 row 는 감사 목적 보존.
- **테이블 무한 증가** — 개인 사용자 query 무한 아님. TTL/eviction 보류 (over-engineering — vision_page_cache 도 무한 보관).
- **동시성 race** — `ON CONFLICT DO NOTHING` / `ignore_duplicates=True`. race 시 둘 다 HF 1회씩 낭비되지만 먼저 저장된 row 가 canonical → 결과 동일성 영향 없음.

## 명세 ↔ 실제 차이

- 명세: "conftest 의 supabase mock + ENV off 로 격리" — **conftest 없음** (프로젝트는 stdlib `unittest`). → `tests/__init__.py` 에 `JETRAG_EMBED_QUERY_CACHE="0"` 추가 (기존 `JET_RAG_METRICS_PERSIST_ENABLED` 와 동일 패턴). 신규 테스트는 setUp/tearDown 에서 "1"↔"0" 토글 + supabase mock.
- 명세: `_MODEL_ID` 를 provider 또는 헬퍼 중 한 곳에 정의 — **헬퍼(`embed_query_cache._MODEL_ID`)에 단일 정의**, provider 의 `_cache_key` 에서 lazy import. provider 의 `_URL` 조립용 `_MODEL_SLUG` 는 별도 상수(동일 문자열) — module-level cross-import 회피 + `_URL` 은 기존과 byte-identical 유지.
- 명세: provider 의 영구 캐시 호출 graceful — 명세는 "helper 가 graceful" 이지만 추가로 provider 측에도 `try/except`(belt-and-suspenders) — `_cache_key`/import 등 helper 밖 예외까지 흡수 (검색 정상 보장). DoD (d) 강화.
- 명세: `_last_cache_source` "재량" — 추가함 ("lru"/"persistent"/"miss"). search.py 메트릭은 미연결 (기존 `_last_cache_hit` 만 사용 — 영구 hit 도 hit=True 라 비율 측정 영향 없음).

## 사이드 이펙트 체크

- [x] `embed_passages`/`embed_batch`/`embed` (인제스트) 무변경 — diff 없음.
- [x] 검색 ranking/RRF 로직 변경 X. sparse 캐시 X.
- [x] `_URL` 기존과 동일 (verified).
- [x] 마이그 016 새 번호 파일 (기존 015 수정 X).
- [x] 무료 티어 — HF 호출 **감소** (paid cost $0).
- [x] 한국어 로그 메시지 (`_warn_first` — "마이그 016 적용 후 자동 회복").
- [x] SLO — 캐시 hit (DB ~수 ms) ≪ HF (수백 ms~67s). miss 는 기존 + write 오버헤드 미미 (best-effort, 비동기 아님이지만 단일 upsert).
- [x] 단위 테스트 997 OK / skipped 0 / 회귀 0.

## 다음 스코프 (이번 범위 밖)

1. 마이그 016 `apply_migration` 적용 + 검증 SQL (위 ⚠️).
2. (적용 후) `evals/run_s3_d5_search_stack_eval.py --combo a --goldenset v2` 2~3회 → byte-identical 확인 (c-2).
3. 필요 시 search.py `/stats` 에 `_last_cache_source` 분포 노출 (lru vs persistent vs miss 비율).
