# 2026-05-10 retrieval pool 진단 — ch 918/919 가 top-500 에도 없음 (root cause 검증)

> Sprint: search retrieval 정밀화 후속 — rpc_top_k 상향 (50 → 100~500) 효과 검증
> 작성: 2026-05-10
> 마감: rpc_top_k=500 으로 직접 RPC 호출 → ch 918/919 retrieval 가능 여부 명확 진단
> 입력: 직전 sprint (vision adjacent retrieval) 의 failed experiment + G-A-204 의 root cause 미규명

---

## 0. 한 줄 요약

> **retrieval pool 진단 ship — root cause 명확 식별 (failed experiment 의 본질)**.
> G-A-204 query "2026년 경제전망 요약표 어디 있어" + doc=d1259dfe 직접 RPC 호출 (rpc_top_k=500). same-doc rows = 356/500 중 ch **918/919 missing** (no presence). ch 902, 898 은 rank 1-2 (이미 top-10).
> **결론**: rpc_top_k 상향 (50→200→500 모두) 효과 0 — ch 918/919 가 search_hybrid_rrf 의 retrieval 자체 (BM25 + dense) 에서 surface 안 됨. doc 의 994 chunks 중 top-500 = 50% 안에도 없음.
> **진짜 fix**: chunk text augmentation (인덱스 단, embedding 재생성 필요), PGroonga 한국어 tokenization, BGE-M3 vision OCR 표 매칭 — 모두 큰 sprint, 별도 진입.
> 단위 테스트 861 OK / 회귀 0. 누적 cost 변동 0.

---

## 1. 진단 결과

### 1.1 진단 도구 (`/tmp/diag_rpc_204.py`)

```python
# 1. BGE-M3 dense vec query 임베딩
# 2. PGroonga query: "2026년 OR 경제전망 OR 요약표 OR 어디 OR 있어"
# 3. RPC search_hybrid_rrf 호출 (top_k=500, k_rrf=60)
# 4. same-doc rows 필터링 + chunks meta fetch + target chunk 검색
```

### 1.2 결과 (top-500 중 same-doc 356)

| target chunk | found | rank | rrf_score |
|---|---|---:|---:|
| ch 902 (목차) | ✓ | 1 | 0.0325 |
| ch 898 (표지) | ✓ | 2 | 0.0315 |
| **ch 918 (요약표 caption)** | **✗ MISSING** | — | — |
| **ch 919 (요약표 데이터)** | **✗ MISSING** | — | — |

### 1.3 top-30 same-doc rows (참고)

대부분 chunk_idx 800~970 + 0~400 분포. chunk 14, 12, 1, 0 는 첫 몇 페이지 chunks. ch 918/919 가 14페이지 vision OCR 인데도 retrieval surface 안 됨.

### 1.4 chunk_filter 검증 (이전 가설 차단)

`chunks.metadata.chunk_filter` 모두 None — 명시적 필터 없음.

---

## 2. Root cause 분석

### 2.1 가설 차단

| 가설 | 검증 | 결과 |
|---|---|---|
| rpc_top_k 부족 | direct RPC top_k=500 | ❌ — ch 918/919 missing |
| chunk_filter 마킹 | 직접 metadata 조회 | ❌ — 모두 None |
| user_id / doc_id RLS 차단 | same-doc rows 356 정상 | ❌ — RPC 정상 응답 |

### 2.2 진짜 원인 (가설)

**ch 918/919 의 BM25 + dense 매칭 자체가 약함**:

- ch 918 text: `[표] 2026년 2월 기준 경제 전망 요약표\n\n경제전망 요약표 (2026.2월)\n2024 2025 2026e) 2027e)\n연간 상반 하반1) 연간1)2) 상반 하반 연간2) 연간2)\n<전망의 주요 전제>\n세계경제 성장률(%)3)\n3.3 3.4 3.2 3.3[+0.3]`
- query "2026년 경제전망 요약표" 와 keyword 매칭 강해 보이지만 surface 안 됨

가능 원인:
1. **PGroonga 한국어 tokenization 한계** — "경제전망" 이 chunk 의 "경제 전망" (공백) 와 매칭 안 됨
2. **BGE-M3 dense 매칭 약점** — vision OCR 의 표 데이터 (숫자 + 형식) 가 의미 embedding 약함
3. **chunk text 의 line break 영향** — 표 데이터의 \n\n 분리가 token 가중치 ↓
4. **doc 의 chunks pool 매우 큼** (994 chunks) — 같은 doc 의 다른 chunks 가 RRF score 더 높음

### 2.3 비판적 한계

본 진단은 단일 query (G-A-204) 의 retrieval 분석. 다른 vision 표 query 도 동일 한계인지는 별도 진단 필요. 본 sprint scope 는 G-A-204 root cause 식별로 한정.

---

## 3. 다음 sprint 권고 (별도 진입)

### 3.1 1순위 (큰 효과 가능) — chunk text augmentation at index time

- ch 918 (caption + data) 와 ch 919 (data 연속) 통합 또는 caption prepend
- ingest 시 (vision page) chunks 에 caption inheritance
- chunks 재생성 + embedding 재생성 (cost + 데이터 변경)

### 3.2 2순위 — PGroonga query 정밀화

- "2026년" → "2026 OR 2026년 OR 2026.2" 등 normalization
- "경제전망" → "경제 OR 경제전망 OR 경제 전망" 공백 변형 매칭

### 3.3 3순위 — BGE-M3 외 다른 dense 모델 ablation

- multilingual-e5-large 등 한국어 표 데이터 매칭 비교
- 큰 작업

---

## 4. ship 결정 + 사용자 결정 보류 항목

본 sprint scope 는 **진단 자체** — root cause 식별. fix 는 별도 sprint.

| ID | 항목 | 결정 |
|---|---|---|
| Q-rpc-top-k | rpc_top_k 상향 | **불필요** — 이미 doc-scoped 200, top-500 에도 효과 0 |
| Q-chunk-augment | chunk text augmentation (index 단) | **별도 sprint** — chunks/embedding 재생성 |
| Q-pgroonga | PGroonga 한국어 tokenization | **별도 sprint** — 큰 변경 |
| Q-G-A-204-relabel-2 | G-A-204 acceptable 추가 정정 | retrieval 한계 인식 후 라벨 조정 (별도) |

---

## 5. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 일회성 (gitignored, /tmp)
- `/tmp/diag_chunk_filter_204.py` — chunk_filter 진단
- `/tmp/diag_rpc_204.py` — RPC top-500 진단

### 데이터 영향
- 0 건 (진단만)

### 운영 코드 변경 영향
- 0 건

### 외부 cost
- 0 (BGE-M3 cache hit, RPC local)
- 누적 (이번 세션 전체): ~$0.475 (변동 없음)

---

## 6. 한 문장 마감

> **2026-05-10 — retrieval pool 진단 ship**. rpc_top_k=500 직접 호출로 ch 918/919 (G-A-204 정답) 가 top-500 same-doc rows 안에도 **missing** 검증 — rpc_top_k 상향 효과 0 명확. chunk_filter 무관 (모두 None). root cause = BM25/dense 자체의 vision OCR 표 데이터 매칭 약점. **진짜 fix = chunk text augmentation (index 단) 또는 PGroonga/BGE-M3 정밀화** (별도 sprint, 큰 변경). 단위 테스트 861 OK / 회귀 0. 누적 cost 변동 0.
