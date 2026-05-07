# 2026-05-07 E1 1차 ship 실 reingest 검증

> 짝 문서: `2026-05-07 E1 인제스트 ETA latency sprint plan.md` (§11 1차 ship 후 §12 검증 단계)
> commit: `9259241` (E1 1차 ship) → 본 검증 시점에 origin/main push 완료
> PDF: `포트폴리오_이한주 - na Lu.pdf` (15p, 33MB) — 1차 측정과 동일

---

## 0. 한 줄 요약

> **E1 1차 ship sub-stage 분해 효과 부분 확인** — T+60s ETA 213→413s (+94%), 그러나 **DoD ratio 0.7~1.3 미달**. 핵심 한계: vision_usage_log baseline 부재 시 fallback 30000ms/page 가 실측 ~75000ms/page 의 절반. 첫 reingest 는 학습 단계, 다음 reingest 부터 정확도 회복 가설은 **재측정 필요**.

---

## 1. 검증 절차

| 단계 | 내용 |
|---|---|
| 1 | api 서버 uvicorn `--reload` 모드 → eta.py 자동 reload 확인 |
| 2 | 기존 측정 doc (`fa24fabf...`) cascade 삭제 + storage object 삭제 + vlog 17 row DELETE |
| 3 | 동일 PDF (33MB, 15p) 재업로드 (POST /documents, source_channel=drag-drop) |
| 4 | `/tmp/e1_measure.py` 5초 폴링 — T+0/60/180/300/600/900/1080 ETA + stage 캡처 |
| 5 | 종료 후 `/tmp/e1_diag_sql.py` S1~S4 진단 |

---

## 2. 결과 비교 (1차 vs 2차)

### 2.1 timeline ETA + 실측

| 시점 | 1차 (E1 미적용) | 2차 (E1 적용) | Δ |
|---|---:|---:|---|
| 실측 | 957.9s | **1130.8s** | +173s (503 wave 더 강함, 우연적 변동) |
| extract 점유 | 96.4% | **97.3%** | 일관 |
| **T+0 ETA** | 224.5s | **224.5s** | 0 — queued 상태는 stage_progress=None 이라 sub-stage 비활성 |
| **T+60s ETA** | 213.1s | **413.5s** | **+200.5s ⬆ sub-stage 분해 활성** |
| T+180s ETA | 99.1s | 125.5s | +26.4s |
| T+300s ETA | 87.7s | 125.5s | +37.8s |
| T+600s ETA | 87.7s (stuck) | 125.5s (stuck) | 양쪽 모두 page 14 stuck |
| T+900s ETA | (extract 끝) | 89.5s @ 14p | 2차 sweep 더 길게 |
| T+1080s | (이전 T+905s) | tag_summarize 진입 | extract 175s 더 걸림 |

### 2.2 ratio (정확도)

| 측정 기준 | 1차 | 2차 | Δ |
|---|---:|---:|---|
| **T+0 ETA / 실측** | 0.234 | **0.198** | -0.036 (실측이 길어진 영향) |
| **T+60s ETA / 남은 실측** | 0.237 | **0.386** | **+64%** 개선 ⬆ |
| (T+60s + elapsed) / 실측 (체감) | 0.285 | **0.420** | +47% |
| **DoD 진입 (0.7~1.3)** | ✗ | ✗ | **미달** |

### 2.3 503 분포 (S3)

| | 1차 | 2차 |
|---|---:|---:|
| retry 0 | 1 | 2 |
| retry 1 | 12 | 13 |
| retry 2 | 1 | 1 |
| retry 3 | 3 (이 중 2건 fail) | 0 |
| **vlog total** | 17 | 16 |
| **fail count** | 2 (page 12, 14) | 0 |

→ 2차 reingest 는 503 wave 약해서 retry 3 fail 0. 그러나 page 14 가 sweep 1 단계 retry 1~2 에서 4분+ stuck → 실측 1130s 의 핵심 원인.

---

## 3. 효과 분석

### 3.1 작동한 것 ✓

1. **sub-stage 분해 활성** — running + extract + unit='pages' 진입 시 ETA 가 213 → 413s 로 약 2배 상향. plan §11.3 의 의도대로 작동.
2. **vlog 미가용 fallback** — vlog 빈 상태에서 fallback 30000ms/page × 1.2 적용 확인. plan §11.5 의 보호 케이스 통과.
3. **503 wave 시 sweep retry latency 부분 반영** — sweep buffer 1.2 가 절반 흡수. ratio 0.24 → 0.39.

### 3.2 작동 안 한 것 ✗

1. **DoD ratio 0.7~1.3 미진입** — 최선 케이스 0.42. 0.7 까지 +0.28 부족.
2. **T+0 (queued) ETA 변화 없음** — 사용자 보고 "3분 표시" 가 그대로. queued 시점에는 PDF 페이지 수 미상이라 sub-stage 분해 비활성. **사용자 체감 첫 인상 불변**.
3. **page stuck 시 ETA 정지 그대로** — page 14 503 retry 4분+ 동안 ETA 125.5s 고정. plan §10.5 인사이트 #5 (sweep 카운터 노출) 미진입.
4. **vision_p95 fallback 30000 vs 실측 75000** — vlog 비어 있을 때 fallback 너무 낮음. 실측 1130s/15p = 75333ms/page 인데 fallback 의 절반.

---

## 4. 근본 한계 + 가설

### 4.1 vision_usage_log 학습 가설

senior-developer 설계: vlog 의 latency p95 가 실 sample 누적 시 정확. 본 검증은 vlog 0 → fallback 30000 사용 → 절반 underestimate.

**가설**: 본 reingest 의 vlog 16 row 가 누적되어 **3차 reingest 시점에는 vision_p95 = 본 측정의 latency p95 (~80~100s)** 사용 → ratio 0.7~1.3 진입 가능.

**가설 검증 방법**: vlog 데이터 보존 (DELETE X) + 3차 reingest 측정 1회 추가. 이번 검증의 진단 데이터로는 vision_p95 가 어떻게 계산될지 직접 확인 가능.

### 4.2 503 wave 변동성

본 PDF (page 12, 14) 가 vision API 호출 시 일관되게 503 임 (1차·2차 모두). 이는 PDF 자체의 vision-heavy 페이지 특성 + Gemini 무료 티어 한도 영향. retry 3+sweep 3 누적 latency 가 ETA 공식의 sweep_buffer 1.2 만으로는 못 잡음.

**대안**: sweep_buffer 1.2 → 1.5 또는 1.8 상향. 단 정상 PDF over-estimate 더 심해짐. trade-off.

### 4.3 queued 상태 ETA

업로드 직후 stage_progress=None 이라 fallback STAGE_ORDER 합산. extract 의 _FALLBACK_STAGE_MS = 120000ms 이지만 sample 충분 (n=16+) 시 medians (244756) 사용 → 224466 = ~244756 + 다른 stages 합. 즉 queued 시점 ETA 는 medians 기반.

**대안**: 업로드 시 PDF 페이지 수 미리 추출 (PyMuPDF open) → queued 시점부터 sub-stage 분해 활성. 단 업로드 시점 latency +~500ms 추가.

---

## 5. DoD 평가

| 기준 (plan §5.1) | 결과 |
|---|---|
| DoD 0.7~1.3 (50p PDF 2회 추가 reingest 평균) | ✗ 0.42 (T+60s 기준) — 미달 |
| 단위 테스트 시뮬 ratio | ✓ 0.99 (vision p95=50000 가정 시) |
| **현실 vlog 학습 후 재측정** | ⏳ 미진행 (3차 reingest 필요) |

**판정**: E1 1차 ship 의 **단위 테스트 DoD 통과, 실 측정 DoD 미달**.

---

## 6. 다음 결정 (사용자 확인 필요)

| # | 옵션 | 작업량 | 권고 근거 |
|---|---|---|---|
| **a** | **3차 reingest 측정** (vlog 누적 후, 가설 4.1 검증) | 20분 | 가장 빠른 검증, sub-stage 가설 진위 확정 |
| b | **sweep_buffer 1.2 → 1.5~1.8 상향 + fallback 30000 → 75000** (E1-A1 보강) | 0.5일 | 실측 baseline 반영. 단위 테스트 갱신 + 정상 PDF over-estimate 영향 검증 필요 |
| c | **E1 2차 ship 진입** (E1-A3 vision_page_cache + E1-A2 페이지 동시 호출) | 2~3일 | latency 자체 줄이면 ratio 자연 향상 + S0 D2 마이그 015 본진입. master plan 정합 |
| d | **E1 deferred + S0 미완 마무리** (D3 budget·D4 cap·D5 24h cap) | 1~3일 | master plan 정합 우선. ETA 정확도는 실측 데이터 누적 후 자연 회복 |

**권고**: **(a) 3차 reingest 측정 → (c) 2차 ship 진입**. (a) 가 20분이면 가설 4.1 진위 확정 가능. (a) 결과에 따라 (b) 또는 (c) 분기.

---

## 7. 활성 한계 (다음 진입 시 점검 필수)

| # | 한계 | 회복 절차 |
|---|---|---|
| 1 | DoD ratio 0.7~1.3 미달 — 실측 0.42 | 가설 4.1 검증 (3차 reingest) 또는 (b)/(c) 진입 |
| 2 | T+0 (queued) ETA 변화 없음 | queued 단계에서 PDF 페이지 수 미리 추출 (별도 구현) |
| 3 | page stuck 시 ETA 정지 | sweep 카운터 노출 (plan §10.5 #5) 또는 SSE (E1-A7) |
| 4 | 정상 PDF over-estimate 가능 (시뮬 ratio 2.4) | 실 측정 (정상 vision-heavy 아닌 PDF) 별도 필요 |

---

## 8. 한 문장 요약

> E1 1차 ship 의 sub-stage 분해는 작동 (T+60s ETA +94% 상향) 하지만 DoD 미달 (0.42). vlog 학습 후 ratio 회복 가설 검증 위해 **3차 reingest 1회 측정 권고**, 그 결과로 sweep_buffer 상향 / 2차 ship 진입 / S0 마무리 분기.
