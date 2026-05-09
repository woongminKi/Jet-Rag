# 2026-05-09 표본 확장 + combo c + cross_doc 종합 + 다음 진입 핸드오프

> 프로젝트: Jet-Rag
> 작성: 2026-05-09 마감 시점 (cross_doc 라벨 재검증 ship 직후)
> 목적: **다음 세션 / 다른 PC** 진입 시 본 1장만 보고 5분 안 컨텍스트 회복 + 다음 sprint 이어 진입 가능. 직전 종합 핸드오프 (commit `0eb567e`) 이후 신규 5 commits / 5 sprints 통합.

---

## 0. 한 줄 요약

> **2026-05-09 (이번 세션) — 5 commits push 누적** (`319e33c` ~ `ef98e7f`, origin/main). **golden v2 표본 확장 + combo c 운영 default 측정 (root cause 2회 정정) + multi-doc cross_doc 진단 + cross_doc 라벨 재검증**. 단위 테스트 775 OK / skipped 1 / 회귀 0 (직전 핸드오프 대비 변동 없음). Overall R@10 = **0.7264** (n_eval 163, golden v2 172 row), **caption=true R@10 0.7795** (caption gap 반전 -0.0642), **cross_doc R@10 0.2917 → 0.4738 (+0.182 🚀)**, table_lookup R@10 0.7215, vision_diagram 0.9333. **DoD R@10 ≥ 0.75 까지 -0.0236**. combo c 측정에서 **HF API latency 의 시간대별 변동성 발견** (어제 D6 263ms vs 오늘 71.7s, 273배). 운영 코드 변경 0 (이번 세션 모든 ship). 다음 PC 진입 시 1순위 권고 = **doc-size bias 완화 fix** (Pattern 1 잔존, 운영 코드 변경 1~2일).

---

## 1. 이번 세션 누적 ship (5 commits, 5 sprints, 모두 origin push 완료)

| # | sprint | commit | 핵심 변경 | 측정 변동 |
|---|---|---|---|---|
| 1 | **golden v2 minority qtype 표본 확장** | `319e33c` | 157 → 172 row (+15) — table_lookup 6→12, fuzzy_memory 6→10, caption_dependent=true 18→29, vision_diagram 3→6. cost 0, 운영 코드 변경 0 | **caption gap 반전** (+0.013 → -0.070), **caption=true R@10 +0.0838 🚀**, table_lookup R@10 +0.112, vision_diagram +0.10, exact_fact+caption=true top-1 0.818 (DoD 도달 cell ✅) |
| 2 | **combo c 측정 1차 (cap 임계 영향)** | `3e56f88` | combo c × golden v2 172 row 측정. R@10 0.7315 (+0.0096 vs RRF-only) / top-1 0.6424 (+0.0289). P95 29568ms 비정상. 1차 root cause 추정 = reranker monthly cap 임계 도달 (800/1000) | DoD R@10 0.75 미달 (-0.0185). 운영 default 채택 보류 |
| 3 | **combo c P95 root cause 2차 정정** | `e65d53c` | cap 상향 (5000) 후 재측정 → degrade 0.000 인데도 P95 71.7s. cap 임계 도달도 root cause 아님 → **HF API 자체의 시간대별 부하 / latency 변동** (어제 263ms → 오늘 71.7s, 273배). work-log 정정 | 운영 default 채택 보류 (HF SLO 부적합) |
| 4 | **multi-doc cross_doc retrieve 진단** | `8ab7acf` | 7 multi-doc U-row 의 search 응답 추적 → 4 root cause 양상 분리: (1) doc-size bias, (2) 정답 chunk 미매칭, (3) 정상 작동 1건 (G-U-026), (4) 라벨 부정확 의심 | cross-doc 약점 root cause 분리 (cost 0 진단) |
| 5 | **cross_doc 라벨 재검증** | `ef98e7f` | 4 row 라벨 정정 — G-U-015 한마음 위원회 0건 → 직제 ch 102 만 정답, G-U-031 sonata ch 129→113 (스마트센스 안전), G-U-018/032 acceptable 인접 chunks 확장 | **cross_doc R@10 0.2917 → 0.4738 (+0.182, 62%↑) 🚀, top-1 0.2500 → 0.5000 (+0.250, 100%↑)**, Overall R@10 +0.0045 |

**누적 단위 테스트**: 775 OK / skipped 1 / 회귀 0 (직전 핸드오프 대비 변동 없음)

---

## 2. Master plan §6 sprint 진척률 (2026-05-09 마감 시점)

| Sprint | 목표 | 진척률 | 마감일 | 비고 |
|---|---|---:|---|---|
| **S0** 비용 계측 / 캐시 / cap | 1주 | **100% ✅** | 2026-05-07 | (어제와 동일) |
| **S1** 골든셋 + 실 query 로그 | 1주 | **100% ✅** | 2026-05-07 | |
| **S1.5** vision_need_score PoC | 3일 | **100% ✅** | 2026-05-07 | |
| **S1.5 v3** multi-line table 휴리스틱 | 0.5일 | **100% ✅** | 2026-05-09 | |
| **S2** Vision 선별 + budget 본 ship | 1주 | **95%** (D5 phase 1) | 2026-05-09 | 어제와 동일 |
| **S3** Cheap router + decomposition + reranker | 1주 | **100% ✅** | 2026-05-10 | 어제 ship + 오늘 combo c 측정 재현 (P95 변동성 확인) |
| **S4** 인제스트 보조 인덱스 | 1~2주 | **~45%** | 진행 중 | S4-A D1+D2(+OCR/split fix)+D3 Phase 1+2-A+D4+D5 본격 + golden 표본 확장 + cross_doc 라벨 정정 ship |
| **S5** /answer UX | 1주 | 0% | — | |

### 표본 확장 효과 (이번 세션 #1)
- table_lookup R@10 +0.112, vision_diagram +0.10
- caption gap 반전 (+0.013 → -0.064)
- exact_fact + caption=true cell top-1 **0.818 (DoD ≥ 0.80 도달 ✅)**

### combo c 측정 + root cause 정정 (이번 세션 #2-3)
- R@10 +0.0096 / top-1 +0.0289 (RRF-only 대비) 효과 일관
- P95 latency 273배 변동 — HF inference free tier 의 시간대별 부하
- **운영 default 채택 보류** (HF SLO 부적합)

### multi-doc 진단 + 라벨 정정 (이번 세션 #4-5)
- cross_doc R@10 +0.182 (62%↑), top-1 +0.250 (100%↑)
- Pattern 1 (doc-size bias) 잔존 — 운영 코드 변경 필요
- multi-doc helper 자체는 정상 (G-U-026 R@10=1.0 검증)

---

## 3. 측정 추이 (이번 세션 누적, golden v2 172 row baseline)

### 3.1 RRF-only (D4 baseline)

| 시점 | n_eval | Overall R@10 | top-1 | caption=true R@10 | cross_doc R@10 | caption gap |
|---|---:|---:|---:|---:|---:|---:|
| 직전 핸드오프 (commit 0eb567e) | 148 | 0.7072 | 0.6284 | 0.6957 | 0.2917 | +0.013 |
| 표본 확장 후 | 163 | 0.7219 | 0.6135 | 0.7795 ⭐ | 0.2917 | -0.070 |
| **cross_doc 라벨 정정 후** | **163** | **0.7264** | **0.6196** | 0.7795 | **0.4738** ⭐ | -0.064 |

→ Overall R@10 +0.019 (직전 핸드오프 대비), cross_doc R@10 +0.182 (62%↑), caption gap 반전.

### 3.2 combo c (실 BGE-reranker, 오늘 측정)

| 측정 | n_eval | R@10 | top-1 | P95 (ms) | degrade | 비고 |
|---|---:|---:|---:|---:|---:|---|
| 어제 D6 (golden v2 150) | — | 0.7049 | 0.6667 | **263** | 낮음 | 어제 정상 |
| 오늘 1차 cap=1000 (172) | 165 | 0.7315 | 0.6424 | 29568 | 0.145 | cap 임계 도달 시점 |
| 오늘 2차 cap=5000 (172) | 165 | 0.7256 | 0.6606 | **71687** ⚠⚠ | 0.000 | **HF latency 폭증** |

→ R@10/top-1 효과 일관 (+0.01~0.04 vs RRF-only). P95 273배 변동성으로 운영 default 보류.

### 3.3 qtype × caption_dependent (DoD 도달 cell)

| qtype | caption | n_eval | R@10 | top-1 |
|---|:---:|---:|---:|---:|
| **exact_fact + caption=true** | true | 11 | 0.7729 | **0.8182 ✅ DoD top-1 ≥ 0.80 도달** |
| vision_diagram + caption=true | true | 5 | **0.9333 ⭐** | 0.6000 |
| table_lookup + caption=true | true | 12 | 0.7215 | 0.3333 |
| **cross_doc + caption=false** | false | 4 | **0.4738** ⭐ | **0.5000** ⭐ |

---

## 4. 다른 PC 진입 절차 (5분)

### 4.1 코드 + work-log 동기화

```bash
git clone https://github.com/woongminKi/Jet-Rag.git
cd Jet-Rag && git pull origin main
git log --oneline -10
# 기대 head: ef98e7f / 8ab7acf / e65d53c / 3e56f88 / 319e33c / 0eb567e / ...

cd api && uv sync
cd ../web && pnpm install

cd ../api && DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' uv run python -m unittest discover tests
# 기대: Ran 775 tests, OK (skipped=1)
```

### 4.2 환경 변수 (`<repo>/.env`)

직전 핸드오프 §4.2 그대로 + 추가 발견:

```bash
DEFAULT_USER_ID=00000000-0000-0000-0000-000000000001
SUPABASE_URL=...
SUPABASE_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
GEMINI_API_KEY=...
HF_API_TOKEN=...

# ── reingest / 측정 시 주의 ──
# JETRAG_DOC_BUDGET_USD default $0.10 (운영 보호)
# JETRAG_RERANKER_MONTHLY_CAP_CALLS default 1000 (월간 cap)
# 시범 측정 시 1회성 ENV 상향 후 unset
```

### 4.3 본 PC 의 13 docs 적재 상태 (직전 핸드오프 §4.3 동일)

```
documents (13건, deleted_at=NULL):
- pdf 7건 (sample-report 994 chunks 포함, caption 부착 89건)
- hwpx 2 + hwp 1 + docx 2 + pptx 1 = 6건
- stale doc_id: 0
```

### 4.4 vision_page_cache + reranker 카운터 상태

```
sample-report (sha b35f5b1955):
  v1=8 page (51-60 일부)
  v2=37 page (page 1~11/13~18/20/23~40)
  total = 45 rows, cost SUM = $0.6268

다른 12 docs: vision_page_cache = 0 (v2 reingest 안 됨)

reranker_invoke 이번 달 누적:
  ~800 calls (degrade threshold 도달)
  → 운영 cap default 1000 / threshold 0.8 = 800
  → 다음 달 자동 회복, 또는 ENV cap 상향
```

### 4.5 dev 시작

```bash
# Terminal 1
cd api && DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' uv run uvicorn app.main:app --reload

# Terminal 2
cd web && pnpm dev
```

---

## 5. 다음 후보 우선순위 (이번 세션 마감 후 재정렬)

### 5.1 1순위 — doc-size bias 완화 fix (운영 코드 변경, 1~2일)

**왜?**
- multi-doc 진단의 Pattern 1 잔존 (G-U-018/027 정답 doc 자체가 search top-50 밖)
- 큰 doc (sample-report 994, 데이터센터 443) 의 chunks 가 RRF top 우선
- 라벨링으로는 회복 불가
- cross_doc R@10 0.4738 → ~0.65 가능 추정

**작업 plan**:
- multi-doc query detector (query 안 doc 키워드 인식, e.g. "law sample 자료에서")
- per-doc top-K cap retrieval (각 doc 별 균등 retrieve 후 RRF 합산)
- 단위 테스트 + golden v2 회귀 검증
- 운영 코드 변경, 회귀 risk → 사용자 승인 필요

### 5.2 2순위 — G-U-017 query 정정 또는 row 제외 (cost 0)

임대차 자료 자체 부재 — query 를 다른 의도로 변경 또는 negative=true 처리.

### 5.3 3순위 — 12 docs v2 prompt reingest (cost ~$0.5~1.5)

D2 fix 의 효과를 sample-report 외 12 docs 로 확장. 사용자 cost 승인 필요.

### 5.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | combo c P95 안정성 재측정 (HF cap 회복 후 다른 시간대) | 0.25일 | 0 | ★★ |
| 5 | numeric_lookup R@10 0.5295 진단 | 0.5일 | 0 | ★★ |
| 6 | fuzzy_memory top-1 -0.25 진단 | 0.5일 | 0 | ★ |
| 7 | chunk_filter 45.5% 마킹 분석 | 0.5일 | 0 | ★★ |
| 8 | budget_guard cache hit 무료 인식 | 0.5일 | 0 | ★ |
| 9 | S4-B 핵심 엔티티 추출 | 3일 | 0 | ★★ |
| 10 | S5 /answer UX | 1주 | 0 | ★★ |

---

## 6. 활성 한계 (다른 PC 진입 시 점검)

| # | 한계 | 영향 | 회복 절차 |
|---|---|---|---|
| 1 | **doc-size bias** (Pattern 1) | G-U-018/027 정답 doc top-50 밖 | per-doc top-K cap retrieval (1순위) |
| 2 | **HF API latency 시간대 변동** (P95 263ms~71.7s) | combo c 운영 default 채택 부적합 | HF latency 모니터링 + 다른 시간대 측정 |
| 3 | **reranker monthly cap 800/1000** | combo b/c 측정 시 degrade 발동 | 다음 달 자동 회복 또는 ENV 상향 |
| 4 | **G-U-017 임대차 query 정답 부재** | fuzzy_memory R@10 -0.083 잔존 | query 정정 (2순위) |
| 5 | **다른 12 docs v1 cache 그대로** | sample-report 외 doc 의 caption gap 회수 0 | 12 docs v2 reingest (cost) |
| 6 | **cross_doc 표본 4 row** | 통계 신뢰도 한계 (1 row = 0.25 swing) | 표본 확장 추가 (별도 sprint) |
| 7 | **G-U-015 한마음 위원회 chunks 0건** | 라벨 정정 후도 multi-doc 의도 부분만 가능 | 한마음 chunk 적재 검증 |
| 8 | **fuzzy_memory top-1 -0.25 (G-U-100~103 신규)** | 신규 라벨 정확도 검증 필요 | 라벨 재검증 sprint |
| 9 | **DEFAULT_USER_ID UUID 정합성** | reingest pipeline dedup uuid 검증 | env 가드 |
| 10 | **doc cap = all-time SUM** (24h sliding 아님) | reingest 누적 cost 영구 차단 | ENV 1회성 상향 |
| 11 | **chunk_filter 45.5% 마킹** (sample-report) | reingest 시 chunks 변동 (898→987→994) | chunk_filter 분석 sprint |
| 12 | **vision_usage_log.estimated_cost** (cost_usd 컬럼 부재) | reingest cost 자동 모니터링 시 fail | 사실 컬럼명 정정 (alias 추가) |

---

## 7. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-doc-bias | doc-size bias 완화 fix 진입 | 운영 코드 변경 + 회귀 risk + 1~2일 | 다음 sprint |
| Q-G-U-017 | G-U-017 query 정정 또는 제외 | 임대차 자료 부재 → query 변경 | 후순위 |
| Q-other-docs | 12 docs v2 prompt reingest | 사용자 명시 cost 승인 | 후순위 |
| Q-combo-c-stability | combo c P95 안정성 재측정 | HF cap 회복 후 (다음 달 자동 또는 ENV) 다른 시간대 측정 | 후순위 |
| Q-cap-policy | `JETRAG_DOC_BUDGET_USD` 운영 default | $0.10 그대로 유지 (시범 한정 0.55 까지 1회성) | 차후 |
| ~~Q-sample-expand~~ | ~~minority qtype 표본 확장~~ | **해소** — 오늘 ship |
| ~~Q-multi-doc-priority~~ | ~~multi-doc cross_doc 진단~~ | **해소** — 오늘 ship |
| ~~Q-cross-label~~ | ~~acceptable_chunks 확장 + 라벨 재검증~~ | **해소** — 오늘 ship |
| (이전 잔존) | 별도 sprint | | |

---

## 8. 참고 문서 우선순위 (5분 회복)

| # | 문서 | 5분 행동 |
|---|---|---|
| 1 | **본 문서 §0 / §3 / §5** | 마감 상태 + 측정 추이 + 다음 후보 |
| 2 | `2026-05-09 cross_doc 라벨 재검증 ship.md` | 라벨 정정 4 row + cross_doc R@10 +0.182 |
| 3 | `2026-05-09 multi-doc cross_doc retrieve 진단 ship.md` | 4 root cause 양상 분리 + Pattern 1 잔존 |
| 4 | `2026-05-09 combo c 운영 default 측정 재현.md` | combo c R@10 +0.01~0.04, P95 273배 변동, 채택 보류 |
| 5 | `2026-05-09 golden v2 minority qtype 표본 확장 ship.md` | 표본 확장 효과 (caption gap 반전, table_lookup +0.112) |
| 6 | `2026-05-09 D5 본격 + D2 보강 + 회귀 진단 종합 + 다음 진입 핸드오프.md` | 직전 종합 핸드오프 (D5/D2 누적) |
| 7 | `2026-05-06 무료유료 모델 전략 통합 plan + 다른 컴퓨터 핸드오프.md` | Master plan §6 의 6 sprint 정의 |

---

## 9. 핵심 변경 파일 목록 (이번 세션 5 commits)

### 신규 work-log 6건
- `2026-05-09 golden v2 minority qtype 표본 확장 ship.md`
- `2026-05-09 combo c 운영 default 측정 재현.md`
- `2026-05-09 multi-doc cross_doc retrieve 진단 ship.md`
- `2026-05-09 cross_doc 라벨 재검증 ship.md`
- 본 종합 핸드오프 1건

### 운영 코드 수정
- **0 건** — 이번 세션 모든 ship 이 측정 도구 + 골든셋 + work-log

### 골든셋 수정 (commit `319e33c`, `ef98e7f`)
- `evals/golden_v2.csv` — 157 → 172 row (+15 표본 확장) + 4 row 라벨 정정 (cross_doc)

### gitignored 산출물 (로컬 only, 결과 텍스트는 work-log 본문에 포함)
- `evals/results/s4_a_d4_results.md` + `s4_a_d4_raw.json` — 표본 확장 → 라벨 정정 누적 측정
- `evals/results/s3_d5_combo_c_v2_172.md` + `.json` — combo c 1차 측정 (cap 임계)
- `evals/results/s3_d5_combo_c_v2_172_capclean.md` + `.json` — combo c 2차 측정 (cap clean)
- `/tmp/diagnose_multi_doc.py` — 진단 스크립트 (일회성)

### 데이터 영향
- 0 건 (chunks / vision_page_cache 변동 없음)
- ENV 1회성 상향 (`JETRAG_DOC_BUDGET_USD=0.55`, `JETRAG_RERANKER_MONTHLY_CAP_CALLS=5000`) → unset

---

## 10. 다른 PC 진입 후 첫 명령 (즉시 실행)

```bash
# 1) 동기화 + baseline (3분)
cd Jet-Rag && git pull origin main
cd api && uv sync
DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' uv run python -m unittest discover tests
# 기대: Ran 775 tests, OK (skipped=1)

# 2) D4 측정 baseline 재현 (1분, cost 0)
DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' \
  uv run python ../evals/run_s4_a_d4_breakdown_eval.py
# 기대: R@10≈0.7264, top-1≈0.6196, n_eval=163, cross_doc R@10≈0.4738

# 3) (선택) multi-doc 진단 재실행 (cost 0)
DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' \
  uv run python /tmp/diagnose_multi_doc.py
# 기대: 7 multi-doc U-row × search top-5 추적

# 4) 1순위 진입 (doc-size bias 완화 fix)
# - multi-doc query detector 설계
# - per-doc top-K cap retrieval 구현 (api/app/routers/search.py)
# - 단위 테스트 + golden v2 회귀 검증
# - 운영 코드 변경 (사용자 승인 필수)
```

---

## 11. 한 문장 마감

> **2026-05-09 마감 (이번 세션) — 5 commits push 누적** (`319e33c` ~ `ef98e7f`). **golden v2 표본 확장 (157→172) + combo c 측정 재현 (P95 273배 변동성 발견 → 운영 default 보류) + multi-doc cross_doc 진단 + cross_doc 라벨 재검증 ship**. 단위 테스트 775 OK / 회귀 0. **caption gap 반전 (+0.013 → -0.064), caption=true R@10 +0.0838, table_lookup R@10 +0.112, cross_doc R@10 +0.182 (62%↑) / top-1 +0.250 (100%↑)**. exact_fact+caption=true cell top-1 0.818 (DoD ≥ 0.80 도달 ✅). Overall R@10 0.7264 (DoD 0.75 까지 -0.0236). 운영 코드 변경 0 (이번 세션 모든 ship). 다음 PC 진입 시 §4 절차 5분 + §5.1 의 **doc-size bias 완화 fix** (운영 코드 변경, 1~2일) — 1순위 권고.
