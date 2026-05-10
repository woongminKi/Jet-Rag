# 2026-05-10 S4-B ingest pipeline 통합 — chunks.metadata.entities ship

> Sprint: S4-B 후속 — ingest pipeline 통합 (룰 기반 entities 자동 chunks.metadata 저장)
> 작성: 2026-05-10
> 마감: chunk.py 의 `_to_chunk_records` 에 `extract_entities` 통합 + 회귀 가드 단위 테스트
> 입력: 직전 sprint (S4-B 룰 기반 helper) + master plan §6 P1

---

## 0. 한 줄 요약

> **S4-B ingest 통합 ship**.
> `api/app/ingest/stages/chunk.py` 의 `_to_chunk_records` 가 chunk.text 에서 `extract_entities` 호출 → `metadata.entities = {dates, amounts, percentages, identifiers}` 자동 저장. 빈 entities 시 키 자체 미주입 (graceful, metadata 부풀림 회피). try/except 로 chunk 저장 차단 회피.
> **회귀 가드 3 unit tests** — 추출 정상 / 빈 entities → 키 미주입 / 다른 metadata (overlap) 와 공존.
> 새 ingest 부터 chunks.metadata.entities 자동 채워짐. 기존 chunks 영향 0 (backfill 별도 sprint).
> search filter / boost 별도 sprint (ENV opt-in, 측정 ablation).
> 단위 테스트 877 → **880 (+3) / 회귀 0**. 누적 cost 변동 0.

---

## 1. 변경 내역

### 1.1 `api/app/ingest/stages/chunk.py` `_to_chunk_records` 통합

text NFC 정규화 직후 (line ~415) entities 추출 + metadata 통합:

```python
# 2026-05-10 — S4-B 엔티티 추출 (룰 기반, master plan §6 P1).
try:
    from app.services.entity_extract import extract_entities

    entities = extract_entities(text_nfc)
    if not entities.is_empty():
        metadata["entities"] = entities.to_dict()
except Exception:  # noqa: BLE001 — chunk 저장 차단 회피
    pass
```

**graceful 보장**:
- 빈 entities (모든 카테고리 비어있음) → metadata 키 자체 미주입 (DB 부풀림 회피)
- 추출 실패 (정규식 예외) → try/except 로 chunk 저장 자체는 차단 X
- 외부 의존성 0 (정규식만)

### 1.2 신규 — `api/tests/test_chunk_entities.py` (3 tests)

- `test_extracts_entities_into_metadata` — entities 정상 추출 + metadata.entities 채워짐
- `test_empty_entities_no_metadata_key` — 일반 텍스트 → entities 키 미주입
- `test_entities_coexists_with_other_metadata` — 2nd chunk 의 overlap_with_prev_chunk_idx 와 공존

### 1.3 검증

- **단위 테스트**: 877 → **880 (+3) / OK / skipped=1 / 회귀 0**
- 회귀 검증: 기존 ingest pipeline 단위 테스트 (test_vision_caption_propagation, test_chunk_filter, test_chunk_w4_q14, test_e2e_pipeline, test_nfc_normalize) 모두 통과

---

## 2. 동작 효과

### 2.1 새 ingest 시 자동 metadata 추가

```
chunks.metadata = {
    "overlap_with_prev_chunk_idx": 17,  # 기존
    "table_caption": "[표] 2026년 ...", # 기존 (vision-derived)
    "figure_caption": "[그림] ...",     # 기존 (vision-derived)
    "entities": {                       # 2026-05-10 신규
        "dates": ["2024년 4월 30일"],
        "amounts": ["50,000원"],
        "percentages": ["5%"],
        "identifiers": ["2288-7083"]
    }
}
```

### 2.2 기존 chunks 영향 0

기존 chunks 의 metadata.entities 미존재 — search filter / boost 시 graceful fallback 권고 (entities 없는 chunks 도 후보).

### 2.3 backfill 권고 (별도 sprint)

기존 chunks (~2000+ rows across 13 docs) 의 entities backfill — DB 변경. cost 0 (정규식만), 단 사용자 명시 권고:

```python
# tools/backfill_entities.py (별도 sprint)
# 1. chunks 전체 fetch (chunk_id, text, metadata)
# 2. entities = extract_entities(text)
# 3. UPDATE chunks SET metadata = jsonb_set(metadata, '{entities}', ...)
```

---

## 3. 비판적 한계 (정직 인정)

| # | 한계 | 영향 | 회복 |
|---|---|---|---|
| 1 | **새 ingest 만 적용** | 기존 chunks 영향 0 | backfill helper (별도 sprint) |
| 2 | **search 활용 X** | filter / boost 통합 X (별도 sprint) | search 옵션 sprint (ENV opt-in + ablation) |
| 3 | **룰 기반 만** | LLM 보강 (제품명/기관명/인명) 없음 | Flash-Lite 보강 (별도 sprint, cost ~$1.0 cap) |
| 4 | **DB 부풀림 risk** | 모든 chunks 에 entities 키 추가 가능성 (대량 ingest) | is_empty() 체크로 빈 entities 키 미주입 — 부풀림 방지 ✅ |
| 5 | **기존 ingest 회귀 가능성** | chunk.py 변경 → 기존 단위 테스트 통과 검증 | 877 → 880 (+3) / 회귀 0 ✅ |

---

## 4. 사용자 결정 보류 항목 변동

| ID | 항목 | 직전 | 갱신 |
|---|---|---|---|
| Q-S4B-ingest | ingest pipeline 통합 | 별도 sprint | **해소 ✅** |
| Q-S4B-backfill | 기존 chunks entities backfill | 신규 | 별도 sprint (cost 0, 단 DB 변경 → 사용자 명시) |
| Q-S4B-search | search filter / boost | 별도 sprint | 별도 sprint (ENV opt-in + ablation) |
| Q-S4B-llm | Flash-Lite LLM 보강 | 별도 sprint (cost ~$1.0) | 유지 |

---

## 5. 다음 후보

### 5.1 1순위 — S4-B search filter / boost (cost 0, 0.5 day)

ENV `JETRAG_ENTITY_BOOST=true` (opt-in, default OFF) — query 의 entities 와 chunks.metadata.entities 매칭 chunks score boost.

### 5.2 2순위 — S4-B chunks entities backfill (cost 0, 0.5 day)

기존 chunks 에 룰 기반 entities 채움. 사용자 명시 권고 (DB 변경).

### 5.3 3순위 — S4-B Flash-Lite LLM 보강 (cost ~$1.0 cap, 1 day)

비정형 entities (제품명/기관명/인명) — cost 발생, 사용자 cost 명시.

### 5.4 4~10순위

- 3순위 multimodal LLM judge (cost ~$0.10~$0.30)
- chunk text augmentation (큰 변경)
- 5~10 reingest / master plan 큰 sprints

---

## 6. 핵심 변경 파일 목록

### 수정
- `api/app/ingest/stages/chunk.py` — `_to_chunk_records` 에 entities 추출 통합 (~10 LOC)

### 신규
- `api/tests/test_chunk_entities.py` (3 tests)
- 본 work-log

### 데이터 영향
- 새 ingest 부터 chunks.metadata.entities 자동 추가
- 기존 chunks 영향 0 (backfill 별도)

### 운영 코드 변경 영향
- ingest pipeline 변경 — 새 chunks 영향 (graceful, 기존 metadata 보존)

### 외부 cost
- 0 (룰 기반)
- 누적 (이번 세션 전체): ~$0.475 (변동 없음)

---

## 7. 한 문장 마감

> **2026-05-10 — S4-B ingest pipeline 통합 ship**. chunks.metadata.entities 자동 저장 (룰 기반, 정규식). graceful (빈 entities → 키 미주입, 추출 실패 → chunk 저장 차단 X). 단위 테스트 877 → **880 (+3) / 회귀 0**. 누적 cost 변동 0. 다음 = search filter/boost (cost 0, 0.5 day) 또는 backfill (사용자 명시).
