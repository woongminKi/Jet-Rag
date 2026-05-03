# 2026-05-03 W13 Day 2 — 하이브리드 +5pp ablation 인프라 (mode 파라미터)

> 기획서 §13.1 KPI "하이브리드 +5pp 우세" 측정 가능 인프라.
> Day 1 §7 추천 1순위 — 응용 layer 후처리로 마이그레이션 회피.

---

## 0. 한 줄 요약

W13 Day 2 — `/search?mode=hybrid|dense|sparse` Query param ship. ablation 측정 인프라 — 같은 query 로 3 모드 호출 후 비교 가능. 단위 테스트 **232 → 236** ran (+4), 회귀 0.

---

## 1. 비판적 재검토

| 옵션 | 설계 | 결정 |
|---|---|---|
| A | RPC 자체 분기 (search_hybrid_dense_only / search_sparse_only) | ❌ 마이그레이션 필요 |
| **B** | 응용 layer 후처리 (RPC 결과에서 dense_rank/sparse_rank 필터) | ✅ 채택 |
| C | 별도 ablation script (라이브 backend) | ⚠ CI 불가 + 사용자 환경 의존 |

→ **B 채택**. RPC 결과에 이미 `dense_rank` / `sparse_rank` 컬럼 존재 → 응용 layer 필터만으로 mode 분기 가능.

### 1.1 mode 별 동작

| mode | RPC 호출 | 응용 layer 필터 | 효과 |
|---|---|---|---|
| `hybrid` (default) | dense+sparse RRF | (필터 없음) | 기존 동작 |
| `dense` | 동일 RPC | `dense_rank IS NOT NULL` 만 | sparse-only 매칭 row 제외 |
| `sparse` | 동일 RPC (또는 sparse fallback) | `sparse_rank IS NOT NULL` 만 | dense-only 매칭 row 제외 |

→ 같은 query 로 3 호출 후 top-K precision 비교 → "하이브리드 +5pp 우세" 측정 가능.

---

## 2. 구현

### 2.1 변경 파일

| 파일 | 변경 |
|---|---|
| `api/app/routers/search.py` | `mode` Query param + 화이트리스트 검증 + 응용 layer 필터 |
| `api/tests/test_search_doc_id_filter.py` | `SearchModeAblationTest` 4 시나리오 |
| 5 기존 테스트 파일 | `mode="hybrid"` 명시 추가 (FastAPI Query default 회피 패턴) |

### 2.2 핵심 로직

```python
# Query param + 검증
mode: str = Query(default="hybrid", description="hybrid|dense|sparse")

if mode not in ("hybrid", "dense", "sparse"):
    raise HTTPException(400, ...)

# 응용 layer 필터 (rpc_rows + doc_id 필터 직후)
if mode == "dense":
    rpc_rows = [r for r in rpc_rows if r.get("dense_rank") is not None]
elif mode == "sparse":
    rpc_rows = [r for r in rpc_rows if r.get("sparse_rank") is not None]
# hybrid: 필터 없음 (기존 동작)

# dense_hits / sparse_hits / fused 카운트 자동 갱신 (필터된 rows 기준)
```

### 2.3 단위 테스트 (4 신규)

| 시나리오 | 검증 |
|---|---|
| `test_mode_hybrid_keeps_all_rows` | 3 rows (dense+sparse / dense-only / sparse-only) 모두 통과 |
| `test_mode_dense_filters_sparse_only_rows` | dense_rank 있는 2 rows 만 (sparse-only 제외) |
| `test_mode_sparse_filters_dense_only_rows` | sparse_rank 있는 2 rows 만 (dense-only 제외) |
| `test_invalid_mode_rejected` | mode="bogus" → 400 |

### 2.4 호환성 갱신

5 기존 search 테스트 파일 — FastAPI Query default 회피 패턴 일관 (모든 optional 명시):
- test_bgem3_singleton.py
- test_pgroonga_migration.py
- test_search_503_retry_after.py
- test_search_user_isolation.py
- test_search_doc_id_filter.py (W11 Day 4 신규)

→ `mode="hybrid"` 명시 추가.

---

## 3. KPI 11개 매트릭스 갱신

| KPI | 결과 | 상태 |
|---|---|:---:|
| **하이브리드 +5pp 우세** | ablation 인프라 ship — 골든 셋에 3 모드 호출 후 측정 가능 | 🆕 측정 가능 |
| (그 외 10개) | (기존) | (변동 없음) |

**충족 2 + 부분 1 + 측정 가능 2 + 미측정 6** (이전 미측정 7 → 6).

---

## 4. 검증

```bash
uv run python -m unittest discover tests
# Ran 236 tests in 4.082s — OK (232 → 236, 회귀 0)
```

라이브 ablation 측정은 사용자 환경에서:
```bash
for q in "GPU" "계약" "삼국시대"; do
  for mode in hybrid dense sparse; do
    curl -s "http://localhost:8000/search?q=$(urlencode $q)&mode=$mode&limit=10" \
      | jq "{mode: \"$mode\", q: \"$q\", total: .total, top_3: [.items[:3] | .[] .doc_title]}"
  done
done
```

---

## 5. 누적 KPI (W13 Day 2 마감)

| KPI | W13 Day 1 | W13 Day 2 |
|---|---|---|
| 단위 테스트 | 232 | **236** (+4) |
| KPI 측정 가능 | 3.5 | **4.5** (+ 하이브리드 +5pp 우세) |
| 한계 회수 누적 | 20 | 20 |
| 마지막 commit | 84233d0 | (Day 2 commit 예정) |

---

## 6. 알려진 한계 (Day 2 신규)

| # | 한계 | 회수 시점 |
|---|---|---|
| 74 | dense/sparse mode 도 RPC 호출 비용은 동일 (응용 layer 필터만) — 진정한 ablation 위해서는 RPC 분리 필요 | 마이그레이션 도입 시 |
| 75 | top-K cap 안에서 필터되어 mode=dense 시 부족할 수 있음 (특히 sparse 매칭 우세 query) | RPC 인자 추가 (W14+) |

---

## 7. 다음 작업 — W13 Day 3 (자동 진입)

| 우선 | 항목 | 사유 |
|---|---|---|
| 1 | **OpenAI 어댑터 스왑 시연** | DoD ④ (~3h) |
| 2 | **augment 본 검증** (한계 #48) | quota 회복 |
| 3 | **monitor CI yaml + 가이드** | 운영 |
| 4 | **frontend ablation 시각화** | 개발자 가시성 |
| 5 | **doc 스코프 fallback UX** (한계 #68) | 사용자 피드백 |

**Day 3 자동 진입 대상**: monitor CI yaml — 운영 인프라 + ~30분 작은 sprint (토큰 효율).

---

## 8. 한 문장 요약

W13 Day 2 — `/search?mode=hybrid|dense|sparse` ablation 인프라 ship + 5 기존 테스트 호환 갱신. 단위 테스트 232 → 236 ran 회귀 0. KPI 측정 가능 3.5 → 4.5.
