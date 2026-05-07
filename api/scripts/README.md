# api/scripts — 운영·진단·백필 스크립트

운영 환경에서 직접 호출하는 ad-hoc 스크립트 모음. 모두 stdlib + 기존 의존성 (`uv run`) 으로 동작 — 외부 의존성 추가 0.

| 스크립트 | 용도 | 도입 |
|---|---|---|
| `golden_batch_smoke.py` | 라이브 검색 회귀 smoke (top-1/top-3 hit · ablation 비교 · CI gate) | W4 Day 5, W21 Day 1 강화 |
| `monitor_search_slo.py` | `/stats.search_slo` snapshot 기록 (work-log 추적용) | W7 Day 2 |
| `diagnose_chunk_quality.py` | DE-62 청크 품질 진단 markdown 리포트 (DB 변경 0) | W3 Day 2 |
| `dryrun_chunk_repolicy.py` | W4-Q-14 새 chunk 정책 dry-run (DB 변경 0) | W4 |
| `backfill_chunk_flags.py` | chunks.flags.filtered_reason 일괄 백필 마킹 | W3 v0.5 §3.G(3) |
| `backfill_extract_skipped.py` | `flags.extract_skipped=true` doc 일괄 reingest | W2 Day 7 |
| `compute_budget.py` | vision_usage_log 기반 doc/일 budget 초기값 산정 (S0 D4 cap 의존성) | S0 D3 (2026-05-07) |

## golden_batch_smoke.py

W3 Day 5 baseline (5/5 top-1) 회귀 측정 + W14+ ablation 비교 (KPI '하이브리드 +5pp 우세').

### 기본 사용

```bash
# 사전 조건: uvicorn 가동 (default http://localhost:8000)
cd api

# 1. mode=hybrid 단일 실행 (default)
uv run python scripts/golden_batch_smoke.py

# 2. ablation 비교 — 3 mode 순차 실행 + markdown 출력
uv run python scripts/golden_batch_smoke.py --mode all -o ../work-log/golden-ablation.md

# 3. CI gate — top-1 hit 율 < 70% 시 exit 1
uv run python scripts/golden_batch_smoke.py --require-top1-min 0.7
```

### 옵션

| 옵션 | default | 효과 |
|---|---|---|
| `--mode {hybrid,dense,sparse,all}` | `hybrid` | 검색 mode. `all` 시 3 mode ablation |
| `--require-top1-min 0.0~1.0` | (미설정) | top-1 hit 비율 임계. 미달 시 exit 1 |
| `--output / -o PATH` | (stdout) | markdown 출력 경로 |

### CI 통합 예시

`.github/workflows/golden-smoke.yml` (예시 — 사용자 결정 후 추가):

```yaml
- name: Golden batch smoke
  run: |
    cd api
    uv run python scripts/golden_batch_smoke.py \
      --mode all \
      --require-top1-min 0.85 \
      --output golden-result.md
- uses: actions/upload-artifact@v4
  with:
    name: golden-result
    path: api/golden-result.md
```

## monitor_search_slo.py

`/stats.search_slo` 의 in-memory ring buffer snapshot 을 work-log 에 기록. 운영자 수동 호출 + W14 monitor-search-slo CI workflow (자동) 에서 사용.

```bash
cd api && uv run python scripts/monitor_search_slo.py
```

## diagnose_chunk_quality.py

DE-62 청크 품질 진단 — DB 변경 0. markdown 리포트 출력. chunk_filter 룰 결정 시 참고용.

```bash
cd api && uv run python scripts/diagnose_chunk_quality.py
```

## dryrun_chunk_repolicy.py

W4-Q-14 새 chunk 정책 dry-run — 청크 수/평균 길이/section_title 채움 비율 시뮬레이션.

```bash
cd api && uv run python scripts/dryrun_chunk_repolicy.py
```

## compute_budget.py

S0 D3 (2026-05-07) — `vision_usage_log` 누적 데이터에 master plan §7.5 공식 적용 → S0 D4 budget_guard 의 운영 초기값 산출.

```
doc_budget_usd  = avg_cost_per_page × avg_pages_per_doc × 0.5 × 1.5
daily_budget_usd = doc_budget_usd × daily_docs
```

```bash
cd api && uv run python scripts/compute_budget.py
cd api && uv run python scripts/compute_budget.py --lookback-days 14 --daily-docs 10
cd api && uv run python scripts/compute_budget.py --output ../work-log/budget-snapshot.md
```

| 옵션 | default | 효과 |
|---|---|---|
| `--lookback-days N` | 7 (ENV `BUDGET_LOOKBACK_DAYS`) | 최근 N일 row fetch |
| `--daily-docs N` | 5 (ENV `BUDGET_DAILY_DOCS`) | 일일 인제스트 doc 가정 |
| `--krw-per-usd N` | 1380 (ENV `BUDGET_KRW_PER_USD`) | 환율 (KRW 환산용) |
| `--source-type X\|all` | `pdf_vision_enrich` | source_type 필터. `all` 시 해제 |
| `--output / -o PATH` | (stdout) | markdown 출력 경로 |

데이터 누적 부족 시 (n<30 row 또는 unique_doc<5) 잠정값 + WARN 출력 + exit 1 (CI gate 호환).
충분 sample 시 측정값 + exit 0.

## backfill_chunk_flags.py / backfill_extract_skipped.py

기존 chunks/documents 에 새 정책 일괄 적용 (DB UPDATE).

**주의** — 운영 환경 직접 적용 전 staging 또는 dry-run 검증 권장.

```bash
cd api && uv run python scripts/backfill_chunk_flags.py
cd api && uv run python scripts/backfill_extract_skipped.py
```

---

## 디자인 정책

- **외부 의존성 0** — stdlib (`urllib`, `argparse`, `statistics`) + 기존 `app.*` 모듈만 사용
- **DB 변경 게이트** — `backfill_*` / 본격 wiring 외 모두 dry-run / snapshot only
- **운영 안전망** — 모든 변경 스크립트는 `--dry-run` 또는 명시 confirm gate 필수 (개별 docstring 참조)
- **CI 통합 가능** — `golden_batch_smoke.py` 의 `--require-top1-min` 같은 exit code gate 패턴
