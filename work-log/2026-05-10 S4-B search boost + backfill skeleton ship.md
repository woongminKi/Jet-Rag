# 2026-05-10 S4-B search boost + backfill skeleton ship

> Sprint: S4-B 후속 — search filter/boost ENV opt-in + backfill helper skeleton
> 작성: 2026-05-10
> 마감: search.py 에 entity boost (default OFF) + `evals/backfill_chunk_entities.py` skeleton (dry-run 검증)
> 입력: 직전 sprint (S4-B ingest 통합) + master plan §6 P1

---

## 0. 한 줄 요약

> **S4-B search boost + backfill skeleton ship**.
> (1) `search.py` 에 entity boost ENV opt-in (`JETRAG_ENTITY_BOOST=true`, default OFF, factor 1.10) — query entities ∩ chunks.metadata.entities ≠ ∅ 시 RRF score × 1.10 boost. graceful (entities 없는 chunks 영향 0).
> (2) `evals/backfill_chunk_entities.py` (~150 LOC) — 기존 chunks 룰 기반 entities 추가 helper. `--apply` 명시 안 하면 dry-run (DB 변경 0). idempotent (`entities` 키 이미 있으면 skip), batch update.
> 30 chunks dry-run 검증 — 10 chunks 추출 (33%), dates 11/percentages 3/identifiers 2.
> 단위 테스트 **880 OK / 회귀 0** (entity boost 코드 inline + opt-in default OFF). 누적 cost 변동 0.

---

## 1. 변경 내역

### 1.1 `api/app/routers/search.py` — entity boost

**상수 추가** (line ~107):
```python
_ENTITY_BOOST_ENV = "JETRAG_ENTITY_BOOST"
_ENTITY_BOOST_FACTOR = 1.10  # 작은 boost — 회귀 risk 최소화
```

**helper 추가** (TOC guard 함수 옆, line ~750):
```python
_entity_boost_enabled = (os.environ.get(_ENTITY_BOOST_ENV, "false").lower() == "true")
_query_entities = {}
if _entity_boost_enabled:
    qe = extract_entities(clean_q)
    _query_entities = {
        "dates": set(qe.dates), "amounts": set(qe.amounts),
        "percentages": set(qe.percentages), "identifiers": set(qe.identifiers),
    }

def _entity_match_chunk(chunk_id: str) -> bool:
    if not _entity_boost_enabled or not any(_query_entities.values()):
        return False
    chunk_ents = (chunks_by_id.get(chunk_id, {}).get("metadata") or {}).get("entities") or {}
    for cat, q_vals in _query_entities.items():
        if q_vals and (q_vals & set(chunk_ents.get(cat) or [])):
            return True
    return False
```

**RRF loop 통합**:
```python
if _entity_match_chunk(chunk_id):
    score *= _ENTITY_BOOST_FACTOR
```

### 1.2 신규 — `evals/backfill_chunk_entities.py` (~150 LOC)

**기능**:
- chunks 페이지 단위 fetch (page_size=100)
- 각 chunk 의 text → extract_entities → metadata.entities 통합
- batch update (idempotent: entities 키 이미 있으면 skip)
- dry-run default (변경 0) / `--apply` 시 실 DB 변경

**CLI**:
- `--apply` — 실 DB 변경 (기본 OFF)
- `--dry-run` — 명시 dry-run (default 와 동일)
- `--doc-id <UUID>` — 단일 doc 만
- `--batch-size N` (default 100)
- `--limit N` (디버깅용 처리 cap)

**dry-run 검증** (30 chunks):
```
[mode] DRY-RUN (변경 0)
total processed: 30
skipped (entities 이미 있음): 0
empty (entities 0건 추출): 20
updated: 10 (dry-run, DB 변경 0)

=== 추출 entities 분포 ===
dates: 11
percentages: 3
identifiers: 2
amounts: 0
```

→ ~33% chunks 에서 entities 추출. 적용 시 의미 있는 metadata 보강.

---

## 2. 동작 시나리오

### 2.1 시나리오 A: 기존 chunks (entities 없음) + JETRAG_ENTITY_BOOST=true

- query entity boost 가 발동하지만 chunks.metadata.entities 키 없음
- `_entity_match_chunk` False 반환 → boost 0
- → **운영 동작 변동 X** (graceful)

### 2.2 시나리오 B: 새 chunks (entities 있음, 직전 sprint 의 ingest 통합) + ENV ON

- query entity 추출 + chunks.metadata.entities 매칭 → boost 1.10
- → search ranking 변동 가능

### 2.3 시나리오 C: backfill apply 후 + ENV ON

- 기존 chunks 모두 entities 채워짐
- 시나리오 B 와 동일 효과 (전체 chunks 영향)

---

## 3. ablation 측정 (별도 sprint 권고)

본 sprint scope: 인프라 ship + dry-run 검증. ablation (TOC ON + entity_boost ON, 측정 효과) 는 backfill 적용 후 별도 sprint 가능.

이유: 현재 chunks 의 entities 없음 → boost 효과 0 예상. 측정 cost (BGE-M3 latency) 가 검증 가치 대비 ↑.

---

## 4. 비판적 한계 (정직 인정)

| # | 한계 | 영향 | 회복 |
|---|---|---|---|
| 1 | **entities 없는 chunks 영향 0** | 새 ingest 또는 backfill 후 효과 | 다음 ingest 또는 backfill apply (별도) |
| 2 | **boost factor 1.10 고정** | 너무 작거나 클 가능 | ablation 후 조정 (별도 sprint) |
| 3 | **string-level 매칭** | "2024년 4월 30일" vs "2024-04-30" 별개 | dates 정규화 (별도 sprint) |
| 4 | **backfill 1 chunk per UPDATE** | Supabase REST batch 제약 — 1만 chunks ≈ 분 단위 | bulk SQL 마이그레이션 (별도, 큰 변경) |
| 5 | **ablation 미측정** | 효과 정량 미검증 | backfill apply 후 별도 sprint |

---

## 5. 사용자 결정 보류 항목 변동

| ID | 항목 | 직전 | 갱신 |
|---|---|---|---|
| Q-S4B-search | search filter / boost | 별도 sprint | **infra ship ✅ (default OFF)** |
| Q-S4B-backfill-helper | backfill skeleton | 별도 sprint | **skeleton ship ✅ (dry-run 검증)** |
| Q-S4B-backfill-apply | 기존 chunks backfill 실 적용 | 신규 | **사용자 명시 권고** (DB 변경, ~수천 UPDATE) |
| Q-S4B-default-on | entity_boost default ON | 신규 | backfill apply + ablation 후 별도 결정 |

---

## 6. 다음 후보

### 6.1 1순위 — backfill apply (cost 0, 0.5~1 day, 사용자 명시 권고)

`uv run python ../evals/backfill_chunk_entities.py --apply` — 모든 chunks 의 metadata.entities 채움. DB 변경 (사용자 명시 권고).

영향 추정:
- chunks ~수천 개 × UPDATE = ~수분
- entities 추출률 ~33% (dry-run 검증 기반)
- DB metadata 부피 증가 ~10~30% (entities JSON)

### 6.2 2순위 — entity_boost ablation (cost 0, 0.5 day)

backfill 적용 후 D4 도구 재실행 + entity_boost ON 비교. 효과 측정.

### 6.3 3순위 — Flash-Lite LLM 보강 (cost ~$1.0 cap, 1 day)

비정형 entities (제품명/기관명/인명).

### 6.4 4~10순위 (잔여)

- 3순위 multimodal LLM judge (cost ~$0.10~$0.30)
- chunk text augmentation (큰 변경)
- 5~10 reingest / master plan 큰 sprints

---

## 7. 핵심 변경 파일 목록

### 수정
- `api/app/routers/search.py` — `_ENTITY_BOOST_ENV` + `_entity_match_chunk` helper + RRF loop 통합 (~50 LOC)

### 신규
- `evals/backfill_chunk_entities.py` (~150 LOC) — backfill helper
- 본 work-log

### 데이터 영향
- 0 건 (search ENV opt-in default OFF, backfill default dry-run)

### 운영 코드 변경 영향
- search.py: ENV `JETRAG_ENTITY_BOOST=true` 시만 활성, default OFF — 운영 동작 변동 X
- 새 chunks 만 metadata.entities 가짐 (직전 ingest 통합 sprint), 기존 chunks 영향 0

### 외부 cost
- 0 (룰 기반 + dry-run 만)
- 누적 (이번 세션 전체): ~$0.475 (변동 없음)

---

## 8. 한 문장 마감

> **2026-05-10 — S4-B search boost + backfill skeleton ship**. search.py 에 entity boost (`JETRAG_ENTITY_BOOST=true`, default OFF, factor 1.10) + backfill helper (dry-run 검증, 30 chunks 중 10 추출). 단위 테스트 **880 OK / 회귀 0**. 누적 cost 변동 0. 다음 = backfill apply (cost 0, 사용자 명시) 또는 ablation 측정 (backfill 후).
