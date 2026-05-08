# 2026-05-09 S2 D4 옵션 A — vision_need_score threshold 튜닝

master plan §6 S2 D4 옵션 A 진입 — D3 ship 임계 (5 신호 OR rule) 의 11 후보를 D3 raw signal 기반으로 시뮬레이션 측정. 운영 모듈 변경 0, 외부 vision API 호출 0, DB 쓰기 0.

## 1. 작업 내용

### 1.1 신규 파일
- `evals/run_s2_d4_threshold_ablation.py` (1067 LOC) — ablation 스크립트
- `api/tests/test_s2_d4_threshold_ablation.py` (321 LOC) — 단위 테스트 13건

### 1.2 보강 파일
- `api/app/services/vision_need_score.py` — 5 모듈 상수 docstring 보강 (P2-1, "S1.5 D3 결정값, S2 D4 ablation 으로 정정 후보 측정 중" 표기)

### 1.3 산출물
- `evals/results/vision_need_score_d3.csv` — 본 PC 재생성 (195 pages × 6 PDFs, assets 재귀 스캔)
- `evals/results/s2_d4_threshold_ablation.md` — 11 후보 종합 표 + per-doc skip + 자동 권고
- `evals/results/s2_d4_threshold_ablation.json` — 후보별 raw 결과 (machine-readable)
- `evals/results/s2_d4_threshold_ablation_hints.csv` — hint cross-check per-row CSV (후보 × golden_id)

### 1.4 핵심 설계
- **vision_need_score 모듈 상수 변경 0** — `_or_rule_with_thresholds()` 동등 함수 본 스크립트 내부 정의 (운영 모듈 격리, monkey-patch 금지)
- **Threshold dataclass** — 5 신호 임계 + trigger_* 활성 플래그 (P2-1 magic number 제거)
- **§6.2 결정 트리 자동 산출** — Q1 → Q2 → Q3 분기로 권고 후보 + S1.5 v3 trigger 여부 자동 도출
- D4-pre (`run_s2_d4_pre_regression.py`) 의 loader / cross-check / markdown formatter 패턴 재사용

## 2. 사이드 이펙트 점검

- [x] 운영 파이프라인 (ingest / extract / search) 영향 0 — ablation 스크립트는 evals/ 격리
- [x] vision_need_score.py 모듈 상수 값 변경 0 (docstring 만 보강)
- [x] 의존성 추가 0 (stdlib + Path + dataclass 만 사용)
- [x] DB 쓰기 0 — `--use-db` 옵션도 chunks 테이블 read-only SELECT 만
- [x] 외부 vision API 호출 0 (vision_page_cache invalidate / reingest 없음)
- [x] 단위 테스트 회귀 0 — 654 → **667** (+13)
- [x] 무료 티어 한도 영향 0

## 3. 단위 테스트 결과

```
cd api && uv run python -m unittest discover tests
Ran 667 tests in 15.669s OK (skipped 1 / 회귀 0)
```

13 신규 케이스 (D4 ablation 격리 모듈):

| 클래스 | 메서드 | 결과 |
|---|---|---|
| ThresholdRecomputeTest | test_density_alone_triggers / test_table_alone_triggers / test_all_signals_off / test_multiple_signals_simultaneous / test_quality_inverse_direction | ok × 5 |
| AblationOnlyTest | test_a1_density_only_other_signals_off / test_a2_table_only / test_a3_image_only / test_a4_quality_only / test_a5_caption_only | ok × 5 |
| DatacenterP40CatchTest | test_only_c5_density_aggr_catches_p40 | ok × 1 |
| GoldenHintCrossCheckTest | test_cross_check_basic_match / test_cross_check_unknown_page_and_doc | ok × 2 |

## 4. 실측 결과 (본 PC 9 골든 row 중 측정 가능 3 row)

### 4.1 11 후보 종합

| 후보 | density | table | image | quality | caption | overall_skip% | hint_hit_rate | DC p.40 catch |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| C0_baseline | 1.0e-03 | 0.30 | 0.30 | 0.40 | 0.20 | 37.4% (73/195) | 33.3% (1/3) | No |
| C1_conservative | 1.0e-03 | 0.40 | 0.40 | 0.30 | 0.30 | 48.2% (94/195) | 33.3% (1/3) | No |
| C2_aggressive | 1.5e-03 | 0.20 | 0.20 | 0.50 | 0.10 | 18.5% (36/195) | 33.3% (1/3) | No |
| C3_caption_aggr | 1.0e-03 | 0.30 | 0.30 | 0.40 | 0.10 | 28.7% (56/195) | 33.3% (1/3) | No |
| C4_image_aggr | 1.0e-03 | 0.30 | 0.20 | 0.40 | 0.20 | 35.9% (70/195) | 33.3% (1/3) | No |
| **C5_density_aggr** | **2.0e-03** | 0.30 | 0.30 | 0.40 | 0.20 | **16.4% (32/195)** | **66.7% (2/3)** | **Yes** |
| A1_density_only | 1.0e-03 | — | — | — | — | 72.8% (142/195) | 33.3% (1/3) | No |
| A2_table_only | — | 0.30 | — | — | — | 74.4% (145/195) | 0.0% (0/3) | No |
| A3_image_only | — | — | 0.30 | — | — | 85.1% (166/195) | 33.3% (1/3) | No |
| A4_quality_only | — | — | — | 0.40 | — | 100.0% (195/195) | 0.0% (0/3) | No |
| A5_caption_only | — | — | — | — | 0.20 | 77.4% (151/195) | 0.0% (0/3) | No |

### 4.2 hint cross-check (C0_baseline 기준 row set, 모든 후보 공통)

| id | query_type | doc title | hint page | C0 needs_vision | triggers | note |
|---|---|---|---:|:---:|---|---|
| G-U-003 | table_lookup | sonata-the-edge_catalog | — | — | — | page 미상 |
| G-U-005 | vision_diagram | sonata-the-edge_catalog | — | — | — | page 미상 |
| G-U-006 | vision_diagram | 2025년 데이터센터 | 6 | True | low_density\|image_area | OK |
| G-U-016 | vision_diagram | 직제_규정 | — | — | — | doc 미매칭 |
| G-A-008 | table_lookup | (붙임2) 2025년 데이터센터 | 40 | False | — | OK (회귀 위험) |
| G-A-011 | table_lookup | 브랜딩_스튜디오앤드오어 | 2 | — | — | doc 미매칭 |
| G-A-021 | table_lookup | sample-report | 91 | False | — | OK (회귀 위험) |
| G-A-107 | table_lookup | 포트폴리오_이한주 | 7 | — | — | doc 미매칭 |
| G-A-111 | table_lookup | 포트폴리오_이한주 | 7 | — | — | doc 미매칭 |

- **측정 가능 3 row**: G-U-006 (p.6) / G-A-008 (p.40) / G-A-021 (p.91)
- **C0_baseline hits**: 1/3 = G-U-006 만 catch
- **C5_density_aggr hits**: 2/3 = G-U-006 + G-A-008 catch (density 2e-3 임계로 p.40 회수)
- 본 PC 자료 한계 — 6 row 가 doc 미매칭/page 미상으로 측정 불가 (직제규정·브랜딩·포트폴리오 PDF 부재, sonata source_hint 가 page 미상)

### 4.3 데이터센터 p.40 catch (G-A-008 회귀 위험 row)

raw signal: density 1.62e-3 / table 0 / image_area 0.009 / text_quality 0.97 / caption 0.067

- **catch 후보 1개**: `C5_density_aggr` (density 2e-3 임계 → 1.62e-3 < 2e-3 trigger)
- 다른 10 후보 catch 0 — table / image / quality / caption 신호로는 본 페이지 catch **구조적 불가** (실 신호값이 후보 임계 어떤 값에도 미달)

## 5. §6.2 결정 트리 자동 권고 (스크립트 산출)

```
[Q1] hint hit_rate ≥ 5/6 (83.3%) 후보는?
  → 0개 (최고가 C5_density_aggr 의 66.7%)
  → C0 baseline 유지 + S1.5 v3 시급도 ↑
```

- **권고 후보**: 없음 (C0 baseline 유지)
- **S1.5 v3 trigger 권고** — table 휴리스틱 v3 / multi-line table fallback / 추가 신호 검토 필요

### 5.1 단, 본 PC 자료 한계 보완 해석 (사용자 결정 필요)

본 PC 측정 가능 row 가 3건뿐 (전체 9 골든 row 중 6건 doc 미매칭/page 미상) — 어제 다른 PC 의 6건 baseline 과 다름. 결정 트리의 5/6 임계는 본 PC 상으로 3/3 = 100% 와 동치라 도달 불가능 영역.

대안 해석:
- **C5_density_aggr 잠정 채택 검토** — 측정 가능 3 row 중 2 row catch (66.7%, 데이터센터 p.40 회귀 회수) + skip rate 16.4% (cost ↓ 추가 효과). 단 이는 hit_rate 임계 미달 + 본 PC 단독 측정의 한계.
- **다른 PC (115 pages 환경) 에서 동일 ablation 재측정** — 6 골든 row 모두 측정 가능한 환경에서 재검증 필요 (Q-S2-D4-1 신규).

## 6. 발견 이슈 / 추가 결정 필요 항목

### 6.1 본 PC 자료 한계 — 명세 가설 (115 pages / 6 측정 가능 row) 과 본 실측 (195 pages / 3 측정 가능 row) 차이
- 본 PC 의 assets 디렉터리 PDF 6 개 = sonata / 데이터센터 / 보건의료 / sample-report / law sample2 / law sample3
- 골든 v1 의 직제규정 / 브랜딩 / 포트폴리오 PDF 는 본 PC 에 부재
- sonata 의 G-U-003 / G-U-005 는 source_hint 가 빈 문자열 (page 미상)

### 6.2 C5_density_aggr 가 유일한 데이터센터 p.40 catch 후보
- **명세의 "raw signal 분석" 주석은 잘못된 추정** (5 신호 모두 미달이라 catch 불가 → 실제 density 1.62e-3 < 2e-3 인 C5 만 catch 가능)
- 단위 테스트 expected set 을 실측에 맞게 수정 (`EXPECTED_CATCHERS = {"C5_density_aggr"}`)
- 이는 **density 임계 완화가 데이터센터 p.40 회수의 유일한 5-신호 경로** 라는 의미 있는 발견

## 7. 남은 이슈 / 사용자 결정 필요 (Q-S2-D4-1 ~ 4)

### Q-S2-D4-1 (신규) — 다른 PC 에서 동일 ablation 재측정
- 본 PC 자료로는 측정 가능 row 가 3건뿐 → 어제 다른 PC (115 pages, 6 측정 가능 row) 에서 동일 스크립트 실행 후 결정 트리 재산출 필요
- 또는 본 PC 에 직제규정 / 브랜딩 / 포트폴리오 PDF 추가 후 D3 CSV 재생성

### Q-S2-D4-2 — C5_density_aggr 잠정 채택 여부
- 본 PC 측정 한정 hit_rate 66.7% / skip_rate 16.4% / 데이터센터 p.40 catch
- §6.2 결정 트리의 hit_rate 83.3% 임계는 미달이지만, 측정 가능 row 부족이 원인
- 사용자 결정 — (A) C0 유지 + S1.5 v3 진입 / (B) C5 잠정 채택 + 다른 PC 재측정 후 확정 / (C) 다른 PC 재측정 우선

### Q-S2-D4-3 (명세 §4 P2-1) — IngestModeSelect / VisionPageCapExceededCard 의 라벨 magic number
- "≤10페이지" / "≤50페이지" mode 별 임계 set 분리 여부
- D4 범위 밖 — 본 작업에서 변경 0 (명세대로)

### Q-S2-D4-4 — 채택 후보 skip rate < 30% 사용자 확인
- C5_density_aggr 의 skip_rate 16.4% — 30% 미만이라 본 항목 발화 가능
- 단 채택 결정 (Q-S2-D4-2) 이 (B) 또는 (C) 인 경우만 적용

## 8. 다음 스코프 — S2 D5

### 8.1 D5 진입 조건
- Q-S2-D4-1 (다른 PC 재측정) 결과 + Q-S2-D4-2 (C5 채택 여부) 결정 필요
- 결정 트리상 권고 = "C0 유지 + S1.5 v3" 인 경우 D5 는 임계 patch 가 아닌 S1.5 v3 진입으로 분기

### 8.2 D5 작업 (채택 가정)
- vision_need_score.py 모듈 상수 patch — `_DENSITY_NEEDS_AT = 2e-3` (또는 채택 후보)
- 운영 chunks 테이블 reingest (vision_page_cache invalidate + 데이터센터 PDF 재처리) — Gemini 비용 발생 (cost budget 영향 측정 필요)
- 골든셋 v1 R@10 회귀 측정 — D4-pre baseline 대비 ±X pp 산출
- ragas 회귀 회피 가드 — S1 D5 baseline (47점) 과 비교

### 8.3 S1.5 v3 분기 (권고 채택 시)
- table 휴리스틱 v3 — multi-line table fallback (한 cell 이 여러 line 으로 split 된 한국어 PDF 대응)
- 추가 신호 검토 — keyword density (표/그림/Figure/Table) / page header pattern / numeric ratio

## 9. 본 작업 검증 명령어

```bash
# (1) D3 raw signal CSV 재생성 (본 PC, 외부 API 0)
cd api && uv run python scripts/poc_vision_need_score.py --pdf-dir ../assets --recursive

# (2) ablation 실행
cd api && uv run python ../evals/run_s2_d4_threshold_ablation.py

# (3) 단위 테스트 회귀 검증
cd api && uv run python -m unittest discover tests
# → Ran 667 tests in ~15.7s OK
```

## 10. 산출물 경로

- 코드: `evals/run_s2_d4_threshold_ablation.py` / `api/tests/test_s2_d4_threshold_ablation.py`
- 보강: `api/app/services/vision_need_score.py` (docstring 만)
- 측정 산출: `evals/results/s2_d4_threshold_ablation.{md,json}` + `_hints.csv`
- 입력 (재생성): `evals/results/vision_need_score_d3.csv`
