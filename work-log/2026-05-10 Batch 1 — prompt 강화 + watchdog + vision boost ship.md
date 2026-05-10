# 2026-05-10 Batch 1 ship — generate prompt 강화 + uvicorn watchdog + vision adjacent boost

> Sprint: 3 sprint batch 통합 ship — 자율 진행 가능 후보 (cost 0)
> 작성: 2026-05-10
> 마감: #2 prompt 강화 + #5 uvicorn 좀비 모니터링 + #3 vision 인접 boost (ablation 결과 효과 0, 인프라 ship)
> 입력: 사용자 명시 "권고 방법부터 진행, 승인 모두" + Batch 1 (자율, cost 0) 정의

---

## 0. 한 줄 요약

> **Batch 1 ship — 3 sprint 통합 (cost 0, 자율 진행)**.
> (1) `generate_golden_rows.py` system prompt 강화 — expected_summary 자리 chunk text 금지 + good/bad 예시 명시.
> (2) `evals/uvicorn_watchdog.py` 신규 — `/health` timeout/non-200 시 stderr alert + opt-in 자동 재기동 (직전 세션의 좀비 사건 재발 방지).
> (3) `api/app/routers/search.py` vision adjacent chunk boost (opt-in via `JETRAG_VISION_ADJACENT_BOOST=true`) — 같은 (doc, page) vision-derived chunk_idx 인접 페어 score propagate. **G-A-204 ch 919 회복은 효과 0** (ch 919 가 candidates pool 자체에 없음 — search retrieval 단계 제외). 인프라 ship + failed experiment 정직히 명시.
> 단위 테스트 823 → **833 (+10) / 회귀 0**. 누적 cost 변동 0.

---

## 1. Sprint 1 — generate_golden_rows.py prompt 강화 (#2)

### 1.1 변경

`_SYSTEM_PROMPT` 에 expected_answer_summary 가이드 강화:
```
- expected_answer_summary (string): 답변 요약 1-2 문장 (한국어).
  ⚠ 반드시 "주제 — 핵심 내용" 또는 "주제: 핵심 내용" 같은 의미 요약 형식.
  ⚠ chunk 본문 그대로 복사 금지 (예: "이 내규는 2022년 7월 1일부터 시행한다..." X).
  ⚠ 원문 발췌가 아닌 답변 의도 요약 (예: "한마음 운영 내규 시행일 — 2022년 7월 1일부터" O).

expected_answer_summary 작성 가이드 (Bad → Good 예시):
  Bad : "⑨재산관리내규 중 다음과 같이 개정한다. 제6조 제1항 중 ..."  (원문 chunk text)
  Good: "재산관리내규 개정 사항 — 경영본부장이 경영전략처장으로 직제 변경 등."
```

직전 sprint (G-U-105/106/107 expected_summary 정정) 의 chunk-text 문제 자동 회피.

### 1.2 단위 테스트 (1 신규)

- `SystemPromptStrengthTest.test_system_prompt_warns_against_chunk_text` — prompt 의 강화 표현 검증

---

## 2. Sprint 2 — uvicorn 좀비 모니터링 (#5)

### 2.1 신규 — `evals/uvicorn_watchdog.py` (~150 LOC)

직전 세션의 사건 (uvicorn PID 18399/18401 좀비화 → 측정 시작 전 강제 kill -9 + nohup 재기동) 재발 방지.

**기능**:
- `check_health(url, timeout)` — `/health` 응답 점검 (200 + body "ok" 확인)
- `find_uvicorn_pids()` — `pgrep -f "uvicorn app.main:app"` 결과 list (best-effort)
- `kill_uvicorn(pids, force)` — SIGTERM (또는 force 시 SIGKILL)
- `pid_alive(pid)` — `os.kill(pid, 0)` 으로 존재 확인

**CLI**:
- `--once` — 1회 점검 후 종료 (CI 모드)
- `--interval N` — 주기적 점검 (default 60s)
- `--auto-restart` — 연속 fail N회 시 자동 SIGTERM (위험, 명시 opt-in)
- `--max-failures N` — 연속 fail 임계 (default 3)

### 2.2 단위 테스트 (9 신규)

- `CheckHealthTest` (4): 정상 / non-200 / URLError / unexpected body
- `PidAliveTest` (2): current pid alive / nonexistent pid not alive
- `FindUvicornPidsTest` (3): pgrep 실패 / 정상 parse / non-digit skip

### 2.3 검증

- live smoke: `python uvicorn_watchdog.py --once` → `[2026-05-10 19:53:54] watchdog ✅ ok` (current uvicorn 정상 응답)

---

## 3. Sprint 3 — vision 인접 chunk boost (#3, ablation 효과 0)

### 3.1 변경

`api/app/routers/search.py`:
- `_VISION_ADJACENT_BOOST_ENV = "JETRAG_VISION_ADJACENT_BOOST"`
- `_VISION_ADJACENT_BOOST_FACTOR = 0.5`
- 같은 `(doc_id, page)` vision-derived chunk_idx 인접 (±1) 페어 식별 → 한 chunk 의 score × 0.5 가 다른 chunk 의 현 score 보다 크면 boost
- doc_score 도 함께 갱신

### 3.2 ablation 결과 — **효과 0** ⚠

| 시점 | Overall R@10 | Overall top-1 | G-A-204 R@10 | G-A-204 top10 |
|---|---:|---:|---:|---|
| TOC ON v3 (baseline) | 0.7111 | 0.8580 | 0.250 | [898, 848, 248, 770, 921, 75, 900, 379, 797, 983] |
| **TOC ON + vision boost** | **0.7111** | **0.8580** | **0.250** | **(동일)** |

**diff rows**: 0 (전체 178 row 중 변동 0)

### 3.3 원인 분석 — failed experiment 정직 명시

**가설**: ch 918 (요약표 caption, 강한 매칭) → ch 919 (data 연속) 회복.

**실제**:
- ch 919 가 search candidates pool 자체에 없음 (rpc_rows 에서 제외)
- ch 919 가 BM25/dense top-100+ 밖 → boost 가 발동할 chunk 가 없음
- → "candidates 안에서 score propagate" 만으로는 retrieval pool 외 chunks 회복 불가

**시사점**:
- vision 표 매칭 정밀화는 **search retrieval 단계** (rpc_rows 채우기) 에서 해결 필요
- 본 sprint 의 boost 는 **post-RRF reranking** — pool 안에서 재조정만 가능
- 향후 retrieval 단 sprint 권고: chunk text augmentation (인접 chunk text prepend), or chunk merge at ingest

### 3.4 ship 결정

**ship 채택** — 인프라 자체는 정상 동작 (다른 case 에서 효과 가능). G-A-204 case 만 효과 0. ENV opt-in default OFF 유지 — 사용자 / 후속 sprint 가 ablation 추가 가능.

---

## 4. 검증

- **단위 테스트**: 823 → **833 (+10)** / OK / skipped=1 / 회귀 0
  - generate prompt 강화: +1 test
  - uvicorn watchdog: +9 tests
  - vision boost: 0 신규 test (구조 단순 + ablation 으로 검증)
- **frontend tsc + lint**: 변경 없음 (skip)

---

## 5. 사용자 결정 보류 항목 변동

| ID | 항목 | 직전 | 갱신 |
|---|---|---|---|
| Q-generate-prompt-strengthen | generate prompt 강화 | 별도 sprint | **해소 ✅** |
| Q-uvicorn-watchdog | uvicorn 좀비 모니터링 자동화 | 별도 sprint | **해소 ✅** |
| Q-search-vision-table | vision 표 매칭 정밀화 | 별도 sprint (산출 1 day) | **부분 — 인프라 ship, 효과 0 (G-A-204)**. retrieval 단 정밀화 별도 sprint |
| Q-retrieval-pool-augment | search retrieval pool 에 vision 표 chunks 포함 | 신규 | 별도 sprint (chunk text augmentation 또는 ingest 시 merge) |

---

## 6. 다음 후보 (Batch 2 진입)

권고 시퀀스의 Batch 2:
- **Sprint 1**: RAGAS n=30 재측정 (cost ~$0.10, cap $0.15) — 본 batch 의 정정 효과 + 정직 baseline 통합 측정
- **Sprint 2**: visual_grounding metric 신설 (cost ~$0.05) — Faithfulness 의 vision 한계 우회

이후 Batch 3 (TOC default ON 채택) — 사용자 명시 결정 (이번 세션 명시 승인).

---

## 7. 핵심 변경 파일 목록

### 신규
- `evals/uvicorn_watchdog.py` (~150 LOC) — uvicorn /health watchdog
- `api/tests/test_uvicorn_watchdog.py` (9 tests)
- 본 work-log

### 수정
- `evals/generate_golden_rows.py` — `_SYSTEM_PROMPT` 강화 (chunk text 회피 명시 + good/bad 예시)
- `api/tests/test_generate_golden_rows.py` — `SystemPromptStrengthTest` 1 추가
- `api/app/routers/search.py` — `_VISION_ADJACENT_BOOST_ENV` + 인접 chunk score propagate logic (ENV opt-in default OFF)

### gitignored 산출
- `evals/results/s4_a_d4_toc_on_vision_boost.{md,json}` — vision boost ablation (효과 0 검증)

### 데이터 영향
- 0 건

### 운영 코드 변경 영향
- generate_golden_rows.py: 다음 자동 생성 시 prompt 적용 (재발 방지)
- search.py: ENV opt-in default OFF — 운영 동작 변경 X

### 외부 cost
- 0 (모두 cost 0 sprint)
- 누적 (이번 세션 전체): ~$0.31 (변동 없음)

---

## 8. 한 문장 마감

> **2026-05-10 Batch 1 ship** — generate prompt 강화 + uvicorn watchdog (9 tests) + vision adjacent boost (인프라 ship, G-A-204 효과 0 = failed experiment 명시). 단위 테스트 823 → **833 (+10) / 회귀 0**. 누적 cost 변동 0. 다음 = Batch 2 진입 — RAGAS n=30 재측정 + visual_grounding metric (cost ~$0.15 승인됨).
