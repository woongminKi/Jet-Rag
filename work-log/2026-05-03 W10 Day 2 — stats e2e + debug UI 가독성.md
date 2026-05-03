# 2026-05-03 W10 Day 2 — stats router e2e + debug UI 가독성

> Day 1 의 .count 자산 회수 + 사용자 가시성 한계 #16 회수.

---

## 0. 한 줄 요약

W10 Day 2 — stats router e2e (한계 — chunks 분포 정확도 검증) + ChunkDebugPanel font 11px 가독성 개선 (한계 #16) ship. JSONB path (`flags->>filtered_reason`) 시뮬 추가로 FakeSupabaseClient 인프라 강화. 단위 테스트 **212 → 213** ran, 회귀 0.

---

## 1. F1 — stats router e2e

### 1.1 비판적 재검토 시점 발견 — JSONB path 미시뮬

stats 라우터의 `_compute_chunks_stats` 가 `not_.is_("flags->>filtered_reason", "null")` JSONB path query 사용 → FakeTableQuery 의 `_matches_filters` 가 컬럼 dict get 만 시도 → 모든 row 가 "null" 처리되어 effective 부정확.

**fix**: `_resolve_column` 헬퍼 추가 — `"flags->>key"` → `row["flags"][key]` 해석.

```python
@staticmethod
def _resolve_column(row, col):
    if "->>" in col:
        top, key = col.split("->>", 1)
        container = row.get(top)
        if isinstance(container, dict):
            return container.get(key)
        return None
    return row.get(col)
```

이제 모든 op (eq / neq / is / not_is / in) 가 JSONB path 자동 해석.

### 1.2 시나리오 — `StatsRouterE2ETest`

documents 3건 (1 failed) + chunks 5건 (3 effective + 1 table_noise + 1 extreme_short) + ingest_jobs 3건 seed → `stats()` 직접 호출 → 응답 검증:

| 응답 필드 | 기대값 | 검증 |
|---|---|---|
| documents.total | 2 (failed 분리) | ✅ |
| documents.failed_count | 1 | ✅ |
| documents.by_doc_type | {pdf: 1, docx: 1} | ✅ |
| documents.total_size_bytes | 3072 | ✅ |
| chunks.total | 5 | ✅ |
| chunks.effective | 3 | ✅ (count 자산 활용) |
| chunks.filtered_breakdown | {table_noise:1, extreme_short:1} | ✅ |
| chunks.filtered_ratio | 0.4 | ✅ |
| jobs.by_status | {completed:2, failed:1} | ✅ |
| search_slo.sample_count | 0 (reset) | ✅ |
| vision_usage.total_calls | 0 (reset) | ✅ |

setUp 에서 `search_metrics._ring.clear()` + `vision_metrics.reset()` — 이전 테스트 영향 차단.

---

## 2. F2 — debug UI 가독성 (한계 #16)

### 2.1 변경

`web/src/components/jet-rag/result-card.tsx` `ChunkDebugPanel`:

| 속성 | 이전 | 이후 |
|---|---|---|
| text size | `text-[10px]` | `text-[11px]` |
| 본문 색 | `text-muted-foreground` | `text-foreground/85` (contrast↑) |
| key 색 | `text-foreground/70` | `text-foreground/95` + `font-semibold` |
| 배경 | `bg-background/40` | `bg-background/60` (구분↑) |
| padding | `px-2 py-1.5` | `px-2.5 py-2` |
| metadata 헤더 | 일반 텍스트 | `font-semibold uppercase tracking-wide text-[10px]` |
| line-height | 기본 | `leading-relaxed` |

→ 동일 컴팩트 디자인 유지 + 가독성 명확 개선.

### 2.2 비판적 재검토

| 옵션 | 결정 |
|---|---|
| text-xs (12px) | ❌ 너무 큼 — 컴팩트 손실 |
| **text-[11px]** | ✅ 채택 — 1px 증가로 가독성↑ |
| 패널 색 변경 (theme) | ❌ 디자인 일관성 손상 |
| key/value 폰트 분리 | ⚠ font-mono 일관성 유지하되 weight 차별화 |

### 2.3 검증

- tsc 0 error
- lint 0 error
- 기존 ResultCard 회귀 0 (단순 className 변경)

---

## 3. 누적 KPI (W10 Day 2 마감)

| KPI | W10 Day 1 | W10 Day 2 |
|---|---|---|
| 단위 테스트 | 212 ran | **213 ran** (+1) |
| 한계 회수 누적 | 13 | **14** (+ #16) |
| FakeSupabaseClient | count + 기본 op | **+ JSONB path 시뮬** |
| 마지막 commit | 88d5ffc | (Day 2 commit 예정) |

---

## 4. 알려진 한계 (Day 2 신규)

| # | 한계 | 회수 시점 |
|---|---|---|
| 60 | JSONB path 한 단계 (`a->>b`) 만 지원 — 이중 (`a->>b->>c`) 미지원 | 사용 시점에 확장 |
| 61 | search_metrics._ring.clear() 직접 호출 — 외부 reset API 부재 | search_metrics.reset() 추가 검토 |

---

## 5. 다음 작업 — W10 Day 3 후보

| 우선 | 항목 | 사유 |
|---|---|---|
| 1 | **augment 본 검증** (한계 #48) | quota 회복 시점 |
| 2 | **VisionUsageCard 한계 #38** | API quota header 직접 파싱 |
| 3 | **monitor_search_slo CI 보강** | GitHub Actions cron yaml |
| 4 | **search_metrics.reset() API** | 한계 #61 |
| 5 | **debug UI nested metadata yaml** | 한계 #17 |

**추천: search_metrics.reset() + nested metadata yaml** — 작은 sprint 묶음 (~30분).

---

## 6. 한 문장 요약

W10 Day 2 — stats router e2e ship + JSONB path 시뮬 추가 + debug UI 11px 가독성 개선. 단위 테스트 212 → 213 ran 회귀 0. 한계 #16 회수.
