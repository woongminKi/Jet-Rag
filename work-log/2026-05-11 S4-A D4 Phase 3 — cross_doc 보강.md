# 2026-05-11 — S4-A D4 Phase 3: cross_doc qtype 보강

> 프로젝트: Jet-Rag
> 작성: 2026-05-11
> 목적: cross_doc qtype 표본을 n=4 → n=8 로 확장하여 §1 qtype breakdown 의 신뢰도 회복 + G-A-075 의 잘못된 cross_doc 라벨 정정.

---

## 0. 한 줄 요약

> **cross_doc 5건 신규 추가 + G-A-075 → exact_fact 정정**. `golden_v2.csv` 157 → 162 row (cross_doc 4 → 8). build 단계 hook 그대로 통과 (`stale_doc_id_count=0`). 단위 테스트 3건 (n_rows 157 → 162, caption_false 139 → 144) hardcoded 값만 업데이트 / 회귀 0.

> **(close)** 통합 sprint Phase 0~3 의 일부로 ship 완료 — 종합 핸드오프 (`2026-05-11 종합 + 2026-05-12 진입 핸드오프.md`) 참조.

---

## 1. 작업 배경

Phase 0~2 ship 후 §4.5 deferred caveat G3 ("n ≤ 4 qtype 신뢰도 — cross_doc / vision_diagram / synonym_mismatch") 에서 cross_doc 표본 우선 확장 권고. 기존 cross_doc 4건 (G-U-015, G-U-031, G-U-032, G-A-075) 의 R@10 = 0.1667 은 신뢰구간 매우 큼.

추가로 G-A-075 ("금성과 지구의 환경 차이 원인은?") 는 단일 doc query 임에도 cross_doc 으로 라벨링됨 — title `승인글 템플릿1` (단일) / doc_id 부착 — 명백한 라벨 오류.

---

## 2. 작업 범위

### 2.1 산출물

| # | 파일 | 변경 |
|---|---|---|
| 1 | `evals/golden_v2.csv` | cross_doc 신규 5건 추가 (G-A-124~G-A-128) + G-A-075 query_type cross_doc → exact_fact 정정 |
| 2 | `api/tests/test_run_s4_a_d4_breakdown.py` | n_rows 157 → 162, caption_false 139 → 144 (3 테스트 hardcoded 정수 갱신) |
| 3 | `evals/_phase3_chunks_probe.py` | **일회용** — 후보 doc 의 chunks dump 도구 (재실행 보존, 다음 phase 재사용) |
| 4 | `evals/_phase3_append_rows.py` | **일회용** — golden_v2.csv 끝에 row 5건 안전 append (CRLF / BOM 유지) |
| 5 | `evals/_phase3_validate_v2.py` | **일회용** — v2.csv 의 doc_id 무결성 1회 검증 |
| 6 | `evals/results/s4_a_d4_results.md` | §0 / §1 / §4.5 G3 deferred caveat 갱신 |

### 2.2 신규 cross_doc row 5건

| id | query | doc_pair | relevant_chunks | doc_type |
|---|---|---|---|---|
| G-A-124 | 운영내규와 직제규정에서 개정 절차가 어떻게 다른가요? | 한마음 운영내규 \| 직제규정 | 22, 58 | hwpx |
| G-A-125 | 데이터센터 안내서와 보건의료 빅데이터 자료에서 예산 규모가 어떻게 다른가요? | 데이터센터 안내서 \| 보건의료 빅데이터 | 15, 9 | pdf |
| G-A-126 | 기웅민 이력서와 이한주 포트폴리오의 핵심 역량은 어떻게 다른가요? | 기웅민 이력서 \| 이한주 포트폴리오 | 7, 11 | pdf |
| G-A-127 | 승인글 템플릿1과 템플릿3은 어떤 주제를 다루고 있나요? | 템플릿1 \| 템플릿3 | 0, 0 | docx |
| G-A-128 | law sample2와 law sample3 두 판결에서 대법원이 내린 결정은 무엇인가요? | law sample2 \| law sample3 | 10, 13 | hwp |

#### relevant_chunks 식별 근거 (Supabase chunks 조회 결과)

각 doc 의 답 chunk 는 `evals/_phase3_chunks_probe.py` 로 dump 한 `_phase3_dumps/*.tsv` 에서 수동 식별:

- **G-A-124**:
  - 한마음 운영내규 chunk 22 — 제13조 (운영시간 변경 시 사전 고지 절차)
  - 직제규정 chunk 58 — 제3조 (직제 개편 시 이사회 의결 + 정원 변경 시 시장 승인)
- **G-A-125**:
  - 데이터센터 안내서 chunk 15 — "분야별 과제당 예산" 표 시작 (테스트베드 최대 25.33억 등)
  - 보건의료 chunk 9 — "【2018년사업예산개요】 플랫폼 46억 / 네트워크 24억"
- **G-A-126**:
  - 기웅민 이력서 chunk 7 — 기술 스택 (React/Node.js/TypeScript 등) + 자기소개 (개발자 정체성)
  - 이한주 포트폴리오 chunk 11 — 자기소개 + 강점 (효율적 업무툴, ESTJ, 책임감) + PM 정체성
- **G-A-127**:
  - 템플릿1 chunk 0 — "승인글 카테고리 50개 모아두는 공간 / 선택한 주제: 태양계 구조와 특징"
  - 템플릿3 chunk 0 — "최종 선택 완료! 선택한 주제: 삼국시대 정치 구조"
- **G-A-128**:
  - law sample2 chunk 10 — "원심판결 중 제1·2 예비적 청구에 관한 원고 패소 부분을 각 파기 / 환송"
  - law sample3 chunk 13 — "원심판결 중 주위적 청구에 관한 원고 패소 부분을 파기 / 환송"

### 2.3 G-A-075 정정 — 옵션 (a) qtype exact_fact 변경

- **현황**: query="금성과 지구의 환경 차이 원인은?" / doc_id=88920c9e (단일 doc) / title=승인글 템플릿1 (단일 doc) / query_type=cross_doc
- **판정**: 단일 doc query 임이 명확 — cross_doc 라벨 잘못
- **선택 옵션**: (a) qtype → exact_fact (단일 doc query 본질 보존, 측정 row 수 손실 0)
- **선택 사유**:
  - (b) 제거는 측정 row 수 -1 (157 → 156) 로 다른 측정 baseline 비교 불가
  - (c) 그대로는 cross_doc subset 의 noise 추가 — 의도 보존 우선

---

## 3. 검증

### 3.1 무결성 검증 (`_phase3_validate_v2.py`)

```bash
cd api && uv run python ../evals/_phase3_validate_v2.py
# [INFO] 활성 doc 13건
# [STATS] cross_doc=12 / single_doc=145 / negative=5
# [STATS] stale_doc_id_count=0
# [OK] 무결성 검증 통과 (stale_doc_id_count=0)
```

- `cross_doc=12` 는 `|` 분리 title 기준 (query_type 무시) — query_type=summary/exact_fact 인 G-U-026/027/G-A-075 정정 후도 title 에 `|` 있어 포함. 본 phase 의 query_type=cross_doc 신규 row 는 8건 (= 4 + 5 - 1).
- cross_doc row 는 doc_id 컬럼 비어 stale 검증 skip — 의도된 동작.

### 3.2 단위 테스트 — 778 → 778 (회귀 0)

```bash
cd api && uv run python -m unittest discover -s tests -p "test_*.py" -t .
# Ran 778 tests in 15.783s — OK
```

- 변경: `test_validate_golden_v2_schema_returns_expected_columns` n_rows 157 → 162
- 변경: `test_caption_dependent_18_true_144_false` (이름 변경) caption_false 139 → 144
- 변경: `test_load_golden_rows_parses_caption_bool` len 157 → 162, caption_false 139 → 144
- **회귀 0** — 다른 테스트 영향 없음.

### 3.3 재측정 — `run_s4_a_d4_breakdown.py --goldenset v2`

(측정 결과는 §4 참조)

---

## 4. 재측정 결과

측정 도구: `evals/run_s4_a_d4_breakdown.py --goldenset v2`
측정 시간: 167.3s (1.03s/row 평균, 162 row)
실행: `uv run --project api python evals/run_s4_a_d4_breakdown.py ...`

| metric | 정정 전 (n=157, cross_doc=4) | Phase 3 후 (n=162, cross_doc=8) | Δ |
|---|---:|---:|---:|
| 전체 R@10 | 0.6547 | **0.6368** | **-0.0179 (임계 -0.03pp 안쪽 = 통과)** |
| 전체 R@5 | 0.5941 | 0.5782 | -0.0159 |
| 전체 MRR | 0.5502 | 0.5346 | -0.0156 |
| 전체 fail | 1 | 1 | **0 (G-N-004 의도된 fail 1건 그대로)** |
| 전체 n_eval | 150 | 155 | +5 |
| cross_doc n_eval | 4 | **8** | **+4 (목표 8~10 충족)** |
| cross_doc R@10 | 0.1667 | **0.0625** | -0.1042 |
| cross_doc R@5 | 0.1250 | 0.0625 | -0.0625 |
| cross_doc MRR | 0.1250 | 0.0417 | -0.0833 |
| exact_fact n_eval | 113 | 114 (G-A-075 합류) | +1 |
| exact_fact R@10 | 0.6894 | 0.6892 | -0.0002 (microscopic) |
| caption=true | 18 (n_eval 17) R@10=0.6119 | 18 (n_eval 17) R@10=0.6119 | 0 (cross_doc 5건 모두 caption=false) |
| caption=false n_total | 139 | 144 | +5 (cross_doc 5건 합류) |
| caption=false R@10 | 0.6602 | 0.6399 | -0.0203 |

### 회귀 임계 판정

- 전체 R@10 -0.0179pp < 임계 -0.03pp → **통과**.
- fail 1건 그대로 — 신규 row 가 모두 R@K 측정 가능 (acceptable_chunks 자동 채움 포함).
- cross_doc R@10 더 떨어짐 (-0.1042pp) — 표본 4건 → 8건 확장 후 더 robust, 어제 핸드오프 §4.1 "RRF only 의 한계 일관" 결론이 표본 확장 후 강화. cross_doc qtype 본질 난이도가 hybrid + reranker 만으로는 극복 어려움.

### 해석

1. **cross_doc 의 본질 난이도 확정**: n=4 R@10=0.1667 → n=8 R@10=0.0625 — 표본 4배 확장 후 더 낮은 평균. 두 doc 의 답 chunk 가 각각 top-K 안에 들어와야 하는 cross_doc 의 구조적 어려움 + RRF 만으로는 multi-hop 의도 분해 불가능. **S4-B 의 query decomposition (S3 D3 deferred) 또는 multi-hop retrieval 검토 우선순위 높음**.
2. **exact_fact 거의 무변동** (0.6894 → 0.6892, G-A-075 합류 1건만) — single doc query 의 측정 안정성 확인.
3. **전체 R@10 회귀는 분포 효과**: 검색 path 변경 0, 신규 cross_doc 5건의 R@10 평균 (~0.06) 이 전체 평균 (~0.65) 보다 낮아 합류로 평균이 끌어내려짐. 운영 회귀 아님.

---

## 5. 남은 이슈

- **cross_doc R@10 0.0625 의 절대값 낮음**: hybrid + reranker 만으로는 한계. 본 phase 는 표본 신뢰도 회복까지로 한정 — 검색 path 개선은 S4-B (decomposition / multi-hop) 별도 phase.
- **vision_diagram (n=3) / synonym_mismatch (n=4) 표본 부족 잔존**: §4.5 deferred caveat 에서 cross_doc 만 제외, 나머지 2 qtype 은 잔존. 표본 확장은 우선순위 낮음 (caption 합성 측정 등 후속에서 자연 합류).
- **prompt v2 라벨 0건 (v1) 유지**: D5 reingest 가 본 차원 데이터 갱신 필수 (Phase 4 진입 시).
- **measurement framework 유지**: `run_s4_a_d4_breakdown.py` 의 RRF + reranker default — Phase 4 D5 reingest 도 동일 도구로 일관성 보존.

---

## 6. 다음 스코프 — Phase 4 D5 reingest 진입 가부

**판정**: **Phase 4 진입 권고** (회귀 임계 통과 — 전체 R@10 -0.0179pp < -0.03pp / fail 1건 그대로).

- D5 reingest 는 6 doc 의 vision_enrich + chunk.text 합성 재실행으로 `vision_page_cache.prompt_version=v1` 베이스라인 확보 — claim C ("prompt v2 vs v1 동일 doc 비교") 정량 측정 가능.
- 본 phase 의 cross_doc n=8 baseline 은 Phase 4 (D5 reingest) 측정의 회귀 기준점으로 활용.
- D5 진입 전 사용자 승인 필요 (의뢰서 "Phase 4 D5 reingest 자체 진입 금지" 명시) — 측정 framework 변경 없이 reingest 만 실행하는 단순 trigger.

---

## 7. 파일 변경 요약

```
evals/golden_v2.csv                                    +5 -0 (row 추가)
                                                       +1 -1 (G-A-075 qtype 정정)
evals/_phase3_chunks_probe.py                          신규 (도구 — 일회용 보존)
evals/_phase3_append_rows.py                           신규 (도구 — 일회용 보존)
evals/_phase3_validate_v2.py                           신규 (도구 — 일회용 보존)
api/tests/test_run_s4_a_d4_breakdown.py                3 테스트 hardcoded 정수 갱신
evals/results/s4_a_d4_results.md                       §0 / §1 / §4.5 갱신
work-log/2026-05-11 ... Phase 3 cross_doc 보강.md     본 문서
```

---

## 8. 금지 사항 준수

- 임의 커밋 X (사용자 명시 요청 없음)
- 새 패키지 설치 X (Pillow / supabase-py 등 기존 venv 활용)
- chunks DB 변경 X
- vision_page_cache DB 변경 X
- Phase 4 D5 reingest 자체 진입 X
- Phase 2 hook 비활성화 X (default OFF 유지, 본 phase 는 validate 도구로 1회 점검만)
