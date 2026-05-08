# 2026-05-09 — S4-A D1 vision prompt 5필드 + cache invalidate (v1 → v2 bump)

> senior-planner v0.1 명세 그대로 ship. Gemini Vision prompt 에 `table_caption` /
> `figure_caption` 2필드 추가, dataclass 까지 wire, vision_cache prompt_version
> 을 v1 → v2 bump 해 자동 invalidate. extract.py 영향 0 — chunks.metadata 전파는
> D2 후속.

## 1. 작업 개요

### 1.1. 목적

S4-A 표/그림 검색 보강의 첫 단계. PDF 페이지 단위 vision 호출이 표/그림이 있는
페이지에 대해 한국어 제목/주제 한 문장 (≤ 60자) 을 별도 필드로 반환하도록 prompt
계약을 5필드로 확장. 표 검색 (D2) · 그림 검색 보조 인덱스 (S4-B) 의 데이터 토대.

### 1.2. ship 단위

- D1 만 — dataclass + prompt + cache invalidate 까지
- D2 후속: `_enrich_pdf_with_vision` 가 chunks.metadata 로 5필드 전파
- D3~D5: 표 검색 path / 그림 보조 인덱스 / 통합 평가

### 1.3. 사용자 결정 (모두 권고대로)

- Q-S4-A-D1-1: caption 길이 상한 **60자** (caption 80자 와 구분)
- Q-S4-A-D1-2: 단일 페이지에 표 N개 → **첫 표 1개만** 추출 (D1)
- Q-S4-A-D1-3: v1 → v2 bump 후 **v1 row 삭제 X** (감사용 보존)
- Q-S4-A-D1-4: structured.표 필드와 table_caption **둘 다 유지** (병행)

## 2. 변경 사항

### 2.1. 변경 파일 목록 + LOC

| 파일 | 종류 | LOC delta | 비고 |
|---|---|---|---|
| `api/app/adapters/impl/gemini_vision.py` | 수정 | +18 / -3 | _PROMPT 2필드 추가 + _parse 안전 캐스팅 + VisionCaption 생성 |
| `api/app/adapters/vision.py` | 수정 | +5 / 0 | VisionCaption dataclass 2 필드 (default None) |
| `api/app/services/vision_cache.py` | 수정 | +21 / -5 | _VISION_PROMPT_VERSION v1→v2 + _serialize/_deserialize 2필드 |
| `api/tests/test_gemini_vision_caption_5fields.py` | 신규 | +99 | parse 5필드 / graceful 정규화 — 2건 |
| `api/tests/test_vision_cache_prompt_version_v2.py` | 신규 | +73 | v1 → v2 bump 종합 — 1건 |

총 신규 파일 2 / 수정 3 / 마이그 0.

### 2.2. _PROMPT diff 요약

기존 4필드 (type / ocr_text / caption / structured) 사이에 다음 2필드 추가:

```json
"table_caption": "이미지에 표가 있을 때 표의 한국어 제목/주제 한 문장 (≤ 60자, 없으면 null). 표가 여러 개면 첫 표 기준",
"figure_caption": "이미지에 그림/도식/차트가 있을 때 그림의 한국어 제목/주제 한 문장 (≤ 60자, 없으면 null)"
```

- 기존 `caption` (이미지 전체 ≤ 80자 요약) 은 그대로 유지 — 혼동 방지
- `table_caption` 과 `figure_caption` 은 동시 비-null 가능 (예: 표 + 표 설명 그림이 함께 있는 페이지)
- `structured.표.headers/rows` 와 `table_caption` 은 병행 유지 — 후자는 검색용 한국어 제목

### 2.3. _VISION_PROMPT_VERSION 변경

```python
# Before
_VISION_PROMPT_VERSION = os.environ.get("JETRAG_VISION_PROMPT_VERSION", "v1").strip() or "v1"
# After
_VISION_PROMPT_VERSION = os.environ.get("JETRAG_VISION_PROMPT_VERSION", "v2").strip() or "v2"
```

기본값만 변경 — ENV override 경로는 그대로. 기존 `JETRAG_VISION_PROMPT_VERSION` 을
명시적으로 설정한 환경은 그대로 동작.

### 2.4. _serialize / _deserialize 변경

- `_serialize`: row dict 에 `table_caption` / `figure_caption` 키 추가 (None 도 그대로 직렬화 → JSONB 의 null 보존)
- `_deserialize`: 두 키에 대해 str/None 안전 캐스팅 (빈 문자열·공백·비-str 모두 None)
- v1 row (두 키 부재) 도 graceful 복원 가능하지만, prompt_version 이 v2 라 lookup 단계에서 자연 격리 → 실제로는 도달 안 함

## 3. 사이드 이펙트

### 3.1. extract.py 영향 0

`_enrich_pdf_with_vision` 는 caption/ocr_text/structured 만 chunks.metadata 로
전파하는 기존 path 유지. table_caption / figure_caption 은 dataclass 에는 들어가지만
**chunks 에 적재되지 않음**. D1 의 의도된 범위 — D2 에서 metadata 전파 wire.

### 3.2. ImageParser 영향 0

`ImageParser.parse` 는 VisionCaption 의 caption/ocr_text/structured 만 sections
합성에 사용. 신규 2필드는 dataclass attribute 로만 존재 — 호출자가 명시적으로 읽어야
사용됨. 기존 회귀 테스트 (`test_vision_cache.ImageParserCacheIntegrationTest`) 전부
green — default None 으로 cache hit/miss path 모두 안전.

### 3.3. vision_metrics 영향 0

vlog insert / record_call / 누적 카운터 모두 변경 0. 5필드는 vision_metrics 가
관여하지 않는 영역.

### 3.4. 기존 cache row 격리

마이그 015 의 vision_page_cache row 들은 prompt_version='v1' 로 적재돼 있음. v2
lookup 은 `.eq("prompt_version", "v2")` 필터로 격리되므로 v1 row 와 매칭 0 →
**v1 row 는 DELETE 없이 자연 보존** (감사용). 운영자가 필요하면 추후 SQL 로 v1 row
정리 가능.

## 4. 단위 테스트

### 4.1. 신규 3건

| 파일 | 테스트 | 검증 |
|---|---|---|
| test_gemini_vision_caption_5fields.py | test_parse_table_and_figure_caption | 5필드 JSON → dataclass 5필드 모두 보존 |
| test_gemini_vision_caption_5fields.py | test_parse_missing_table_figure_keys_graceful | 부재/null/빈문자열/비-str 4 케이스 모두 None |
| test_vision_cache_prompt_version_v2.py | test_v1_row_miss_after_bump | 기본값 v2 + v1 row lookup miss + upsert v2 적재 + 신규 2필드 직렬화 종합 |

### 4.2. final count

```
Ran 735 tests in 16.086s
OK
```

- baseline 732 → final **735** (+3) — 명세 목표 정합
- skipped 1 / 회귀 0 / 신규 실패 0
- 기존 vision 회귀 테스트 (caption / vision_cache / image_parser / vision_cache_invalidate_policy / extract_pdf_vision_enrich / incremental_vision / vision_need_score / vision_metrics) 전부 green

### 4.3. 회귀 가드 결과

- [x] 기존 vision 회귀 테스트 green 유지 (default None 으로 호출자 영향 0)
- [x] v1 row 보존 (DELETE X) — 마이그/SQL 변경 0
- [x] extract.py 변경 0 → chunks 적재 영향 0
- [x] ImageParser sections 합성 영향 0 — 기존 caption/ocr_text 만 사용
- [x] vision_cache_invalidate_policy 회귀 (3건) — `_VISION_PROMPT_VERSION` 을 명시 monkey-patch 하므로 기본값 변경 영향 0

## 5. cold-start reingest 비용 spike 운영 메모

### 5.1. 영향 범위

prompt_version v1 → v2 bump 직후 **모든 PDF page 가 일시적으로 cache miss**.
운영 환경에 v1 row 가 N 개 누적돼 있다면 다음 reingest 사이클에서 N 개 페이지가
captioner.caption 재호출 발생 → vision API 비용 spike.

### 5.2. 비용 추정 (참고치)

- gemini-2.5-flash 페이지당 평균 estimated_cost ≈ $0.0007 (P1 D1 측정 기준)
- 1,000 페이지 reingest 시 spike: ≈ $0.7
- 무료 티어 한도 (1,500 RPM) 대비 단발성 — 누적 비용 영향 미미

### 5.3. 모니터링 권고

- vision_metrics 의 `total_calls` / `estimated_cost_total` 을 reingest 직후 시점에
  baseline 대비 비교
- 비정상 spike 시 `JETRAG_VISION_CACHE_ENABLED=0` 으로 임시 disable 후 prompt 검토
- 정상 시 24~48시간 내 v2 row 재축적 → cache hit rate 회복

### 5.4. v1 row 정리 (선택)

운영 안정 후 v1 row 가 더 이상 lookup 되지 않으므로 SQL 로 정리 가능 (의무 X):

```sql
-- 운영자 판단 시점에만 — D1 ship 자체는 SQL 변경 0
DELETE FROM vision_page_cache WHERE prompt_version = 'v1';
```

D1 시점에는 감사 보존 — 추후 prompt 회귀 (v2 → v1 롤백 검토) 시 즉시 hit 가능한
fallback 자산으로 활용.

## 6. 발견 이슈

- 없음 — 명세 그대로 ship, 회귀 0
- senior-planner 명세에서 신규 테스트 2건이 `test_gemini_vision_caption_5fields.py`
  에 동시 거주하도록 정의됐고, 정확히 그대로 적용 (graceful 케이스 4 sub-case 는 단일
  메서드 안에 통합 — sub-case 기반 단언으로 한 테스트 안에 보존)

---

## 남은 이슈

### I-S4-A-D1-001: chunks.metadata 5필드 전파 미구현

**현황**: D1 ship 후 vision 호출 시 caption 5필드를 받지만 chunks 에는 4필드만 적재
(`_enrich_pdf_with_vision` 는 caption/ocr_text/structured 만 metadata 에 wire).

**영향**: 검색 보조 인덱스 (S4-B) 가 table_caption / figure_caption 컬럼을 읽지
못함 — vision_cache 에는 v2 row 로 저장되지만 retrieve 단계에서 활용 0.

**해결 시점**: D2 — `_enrich_pdf_with_vision` 가 두 필드를 chunks.metadata 의
`vision_table_caption` / `vision_figure_caption` 키로 전파. metadata JSONB 컬럼이라
마이그 0.

### I-S4-A-D1-002: cold-start reingest 시 vision API 일시 spike

**현황**: prompt_version bump 로 모든 v1 row 가 lookup miss → reingest 시 일시적
captioner 재호출.

**영향**: 단발성 — 24~48시간 내 v2 row 재축적 후 cache hit rate 회복.

**대응**: §5 운영 메모 참조. 모니터링은 vision_metrics.total_calls / estimated_cost
로 충분.

---

## 다음 스코프 (D2)

### A. _enrich_pdf_with_vision metadata 전파

- `chunk.metadata["vision_table_caption"]` / `vision_figure_caption` 키 추가
- vision_cache hit 경로에서도 동일 metadata wire (cache hit 시 dataclass 의 두 필드 사용)
- 회귀 가드: 기존 metadata 키 (`vision_type` / `vision_caption` / `vision_ocr_text`) 유지

### B. extract pipeline 단위 테스트 (신규 2~3건)

- 5필드 응답이 chunks.metadata 에 정확히 들어가는지
- table_caption 만 비-null / figure_caption 만 비-null / 둘 다 null 3 sub-case
- cache hit path 도 동일 metadata 보장

### C. 검색 path 영향 분석

- bge-m3 임베딩 입력에 table_caption / figure_caption 을 어떻게 합성할지 의사결정
- 옵션 A: chunk 본문 앞에 prefix (예: "[표 제목] {table_caption}\n\n{본문}")
- 옵션 B: 별도 보조 chunk 로 분리 적재 (chunk_idx 별도)
- 옵션 C: metadata 만 보존 — 검색은 안 변경, rerank/필터에만 사용
- D2 작업 전 senior-planner 와 재협의 필수 (S4-B 와 강한 결합)

### D. 골든셋 v1 표/그림 row 재라벨링 (옵션)

- 표/그림 검색 보강 KPI 측정용 — 골든셋에 표/그림 관련 query 가 충분한지 점검
- 부족 시 v1.1 로 표/그림 query 10~20건 보강

### E. ship 정책

- D2 ship 전 베타 데이터 1주 누적 후 KPI 측정 (RAGAS 점수 개선 베타 후 판단 원칙)
- prompt_version 기본 v2 유지 — D3+ 추가 변경 시 v3 으로 다시 bump

---

## baseline 핸드오프 (다음 PC / 다음 세션 진입)

- 단위 테스트: **735 / skipped 1 / 회귀 0**
- 21 commits ahead origin/main (S4-A D1 commit 미반영 — 사용자 명시 시 commit)
- branch: main
- 현재 PC 절대경로: `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag`
- ENV 변경 0 (JETRAG_VISION_PROMPT_VERSION 미설정 시 자동 v2)
- 마이그 0 (jsonb 컬럼 안)
- D2 진입 전 senior-planner 협의 필요 — §다음 스코프 C (검색 path 영향)
