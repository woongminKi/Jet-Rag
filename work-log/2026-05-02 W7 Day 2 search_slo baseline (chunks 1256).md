# search_slo monitoring snapshot

- 측정 시각 (UTC): `2026-05-02T14:42:36+00:00`
- API base: `http://localhost:8000`

## search_slo (in-memory ring buffer)

- sample count: **16**
- p50: **170ms**
- p95: **610ms**
- avg dense_hits: 49.1 · sparse_hits: 2.88 · fused: 50.0
- fallback breakdown: {"transient_5xx": 0, "permanent_4xx": 0, "none": 16}
- **cache hit rate**: 0.500 (8 / 16)

## warmup batch (cache miss + cache hit 혼합)

- 실행 query 수: 16
- avg: 831ms
- p50: 300ms
- p95: 610ms
- max: 8813ms

## 평가 가이드

| 지표 | 정상 (W6 cache hit) | 경고 임계 | 위험 임계 |
|---|---|---|---|
| p95 | < 200ms | 200~500ms | > 500ms (KPI §13.1 위협) |
| cache_hit_rate | > 0.4 (반복 쿼리 환경) | 0.2~0.4 | < 0.2 (cache 효과 ↓, 자료 다양성 ↑) |
| fallback_count | 0 | 1~5/500 sample | ≥ 5/500 sample (HF API 안정성 ↓) |

## 알려진 한계

- in-memory ring buffer (maxlen=500) — uvicorn 재시작 시 reset (W3 P3 F-4)
- 누적 자료 (50+ doc) 시 HNSW 인덱스 부담 ↑ → p95 추적 필요
- 본 스크립트는 단일 snapshot — 트렌드 추적은 W4-Q-16 (DB 영속화) 후