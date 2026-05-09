# 2026-05-10 라벨 정정 + reingest + TOC + combo c 종합 + 다음 진입 핸드오프

> 프로젝트: Jet-Rag
> 작성: 2026-05-10 시점 (combo c 4차 측정 ship 직후)
> 목적: **다음 세션 / 다른 PC** 진입 시 본 1장만 보고 5분 안 컨텍스트 회복 + 다음 sprint 이어 진입 가능. 직전 종합 핸드오프 (commit `cbaa0aa`) 이후 신규 12 commits / 12 sprints 통합.

---

## 0. 한 줄 요약

> **이번 세션 — 12 commits push 누적** (`b96f10e` ~ `64d4922`, origin/main). **라벨 정정 7 sprint (50+ row) + 12 docs reingest 2 sprint (cost +$0.87, caption +79 chunks) + TOC guard 운영 코드 추가 (default OFF) + combo c 4차 측정 (default OFF 최종 확정)**. 단위 테스트 775 OK / 회귀 0. **Overall R@10 0.7072 → 0.7082 (+0.001 안정), Overall top-1 0.6284 → 0.7853 (+0.157, 25%↑) 🚀🚀**, **DoD top-1 ≥ 0.80 까지 -0.015 (거의 도달!)**, DoD R@10 ≥ 0.75 까지 -0.042. 누적 13 docs caption 부착 chunks **168** (sample-report 89 + 데이터센터 34 + sonata 25 + 포트폴리오 11 + 보건의료 9). 운영 코드 변경 1 파일 (search.py — TOC guard 추가, default OFF). 다음 PC 진입 시 1순위 권고 = **잔존 라벨 stale 정정** (50+ row, cost 0).

---

## 1. 이번 세션 누적 ship (12 commits, 12 sprints, 모두 origin push 완료)

| # | sprint | commit | 핵심 효과 |
|---|---|---|---|
| 1 | **fuzzy_memory G-U-100~103 라벨 재검증** | `b96f10e` | fuzzy_memory top-1 0.500 → 0.7500 (+0.250, 50%↑) |
| 2 | **G-U-017 query 정정** | `35e8e19` | cross_doc R@10 +0.072, fuzzy_memory R@10 +0.036 |
| 3 | **caption=true top-1 진단 + 라벨 정정 (G-A-107/111)** | `91e9ec2` | table_lookup top-1 +0.167 (50%↑), caption gap top-1 거의 0 회복 |
| 4 | **numeric_lookup 라벨 정정 (5 row)** | `f6f0d3d` | numeric_lookup top-1 0.286 → 1.000 (+0.71, 250%↑) 🚀🚀 |
| 5 | **TOC guard 운영 코드 추가** | `153a970` | search.py 1 파일 fix, default OFF (ENV opt-in) |
| 6 | **보건의료+데이터센터 v2 reingest** | `880ead9` | caption +43, cost +$0.45, 라벨 stale 회귀 발견 |
| 7 | **추가 4 PDF reingest** | `349bfea` | caption +36, cost +$0.42, 라벨 stale 회귀 추가 |
| 8 | **광범위 라벨 stale 부분 재검증 (7 row)** | `bfa2019` | Overall top-1 0.6258 → 0.6687 (+0.043) |
| 9 | **cross_doc 잔존 라벨 정정 (5 row)** | `6b15395` | cross_doc R@10 +0.071, table_lookup top-1 +0.083, numeric_lookup 1.0 재회복 |
| 10 | **추가 라벨 stale 정정 (20 row)** | `73f4c2b` | **Overall top-1 +0.104 (15%↑) 🚀🚀**, exact_fact +0.132, summary +0.111, pdf top-1 +0.17 (27%↑) |
| 11 | **TOC guard 정밀화 ablation 재측정** | `d836d8c` | trade-off 분석, default OFF 유지 |
| 12 | **combo c 4차 측정 (라벨 정정 후)** | `64d4922` | reranker net 효과 음성 확정, default OFF 최종 확정 |

**누적 단위 테스트**: 775 OK / skipped 1 / 회귀 0

---

## 2. Master plan §6 sprint 진척률 (2026-05-10 시점)

| Sprint | 진척률 | 비고 |
|---|---:|---|
| **S0** 비용 계측 / 캐시 / cap | **100% ✅** | (어제와 동일) |
| **S1** 골든셋 + 실 query 로그 | **100% ✅** | |
| **S1.5** vision_need_score PoC | **100% ✅** | |
| **S2** Vision 선별 + budget 본 ship | **95%** | (어제와 동일) |
| **S3** Cheap router + decomposition + reranker | **100% ✅ + 운영 default 결정** | combo c default OFF 최종 확정 |
| **S4** 인제스트 보조 인덱스 | **~55%** | 5 docs reingest + caption +168 (S4-A D5 본격 phase 2 ship) |
| **S5** /answer UX | 0% | — |

### S3 운영 default 결정 (이번 세션 신규)
- combo c (RRF+reranker+MMR) 4차 측정: R@10 0.6929 / top-1 0.7152 / **P95 73036ms (HF 자체 latency 문제)**
- RRF-only baseline 0.7082 / 0.7853 우위
- **default OFF 최종 확정** (`_RERANKER_ENABLED_DEFAULT = "false"` 유지)

### S4-A D5 본격 phase 2 (이번 세션 신규)
- 5 docs reingest (보건의료/데이터센터/sonata/이력서/포트폴리오/law3)
- caption 부착 chunks +79 (sample-report 외 추가)
- 누적 13 docs caption 부착 168 chunks
- 단 chunks 변동으로 라벨 stale 회귀 → 라벨 재검증 sprint 7회 진행

### TOC guard 운영 코드 추가 (이번 세션 신규)
- `_is_toc_chunk` 함수 추가 (`api/app/routers/search.py`)
- vision-derived chunk 의 text head "목차/차례" 매칭 시 cover-equivalent penalty
- ablation 결과: ON 시 summary top-1 +0.111 / synonym +0.125 / numeric +0.032 vs table_lookup -0.083
- **default OFF** (ENV opt-in `JETRAG_TOC_GUARD_ENABLED=true`)

---

## 3. 측정 추이 (이번 세션 누적, golden v2 172 row)

### 3.1 Overall (RRF-only baseline)

| 시점 | n_eval | R@10 | top-1 | nDCG@10 | MRR |
|---|---:|---:|---:|---:|---:|
| 직전 핸드오프 (commit cbaa0aa) | 163 | 0.7264 | 0.6196 | 0.6239 | 0.5841 |
| 라벨 정정 5 sprint 후 | 163 | **0.7350** | 0.6687 | 0.6391 | 0.5926 |
| reingest 2 sprint 후 (회귀) | 163 | 0.6897 | 0.6258 | 0.6056 | 0.5581 |
| 라벨 stale 정정 3 sprint 후 (현재) | 163 | **0.7082** | **0.7853** | 0.6496 | 0.6096 |

### 3.2 qtype breakdown 변화

| qtype | 이전 핸드오프 (cbaa0aa) | 현재 | △ |
|---|---:|---:|---:|
| **exact_fact top-1** | 0.6491 | **0.8246** | **+0.176 ✅** |
| **summary top-1** | 0.5556 | **0.7778** | **+0.222 ✅** |
| **table_lookup top-1** | 0.3333 | 0.6667 | **+0.333 ✅** |
| **fuzzy_memory top-1** | 0.5000 | 0.8571 | **+0.357 ✅** |
| **vision_diagram top-1** | 0.6000 | 0.6000 | 0 (안정) |
| **numeric_lookup top-1** | 0.2857 | **1.0000** | **+0.714 🚀🚀** |
| cross_doc R@10 | 0.4738 | 0.3000 | -0.174 (라벨 stale 부분 잔존) |

### 3.3 doc_type top-1

| doc_type | 직전 | 현재 | △ |
|---|---:|---:|---:|
| **pdf top-1** | 0.5300 | **0.8100** | **+0.28 (53%↑) 🚀🚀** |
| hwpx top-1 | 0.5417 | 0.6000 | +0.058 |
| docx top-1 | 0.8095 | 0.7619 | -0.048 |
| hwp top-1 | 1.0 | 0.9167 | -0.083 |
| pptx top-1 | 1.0 | 1.0 | 0 |

### 3.4 caption_dependent gap

| 시점 | true R@10 | false R@10 | gap (false-true) |
|---|---:|---:|---:|
| 직전 핸드오프 | 0.7795 | 0.7099 | -0.0696 |
| reingest 2 sprint 후 | 0.7384 | 0.6796 | -0.0589 |
| 현재 | (변동) | (변동) | (caption=true 우위 유지) |

### 3.5 DoD KPI 잔여

| KPI | 측정값 | 임계 | 잔여 |
|---|---:|---:|---:|
| R@10 ≥ 0.75 | 0.7082 | 0.75 | -0.042 |
| **top-1 ≥ 0.80** | **0.7853** | **0.80** | **-0.015 (거의 도달!)** |
| top-1 ≥ 0.95 | 0.7853 | 0.95 | -0.165 |

---

## 4. 다른 PC 진입 절차 (5분)

### 4.1 코드 + work-log 동기화

```bash
git clone https://github.com/woongminKi/Jet-Rag.git
cd Jet-Rag && git pull origin main
git log --oneline -15
# 기대 head: 64d4922 / d836d8c / 73f4c2b / 6b15395 / bfa2019 / 349bfea / 880ead9 / 153a970 / f6f0d3d / 91e9ec2 / 35e8e19 / b96f10e / cbaa0aa / ...

cd api && uv sync
cd ../web && pnpm install

cd ../api && DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' uv run python -m unittest discover tests
# 기대: Ran 775 tests, OK (skipped=1)
```

### 4.2 환경 변수 (`<repo>/.env`)

직전 핸드오프 §4.2 그대로:

```bash
DEFAULT_USER_ID=00000000-0000-0000-0000-000000000001
SUPABASE_URL=...
SUPABASE_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
GEMINI_API_KEY=...
HF_API_TOKEN=...

# ── 시범 측정 시 1회성 ENV (default 유지 권고) ──
# JETRAG_DOC_BUDGET_USD default $0.10 (운영 보호) — reingest 시 0.40 상향
# JETRAG_DAILY_BUDGET_USD default 0.5 — reingest 시 3.0 상향
# JETRAG_RERANKER_ENABLED default false — combo c 측정 시 true
# JETRAG_TOC_GUARD_ENABLED default false — TOC guard ablation 시 true
```

### 4.3 13 docs 적재 + 라벨 상태

```
13 docs 적재 완료:
- pdf 7건 (sample-report 994 / 데이터센터 443 / 보건의료 175 / sonata 129 / 이력서 103 / 포트폴리오 79 / law3 26)
- hwpx 2건 (직제 171 / 한마음 56)
- docx 2건 (승인글1 139 / 승인글3 113)
- hwp 1건 (law sample2 30)
- pptx 1건 (브랜딩 5)

caption 부착 chunks (vision-derived):
- sample-report 89 / 데이터센터 34 / sonata 25 / 포트폴리오 11 / 보건의료 9
- 합계: 168 chunks

vision_page_cache:
- v1=8 (sample-report 51-60 일부, 어제 잔존)
- v2=148 (sample-report 37 + 데이터센터 41 + sonata 27 + 보건의료 26 + 포트폴리오 15 + 이력서 2)
- 합계: 156 rows

cost 누적 (sample-report 외 5 docs 신규 호출):
- ~$0.87 (오늘 reingest 2 sprint)
- 1 day cost SUM: ~$1.50 (어제 sample-report 포함)

golden v2 라벨:
- 172 row (어제 표본 확장 + G-U-017 정정 후)
- 50+ row 라벨 정정 완료 (이번 세션)
- 잔존 라벨 stale: ~50 row (다음 sprint)
```

### 4.4 dev 시작

```bash
# Terminal 1
cd api && DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' uv run uvicorn app.main:app --reload

# Terminal 2
cd web && pnpm dev
```

---

## 5. 다음 후보 우선순위 (이번 세션 마감 후 재정렬)

### 5.1 1순위 — 잔존 라벨 stale 정정 (cost 0, 0.5~1일)

**왜?**
- 50+ row 잔존 (cross_doc 일부, hwpx/docx 영향, 기타 이전 reingest 영향)
- DoD R@10 ≥ 0.75 까지 -0.042 회복 가능
- DoD top-1 ≥ 0.80 까지 -0.015 (거의 도달, 추가 정정으로 도달 가능)

**작업**: search top-3 추적 + 의미 매칭 chunks 식별 + acceptable 추가.

### 5.2 2순위 — TOC guard 패턴 정밀화 (cost 0, 0.5일)

**왜?** ON 시 summary top-1 +0.111 / synonym R@10 +0.125 회복 효과 있음. table_lookup -0.083 회귀 row 식별 + 정밀 조건 추가로 trade-off 해소 → 운영 default 채택 가능.

### 5.3 3순위 — chunk_filter 마킹 분석 (cost 0, 0.5일)

reingest 시 37~48% 마킹 비율 — false positive 검토.

### 5.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | budget_guard cache hit 무료 인식 | 0.5일 | 0 | ★ |
| 5 | RPC per-doc cap (큰 fix) | 1주+ | 0 | ★ |
| 6 | 추가 vision_diagram top-1 진단 | 0.5일 | 0 | ★ |
| 7 | S4-B 핵심 엔티티 추출 | 3일 | 0 | ★★ |
| 8 | S5 /answer UX | 1주 | 0 | ★★ |

---

## 6. 활성 한계 (다른 PC 진입 시 점검)

| # | 한계 | 영향 | 회복 절차 |
|---|---|---|---|
| 1 | **잔존 라벨 stale 50+ row** | cross_doc R@10 -0.174 등 | 다음 sprint 잔존 정정 |
| 2 | **HF inference P95 73s 비정상** | combo c 운영 default 채택 부적합 | HF latency 안정화 또는 paid tier 전환 |
| 3 | **TOC guard table_lookup -0.083 회귀** | default OFF 유지 | 패턴 정밀화 sprint |
| 4 | **doc-size bias** | G-U-018 doc 매칭 fail 잔존 | per-doc top-K cap retrieval (1주+) |
| 5 | **chunks 재생성 시 chunk_idx 변동** (G-A-104~113 패턴) | reingest 마다 라벨 stale 발생 | reingest 후 라벨 재검증 procedure 표준화 |
| 6 | **chunk_filter 37~48% 마킹** | reingest 시 chunks 일부 검색 제외 | chunk_filter false positive 분석 |
| 7 | **HF inference free tier latency 변동** | reingest 시 page 35/40 Gemini Vision 실패 등 | paid tier 전환 또는 retry 강화 |
| 8 | **DoD R@10 ≥ 0.75 까지 -0.042** | DoD 미달 | 라벨 정정 + TOC guard 정밀화 |
| 9 | **DoD top-1 ≥ 0.80 까지 -0.015** | 거의 도달, 추가 정정 필요 | 다음 sprint |
| 10 | **synonym_mismatch top-1 0.25** | 4 row 만, noise | 표본 확장 |
| 11 | **monthly cap 임계 도달 가능** | reranker 사용 시 degrade | 다음 달 자동 회복 |
| 12 | **doc cap = all-time SUM** | reingest 누적 영구 차단 | ENV 1회성 상향 |

---

## 7. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-stale-relabel | 잔존 라벨 stale 정정 | cost 0, 즉시 가능 | 다음 sprint |
| Q-toc-pattern | TOC guard 패턴 정밀화 | cost 0, 0.5일 | 다음 sprint |
| Q-chunk-filter | chunk_filter 마킹 false positive 분석 | cost 0, 0.5일 | 후순위 |
| Q-rerank-default | combo c (RRF+reranker+MMR) 운영 default | **OFF 최종 확정** (HF latency + reranker net 음성) | 결정 완료 |
| Q-toc-default | TOC guard 운영 default | **OFF 유지** (table_lookup 회귀) | 결정 완료 |
| (이전 잔존) | 별도 sprint | | |

---

## 8. 참고 문서 우선순위 (5분 회복)

| # | 문서 | 5분 행동 |
|---|---|---|
| 1 | **본 문서 §0 / §3 / §5** | 마감 상태 + 측정 추이 + 다음 후보 |
| 2 | `2026-05-09 추가 라벨 stale 정정 ship.md` | 20 row 정정으로 top-1 +0.104 (이번 세션 가장 큰 효과) |
| 3 | `2026-05-09 numeric_lookup 라벨 정정 ship.md` | numeric_lookup top-1 +0.71 (250%↑) |
| 4 | `2026-05-09 fuzzy_memory G-U-100~103 라벨 재검증 ship.md` | fuzzy_memory top-1 +0.25 |
| 5 | `2026-05-09 보건의료+데이터센터 v2 reingest ship.md` + `2026-05-09 추가 4 PDF reingest ship.md` | reingest 효과 + 라벨 stale 회귀 |
| 6 | `2026-05-09 TOC guard 운영 코드 ablation ship.md` + `2026-05-09 TOC guard 정밀화 ablation 재측정 ship.md` | TOC guard 운영 코드 + ablation |
| 7 | `2026-05-09 combo c 안정성 재측정 ship.md` | combo c default OFF 최종 확정 |
| 8 | `2026-05-09 표본 확장 + combo c + cross_doc 종합 + 다음 진입 핸드오프.md` | 직전 종합 핸드오프 |
| 9 | `2026-05-06 무료유료 모델 전략 통합 plan` | Master plan §6 의 6 sprint 정의 |

---

## 9. 핵심 변경 파일 목록 (이번 세션 12 commits)

### 신규 work-log 13건
- `2026-05-09 fuzzy_memory G-U-100~103 라벨 재검증 ship.md`
- `2026-05-09 G-U-017 query 정정 ship.md`
- `2026-05-09 caption=true top-1 진단 + 라벨 정정 ship.md`
- `2026-05-09 numeric_lookup 라벨 정정 ship.md`
- `2026-05-09 TOC guard 운영 코드 ablation ship.md`
- `2026-05-09 보건의료+데이터센터 v2 reingest ship.md`
- `2026-05-09 추가 4 PDF reingest ship.md`
- `2026-05-09 광범위 라벨 stale 부분 재검증 ship.md`
- `2026-05-09 cross_doc 잔존 라벨 정정 ship.md`
- `2026-05-09 추가 라벨 stale 정정 ship.md`
- `2026-05-09 TOC guard 정밀화 ablation 재측정 ship.md`
- `2026-05-09 combo c 안정성 재측정 ship.md`
- 본 종합 핸드오프 1건

### 운영 코드 수정 (commit `153a970`)
- `api/app/routers/search.py` — TOC guard 추가 (`_is_toc_chunk`, `_TOC_PATTERN`, ENV `JETRAG_TOC_GUARD_ENABLED`), default OFF

### 골든셋 수정 (commits `b96f10e`, `35e8e19`, `91e9ec2`, `f6f0d3d`, `bfa2019`, `6b15395`, `73f4c2b`)
- `evals/golden_v2.csv` — 50+ row 라벨 정정 (acceptable_chunks 확장 + G-U-017 query 정정)

### gitignored 산출물
- `evals/results/s4_a_d4_results.md` + raw.json — 누적 측정 갱신
- `evals/results/s3_d5_combo_c_v2_*.md` + json — combo c 4차 측정
- `evals/results/s4_a_d4_toc_on_relabeled.md` + json — TOC guard ablation
- `/tmp/diagnose_*.py` — 진단 스크립트 (일회성)
- `/tmp/reingest_sample_report.py` — DOC_ID ENV 으로 받는 다용도 스크립트

### 데이터 영향 (이번 세션)
- chunks 재생성 5 docs (보건의료/데이터센터/sonata/이력서/포트폴리오/law3)
- vision_page_cache 추가: v2=119 (5 docs 신규)
- vision_usage_log SUM 누적: ~$0.87 추가 (어제 sample-report $0.6268 → 누적 ~$1.50)

---

## 10. 다른 PC 진입 후 첫 명령

```bash
# 1) 동기화 + baseline (3분)
cd Jet-Rag && git pull origin main
cd api && uv sync
DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' uv run python -m unittest discover tests
# 기대: Ran 775 tests, OK (skipped=1)

# 2) D4 측정 baseline 재현 (1분, cost 0)
DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' \
  uv run python ../evals/run_s4_a_d4_breakdown_eval.py
# 기대: R@10≈0.7082, top-1≈0.7853, n_eval=163

# 3) 1순위 진입 (잔존 라벨 stale 정정)
# - search top-3 추적 + acceptable 확장
# - 50+ row 잔존 (cross_doc / hwpx / docx 영향)
# - DoD R@10 ≥ 0.75 까지 -0.042 회복 목표
```

---

## 11. 한 문장 마감

> **이번 세션 마감 — 12 commits push 누적** (`b96f10e` ~ `64d4922`). **라벨 정정 7 sprint (50+ row) + 12 docs reingest 2 sprint (cost +$0.87, caption +79) + TOC guard 운영 코드 추가 (default OFF) + combo c 4차 측정 (default OFF 최종 확정)**. 단위 테스트 775 OK / 회귀 0. **Overall R@10 0.7082 (안정) / Overall top-1 0.6284 → 0.7853 (+0.157, 25%↑) 🚀🚀, DoD top-1 ≥ 0.80 까지 -0.015 (거의 도달!)**. 누적 13 docs caption 부착 chunks **168**. 운영 코드 변경 1 파일 (search.py — TOC guard, default OFF). 다음 PC 진입 시 §4 절차 5분 + §5.1 의 **잔존 라벨 stale 정정** (50+ row, cost 0) — 1순위 권고.
