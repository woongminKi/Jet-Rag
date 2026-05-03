# 2026-05-03 W9 Day 3 — PPTX reingest 측정 → cap 버그 발견 + fix

> Day 1 augment 효과 측정 sprint. 진행 중 PptxParser cap 정책 버그 발견 → root cause fix 까지 완수.

---

## 0. 한 줄 요약

W9 Day 3 — PPTX reingest 도중 **cap 정책 버그 발견** (신규 한계 #47). 성공 카운트가 아닌 *시도* 카운트로 fix 후 단위 테스트 추가. 단위 테스트 **198 → 199** ran. quota 보호 의도 회복.

---

## 1. 진입 배경

W9 Day 2 §6 추천 1순위: PPTX reingest — Day 1 augment 효과 측정 (한계 #43 회수).

기대: chunks 1 → augment 후 증가.

---

## 2. 측정 결과 + Root cause 분석

### 2.1 reingest 결과

```
chunks_count: 1 (이전과 동일, augment 효과 검증 실패)
vision_usage: 11 calls / 1 success / 10 error
stage 로그:
  extract       | succeeded
  …
  tag_summarize | failed | 429 RESOURCE_EXHAUSTED
  load·embed·doc_embed·dedup | succeeded (graceful)
```

### 2.2 Root cause 발견 (신규 한계 #47)

```python
# 이전 코드 (W9 Day 1)
vision_slides_used = 0
for slide in prs.slides:
    if vision_slides_used < _MAX_VISION_SLIDES:  # cap 검사
        ocr_text = _vision_ocr_largest_picture(...)
        if ocr_text:
            vision_slides_used += 1  # ⚠ 성공만 카운트
```

**문제**: Vision 실패 시 `vision_slides_used` 증가 X → cap 무력화 → **모든 슬라이드 (11) 호출 시도**.

**증거**: vision_usage `total_calls=11` (사용자 PPTX 11 slides 정확 일치).

### 2.3 RPD quota 영향

- W8 Day 2 reingest: 5 slides (cap 작동, 정상 cold start)
- W9 Day 3 reingest: **11회 호출** (cap 무력화) → tag_summarize 의 추가 호출 + 누적 quota 초과
- 의도된 RPD 절약 정책 (max 5) 무력화로 두 배 이상 quota 소진

---

## 3. Fix (commit 예정)

### 3.1 cap 카운트 분리

```python
# 수정 후
vision_slides_attempted = 0  # cap 기준
vision_slides_success = 0    # 가시성 (logging 만)

for slide in prs.slides:
    if vision_slides_attempted < _MAX_VISION_SLIDES:
        vision_slides_attempted += 1  # ✅ 시도 시점 증가
        ocr_text = _vision_ocr_largest_picture(...)
        if ocr_text:
            vision_slides_success += 1
```

**효과**: Vision 실패해도 5번 시도 후 cap 작동 → quota 보호 의도 회복.

### 3.2 단위 테스트 (`test_failure_respects_cap_quota_protection`)

- 11 slides 모두 picture-only + ImageParser 항상 raise
- 검증: parse_calls 정확히 **5회** (이전 버그였다면 11회)
- 추가 16/199 ran, 회귀 0

### 3.3 logging 갱신

```python
logger.info(
    "PPTX Vision OCR: attempted=%d success=%d (file=%s, cap=%d)",
    vision_slides_attempted, vision_slides_success, file_name, _MAX_VISION_SLIDES,
)
```

---

## 4. augment 효과 측정 — 별도 sprint 필요

본 sprint 에서 augment 효과 측정은 **불완전 검증**:
- Vision quota 초과로 OCR 결과 1개만 성공 → augment 결과는 사실상 W8 Day 2 와 동일
- fix 후 즉시 재 reingest 는 quota 미회복 상태 (다음 일 reset 필요)
- 정상 검증은 **다음 일 W9 Day 4+ 에 quota 회복 후 reingest** 시 가능

---

## 5. 누적 KPI (W9 Day 3 마감)

| KPI | W9 Day 2 | W9 Day 3 |
|---|---|---|
| 단위 테스트 | 198 ran | **199 ran** (+1 cap fix) |
| 한계 회수 누적 | 7 | 7 (Day 3 는 신규 한계 발견·fix) |
| **신규 한계** | — | **#47 발견 + 즉시 fix** |
| 마지막 commit | 9ebb2dc | (Day 3 commit 예정) |

---

## 6. 알려진 한계 (Day 3 신규)

| # | 한계 | 회수 시점 |
|---|---|---|
| 47 | (회수 완료) cap 성공 카운트 버그 | 본 Day 3 fix |
| 48 | augment 본 검증 미완 — quota 회복 후 재 reingest 필요 | W9 Day 4+ |
| 49 | Vision 응답 RESOURCE_EXHAUSTED 시 fast-fail (이후 슬라이드 즉시 skip) | optional 보강 (현재는 cap 으로 충분) |

---

## 7. 다음 작업 — W9 Day 4 후보

| 우선 | 항목 | 사유 |
|---|---|---|
| 1 | **PPTX reingest 재시도** (한계 #48) | quota 회복 후 augment 효과 측정 |
| 2 | **Vision 429 fast-fail 보강** (한계 #49) | quota 초과 감지 시 즉시 skip — 추가 보호 |
| 3 | **VisionUsageCard 한계 #38 보강** | API quota header 직접 파싱 |
| 4 | **mobile 가독성 (한계 #40)** | 사용자 피드백 후 |
| 5 | **CI 첫 실행 결과 확인** (한계 #44) | 사용자 GitHub Actions 페이지 확인 가이드 |

**추천: PPTX reingest 재시도 (~10분, quota 회복 시점에)** — Day 3 fix 의 본 검증 완수.

---

## 8. 한 문장 요약

W9 Day 3 — PPTX reingest 시 cap 버그 발견 (vision_slides_used 가 성공만 카운트해서 11회 호출됨). 시도 카운트 기반으로 fix + 단위 테스트 1건 추가. 단위 테스트 199 ran, 회귀 0. augment 본 검증은 quota 회복 후 W9 Day 4+ 로 이월.
