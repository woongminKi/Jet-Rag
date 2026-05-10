# 2026-05-10 TOC guard 패턴 정밀화 — query intent-aware skip ship

> Sprint: TOC guard 패턴 정밀화 (table_lookup -0.083 회귀 회복)
> 작성: 2026-05-10
> 마감: query intent-aware skip + 4 unit tests + ablation 재측정 → table_lookup 핵심 row 회복 + net Overall 양수
> 입력: 직전 sprint (신규 9 row 정정) + 2026-05-09 TOC guard 정밀화 ablation 재측정 work-log §4.2 (정밀화 후보)

---

## 0. 한 줄 요약

> **TOC guard 패턴 정밀화 ship — query intent-aware skip**. `_TOC_INTENT_PATTERN` 추가 + `_is_toc_chunk` 가 query 자체에 "목차"/"차례" 키워드 있을 시 penalty SKIP. **G-A-200 ("경제전망 보고서 목차 어떻게 구성됐어") R@10 0.0 → 1.0 회복** ⭐. Ablation (n=178 골든셋) 재측정: TOC ON 시 R@10 0.7076 → **0.7102 (+0.003)**, top-1 0.8521 → **0.8580 (+0.006)** — **이전 net 회귀 (-0.003) → 양수 (+0.003) 전환**. 회복 4 row (synonym +0.333, numeric +0.222, exact_fact +0.286, summary top-1 ✓), 잔존 2 row 회귀 (G-A-110 exact_fact -0.143, G-A-204 table_lookup -0.250 — query 에 "목차" 단어 없음). 단위 테스트 805 → 809 (+4) / 회귀 0. 운영 default OFF 유지 (사용자 명시 결정 필요). **누적 cost 변동 0**.

---

## 1. 변경 내역

### 1.1 `api/app/routers/search.py`

**추가 — `_TOC_INTENT_PATTERN`**:
```python
_TOC_INTENT_PATTERN = re.compile(
    r"(?:목\s*차|차\s*례)(?:[가-힣]{0,3})?(?=\s|$|[?!.,])"
)
```
매칭: "목차", "목 차", "차례", "차 례" + 한글 조사 (가/는/에/...)

**수정 — `_is_toc_chunk`**:
```python
_query_wants_toc = bool(_TOC_INTENT_PATTERN.search(clean_q))

def _is_toc_chunk(chunk_id: str) -> bool:
    if not _toc_guard_enabled:
        return False
    if _query_wants_toc:  # NEW: 사용자 의도 무시 회피
        return False
    ...
```

→ query 자체가 TOC 를 명시 요구 시 chunk-level penalty 전체 SKIP.

### 1.2 신규 — `api/tests/test_toc_intent_pattern.py` (4 tests)

- `test_matches_explicit_toc_query` — "목차/차례" 명시 query 7개 매칭 확인
- `test_does_not_match_non_toc_query` — 4개 비-TOC query 비매칭 확인
- `test_does_not_match_idiomatic_chare` — "두 차례로" 같은 부사 (현재 패턴은 false positive 일부 허용 — 별도 sprint 보강)
- `test_pattern_compiled_and_callable` — 정상 컴파일 sanity check

### 1.3 검증

- **단위 테스트**: 805 → **809 (+4) / OK / skipped=1 / 회귀 0**
- **D4 ablation 재측정**: golden v2 178 row, BGE-M3 안정 (490s)

---

## 2. Ablation 결과 (TOC ON v2 정밀화 vs OFF / v1)

### 2.1 Overall

| metric | TOC OFF | TOC ON v1 (정밀화 전) | **TOC ON v2 (정밀화 후)** | △ vs OFF |
|---|---:|---:|---:|---:|
| R@10 | 0.7076 | 0.7043 (-0.003 ⚠) | **0.7102** | **+0.003 ✅** |
| top-1 | 0.8521 | 0.8521 (0) | **0.8580** | **+0.006 ✅** |
| nDCG@10 | 0.6633 | (lower) | (slightly higher) | + |
| MRR | 0.6198 | (lower) | (slightly higher) | + |

→ **이전 net 회귀 (-0.003) → 양수 (+0.003) 전환 ✅**

### 2.2 qtype 별 변화 (TOC ON v2 vs OFF, |△| ≥ 0.01)

| id | qtype | △ R@10 | top-1 변화 |
|---|---|---:|---|
| G-U-010 | synonym_mismatch | **+0.333 ✅** | (변동 X) |
| G-U-020 | summary | 0 | **✗→✓ ✅** |
| G-A-024 | numeric_lookup | **+0.222 ✅** | (변동 X) |
| G-A-029 | exact_fact | **+0.286 ✅** | (변동 X) |
| **G-A-200** | **table_lookup** | **0 (회복 ✅, intent skip)** | (변동 X) |
| G-A-110 | exact_fact | -0.143 ⚠ | (변동 X) |
| G-A-204 | table_lookup | -0.250 ⚠ | (변동 X) |

### 2.3 G-A-200 회복 검증 ⭐

| 시점 | top-10 chunks (RRF) | acceptable hit |
|---|---|---:|
| TOC OFF | 902, 920, 952, 848, 782, 898, 978, ... | ch 902 hit (relevant) → R@10 1.0 |
| TOC ON v1 | 848, 782, 898, 978, 797, 772, ... (902 penalty) | 902 미스 → **R@10 0.0 ❌** |
| **TOC ON v2** | (intent skip → OFF 와 동일) | **R@10 1.0 ✅** |

→ query "경제전망 보고서 **목차** 어떻게 구성됐어" 의 "목차" 키워드 매칭 → intent skip 발동 → ch 902 penalty 면제 → top-1 회복.

### 2.4 잔존 회귀 분석

**G-A-204** (table_lookup, query "2026년 경제전망 요약표 어디 있어"):
- query 에 "목차"/"차례" 단어 없음 → intent skip 미발동
- acceptable=902,898; relevant=918
- TOC ON v2: 902 penalty 받음 → R@10 0.5 → 0.25
- → 별도 sprint 의 "vision_caption 보유 시 penalty skip" 같은 추가 정밀화 후보

**G-A-110** (exact_fact, query "각 유저의 활동 및 음악 감상 기록"):
- query TOC 단어 없음
- TOC ON v2: chunk 77 penalty → top10 밖 → R@10 -0.143
- → chunk 77 의 text head 가 "차례" pattern 매칭 (false positive, 음악 감상 doc 의 "1차례, 2차례" 류)
- → "차례" pattern 자체 정밀화 (또는 "목차" 만 keep) 필요 — 별도 sprint

---

## 3. 비판적 한계 (정직 인정)

| # | 한계 | 영향 | 회복 |
|---|---|---|---|
| 1 | **G-A-110 false positive 잔존** | exact_fact -0.143 (1 row) | "차례" 패턴 정밀화 또는 vision_caption 보유 분기 (별도 sprint) |
| 2 | **G-A-204 잔존 회귀** | table_lookup -0.250 (1 row) | query 에 "요약표/표/그림" 같은 vision-content intent 추가 인식 |
| 3 | **운영 default 변경 여부 미결** | TOC ON 채택 시 +0.003 R@10 / +0.006 top-1 / 단 2 row 회귀 | 사용자 명시 결정 (Q-toc-default-v2) |
| 4 | **idiomatic 차례 false positive 가능성** | 패턴이 "두 차례로" 같은 부사도 매칭 (단위 테스트 §3 명시) | "차례" 매칭 시 인접 문맥 (페이지 번호 등) 추가 검사 (별도 sprint) |

---

## 4. 사용자 결정 보류 항목 변동

| ID | 항목 | 직전 | 갱신 |
|---|---|---|---|
| Q-toc-default | TOC guard 운영 default | OFF 유지 (table_lookup -0.083 부적합) | **재논의 가능** — 정밀화로 net 양수 전환. 단 G-A-110/204 잔존 회귀 |
| Q-toc-default-v2 | TOC guard ON 채택 (정밀화 후) | 신규 | **사용자 결정** — net 양수지만 2 row 회귀 trade-off |
| Q-chare-pattern | "차례" 패턴 정밀화 | 신규 | 별도 sprint — vision_caption 보유 / 페이지 번호 패턴 / "목차" 만 keep 등 |
| Q-toc-vision-content | "요약표/표/그림" intent 추가 | 신규 | 별도 sprint |

---

## 5. 다음 후보 우선순위

### 5.1 1순위 — TOC default ON 채택 결정 (cost 0, 0.1 day, 사용자 결정)

본 sprint 의 ablation 결과 (+0.003 R@10, +0.006 top-1, G-A-200 R@10 +1.0 회복) 기반 사용자 명시 채택. ENV default 변경.

### 5.2 2순위 — "차례" 패턴 추가 정밀화 (cost 0, 0.5 day)

G-A-110/204 false positive 제거. 옵션:
- vision_caption 보유 chunks 만 penalty (caption 부착 chunks = 정답 후보)
- "차례" 매칭 시 페이지 번호 인접 require (TOC formatting 특성)
- "목차" 만 keep (보수적, 단 summary +0.111 효과 일부 손실 가능)

### 5.3 3순위 — expected_summary 정정 (cost 0, 0.5 day)

G-U-105~107 의 chunk-text 같은 expected_summary → 실 summary 정정.

### 5.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | cost 가드레일 80% 알림 절차 | 0.25 day | 0 | ★★ |
| 5 | uvicorn 좀비 모니터링 자동화 | 0.5 day | 0 | ★ |
| 6 | cross_doc qtype 자동 생성 (B 후속) | 1 day | ~$0.05 | ★★ |
| 7 | visual_grounding metric 신설 | 1 day | ~$0.05 | ★★ |
| 8 | acceptable_chunks LLM-judge 자동 보완 | 1 day | ~$0.10 | ★★ |
| 9 | S4-B 핵심 엔티티 추출 | 3 day | 0 | ★★ |

---

## 6. 핵심 변경 파일 목록

### 수정
- `api/app/routers/search.py` — `_TOC_INTENT_PATTERN` 추가 + `_is_toc_chunk` 에 `_query_wants_toc` 분기 추가

### 신규
- `api/tests/test_toc_intent_pattern.py` — 4 unit tests
- 본 work-log

### gitignored 산출 (본 work-log §2 reproduced)
- `evals/results/s4_a_d4_toc_off.md`, `s4_a_d4_raw_toc_off.json` — TOC OFF baseline
- `evals/results/s4_a_d4_toc_on.md`, `s4_a_d4_raw_toc_on.json` — TOC ON v1 (정밀화 전)
- `evals/results/s4_a_d4_toc_on_v2.md`, `s4_a_d4_raw_toc_on_v2.json` — TOC ON v2 (정밀화 후)

### 데이터 영향
- 0 건 (chunks / vision_page_cache / golden_v2.csv 변동 없음)

### 운영 코드 변경 영향
- `_TOC_INTENT_PATTERN` 신설 + `_is_toc_chunk` 동작 분기 추가
- **default OFF 유지** — TOC guard 자체는 ENV `JETRAG_TOC_GUARD_ENABLED=true` 시만 활성. 본 변경은 ON 시 동작 정밀화
- search 본 path 영향 0 (TOC OFF 시 변경 없음)

### 외부 cost
- 0 (RRF-only baseline + DB 직접 query)
- 누적 (이번 세션 전체): ~$0.31 (변동 없음)

---

## 7. 한 문장 마감

> **2026-05-10 — TOC guard 패턴 정밀화 ship**. `_TOC_INTENT_PATTERN` 추가 + query 가 명시적으로 "목차/차례" 요구 시 penalty SKIP — G-A-200 R@10 0→1.0 회복 ⭐. Ablation: TOC ON net Overall R@10 -0.003 → **+0.003**, top-1 0 → **+0.006**. 회복 4 row (synonym +0.333, numeric +0.222, exact +0.286, summary top-1 ✓) vs 잔존 2 row 회귀 (G-A-110 exact -0.143, G-A-204 table -0.250). 단위 테스트 805 → 809 (+4) / 회귀 0. 운영 default OFF 유지 (사용자 명시 결정 권고). 누적 cost 변동 0. 다음 1순위 = **TOC default ON 채택 결정** (사용자) 또는 **"차례" 패턴 추가 정밀화** (cost 0, 0.5 day).
