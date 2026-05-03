# 2026-05-03 W7 Day 5 — e2e 인제스트 4-stage mock test

> Day 4 §6 의 추천 다음 작업: e2e ingest mock test 회귀 보호 sprint.
> 비판적 재검토로 9 stage 전체 → **4 stage (chunk·chunk_filter·load·embed)** 로 축소.
> 또 비판적 재검토로 "클라이언트 주입 리팩토링" → **`unittest.mock.patch` 5곳 namespace** 로 전환 (회귀 risk 0).

---

## 0. 한 줄 요약

W7 Day 5 — e2e mock test 신규 ship (`1c2fd74`). 단위 테스트 **160 → 163** ran, 회귀 0. 4 stage 협력 흐름 (chunk·chunk_filter·load·embed) 의 통합 회귀 보호 base 확보. 외부 의존성 추가 0.

---

## 1. 진입 배경

Day 4 §6 의 후보 4건 재평가:
- **e2e ingest mock test (4-5h)** — W4·W5·W6 누적 변경 후 통합 회귀 보호. 가치↑
- Vision 회귀 검토 — 자료 부재 + ROI 제한적
- ablation — 정량 base 부재
- mobile debug 노출 — Day 4 한계 #15 는 의도된 정책

**Day 5 결정: e2e mock test sprint.**

---

## 2. 비판적 재검토 (사용자 §1)

### 2.1 9 stage 전체 vs 4 stage 부분

Explore 가 파악한 인제스트 9 stage:
extract → chunk → chunk_filter → content_gate → tag_summarize → load → embed → doc_embed → dedup

**재검토**:
- 9 stage 전체 mock 은 비용 큼 — content_gate / tag_summarize / doc_embed / dedup 은 외부 LLM·embedding 추가 mock
- W4·W5·W6 의 큰 변경 (DE-65, DOCX, chunk_filter 4종) 은 **chunk·chunk_filter·load·embed 4 stage 안에 모두 위치**
- extract 는 기존 parser 단위 테스트 (test_pymupdf_heading 등) 로 충분
- content_gate·tag_summarize 는 graceful fail 정책이라 e2e 우선순위 낮음

**결정: 4 stage 만 e2e** — ROI 최대화.

### 2.2 클라이언트 주입 리팩토링 vs mock.patch

Explore 가 추천한 "클라이언트 argument 주입 패턴 리팩토링":
```python
def run_chunk_stage(..., _client: Client | None = None):
    client = _client or get_supabase_client()
```

**재검토**:
- 9 stage 함수 시그니처 변경 → 기존 단위 테스트 160 회귀 risk
- Pipeline orchestrator (`run_pipeline`) 도 _client 전달 경로 추가 필요 → 수정 범위 확대
- 본 task 의 목적은 **테스트 신규 작성**, 비즈니스 로직은 손대지 말 것

**결정: `unittest.mock.patch` 로 5 namespace 가로채기** — 리팩토링 0, 회귀 risk 0.

### 2.3 mock 대상 (5 namespace)

senior-developer 가 발견한 결정적 사항:
- `from app.db import get_supabase_client` 처럼 모듈 namespace 에 함수 참조 복사 → **모듈별 patch 필요**

```python
patches = [
    "app.db.client.get_supabase_client",
    "app.ingest.jobs.get_supabase_client",            # stage context manager
    "app.ingest.stages.embed.get_supabase_client",    # chunks 재조회
    "app.adapters.impl.supabase_vectorstore.get_supabase_client",  # load 의 upsert
    "app.ingest.stages.embed.get_bgem3_provider",     # 실 HF 호출 차단
]
```

이 발견이 추후 e2e 확장 (content_gate / tag_summarize 등) 시에도 동일 패턴 재사용 가능.

---

## 3. 구현 — `api/tests/test_e2e_pipeline.py`

### 3.1 인프라

| 컴포넌트 | 역할 |
|---|---|
| `_FakeQueryResponse` | supabase-py `APIResponse` 흉내 — `.data` 속성만 노출 |
| `_FakeTableQuery` | chained API (`.table().select().eq().execute()`) 시뮬레이터, op·filter·payload 명시 추적 |
| `FakeSupabaseClient` | `_tables` dict — assertion 이 곧 "DB 에 무엇이 들어갔는지" 직접 확인 |
| `FakeBGEM3Provider` | `embed_batch(texts)` → 길이 N list[EmbeddingResult], 각 dense=1024 dim 더미 |
| `_seed_job` | ingest_jobs row 사전 시드 (`update_stage` 가 silent no-op 되지 않도록) |
| `_make_extraction` | sections list[tuple[text, heading]] → ExtractionResult 헬퍼 |
| `E2EBaseTest` | setUp/tearDown 에서 5 patch 시작·종료, lru_cache 비움 |

### 3.2 시나리오 3건

| ID | 클래스·메서드 | 검증 |
|---|---|---|
| **S1** | `GoldenPathTest.test_full_chain_persists_chunks_and_dense_vec` | 3 sections (제1조·제2조·제3조 한국어 도급계약) → 4 stage 통과 → chunks 카운트·page·section_title 보존·dense_vec 1024 dim |
| **S2** | `ChunkFilterMarkingTest.test_marked_chunks_persist_with_dense_vec` | table_noise (`\| col1 \| col2 \|`) + extreme_short ("2,800") + 정상 1건 → table_noise 1·extreme_short 1 정확 마킹, 마킹 청크도 dense_vec 채움 (검색 제외 정책 ↔ embed 진행 정책 분리 확인) |
| **S3** | `EmptyInputTest.test_no_chunks_no_embed_calls` | sections=[] → chunks 0 row, embed_batch 호출 0회, 예외 0 |

### 3.3 검증

```bash
uv run python -m unittest tests.test_e2e_pipeline -v
# 3 ran, OK (0.875s)
# S2 에서 chunk_filter WARNING 로그 정상 발생 — "마킹 비율 66.7% > 5%" 의도적

uv run python -m unittest discover tests
# Ran 163 tests in 3.616s — OK
# 회귀 0
```

### 3.4 senior-developer 가 발견한 의외 사항

1. **dummy SUPABASE_URL setdefault 회귀** — `test_search_user_isolation` 의 `_has_supabase_env()` 가 dummy 값을 진짜로 인식해 skip 하지 않고 실 호출 시도 → ConnectError 4건 → 즉시 수정. dummy 환경변수 제거. patch 가 settings 평가 자체를 차단하므로 dummy 불필요.
2. **chunk merge 차단** — S1 sections 모두 page=1 이면 `_merge_short_sections` 가 인접 병합 → chunk 수 예측 불가. `_make_extraction` 에서 page 자동 분리.
3. **WARNING 로그가 정상** — S2 마킹 비율 66.7% > 5% 경고는 false positive risk early signal 동작 확인.

### 3.5 commit

`1c2fd74` — `test(api): chunk→chunk_filter→load→embed 4-stage e2e mock 추가 (W7 Day 5)` (+653 lines)

---

## 4. 누적 효과 (W7 Day 5 마감)

| 항목 | W7 Day 4 | W7 Day 5 |
|---|---|---|
| 단위 테스트 | 160 ran | **163 ran** (+3 e2e) |
| 신규 테스트 시리즈 | 5 | **6** (test_e2e_pipeline) |
| 통합 흐름 회귀 보호 | 0 | **chunk·chunk_filter·load·embed 4 stage** |
| 비즈니스 로직 변경 | 0 | **0** (테스트만 신규) |
| 외부 의존성 추가 | 0 | **0** (stdlib unittest + mock) |
| commit | 5edf86e | **1c2fd74** |

### 4.1 W7 누적 commit

| Day | commit | 본질 |
|---|---|---|
| Day 1 | `523ab6d` | search 결과 카드 메타 (frontend) |
| Day 2 | `eace47e` | lint 회수 + monitor_search_slo |
| Day 3 | `be2c537` | /stats chunks 분포 (backend) |
| Day 4 F1 | `6849124` | 홈 ChunksStatsCard (frontend) |
| Day 4 F2 | `d88fdac` | 검색 디버그 모드 (frontend) |
| Day 4 doc | `5edf86e` | Day 4 work-log |
| **Day 5** | **`1c2fd74`** | **e2e 4-stage mock test (api)** |

---

## 5. 알려진 한계 (Day 5 신규)

| # | 한계 | 회수 시점 |
|---|---|---|
| 19 | extract / content_gate / tag_summarize / doc_embed / dedup 5 stage 는 e2e 부재 | 외부 LLM/Vision mock 비용↑ — W8 + 자료 누적 시 단계적 추가 |
| 20 | FakeSupabaseClient 의 chained API 부분 구현 (`.count` 등 미구현) | 새 stage e2e 추가 시 필요 시점에 확장 |
| 21 | 시나리오 3건 — page=1 전부면 chunk merge 트리거 부작용 발견 | `_make_extraction` 의 page 자동 분리로 우회. 필요 시 explicit page 지정 helper |
| 22 | EmbeddingResult / ChunkRecord / ExtractionResult dataclass 의 deep nesting 변경 시 fixture 헬퍼 수정 필요 | 인터페이스 안정 가정 |

---

## 6. 다음 작업 — W7 Day 6 후보 재평가

| 우선 | 항목 | Day 5 후 평가 |
|---|---|---|
| 1 | **content_gate / tag_summarize e2e 확장** | Day 5 의 패턴 재사용 가능. 단 LLM mock 추가 → 비용 ↑ |
| 2 | **frontend mobile 디버그 노출** | Day 4 한계 #15 회수 — 의도였으나 사용자 활용도 ↑ 위해 검토 |
| 3 | **README W7 누적 반영** | gitops 가시성 — Day 5 마감 후 적기 |
| 4 | **monitor_search_slo CI 자동화** | GitHub Actions or 사용자 cron — 사용자 환경 의존성 |
| 5 | **Vision/이미지 처리 회귀 검토** | 자료 부재 — 합성 이미지 우회 가능하지만 ROI 제한적 |
| 6 | **doc_embed / dedup e2e** | dedup 의 Tier 2/3 cosine similarity 검증 — 큰 가치 |

**Day 6 추천: README W7 누적 반영 (~30min, 가성비↑) + dedup e2e 시나리오 추가 (~2h, Day 5 패턴 재사용)**

---

## 7. 한 문장 요약

W7 Day 5 — e2e 4-stage mock test ship (`1c2fd74`). 단위 테스트 160→163 ran, 회귀 0. mock.patch 5곳 namespace 패턴으로 stage 함수 시그니처 변경 0 + 외부 의존성 0. W4·W5·W6 누적 변경의 통합 회귀 보호 base 확보.
