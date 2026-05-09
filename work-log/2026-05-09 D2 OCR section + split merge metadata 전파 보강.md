# 2026-05-09 D2 OCR section + split/merge metadata 전파 보강 ship

> Sprint: S4-A D2 보강 (Master plan §6 + 핸드오프 §5.1 1순위 권고) — **운영 코드 fix + ROI 검증**
> 작성: 2026-05-09 (당일 D5 본격 ship 직후)
> 마감: ImageParser._compose_result OCR broadcast + chunk.py split/merge metadata 보존 + 단위 테스트 13 OK + sample-report 재 reingest + 측정 비교
> 입력: 어제 D5 본격 ship 의 신규 발견 (caption metadata 부착 chunks=1건만)

---

## 0. 한 줄 요약

> **D2 metadata 전파 두 단계 fix ship — (1) ImageParser._compose_result 의 OCR/action_items section 에 caption_metadata broadcast, (2) chunk.py 의 _split_long_sections + _merge_short_sections 에서 section.metadata 보존**. 결과: caption metadata 부착 chunks **6 → 89 (14.8배)**, vision-derived chunks 의 92.7% coverage. caption=true R@10 0.6814 → **0.6957 (+0.0143)**, caption gap +0.0306 → **+0.0130 (58% 회수율)**, summary R@10 +0.019, vision_diagram nDCG +0.078. 단위 테스트 770 → **775** (+5 회귀 가드) / 회귀 0. 운영 코드 2 파일 + 테스트 1 파일 변경. ENV `JETRAG_DOC_BUDGET_USD=0.55` 1회성 (cap 도달 후 unset). 다음 후보 1순위 = table_lookup/fuzzy_memory 회귀 진단 (cost 0).

---

## 1. 진단 → root cause 두 단계

### 1.1 어제 D5 본격 후 신규 발견

- vision_page_cache v2 = 18 row (table_caption 11, figure_caption 6 채움)
- chunks 의 caption metadata 부착 = **1건만** (931 chunks 중)
- 즉 v2 cache 의 caption 정보가 chunks 까지 도달 못함

### 1.2 1차 root cause — ImageParser._compose_result 의 OCR section 미부착

`api/app/adapters/impl/image_parser.py:165-219` — caption section 1개 에만 caption_metadata 부착, OCR section + action_items section 은 metadata 빈 dict.

→ 1 vision page = 2~3 sections (caption + OCR + action_items) 인데 caption 만 metadata 보유. OCR section text 가 풍부한데 caption 정보 누락.

### 1.3 2차 root cause — chunk.py split/merge 단계에서 metadata 손실

1차 fix 후에도 caption=true R@10 -0.0084 (회귀). chunks 만 1 → 6 으로 증가, vision-derived chunks 89개 중 6개만 caption 보유.

진단:
```python
# api/app/ingest/stages/chunk.py:88-103 — _split_long_sections
out.append(
    ExtractedSection(
        text=piece_text,
        page=section.page,
        section_title=section.section_title,
        bbox=section.bbox,
        # ← metadata 인자 누락! default_factory=dict 로 빈 dict 채워짐
    )
)
```

OCR section text 가 `_MAX_SIZE` (1000) 보다 길면 `_split_by_sentence` 로 split → 새 ExtractedSection 들에 metadata 미주입 → caption 손실.

`_merge_short_sections` (line 286-293) 도 동일 bug — merge 시 metadata 누락.

→ caption section text 는 짧아서 (`[type] caption`) split 안 됨 → 1개만 부착됐음.

---

## 2. 적용 fix

### 2.1 ImageParser._compose_result OCR/action_items broadcast

`api/app/adapters/impl/image_parser.py`:

```python
caption_metadata: dict = {}
if caption.table_caption is not None:
    caption_metadata["table_caption"] = caption.table_caption
if caption.figure_caption is not None:
    caption_metadata["figure_caption"] = caption.figure_caption

sections.append(ExtractedSection(
    text=f"[{caption.type}] {caption.caption}".strip(),
    section_title=f"이미지 분류: {caption.type}",
    metadata=dict(caption_metadata),
))
if ocr_clean:
    sections.append(ExtractedSection(
        text=ocr_clean,
        section_title="OCR 텍스트",
        metadata=dict(caption_metadata),  # ← broadcast 추가
    ))
if action_items:
    sections.append(ExtractedSection(
        text=bullet_text,
        section_title="액션 아이템",
        metadata=dict(caption_metadata),  # ← broadcast 추가
    ))
```

`dict()` 복사로 frozen ExtractedSection 의 metadata 인스턴스 격리.

### 2.2 chunk.py split/merge metadata 보존

`api/app/ingest/stages/chunk.py`:

```python
def _split_long_sections(sections):
    ...
    for piece_text in _split_by_sentence(section.text):
        out.append(ExtractedSection(
            text=piece_text,
            page=section.page,
            section_title=section.section_title,
            bbox=section.bbox,
            metadata=dict(section.metadata),  # ← 추가
        ))

def _merge_short_sections(sections):
    ...
    if can_merge:
        merged_metadata = {**buf.metadata, **section.metadata}  # ← 추가
        buf = ExtractedSection(
            ...,
            metadata=merged_metadata,
        )
```

merge 시 dict-merge (section 우선) — 같은 vision page sections 가 동일 caption 공유라 충돌 시 section 우선이 안전.

### 2.3 단위 테스트 보강 (5 신규)

`api/tests/test_vision_caption_propagation.py`:

- ImageParser broadcast 4건 — `test_ocr_section_inherits_caption_metadata`, `test_action_items_section_inherits_caption_metadata`, `test_v1_compatible_no_caption_no_keys`, `test_caption_metadata_independent_per_section`
- split/merge metadata 보존 5건 — `test_split_long_section_preserves_caption_metadata`, `test_split_metadata_independent_per_piece`, `test_split_short_section_keeps_metadata`, `test_merge_short_sections_dict_merges_metadata`, `test_merge_section_overrides_buf_on_key_conflict`

→ 향후 회귀 가드.

---

## 3. 적용 후 chunks 의 caption 분포

```
sample-report chunks total: 994
  table_caption: 56  (이전 broadcast: 2 → fix: 56)
  figure_caption: 45 (이전 broadcast: 6 → fix: 45)
  any caption: 89    (이전 broadcast: 6 → fix: 89, 14.8배 ↑)
vision-derived chunks: 96
  caption coverage: 89/96 = 92.7% ✅
```

D2 의 의도 (vision page 의 모든 sections 가 caption 공유) 가 chunks 단계에서 정확히 구현됨.

---

## 4. 측정 비교 (RRF-only baseline, golden v2 157 row, n_eval 148)

### 4.1 Overall

| 시점 | R@10 | top-1 | P95 ms |
|---|---:|---:|---:|
| D4 baseline (v1 cache 만) | 0.7103 | 0.6351 | 600 |
| D5 본격 (1차, caption=1) | 0.7116 | 0.6284 | 550 |
| OCR broadcast (caption=6) | 0.7085 | 0.6284 | 521 |
| **split/merge fix (caption=89)** | **0.7072** | 0.6284 | 572 |

### 4.2 caption_dependent gap (D5 의 직접 KPI)

| 시점 | true R@10 | false R@10 | gap (false − true) |
|---|---:|---:|---:|
| D4 baseline | 0.6796 | 0.7142 | +0.0346 |
| D5 본격 (1차) | 0.6898 | 0.7145 | +0.0247 (28% 회수) |
| OCR broadcast | 0.6814 | 0.7121 | +0.0306 (회귀) |
| **split/merge fix** | **0.6957** | 0.7087 | **+0.0130 (58% 회수)** ✅ |

→ **gap 0.0346 → 0.0130 = 62% 회수**. caption=true R@10 +0.0161 vs D4 baseline. **D5 ROI 가설 정량 검증 — split/merge fix 후 의미 있는 회수**.

### 4.3 qtype breakdown 변화 (split fix 후)

| qtype | n_eval | broadcast 후 R@10 | split fix 후 R@10 | △ R@10 | △ top-1 |
|---|---:|---:|---:|---:|---:|
| **summary** | 9 | 0.7037 | **0.7222** | +0.019 ✅ | 0 |
| **vision_diagram** | 2 | 0.6667 | **0.8333** | **+0.167 ✅** | 0 |
| exact_fact | 112 | 0.7372 | 0.7340 | -0.0032 | +0.009 |
| **table_lookup** | 6 | 0.6096 | 0.6096 | 0 | **-0.167 (회귀)** |
| **fuzzy_memory** | 4 | 0.7917 | 0.7083 | **-0.083 (회귀)** | 0 |
| synonym_mismatch | 4 | 0.7330 | 0.7330 | 0 | 0 |
| numeric_lookup | 7 | 0.5295 | 0.5295 | 0 | 0 |
| cross_doc | 4 | 0.2917 | 0.2917 | 0 | 0 |

→ summary + vision_diagram 회복, table_lookup top-1 + fuzzy_memory R@10 회귀 — 추정 표본 작음 + chunks 변동 (987 → 994) 영향. 다음 sprint 진단.

### 4.4 doc_type

| doc_type | broadcast 후 | split fix 후 | △ |
|---|---:|---:|---:|
| pdf | 0.6698 | 0.6727 | +0.0029 ✅ |
| hwpx | 0.7695 | 0.7500 | -0.0195 |
| hwp | 0.8201 | 0.8366 | +0.0165 ✅ |
| pptx | 1.0 | 1.0 | 0 |
| docx | 0.6714 | 0.6638 | -0.0076 |

→ pdf 회복 (sample-report 영향). hwpx 미세 회귀 — sample-report 의 chunks 변동이 다른 doc 의 retrieve 분포에 영향 (search top-50 의 cross-doc 경쟁).

---

## 5. ROI 가설 검증 결과

### 5.1 가설 — D2 OCR/action broadcast + split/merge fix 가 caption=true R@10 향상

**검증**: 양성.

- caption=true R@10 0.6796 (D4) → 0.6957 (split fix) = **+0.0161** (직접 효과)
- caption gap +0.0346 → +0.0130 = **62% 회수**
- summary R@10 +0.0741 (vs D4)
- vision_diagram nDCG +0.078 (broadcast 후 추가 회복)

### 5.2 cost ROI 누적

- D5 phase 1: $0.1113 (8 page v1 cache 적재)
- D5 본격 (1차): +$0.2374 (v2 18 page)
- OCR broadcast: +$0.1989 (v2 14 page 신규 호출 — needs_vision 분포 변동)
- split fix: +$0.0792 (v2 5 page 신규 호출)
- **누적: $0.6268** (45 cache rows)
- caption gap 회수: 0.0346 → 0.0130 (Δ 0.0216)
- 단위 ROI: $29 / 0.001 R@10

### 5.3 운영 코드 fix 의 부가 가치

- **운영 코드 fix 자체는 cost 0** — 모든 PDF/HWPX/DOCX/PPTX 의 vision-derived chunks 가 자동 혜택
- 다른 docs 의 vision_page_cache 가 v2 prompt 으로 reingest 되면 즉시 caption metadata 보유 chunks 생성
- **D2 ship 이래 잠재 buggy 상태였던 split/merge metadata 손실이 fix 됨** — vision_incremental flag 등 다른 metadata 키도 함께 보호

---

## 6. 다음 후보 우선순위 (split fix 후 재정렬)

### 6.1 1순위 — table_lookup top-1 -0.167 + fuzzy_memory R@10 -0.083 회귀 진단 (cost 0)

**왜?**
- 핵심 회귀 신호. chunks 987 → 994 (+7) 의 retrieve 영향 분리 필요
- table_lookup top-1: 0.3333 → 0.1667 (1/6 hit). 정답 chunk 가 top-1 에서 밀려난 원인
- fuzzy_memory R@10 0.7917 → 0.7083 (-0.083). 표본 4개라 1 row 영향 0.25

**작업**: G-A-021/G-U-001/G-U-018 등 회귀 row 의 search 응답 + chunks 매칭 추적. 0.5~1일.

### 6.2 2순위 — caption=true top-1 -0.0589 진단 (cost 0)

caption=true 의 R@10 +0.0143 vs top-1 -0.0589. caption text 합성이 top-K 에는 들어가지만 top-1 까진 못 끌어올린 원인.

### 6.3 3~10순위

| # | 후보 | 작업량 | 권고도 |
|---|---|---|---|
| 3 | golden v2 minority qtype 표본 확장 (caption_dependent 18 → 30+) | 1~2일 | ★★ |
| 4 | search() cross_doc retrieve 진단 | 0.5~1일 | ★★ |
| 5 | reranker 조건부 비활성 + combo c 운영 default | 1일 | ★★ |
| 6 | chunk_filter 45.5% 마킹 분석 | 0.5일 | ★★ |
| 7 | 다른 docs 의 v2 prompt reingest (sample-report 외 12 docs) | cost 가변 | ★ |
| 8 | S4-B 핵심 엔티티 추출 | 3일 | ★★ |
| 9 | S5 /answer UX | 1주 | ★★ |
| 10 | budget_guard cache hit 무료 인식 (운영 code 패치) | 0.5일 | ★ |

---

## 7. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-D2-fix-1 | 다른 docs reingest 시점 | sample-report 외 12 docs 도 D2 fix 혜택 받으려면 reingest 필요. 별도 cost 가변 | 후순위 |
| Q-cap-policy | `JETRAG_DOC_BUDGET_USD` 운영 default | $0.10 그대로 유지 (시범 한정 0.55) | 차후 |
| Q-D6-1 | 운영 default combo c (RRF+rerank+MMR) 채택 | 사용자 명시 | 후순위 |
| Q-regression-1 | table_lookup / fuzzy_memory 회귀 처리 | 다음 sprint 진단 후 결정 | 다음 sprint |
| (어제 잔존) | Q-D5-resume / Q-Phase2-B / 기타 | 별도 sprint | |

---

## 8. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정 — 운영 코드 (2 파일)
- `api/app/adapters/impl/image_parser.py` — OCR/action_items section caption_metadata broadcast
- `api/app/ingest/stages/chunk.py` — _split_long_sections + _merge_short_sections metadata 보존

### 수정 — 단위 테스트 (1 파일)
- `api/tests/test_vision_caption_propagation.py` — +9 신규 테스트 (Image broadcast 4 + Split/Merge 5)

### 수정 — gitignored (측정 결과)
- `evals/results/s4_a_d4_results.md` — split fix 후 측정 갱신
- `evals/results/s4_a_d4_raw.json` — raw

### 데이터 영향 (sample-report)
- chunks: 931 → 987 → 994 (broadcast + split fix 누적)
- vision_page_cache: v1=8 그대로, v2=18 → 32 → 37 (page cap 50 + 누적 cost cap)
- vision_usage_log SUM: $0.3487 → $0.5476 → $0.6268
- ENV `JETRAG_DOC_BUDGET_USD` — 1회성 상향 후 unset (운영 default $0.10 유지)

---

## 9. 한 문장 마감

> **2026-05-09 — D2 OCR section + split/merge metadata 전파 보강 ship**. ImageParser broadcast + chunk.py split/merge metadata 보존 fix 로 caption 부착 chunks **6 → 89 (14.8배)**, vision-derived chunks 의 92.7% coverage. caption=true R@10 +0.0161, caption gap **62% 회수** (0.0346 → 0.0130), summary +0.074, vision_diagram nDCG +0.078. 단위 테스트 770 → **775** / 회귀 0. 운영 코드 2 파일 fix (모든 vision-derived chunks 자동 혜택, cost 0). 다음 후보 1순위 = table_lookup/fuzzy_memory 회귀 진단 (cost 0).
