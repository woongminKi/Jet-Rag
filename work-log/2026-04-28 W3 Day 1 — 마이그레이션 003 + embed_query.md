# 2026-04-28 W3 Day 1 — 마이그레이션 003 + embed_query

> W3 정식 일정은 2026-05-06 ~ 05-12 이지만 사용자 결정으로 **Day 1 inert 변경 (마이그레이션 작성 + embed_query) 을 같은 날 안에 선행**. Day 2 부터는 5/6 정식 진입.
>
> **선행 문서**: `work-log/2026-04-28 W3 스프린트 명세.md` (v0.4 CONFIRMED, Option Y)

---

## 0. 다음 세션 진입점

### 0.1 working tree 상태
- `api/migrations/003_hybrid_search.sql` 신규 — 미커밋
- `api/app/adapters/impl/bgem3_hf_embedding.py` — `embed_query` 메서드 추가, 미커밋
- 본 work-log 신규 — 미커밋
- **사용자 액션 필요**: 003 마이그레이션을 Supabase Studio SQL Editor 에서 직접 적용 (MCP `--read-only` 가드 회피)

### 0.2 사용자 액션 — 003 마이그레이션 적용

```bash
# 1) Supabase Studio 에 로그인 → project ref: mpmtydudhojpukuuadrd
# 2) SQL Editor → New query
# 3) api/migrations/003_hybrid_search.sql 의 내용 전체 붙여넣기 → Run
# 4) 빌드 시간 chunks ~465 row 기준 ~10초 추정 (1k 미만 규모)
# 5) 검증 — Studio SQL Editor 또는 Claude MCP execute_sql:
```

```sql
-- 인덱스 종류 확인
SELECT indexname, indexdef FROM pg_indexes
 WHERE tablename IN ('chunks', 'documents')
   AND indexname IN ('idx_chunks_dense', 'idx_documents_embed');
-- 기대: indexdef 안에 'USING hnsw' 포함

-- fts 컬럼 존재 확인
SELECT column_name, data_type FROM information_schema.columns
 WHERE table_name = 'chunks' AND column_name = 'fts';
-- 기대: fts | tsvector

-- 기존 chunks 의 fts 자동 백필 확인
SELECT count(*) AS total, count(fts) AS with_fts FROM chunks;
-- 기대: total = with_fts (STORED 컬럼 자동 백필)

-- pg_trgm extension 확인
SELECT extname FROM pg_extension WHERE extname = 'pg_trgm';
-- 기대: pg_trgm 한 줄

-- search_hybrid_rrf RPC 등록 확인
SELECT proname FROM pg_proc WHERE proname = 'search_hybrid_rrf';
-- 기대: search_hybrid_rrf 한 줄
```

### 0.3 Day 2 진입 순서 (정식 W3 일정 5/7)

| # | 액션 | 예상 |
|---|---|---|
| 1 | `app/routers/search.py` 재작성 — RPC 호출 + `query_parsed` 응답 필드 + 메타 필터 4종 (tags/doc_type/from_date/to_date) | 2h |
| 2 | `app/routers/stats.py` — `search_slo` 키 추가 (`p50_ms · p95_ms · sample_count · avg_dense_hits · avg_sparse_hits · avg_fused`) | 30m |
| 3 | golden 쿼리셋 `/evals/search_queries.jsonl` 20건 수집 (한국어 자연어 10 + 키워드 5 + 메타 혼합 5) | 2h |
| 4 | 단위 검증 — RPC 직접 호출 → 라우터 통합 → live smoke (W2 baseline 6 doc 위에서 P95 측정) | 1h |

---

## 1. 오늘 달성 (W3 Day 1 인프라)

### 1.1 마이그레이션 003 작성 — `003_hybrid_search.sql`

**4 변경**:
1. **HNSW 인덱스** (DE-56) — `idx_chunks_dense`, `idx_documents_embed` IVFFlat → HNSW (`m=16, ef_construction=64`)
2. **chunks.fts STORED tsvector** (DE-57) — `to_tsvector('simple', text)` + GIN 인덱스
3. **pg_trgm extension** + `idx_documents_title_trgm` (Tier 3 dedup 의 파일명 유사도 — Day 4 사용)
4. **`search_hybrid_rrf` RPC** (DE-58) — k=60, dense=sparse=1.0, multi-tenant 대비 user_id 파라미터

**핵심 설계 선택**:
- ALTER TABLE 시점에 STORED generated column 자동 백필 → 별도 백필 스크립트 불요
- HNSW 빌드 본 마이그레이션 실행 시 1회 ~10~50초 (chunks 규모 의존)
- RPC 가 `documents.deleted_at IS NULL` 필터 + 미래 `user_id_arg` 멀티테넌시 옵션 노출

### 1.2 `BgeM3HfEmbedding.embed_query` 메서드 추가 (inert)

```python
def embed_query(self, text: str) -> list[float]:
    """검색 쿼리용 단일 텍스트 → 1024 dim dense vector."""
    def call() -> list[float]:
        resp = self._client.post(_URL, headers=self._headers, json={"inputs": text})
        return _parse_single_response(resp)
    return _with_retry(call, label="bge-m3.embed_query")
```

- chunks 인덱싱과 **같은 모델·endpoint 사용** (검색-인덱싱 일관성)
- DE-58 의 RRF dense 입력 경로
- `embed()` 와 시그니처 다름 (sparse 미사용이라 `EmbeddingResult` 래핑 생략, `list[float]` 직접 반환)

### 1.3 live smoke 통과
한국어 쿼리 `'지난달 받은 화장품법 개정 자료'` → 1024 dim float vector. 검증:
- public method 노출 — `embed`, `embed_batch`, `embed_query` 3종 + `dense_dim`
- 응답 dim == 1024
- 모든 element `float` 타입

---

## 2. 결정

본 Day 1 은 명세 v0.4 CONFIRMED 의 결정을 그대로 코드화한 단계라 **신규 DE 0건**. DE-56 ~ DE-58 은 마이그레이션 003 적용 시점에 RESOLVED 표기 예정.

---

## 3. 발견된 이슈

| # | 이슈 | 처리 |
|---|---|---|
| 1 | MCP `--read-only` 가 DDL 차단 (`apply_migration`, `CREATE INDEX` 등) | 사용자가 Studio 직접 적용. 본 work-log §0.2 가이드 |
| 2 | `idx_chunks_sparse` (sparse_json GIN, 001 정의) — sparse_json 빈 dict 만 가지므로 사실상 미사용 인덱스 | 제거 보류 — 003 의 변경 범위 밖. 추후 cleanup 마이그레이션 (W4+) 에서 함께 정리 |
| 3 | HNSW 빌드 시간 측정 — chunks 465 row 규모는 추정 ~10초 | 사용자 적용 시 실측 |

---

## 4. 잔여 이슈

| # | 이슈 | 영향 | 처리 시점 |
|---|---|---|---|
| 1 | search.py 재작성 (Day 2) — 기존 ilike 백업 또는 삭제 | 백업 불필요 (git history 충분) | Day 2 |
| 2 | golden 쿼리셋 20건 수집 | Recall@10 측정 입력 | Day 2 |
| 3 | RPC 에서 `query_dense IS NULL` 가드 (사용자 검색 시 임베딩 실패 시) | sparse-only fallback 필요 | Day 2 라우터 단에서 처리 |
| 4 | Day 5 측정 결과에 따라 RRF 가중치 (b) 옵션 / PGroonga 전환 옵션 검토 | KPI 충족 보험 | Day 5 |

---

## 5. 변경 범위

### 5.1 신규 파일
- `api/migrations/003_hybrid_search.sql`
- (본 work-log) `work-log/2026-04-28 W3 Day 1 — 마이그레이션 003 + embed_query.md`

### 5.2 수정 파일
- `api/app/adapters/impl/bgem3_hf_embedding.py` — `embed_query` 메서드 추가

### 5.3 의존성
- 신규 의존성 추가 **없음** (Option Y 의 핵심 이익)

---

## 6. AC 종합 (Day 1)

| AC | 결과 |
|---|---|
| 003 SQL 작성 — 4 변경 (HNSW + fts + pg_trgm + RPC) | ✅ |
| `embed_query` live smoke — 한국어 쿼리 → 1024 dim | ✅ |
| 의존성 추가 0 | ✅ |
| inert 변경만 (서버 reload 무영향) | ✅ |
| 사용자 가이드 (Studio 적용) 명확 | ✅ §0.2 |

---

## 7. W3 진행 누적

| 항목 | 우선 | 상태 |
|---|---|---|
| A 하이브리드 검색 — 인프라 (마이그레이션 + embed_query) | P0 | ✅ Day 1 |
| A 하이브리드 검색 — 라우터 + 평가셋 | P0 | ⏳ Day 2 |
| F HWPX/HWPML heading 분석 | 권장 | ⏳ Day 3 |
| Day 3 1차 측정 (Recall@10) | P0 | ⏳ Day 3 |
| C Tier 3 dedup | P1 | ⏳ Day 4 |
| E DNS rebinding 방어 | P2 | ⏳ Day 4 |
| Day 4 ablation (+5pp) | P0 | ⏳ Day 4 |
| Day 5 튜닝 + 재측정 + DoD | P0 | ⏳ Day 5 |

---

## 8. 회고 한 줄

W3 정식 일정 (5/6) 보다 1주 앞당겨 같은 날 안에 인프라 준비 (마이그레이션 SQL + embed_query) 까지 끝냄. 의존성 추가 0 + inert 변경만이라 hot-reload 환경 회귀 risk 0. 사용자가 Studio 에서 003 적용하면 Day 2 (5/7) 부터 search.py 재작성 + golden 쿼리셋 20건 수집 진입 가능.
