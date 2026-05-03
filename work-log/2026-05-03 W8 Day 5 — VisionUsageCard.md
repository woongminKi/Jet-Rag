# 2026-05-03 W8 Day 5 — frontend VisionUsageCard (한계 #37 회수)

> Day 4 백엔드 ship (vision_metrics + /stats vision_usage) 의 사용자 가치 회수.
> W7 Day 4 ChunksStatsCard 패턴 재사용 — 차트 의존성 0.

---

## 0. 한 줄 요약

W8 Day 5 — VisionUsageCard ship (`f7d318a`). Day 4 백엔드 vision_usage 가 사용자 면에서 즉시 보임. RPD 20 cap 기준 progress + 75%/100% 색상 분기. 차트 라이브러리 의존성 0.

---

## 1. 진입 배경

W8 Day 4 §7 추천 작업. Day 4 백엔드 카운터가 `/stats` 응답에 노출됐지만 frontend 미반영 → 사용자 가시성 0. 한계 #37 회수.

---

## 2. 비판적 재검토 (사용자 §1)

### 2.1 시각화 방법

| 옵션 | 설명 | 결정 |
|---|---|---|
| 차트 라이브러리 (recharts) | 의존성 추가 | ❌ over-engineering, W7 Day 4 ChunksStatsCard 패턴과 일관성↓ |
| **Tailwind progress bar** | div + style.width | ✅ 의존성 0, ChunksStatsCard 와 일관 |
| SVG 게이지 | inline SVG | ⚠ progress 와 동등한 정보 — Tailwind 가 simple |

**결정 — Tailwind progress bar**. RPD 20 대비 비율 + 색상 분기 (정상/경고/초과).

### 2.2 RPD 20 의 의미

Gemini 2.5 Flash 무료 티어의 일일 요청 수. 본 카운터는 *프로세스 시작 후 누적* — 매번 재시작 시 0 으로 초기화 (search_metrics 동일 정책).

→ "오늘 일일 RPD" 가 아닌 "이번 세션 호출 수" 를 RPD 20 기준으로 비교 표기. **명시 필요** ("프로세스 시작 후 누적" 라벨 추가).

---

## 3. 구현

### 3.1 변경 파일

| 파일 | 변경 |
|---|---|
| `web/src/lib/api/types.ts` | `VisionUsageStats` 인터페이스 + `Stats.vision_usage` 필드 |
| `web/src/components/jet-rag/cards/vision-usage-card.tsx` | **신규** — progress + row list (성공·실패·마지막 호출) |
| `web/src/components/jet-rag/home-grid.tsx` | ChunksStatsCard 다음에 카드 노출 |

### 3.2 디자인

**progress bar 색상 분기**:
- `< 75%`: primary (정상)
- `75 ≤ x < 100%`: orange-500 (경고)
- `≥ 100%`: destructive (cap 초과 + "429 가능성↑" 안내 문구)

**rows**:
- 성공 (success_calls)
- 실패 4xx/5xx (error_calls)
- 마지막 호출 — 한국어 상대 시간 ("방금 전" / "5분 전" / "3시간 전" / 그 이상은 MM/DD HH:mm)

### 3.3 검증

```bash
pnpm exec tsc --noEmit  # 0 error
pnpm lint               # 0 error
uv run python -m unittest discover tests  # 194 ran (backend 회귀 0)
```

---

## 4. 누적 KPI (W8 Day 5 마감)

| KPI | W8 Day 4 | W8 Day 5 |
|---|---|---|
| 단위 테스트 | 194 ran | 194 ran (frontend 변경) |
| 홈 카드 수 | 6 (+ ChunksStats) | **7** (+ VisionUsage) |
| 한계 회수 | 4건 (#15·#23·#26·#29) | **5건** (+ #37) |
| 차트 라이브러리 의존성 | 0 | **0** (Tailwind progress only) |
| 마지막 commit | 08295c7 | **f7d318a** |

---

## 5. W8 누적 commit (5 day)

| Day | commit | 본질 |
|---|---|---|
| Day 1 | `33cf821` | DE-68 PPTX ship + input_gate fix |
| Day 1 | `d0fd5a9` | doc_embed/dedup/tag_summarize e2e |
| Day 1 doc | `5704f9a` | Day 1 work-log |
| Day 2 | `9fafb61` | PPTX Vision OCR rerouting (한계 #23) |
| Day 3 | `4e42101` | Tier 3 e2e + debug mobile (한계 #15·#26) |
| Day 3 doc | `c48f3a9` | Day 3 work-log |
| Day 4 | `af99e7e` | vision_metrics + /stats (한계 #29) |
| Day 4 doc | `08295c7` | Day 4 work-log |
| **Day 5** | **`f7d318a`** | **VisionUsageCard frontend (한계 #37)** |

---

## 6. 알려진 한계 (Day 5 신규)

| # | 한계 | 회수 시점 |
|---|---|---|
| 38 | RPD 20 cap 은 안내값 — 실 Gemini quota 와 동기 X | API 응답 quota header 직접 파싱 (W9+) |
| 39 | 새벽 0시 (Pacific/UTC) 자동 reset 미반영 | 일일 시간대 인지 카운터 도입 검토 |
| 40 | 카드 mobile 가독성 — RPD 20 / 사용량 row 길어질 수 있음 | 사용자 피드백 후 폰트 조정 |

---

## 7. 다음 작업 — W8 Day 6 후보

| 우선 | 항목 | 사유 |
|---|---|---|
| 1 | **PPTX 텍스트 + Vision 혼합 슬라이드 (한계 #28)** | Vision OCR 보강 — 텍스트가 *부족* 한 슬라이드도 image OCR 결합 |
| 2 | **monitor_search_slo CI 자동화** | GitHub Actions cron yaml |
| 3 | **dedup 후보 0건 케이스 e2e (한계 #32)** | sprint 30분, 패턴 재사용 |
| 4 | **golden v0.3 placeholder 활성** | DOCX 자료 추가 누적 후 |
| 5 | **Ragas 평가 도구 통합** | 사용자 의존성 승인 필요 |

**추천: dedup 후보 0건 e2e (~30분) + monitor_search_slo CI yaml (~1h)** — 회귀 보호 + 운영 인프라.

---

## 8. 한 문장 요약

W8 Day 5 — VisionUsageCard ship (`f7d318a`). RPD 20 progress bar + 색상 분기 + 한국어 상대 시간. 차트 의존성 0. 한계 #37 회수, W8 5건 한계 회수 누적.
