# 2026-05-03 W13 Day 3 — SearchSloCard frontend

> Day 2 ablation 인프라 후 사용자 가시성 보강. VisionUsageCard / ChunksStatsCard 패턴 재사용.

---

## 0. 한 줄 요약

W13 Day 3 — 홈 SearchSloCard ship — `/stats.search_slo` p50/p95/sample/cache_hit_rate + fallback breakdown 노출. SLO 자체 목표 (≤500ms) / 절대 (≤3000ms) 대비 색상 분기. tsc·lint 0 error, backend 회귀 0.

---

## 1. 비판적 재검토

### 1.1 후보 비교

| 옵션 | 비용 | 가치 |
|---|---|---|
| **SearchSloCard (frontend)** | ~30분 | 사용자 가시성↑, 패턴 재사용 |
| monitor CI yaml | ~30분 | 사용자 환경 의존 (Actions secrets) |
| OpenAI 어댑터 스왑 (DoD ④) | ~3h | 토큰 부담 |

→ **Frontend 카드 채택** — Day 1·2 ship한 인제스트 SLO + ablation 인프라 위에 사용자 가시성 마무리.

### 1.2 SLO 색상 분기

기획서 §13.1 P95 검색 응답 ≤3초 (절대) / 자체 목표 500ms:

| p95 범위 | 색상 |
|---|---|
| ≤ 500ms | foreground (정상) |
| 500 ~ 3000ms | orange-500 (경고 — 자체 목표 초과) |
| > 3000ms | destructive (절대 목표 초과) |
| 측정 0건 | muted-foreground |

---

## 2. 구현

### 2.1 변경 파일

| 파일 | 변경 |
|---|---|
| `web/src/components/jet-rag/cards/search-slo-card.tsx` | **신규** — VisionUsageCard 패턴 재사용 |
| `web/src/components/jet-rag/home-grid.tsx` | ChunksStatsCard ↔ VisionUsageCard 사이에 SearchSloCard 노출 |
| `web/src/lib/api/types.ts` | `SearchSloStats` 에 `cache_hit_count` / `cache_hit_rate` 추가 (W4-Q-3 backend 미반영분 회수) |

### 2.2 SearchSloCard 구조

```tsx
<Card>
  <CardHeader>검색 응답 SLO · "최근 500건 (in-memory)"</CardHeader>
  <CardContent>
    {sample_count === 0 ? "측정 데이터 없음 안내" : (
      <>
        {/* 4 metric grid */}
        <Metric label="p50" value="..." />
        <Metric label="p95" value="..." tone="custom" customClass={p95Class} />
        <Metric label="샘플" value="N건" tone="muted" />
        <Metric label="cache hit" value="N%" />

        {/* fallback 발생 시 노출 */}
        {transient/permanent > 0 && <ul>...</ul>}

        <div>자체 목표 ≤500ms · 절대 ≤3000ms</div>
      </>
    )}
  </CardContent>
</Card>
```

### 2.3 types.ts 갱신 — backend 미반영분 회수

W4-Q-3 backend 의 `cache_hit_count` / `cache_hit_rate` 필드가 frontend 타입에 누락되어 있었음 (W7 Day 2 추가 후 frontend 미반영). Day 3 sprint 진입 중 발견 → 함께 보강.

---

## 3. 검증

```bash
cd web && pnpm exec tsc --noEmit && pnpm lint  # 0 error
cd ../api && uv run python -m unittest discover tests  # 236 ran, 회귀 0
```

라이브 smoke (사용자 환경):
- 홈 페이지 우측 사이드바: PopularTags → MyDocStats → ChunksStats → **SearchSlo (신규)** → VisionUsage → SearchTips
- 검색 0건 시: "측정 데이터가 없습니다" 안내
- 검색 N건 후: p50/p95 + cache hit % + fallback breakdown

---

## 4. 누적 KPI (W13 Day 3 마감)

| KPI | W13 Day 2 | W13 Day 3 |
|---|---|---|
| 단위 테스트 | 236 | 236 (frontend 변경) |
| 한계 회수 | 20 | 20 |
| 홈 카드 수 | 7 (W12 까지) | **8** (+ SearchSlo) |
| 마지막 commit | 0d3c8cc | (Day 3 commit 예정) |

---

## 5. 알려진 한계 (Day 3 신규)

| # | 한계 | 회수 시점 |
|---|---|---|
| 76 | search_slo 도 프로세스 재시작 시 휘발 (vision_usage 동일 — 한계 #34) | DB 영속화 W14+ |
| 77 | mode=dense/sparse ablation 별 SLO 분리 측정 미도입 | W14+ — sample 가 mode 별 dict 분리 필요 |

---

## 6. 다음 작업 — W13 Day 4 (자동 진입)

| 우선 | 항목 | 사유 |
|---|---|---|
| 1 | **monitor CI yaml + 가이드** | 운영 인프라 (사용자 enable) |
| 2 | **OpenAI 어댑터 스왑 시연** | DoD ④ |
| 3 | **augment 본 검증** | quota 회복 |
| 4 | **mode=dense/sparse 각 SLO 분리** (한계 #77) | ablation 정확도↑ |

**Day 4 자동 진입**: monitor CI yaml — 운영 인프라 마무리 (~30분).

---

## 7. 한 문장 요약

W13 Day 3 — 홈 SearchSloCard ship + types.ts cache_hit_count/cache_hit_rate 회수. tsc·lint 0 error, 회귀 0. 홈 카드 7 → 8.
