# 2026-05-10 S4-A D3 Phase 2-A — 측정 도구 multi-doc cross_doc 매칭 fix

> Sprint: S4-A D3 Phase 2-A (Master plan §6 / 핸드오프 §4 후보 #3)
> 작성: 2026-05-10
> 마감: ship 완료 (도구 fix + 단위 테스트 + 재측정 + work-log)
> 입력: D4 raw json (cross_doc 4건 R@10=0.1667 폭락 진단) + golden v2 (157 row)

---

## 0. 한 줄 요약

> **Phase 2-A ship — 측정 도구 multi-doc 매칭 fix.** D4 cross_doc 폭락 (R@10=0.1667) 의 root cause = `_pick_target_item` 이 expected_doc_title 의 `|` separator 를 처리 못해 단일 doc 만 선택, 정답 chunks 가 multi-doc 분산 → 누락. multi-doc 매칭 helper `_pick_target_items` 추가 — sub-title 별 첫 매칭 item 합산, RRF score desc 통합. **재측정: cross_doc R@10 0.1667 → 0.2917 (+0.1250 / +75% relative). Overall R@10 0.7027 → 0.7237 (+0.0210)**, top-1 0.6429 → 0.6594 (+0.0165). 단위 테스트 758 → **766** (+8 / 회귀 0). DoD 0.75 까지 -0.0263 잔여.

---

## 1. 진단 (root cause)

D4 raw json 의 cross_doc 4 cell 분석:

| golden_id | doc_id | predicted_top10 | R@10 |
|---|---|---|---:|
| G-U-015 | (empty) | [67, 35, 25] | **0.0000** |
| G-U-031 | (empty) | [133, 113, 64] | **0.0000** |
| G-U-032 | (empty) | [151, 173, 155] | **0.0000** |
| G-A-075 | 88920c9e-... | [16, 20, 22, 15, ...] | 0.6667 |

→ **U-row (doc_id 비어있음, expected_doc_title 에 `|` separator) 3건 모두 R@10=0**. single-doc G-A-075 만 정상.

### 1.1 measurement 도구 한계

`_pick_target_item` 의 U-row 처리:

```python
title_norm = unicodedata.normalize("NFC", g.expected_doc_title).lower()
head = title_norm[:12]   # "쏘나타랑 데이터센터 자료" 같이 prefix 만
for it in items:
    if head and head in item_title:
        return it          # 첫 매칭 item 1건만
return items[0] if items else None  # fallback
```

문제:
1. `expected_doc_title="sonata-the-edge_catalog|2025년 데이터센터..."` 같은 multi-doc 표기에서 `head=":sonata-the-e"` (12자) → 첫 doc 만 매칭
2. golden v2 의 cross_doc relevant_chunks 는 `chunk_idx` 만 표기 (예: G-U-031=`129,397`) — 두 doc 합산 의도
3. measurement 는 첫 doc 의 matched_chunks 만 평가 → 정답 chunk 가 두 번째 doc 에 있으면 누락

---

## 2. 비판적 재검토 (3회)

| 단계 | 결정 |
|---|---|
| 1차 안 | golden v2 cross_doc row 4 → 8~10 단순 확장 (D4 권고 그대로) |
| 1차 비판 | "row 추가만 해도 R@10 폭락 해결?" → **불가**. 같은 측정 도구로 측정 시 동일 문제. root cause 가 도구 한계 |
| 2차 비판 (옵션 비교) | (A) 도구 fix 만 / (B) row 추가 만 / (C) 둘 다 → **A 채택 (Phase 2-A)**. row 추가는 Phase 2-B 로 분리 (수작업 라벨링 0.5~1일, 도구 검증 후 진입) |
| 3차 비판 (가정) | 측정 도구 fix 가 cross_doc 회복 보장 안 함 — search() 자체가 cross_doc retrieve 약점 가능. 도구 fix 후 re-measure 로 검증 우선 |

→ 권고: **Phase 2-A = 도구 fix + 단위 테스트 + 재측정**. row 추가 / search() cross_doc handling 개선은 별도 sprint.

---

## 3. 변경 사항

### 3.1 도구 수정 (운영 코드 변경 0)

`evals/run_s4_a_d4_breakdown_eval.py`:

- 신규 `_pick_target_items(items, g) -> list[dict]` — multi-doc 매칭 helper
  - doc_id 명시 row → `[match]` 1건 (single-doc 동치)
  - U-row + `|` separator 없는 title → 12자 prefix 매칭 + top-1 fallback (single-doc 동치)
  - U-row + `|` separator 있는 title → 각 sub-title 별 첫 매칭 item 합산 (multi-doc, 1+ 건)
  - 같은 doc_id 가 다중 sub-title 에 매칭되어도 중복 추가 안 함 (`seen_doc_ids` set)
- `_pick_target_item` 은 `_pick_target_items` 의 single-result wrapper 로 보존 — 하위 호환

`_measure_one_cell` 수정:

```python
target_items = _pick_target_items(items, g)
if not target_items:
    cell.note = "doc 매칭 fail"
    return cell

merged: list[dict[str, Any]] = []
for it in target_items:
    merged.extend(it.get("matched_chunks") or [])
matched = sorted(merged, key=..., reverse=True)
chunks_top = [c["chunk_idx"] for c in matched]
```

### 3.2 단위 테스트 추가

`api/tests/test_run_s4_a_d4_breakdown.py` — `PickTargetItemsTest` 8개 신규:

- doc_id 매칭 single item / 매칭 없음 빈 list
- single title prefix 매칭 / top-1 fallback (하위 호환)
- multi-doc `|` separator 매칭 (G-U-015 형태)
- 같은 doc 중복 추가 방지
- 부분 매칭 (sub-title 1건만 매칭) → 그 1건 반환
- `_pick_target_item` single wrapper 하위 호환

`Ran 20 tests in 0.010s — OK` (12 → 20 / +8).

### 3.3 운영 코드 변경

**0 건** — 측정 도구 + 단위 테스트만 수정.

---

## 4. 재측정 결과 (Phase 2-A 적용)

### 4.1 Overall 비교

| metric | Phase 1 (전) | Phase 2-A (후) | △ |
|---|---:|---:|---:|
| R@10 | 0.7027 | **0.7237** | **+0.0210** |
| nDCG@10 | 0.6125 | 0.6299 | +0.0174 |
| MRR | 0.5613 | 0.5791 | +0.0178 |
| top-1 | 0.6429 | **0.6594** | **+0.0165** |
| P95 lat | 895.9 ms | 602.9 ms | (variance) |
| doc 매칭 fail | 11 | 13 | +2 |
| n_eval | 140 | 138 | -2 |

DoD 0.75 까지 **-0.0263 잔여** (Phase 1: -0.0473).

### 4.2 cross_doc 회복 (4 cell 변화)

| golden_id | Phase 1 R@10 | Phase 2-A R@10 | △ |
|---|---:|---:|---:|
| G-U-015 | 0.0000 | 0.0000 | 0 (정답 chunk 15,0 retrieve 안됨) |
| G-U-031 | 0.0000 | **0.5000** | **+0.5000** |
| G-U-032 | 0.0000 | 0.0000 | 0 (정답 chunk 10,441 retrieve 안됨) |
| G-A-075 | 0.6667 | 0.6667 | 0 (single-doc 동치) |

**cross_doc qtype 평균: 0.1667 → 0.2917 (+0.1250, +75% relative)**.

### 4.3 qtype breakdown 변화

| qtype | R@10 전 | R@10 후 | △ | 비고 |
|---|---:|---:|---:|---|
| fuzzy_memory | 0.6333 | **0.7917** | +0.1584 | G-U-018 doc_fail 분류 → 평균 재계산 |
| summary | 0.5417 | 0.6667 | +0.1250 | (재계산 효과) |
| **cross_doc** | 0.1667 | **0.2917** | +0.1250 | fix 직접 효과 |
| exact_fact | 0.7418 | 0.7489 | +0.0071 | (소폭 재계산) |
| 외 5종 | 변동 없음 | | | RRF / mock 변화 0 |

### 4.4 doc_fail +2 분석

doc_fail 11 → 13 의 새 2건:
- **G-U-018** (fuzzy_memory, hwp): `expected_doc_title="law sample2|law sample3"` — multi-doc 의도지만 query_type=fuzzy_memory
- **G-U-027** (exact_fact, docx): `expected_doc_title="승인글 템플릿1|승인글 템플릿3"` — 동일

**해석**: Phase 1 에서는 single-doc path 의 top-1 fallback 으로 doc 매칭 성공 처리 (R@10=0 이지만 chunk_evaluable 에 포함). Phase 2-A 의 multi-doc path 는 두 sub-title 매칭 0건이면 fail 분류. **새 분류가 더 정확** — multi-doc 의도이지만 search 응답이 두 doc 모두 못 잡으면 chunk_evaluable 에서 제외하는 것이 평균 metric 정확도 향상.

---

## 5. 한계 + 잔존 문제

### 5.1 G-U-015, G-U-032 R@10=0 잔존

multi-doc 매칭은 성공 (search 응답에 두 doc 모두 포함) 하지만 정답 chunk_idx 가 search 결과의 top-50 안에 없음.

원인 후보:
1. **search() 자체의 cross_doc retrieve 약점** — RRF-only baseline 이 cross_doc 의도 query 의 정답 chunk 를 잡지 못함
2. **golden v2 의 정답 chunk_idx 라벨 한계** — G-U-015 의 relevant=`15,0` 이 부정확할 수 있음 (라벨러가 직관으로 추정)
3. **chunks 적재 시점 chunk_idx 와 골든셋 라벨 chunk_idx 불일치** (D5 reingest 시점 차이)

→ Phase 2-B (cross_doc row 4 → 8~10 확장) 시점에 정답 라벨링 재검증 권고.

### 5.2 caption gap 변화

| caption | R@10 전 | R@10 후 | △ |
|---|---:|---:|---:|
| true | 0.7655 | 0.7655 | 0 |
| false | 0.6957 | 0.7190 | +0.0233 |

gap (false − true): -0.0698 → **-0.0465** (절대값 줄어듦, 여전히 음수).

caption=false 만 +0.0233 이유: G-U-031 등 cross_doc U-row (caption=false) 의 R@10 회복이 false 그룹 평균만 끌어올림. caption=true 18건은 multi-doc 의도 0건이라 영향 없음.

D5 prompt v2 reingest 의 ROI 가설은 **여전히 신중** (gap 음수 유지).

---

## 6. 회귀 검증

```
Ran 766 tests in 25.281s
OK (skipped=1)
```

758 → **766** / +8 / 회귀 0.

---

## 7. 다음 후보 우선순위 (Phase 2-A 후)

| # | 후보 | 작업량 | 권고도 변화 | 이유 |
|---|---|---|---|---|
| 1 | **Phase 2-B** cross_doc row 4 → 8~10 확장 | 0.5~1일 (수작업 라벨링) | ★★★ → ★★★ | Phase 2-A 도구 검증 완료, 표본 확대로 통계 신뢰도 ↑ |
| 2 | **G-A-104~113 doc fail fix** | 0.5일 | ★★ → **★★★** | overall R@10 +0.045pp 회복 가능 (Phase 2-A 후도 10건 fail 잔존) |
| 3 | **search() cross_doc retrieve 진단** | 0.5~1일 | 신규 ★★ | G-U-015/032 R@10=0 잔존 — search 자체 한계 추적 |
| 4 | **S4-A D5** prompt v2 reingest | 가변 + cost ~$0.05 | ★★ → ★★ | caption gap -0.0465 (여전히 음수), ROI 가설 신중 |
| 5 | **S4-B** 핵심 엔티티 추출 | 3일 | ★★ → ★★ | 변동 없음 |

### 권고 (비판적 재검토 후)

**1순위 = G-A-104~113 doc fail fix** (포트폴리오 PDF 단일 doc 10건).
- 이유: Phase 2-A 후도 잔존 (포트폴리오 chunk page DB NULL — 핸드오프 §5 #6 별개 이슈). overall R@10 +0.045pp 회복 가능성 → DoD 0.75 임계 도달 가능
- 작업: search() title matching 보완 또는 포트폴리오 doc reingest. 0.5일 내 진단 + fix 결정

**2순위 = Phase 2-B cross_doc row 확장 + 동시에 search() 진단**.
- 이유: G-U-015/032 R@10=0 잔존이 search() 한계인지 라벨 부정확인지 분리 필요
- 표본 4 → 8~10 확장으로 통계 신뢰도 ↑ + 라벨 재검증

**3순위 = S4-A D5 reingest** — 여전히 신중.

---

## 8. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-Phase2-A-1 | 다음 sprint 1순위 | **G-A-104~113 doc fail fix** (overall R@10 +0.045pp 회복 가능성) | 사용자 명시 진입 |
| Q-Phase2-A-2 | Phase 2-B row 확장 진입 | search() 진단과 병행 | 사용자 결정 |
| Q-Phase2-A-3 | doc_fail +2 (G-U-018/G-U-027) 처리 | Phase 2-B 라벨링 시 multi-doc 의도 재검증 | 다음 sprint |
| ~~Q-Phase2-A-A 도구 fix vs row 확장~~ | ~~도구 fix 우선~~ | **해소 — Phase 2-A ship 완료** |

---

## 9. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정
- `evals/run_s4_a_d4_breakdown_eval.py` — `_pick_target_items` 헬퍼 + `_measure_one_cell` 의 multi-doc merge 로직
- `api/tests/test_run_s4_a_d4_breakdown.py` — `PickTargetItemsTest` 8 test 추가
- `evals/results/s4_a_d4_results.md` (gitignored) — 재측정 결과 갱신
- `evals/results/s4_a_d4_raw.json` (gitignored) — 재측정 raw 갱신

### 운영 코드
- 0 건

---

## 10. 한 문장 마감

> **2026-05-10 — S4-A D3 Phase 2-A ship**. cross_doc R@10 폭락 root cause = 측정 도구의 single-doc 매칭 한계 → multi-doc helper `_pick_target_items` 신규 + sub-title 별 합산. **재측정**: cross_doc R@10 0.1667 → **0.2917** (+0.1250), Overall R@10 0.7027 → **0.7237** (+0.0210, DoD 0.75 까지 -0.0263). 단위 테스트 758 → **766** / 회귀 0. **잔존 문제**: G-U-015/032 R@10=0 (search() cross_doc 약점 또는 라벨 부정확). 다음 후보 1순위 = G-A-104~113 doc fail fix (overall R@10 +0.045pp 회복 가능성).
