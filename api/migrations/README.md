# Migrations

Supabase SQL Editor에서 순서대로 실행.

| # | 파일 | 내용 |
|---|---|---|
| 001 | `001_init.sql` | 초기 스키마 — `documents`, `chunks`, `ingest_jobs`, `ingest_logs` + pgvector/uuid-ossp extension + 인덱스 + RLS enable |
| 002 | `002_documents_received_ms.sql` | W2 Day 2 — `documents.received_ms INT NULL` 추가 (SLO 측정용, `/stats.slo_buckets` 집계 근거) |
| 003 | `003_hybrid_search.sql` | W3 Day 1 — HNSW (chunks.dense_vec / documents.doc_embedding) + chunks.fts (simple FTS) + pg_trgm + RPC `search_hybrid_rrf` (RRF k=60) |
| 004 | `004_pgroonga_korean_fts.sql` | W3 Day 2 v0.5 — PGroonga (Mecab) 로 sparse path 교체. chunks.fts/idx_chunks_fts DROP, idx_chunks_text_pgroonga 신설. chunks.flags JSONB 컬럼 추가 (DE-62). RPC `search_hybrid_rrf` 재작성 + `search_sparse_only_pgroonga` 신설 |
| 004-rollback | `004_rollback.sql` | (옵션 안전망, 미적용) PGroonga 인덱스/RPC 제거 → 003 의 simple FTS 복구. 적용은 운영자 결정 게이트 (PGroonga 빌드 실패·sparse RPC 비정상·호스팅 미지원 통보 중 하나). 적용 후 sparse_hits 회귀 → 사용자 측 검색 품질 정성 점검 의무. |
| 005 | `005_vision_usage_log.sql` | W15 Day 2 — `vision_usage_log` 테이블 + 인덱스 + RLS. Vision API 호출 1건당 row 1건 영구 저장 (휘발성 한계 #34·#62 회수 준비). Python write-through 는 W15 Day 3+. |
| 006 | `006_search_metrics_log.sql` | W15 Day 2 — `search_metrics_log` 테이블 + mode/fallback 인덱스 + RLS. 검색 호출 1건당 row 1건 (한계 #61·#76·#81 회수 준비). |
| 007 | `007_metrics_trend_rpc.sql` | W16 Day 1 — 추세 분석 RPC 2개 (`get_search_metrics_trend(range, mode)` + `get_vision_usage_trend(range)`). epoch floor 기반 24h/7d/30d 시간 버킷 + zero-fill (generate_series). SECURITY DEFINER + service_role GRANT. frontend 시계열 그래프 (W16 Day 3) 의 데이터 소스. |
| 008 | `008_search_mode_split_rpc.sql` | W20 Day 1 — `search_dense_only(query_dense, k_rrf, top_k, user_id_arg)` + `search_sparse_only(query_text, k_rrf, top_k, user_id_arg)` 신규. 기존 `search_hybrid_rrf` 와 schema 100% 동일 (chunk_id·doc_id·rrf_score·dense_rank·sparse_rank). 진정 ablation 측정 — 응용 layer 필터링 제거 (한계 #74 회수). backward compat — search_hybrid_rrf / search_sparse_only_pgroonga 유지. |

## 실행 절차

1. Supabase 대시보드 → SQL Editor → New query
2. 파일 내용을 전체 복사·붙여넣기
3. Run
4. 완료 후 **Table Editor**에서 4개 테이블 생성 확인

## W15+ 일괄 적용 가이드 (005·006·007·008)

W15·W16·W20 누적 마이그레이션 — 한 번에 일괄 적용 권장.

**선결 조건**: 001~004 가 이미 적용된 상태.

**순차 적용** (각 파일 별도 Run, 트랜잭션 분리):

```
1. SQL Editor → New query → 005_vision_usage_log.sql 전체 paste → Run
2. SQL Editor → New query → 006_search_metrics_log.sql 전체 paste → Run
3. SQL Editor → New query → 007_metrics_trend_rpc.sql 전체 paste → Run
4. SQL Editor → New query → 008_search_mode_split_rpc.sql 전체 paste → Run
```

**검증 SQL** (각 파일 헤더에도 명시, 통합 확인용):

```sql
-- 005 vision_usage_log 테이블
SELECT count(*) FROM vision_usage_log;  -- 0 (미사용 시) 또는 누적 호출 수

-- 006 search_metrics_log 테이블
SELECT count(*) FROM search_metrics_log;

-- 007 추세 RPC 2개
SELECT count(*) FROM get_search_metrics_trend('24h', 'all');  -- 25 (1h × 24 + 시작 boundary)
SELECT count(*) FROM get_vision_usage_trend('24h');  -- 25

-- 008 split RPC 2개 — 정상 schema 확인
SELECT proname FROM pg_proc WHERE proname IN ('search_dense_only', 'search_sparse_only');
-- 2 row 반환 기대
```

**적용 후 활성화**:

1. `uvicorn` 재시작 (env 재로드)
2. 홈 dashboard 진입 → "검색 응답 추세" + "Vision API 호출 추세" 카드 자동 노출 (005·006·007 적용 후)
3. 검색 시 `mode=dense` / `mode=sparse` 토글 → 진정 ablation RPC 직접 호출 (008 적용 후)
4. `/stats` endpoint 응답 시점에 `vision_usage_log` / `search_metrics_log` 가 누적되기 시작

**graceful 보호** (005~008 미적용 환경):
- Python 코드가 try/except 로 RPC 부재 catch → 기존 in-memory metrics 만 사용
- `error_code='migrations_pending'` 응답 → frontend 안내 카드
- 미적용 → 적용 시점 사이의 데이터 손실 없음 (in-memory ring buffer 그대로 유지)

**운영 환경 변수** (W15~W18 누적 — `.env` 또는 systemd 등):

| env | default | 효과 |
|---|---|---|
| `JET_RAG_METRICS_PERSIST_ENABLED` | `"1"` | DB write-through 활성/비활성 |
| `JET_RAG_METRICS_PERSIST_ASYNC` | `"1"` | ThreadPoolExecutor fire-and-forget vs sync |
| `JET_RAG_VISION_ERROR_MSG_MAX_LEN` | `"200"` | error_msg DB row 크기 제한 |
| `JET_RAG_QUERY_TEXT_HASH` | `"0"` | search_metrics_log.query_text SHA256 hash (멀티 유저 PII 보호) |

## RLS 정책

현재는 모든 테이블 RLS 활성화 + 정책 없음 상태. `anon`·`authenticated` 키로는 접근 불가,
`service_role` 키만 모든 작업 가능. 백엔드 FastAPI는 `SUPABASE_SERVICE_ROLE_KEY`로 연결.

W5(인증 도입) 시점에 per-user 정책 추가 예정:
- `documents`: `user_id = auth.uid()`
- `chunks`: `doc_id IN (SELECT id FROM documents WHERE user_id = auth.uid())`
- `ingest_jobs`·`ingest_logs`: 동일 패턴

## Storage Bucket

SQL 마이그레이션 외에 대시보드에서 수동 생성 필요:

1. Storage → New bucket → name `documents` → **Private** (Public 체크 해제)
2. 기본 설정 유지 (파일 크기 제한은 입력 게이트에서 별도 검증)
