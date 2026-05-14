# 2026-05-14 P4 — Hard-delete 4 doc + golden_v2 47 row 정리

> 프로젝트: Jet-Rag / 세션: 2026-05-14 추가 / 후속 PRD scope 밖, 운영 위생

## 1. 한 줄 요약

- 사용자 요청으로 4 documents (승인글 템플릿1·승인글 템플릿3·sonata-the-edge_catalog·포트폴리오_이한주 - na Lu) **soft-delete → hard-delete + Storage cleanup** 진행.
- 그 부수 효과로 **golden_v2 의 47 row (25.8%) 가 invalid expected_doc_id 를 참조** → 평가 분모 부풀림 위험 → 47 row 제거 (182→135).
- 위생 잡일 2건 (`golden_batch_smoke.py` API_BASE env override + `bak.20260513` untrack) 함께 정리.
- 단위 테스트 1167 통과 (회귀 0). 2 commit push.

---

## 2. 본 세션 commit 흐름 (2 commit, push 완료)

| # | commit | 한 일 |
|---|---|---|
| 1 | `95ed745` | **P4 본체** — golden_v2.csv 182→135 row + 단위 테스트 3 assert 갱신 |
| 2 | `8cd8be7` | **위생** — golden_batch_smoke env override + bak.20260513 untrack |

→ 현 main HEAD = **`8cd8be7`** (origin push 완료).

---

## 3. 변경 흐름

### 3.1 Phase 1 — Soft-delete (지난 step)

```text
documents.deleted_at = 2026-05-14T12:57:53Z (4건)
→ active documents 13 → 9
→ /search, /documents, /stats 에서 즉시 사라짐
→ chunks/vision_chunks/entities row 는 DB 에 남아있지만 query time 필터로 숨김
```

### 3.2 Phase 2 — Hard-delete + Storage cleanup

```text
Step 1: child tables (child-first FK 회피)
  answer_ragas_evals  → 2 → 0
  chunks              → 460 → 0
  ingest_jobs         → 15 → 0

Step 2: Storage 파일 (bucket=documents)
  920fb380...docx (216KB)
  a93b6ba9...docx (66KB)
  330fb897...pdf  (5MB)
  5789295b...pdf  (34MB) ← 가장 무거움
  → 총 39MB 해제

Step 3: documents row hard-delete
  documents → 4 → 0 (soft-deleted row 도 제거)

Step 4: 최종 확인
  active documents: 9 / total documents row: 9
  chunks: 2469 → 2009 (effective 1710 → 1274)
```

### 3.3 Phase 3 — P4 본체 (golden_v2 row 정리)

```text
삭제된 4 doc_id 참조 row 47건 (25.8%) 제거
182 → 135 row
  query_type 분포:
    exact_fact      115 → 76 (-39)
    fuzzy_memory      9 → 7  (-2)
    table_lookup     12 → 9  (-3)
    vision_diagram    8 → 6  (-2)
    summary           9 → 8  (-1)
    numeric_lookup    7 → 7  (0)
    synonym_mismatch  8 → 8  (0)
    cross_doc         9 → 9  (0)
    out_of_scope      5 → 5  (0)
  caption_dependent:
    true  31 → 25 (-6)
    false 151 → 110 (-41)
```

- backup: `evals/golden_v2.csv.bak.20260514` (gitignore 패턴 적용)
- 단위 테스트 갱신 (`api/tests/test_run_s4_a_d4_compose_off.py`):
  - `n_rows == 135` (was 182)
  - `caption_counts.true == 25`, `caption_counts.false == 110`
  - `load_golden_rows(...) -> 135 / true 25 / false 110`

### 3.4 Phase 4 — 위생 묶음

```text
api/scripts/golden_batch_smoke.py:
  _BASE = "http://localhost:8000"  (하드코딩)
  ↓
  _BASE = os.environ.get("JETRAG_API_BASE_URL", "http://localhost:8000").rstrip("/")
  → 8001 등 alt port 운영 시 env override 가능 (다른 eval 스크립트 정합)

evals/golden_v2.csv.bak.20260513:
  .gitignore 에 evals/golden_v2.csv.bak.* 패턴 있지만 historical tracked → git rm --cached
  디스크 파일 보존 (백업 가치 유지)
```

---

## 4. 검증

```
$ uv run python -m unittest discover
Ran 1167 tests in 26.076s
OK (skipped=1)
```

- M2 W-4 / KPI eval 기준 단위 테스트 1167 통과 (회귀 0).
- 갱신된 3 assert: `test_run_s4_a_d4_compose_off.py` 의 `GoldenV2SchemaTests` 3건.

---

## 5. 영향 분석 (다음 세션 / 평가 시점 의식)

### 5.1 향후 R@10 평가
- **분모 정합성 회복**: P4 이전이면 47 row 가 무조건 hit 0 → R@10 분모 25.8% 부풀림.
- **표본 N 감소 (182→135)**: 통계 신뢰도 약간 손실 (qtype 별로는 cross_doc/numeric/synonym/out_of_scope 4종 그대로 유지).
- M2 W-4 측정값 (top-1 0.7966) 은 이전 분모 기준 — 새 분모 (135) 로 재측정 시 변동 가능.

### 5.2 본 PRD KPI 8개 영향
| KPI | 영향 |
|---|---|
| #6 ① R@10 (0.6738) | 재측정 권고 — 새 분모 135 로 정직 측정 시 약간 상승 예상 (false-negative 분모 빠짐) |
| #7 hybrid (DECISION-13) | qtype 별 top-1 측정 → exact_fact 76건 / table_lookup 9건 / numeric_lookup 7건 기준 재측정 가능 |
| #4·#5·#9 RAGAS | n=30 sampling 이라 별도 영향 없음 (stratified resample 시 의식 필요) |
| #8 출처 일치율 (수동) | 47 row 제외 → 표본 풀 축소, 사용자 검수 시 95% 게이트 유효 |
| #9 환각률 보강 (수동) | 동일 |
| #10·#11 | golden 무관 |

### 5.3 골든 v3 확장 (v1.5 영역)
- N 135 → 250+ 목표 시 4 doc 빠진 만큼 다른 active 9 doc 표본 확장 또는 새 doc 추가 필요.
- 분포 시정 (qtype·doc_type 균형) 함께 추진 — DECISION-10 path.

### 5.4 코드 자산 (재사용)
- `/tmp/jetrag_filter_golden_v2.py` — golden_v2 row 필터 헬퍼 (CSV-aware, BOM 보존, 일회용 후 삭제)
- `/tmp/jetrag_hard_delete_4docs.py` — child-first cascade + Storage remove + documents hard-delete 헬퍼 (일회용 후 삭제)

---

## 6. 잔여 (다음 세션 후보)

### 6.1 PRD scope 안 사용자 수동 (work-log §6.1 그대로)
- #8 출처 일치율 50건 — 표본 풀 135 row 기준 재검수 (~1~2h, $0)
- #9 환각률 보강 20건 (~1h, $0)
- #6 ② Ragas Context Recall (옵션, paid $0.05~0.15)

### 6.2 별도 트랙 (DECISION-12)
- 인제스트 KPI #1·#2·#3 — 벤치셋 30개 별도 sprint

### 6.3 v1.5 영역 (§6.3)
- golden_v3 확장 (135→250+, 분포 시정)
- self-host BGE-M3 + sparse·ColBERT
- G-U-022 류 retriever 회수 약점 fix
- HF self-host (#10·#11 직접 영향)
- vision_page_cache 적극 활용
- US-05 인수인계 추림 / US-10 주제별 타임라인

### 6.4 남은 운영 위생
- `.env` 의 `JETRAG_DOC_BUDGET_USD` / `JETRAG_DAILY_BUDGET_USD` default 복귀 (~5분, 사용자 수동)
- `ingest_jobs` failed 3건 → hard-delete 로 일부 해소됨, 잔여 확인 권고

---

## 7. 인용 / 참조

- 본 세션 commit: `95ed745` (P4) · `8cd8be7` (위생)
- 직전 핸드오프: `7140d71` (`2026-05-14 세션 종합 — M3 자동 측정 마감 + v1.4 핸드오프.md`)
- PRD master: `work-log/2026-05-12 검색 정확도 80% 달성 PRD.md` **v1.4**
- Living spec: `work-log/검색 파이프라인 동작 명세 (living).md` **v0.4**
- 영향받은 테스트: `api/tests/test_run_s4_a_d4_compose_off.py`
- 영향받은 데이터: `evals/golden_v2.csv` (182→135), backup `evals/golden_v2.csv.bak.20260514` (gitignore)

---

## 8. 다음 세션 시작 시 빠른 확인

```bash
# 1) 환경
cd /Users/kiwoongmin/Desktop/piLab/Jet-Rag
git log --oneline -3
# 8cd8be7 chore(repo): 위생 — golden_batch_smoke ...
# 95ed745 fix(evals): P4 — hard-deleted 4 doc 참조 row 47건 제거 ...
# 7140d71 docs(work-log): 2026-05-14 세션 종합 — M3 자동 측정 마감 + v1.4 핸드오프

# 2) 현재 corpus
curl -s http://localhost:8001/stats | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['documents'])"
# active 9 documents, by_type pdf 5 / hwpx 2 / pptx 1 / hwp 1

# 3) golden_v2
wc -l evals/golden_v2.csv  # 136 (header + 135 data)
```
