# 2026-05-10 — S4-A D4 ship: golden_v2 R@10 3축 breakdown 측정 도구

> 프로젝트: Jet-Rag
> 작성: 2026-05-10
> 목적: S4-A D3 Phase 1 으로 보강된 골든셋 v2 (157 row × 14 컬럼) 을 입력으로,
> (1) qtype 9종 (2) caption_dependent true/false (3) prompt_version v1/v2
> 3축 R@10 / R@5 / MRR breakdown 정량 측정 + 도구 ship + 단위 테스트.

---

## 0. 한 줄 요약

> **S4-A D4 ship 완료** — `evals/run_s4_a_d4_breakdown.py` (672 LOC) +
> 단위 테스트 17건 + golden_v2 측정 결과 ship. 단위 테스트 **746 → 763 /
> skipped 1 / 회귀 0 (+17)**.
>
> **2026-05-11 업데이트 (doc_id 버그 fix 후 재측정)**: v2 R@10=**0.6547** (정정
> 전 0.6557 대비 -0.001pp 微小) / fail **11→1** (G-N-004 의도된 out_of_scope
> 1건만 잔존). v1 baseline R@10=0.6819 (정정 전 0.6856 대비 -0.004pp 微小).
> 본 도구 같은 stack 기준 v1→v2 차이 **-0.0272pp** (이전 -0.0299pp 와 거의 동일).
> caption_dependent delta **정정 후 반전**: R@10 -0.0483 (이전 +0.0123) /
> R@5 +0.0531 (이전 +0.1114). prompt v1 라벨 0건 유지 — D5 reingest 필수.

---

## 1. 작업 범위

### 1.1 산출물 4건

| # | 파일 | 종류 | LOC |
|---|---|---|---:|
| 1 | `evals/run_s4_a_d4_breakdown.py` | 측정 도구 신규 | 672 |
| 2 | `api/tests/test_run_s4_a_d4_breakdown.py` | 단위 테스트 신규 | 296 |
| 3 | `evals/results/s4_a_d4_results.md` | 측정 결과 markdown | 55 |
| 4 | `evals/results/s4_a_d4_raw.json` | per-query raw | (json) |

도구 신규 1건 + 테스트 신규 1건 + 결과 산출 2건. **운영 코드(`api/app/` 하위) 변경 0**.

### 1.2 설계 원칙

- **검색 path 변경 0** — S4-A D2 chunk.text 합성 적용 상태에서 측정. mode=hybrid /
  RRF default / reranker default ENV.
- **외부 API cost 0** — Supabase 에 이미 적재된 BGE-M3 dense + sparse embedding
  활용. Gemini / OpenAI / Vision 호출 0. HF embed_query 만 search() 내부에서 호출
  (무료 티어).
- **재사용 우선** — `run_s3_d5_search_stack_eval.py` 의 `_pick_target_item` / chunk
  정렬 / retrieval_metrics 패턴 그대로 차용.
- **prompt_version hook** — `vision_page_cache` 의 `sha256 → documents.id` join 으로
  doc_id 별 majority prompt_version 라벨링. 단위 테스트는 cache_rows mock 주입으로
  DB 호출 0.

### 1.3 실행 명령

```bash
cd api
uv run python ../evals/run_s4_a_d4_breakdown.py --goldenset v2
# 출력:
#   evals/results/s4_a_d4_results.md
#   evals/results/s4_a_d4_raw.json

# 측정 시간: 83.6s (157 row, 0.53s/row 평균)
```

---

## 2. 측정 결과

### 2.1 전체 요약

**2026-05-11 정정 후 (현재 유효):**

| label | n_total | n_eval | R@10 | R@5 | MRR | fail |
|---|---:|---:|---:|---:|---:|---:|
| v2 (전체) | 157 | 150 | **0.6547** | 0.5941 | 0.5502 | 1 |
| v1 baseline (전체) | 157 | 123 | 0.6819 | 0.6297 | 0.5844 | 1 |
| **본 도구 동일 stack v1→v2** | — | — | **-0.0272pp** | -0.0356 | -0.0342 | — |

**정정 전 (참고):**

| label | n_eval | R@10 | fail |
|---|---:|---:|---:|
| v2 | 140 | 0.6557 | 11 |
| v1 baseline | 113 | 0.6856 | 11 |

회귀 가설 (정정 후 재해석):

1. **S3 D5 (0.7172) 와 본 도구 (v1=0.6819) 는 동일 도구 아님** — S3 D5 는 RRF-only
   baseline 별도 evaluator, 본 도구는 hybrid + reranker default ENV. 같은 도구
   기준 본질 차이는 **v1=0.6819 → v2=0.6547 = -0.0272pp**.
2. **doc_type 확장 단독 가설은 약함** — PDF subset 한정에서도 v2 정정 후
   R@10=0.6043 으로 평균 이하. golden_v2 추가 22 row (S4-A D3 Phase 1 보강) +
   caption_dependent 보강 row 가 더 어려운 query 였을 가능성 (체크 보강 필요).
3. **검색 path 변경 0** — 본 -0.0272pp 는 데이터 분포 효과로 운영 회귀 아님.

### 2.2 §1 qtype 별 breakdown (9종)

| qtype | n_total | n_eval | R@10 | R@5 | MRR | fail |
|---|---:|---:|---:|---:|---:|---:|
| exact_fact | 113 | 106 | 0.6941 | 0.6514 | 0.5942 | 7 |
| cross_doc | 4 | 4 | **0.1667** | 0.1250 | 0.1250 | 0 |
| vision_diagram | 3 | 2 | 0.6667 | 0.5000 | 0.3125 | 0 |
| synonym_mismatch | 4 | 4 | 0.7330 | 0.5625 | 0.5312 | 0 |
| fuzzy_memory | 6 | 5 | 0.5000 | 0.5000 | 0.4667 | 0 |
| summary | 9 | 8 | 0.5625 | 0.5000 | 0.6250 | 1 |
| numeric_lookup | 7 | 7 | 0.5295 | 0.3333 | 0.3512 | 0 |
| table_lookup | 6 | 4 | 0.6477 | 0.6667 | 0.4250 | 2 |
| out_of_scope | 5 | 0 | 0.0000 | 0.0000 | 0.0000 | 1 |

핵심 발견:
- **cross_doc 0.1667** — S3 D3 query decomposer + intent_router 가 default OFF (paid)
  인 상태에서는 보조 인덱스 없이 cross-doc retrieve 가 거의 실패. 해당 4 row 모두
  R@10 ≤ 0.25.
- **out_of_scope** n_eval=0 — 정답 chunk 가 없는 row (의도된 unanswerable case). fail 1건은
  의도된 doc 매칭 fail.
- **synonym_mismatch 0.7330** 이 best qtype — chunk.text 합성 (S4-A D2) 의
  `[표: ...]` / `[그림: ...]` prefix 가 synonym query 매칭 보조 가설 일관.
- **numeric_lookup R@5=0.3333** 이 R@10=0.5295 보다 크게 떨어짐 — top-5 안 들어가는 경우
  많음. table chunk 의 ranking 정밀도 이슈로 추정.

### 2.3 §2 caption_dependent breakdown (true 18 vs false 139)

**2026-05-11 정정 후 (현재 유효):**

| caption_dependent | n_total | n_eval | R@10 | R@5 | MRR | fail |
|---|---:|---:|---:|---:|---:|---:|
| true | 18 | 17 | 0.6119 | 0.6412 | 0.5485 | 0 |
| false | 139 | 133 | 0.6602 | 0.5881 | 0.5505 | 1 |

- **R@10 delta = -0.0483** (true 0.6119 vs false 0.6602) — **정정 전 +0.0123 에서 반전**.
- **R@5 delta = +0.0531** — 정정 전 +0.1114 에서 절반으로 축소.

**claim B caveat — chunk.text 합성 효과 단독 가설 보류 사유:**

1. **qtype 분포 편향**: caption=true 18 row 의 qtype 분포 = `exact_fact 9 /
   table_lookup 6 / vision_diagram 3`. caption=false 139 row 에는
   **table_lookup·vision_diagram 0건**. R@5 우위 +0.0531pp 가 chunk.text 합성
   효과인지 아니면 두 qtype 의 본질적 특성인지 분리 불가.
2. **exact_fact subset only normalize** (qtype 통제):

   | subset | n | R@10 | R@5 |
   |---|---:|---:|---:|
   | exact_fact & caption=true | 9 | 0.6013 | 0.6852 |
   | exact_fact & caption=false | 104 | 0.6971 | 0.6311 |
   | delta (true-false) | — | **-0.0958** | **+0.0541** |

3. **결론**: 순수 chunk.text 합성 효과는 D2 적용 전 baseline 측정과의 차분으로만
   확정 가능. 현재 도구 결과만으로 "chunk.text 합성으로 R@5 +0.1114 우위" 라는
   이전 단정은 doc_id 버그 (+stale id 가 만든 artifact) + qtype 분포 편향의
   두 가지 confound 위에 서 있어 보류.

### 2.4 §3 prompt_version breakdown (v1 vs v2 hook)

**2026-05-11 정정 후 (현재 유효):**

라벨 분포:
- **v1: 0건**
- **v2: 77건** (6 doc — vision_enrich 진입한 PDF, 9878d7bd doc 포함 합류)
- **unlabeled: 80건** (7 doc — HWP/HWPX/PPTX/DOCX + vision 호출 0 한 PDF)

| prompt_version | n_total | n_eval | R@10 | R@5 | MRR | fail |
|---|---:|---:|---:|---:|---:|---:|
| v1 | 0 | 0 | N/A | N/A | N/A | 0 |
| v2 | 77 | 75 | 0.5919 | 0.5209 | 0.4714 | 0 |
| unlabeled | 80 | 75 | 0.7175 | 0.6673 | 0.6291 | 1 |

> ⚠️ **v1 라벨 0건 — 직접 비교 불가**.

**claim C 보강 — v2 vs unlabeled 차이의 confound:**

1. **doc 종류 confound 확정**: v2 라벨 doc 6건 = vision_enrich 진입한 PDF,
   unlabeled doc 7건 = HWP/HWPX/PPTX/DOCX + vision 호출 0 PDF 일부.
2. **PDF subset 한정 보강 (정정 후)**:
   - 전체 PDF: n=89/87, R@10=0.6043
   - PDF & v2 라벨: n=77/75, R@10=0.5919
   - **PDF subset 한정에서도 v2 라벨이 평균 이하 (-0.0124pp)** — 즉 v2 라벨 = vision
     의존 PDF = 검색 난이도 높음 (selection bias).
3. **결론**: v2 vs unlabeled 의 -0.1256pp 차이는 prompt v2 효과로 단정 불가.
   같은 doc 의 v1 → v2 추이는 S4-A D5 reingest 후에야 측정 가능.

---

## 3. 단위 테스트

### 3.1 신규 17건

| 클래스 | 테스트 | 검증 대상 |
|---|---|---|
| `GoldenV2SchemaTests` | 6건 | 14 컬럼 / NFC / doc_type 5종 / caption_dependent bool / qtype 9종 |
| `QtypeBreakdownTests` | 2건 | 9종 화이트리스트 groupby + 외부 값 skip |
| `CaptionDependentSubsetTests` | 2건 | true/false subset 분리 + R@10 delta 정확 |
| `PromptVersionHookTests` | 5건 | vision_page_cache mock 주입 v1/v2/unlabeled 라벨링 |
| `AggregationTests` | 2건 | n_eval vs fail_count 분리 + 빈 group |

### 3.2 회귀 검증

```bash
cd api && uv run --with pytest python -m pytest tests/ -q
# 결과: 763 passed, 17 warnings, 31 subtests passed in 15.65s
```

기존 746 → **763** (+17) / skipped 1 / 회귀 0.

---

## 4. 남은 이슈

### 4.1 cross_doc 0.1667 — 보조 인덱스 진입 검토 필요

n=4 로 표본 작지만 일관되게 낮음. S3 D3 paid_decomposition 의 default OFF 상태에서
RRF 만으로는 cross-doc 추출 한계가 분명. S4-B (entity-link 인덱스) 또는 S3 D3 paid
ON 토글 비교가 다음 분석 후보.

### 4.2 v1 라벨 0건 — D5 reingest 가 본 차원의 데이터 갱신 필수

`vision_page_cache` 에 이미 v2 row 67건이 자동 캐시되었으나, v1 baseline 이 0건이라
S4-A D1 prompt 5필드 효과의 정량 측정 자체가 불가능. D5 진입 = (a) 5 vision-enrich PDF
를 reingest → v2 row 갱신 (b) 같은 row subset 의 v1 (어제 측정값) vs v2 (D5 측정값)
delta 산출. 외부 cost 발생 — 사용자 명시 승인 필수.

### 4.3 doc_id 버그 fix 완료 (2026-05-11) — golden_v1/v2 G-A-104~113 정정

doc_id 버그 fix 완료 (v1/v2 양쪽 G-A-104~113 10 row 의 doc_id 를
`9878d7bd-4766-40fa-bebb-7da45f879768` 로 정정).

**원인**: `golden_v0.7_auto.csv` 생성 당시 Supabase 인스턴스의 stale doc_id
(`629332ab-673d-49b7-a956-4857e3e9a5ee`) 가 doc 재적재 후 새 doc_id 로 바뀌었으나
골든셋 rebuild 미진행. `build_golden_v1.py` 는 auto CSV 의 doc_id 컬럼을 그대로
통과시키는 구조이고, `build_golden_v2.py` 도 L498~507 의 doc_id 보존 fallback
(`if existing_doc_id: ... if not matched: pass`) 에서 stale id 가 docs_index 에
없어도 경고 없이 propagate. `auto_goldenset.py` 자체에는 하드코딩 없음 — Supabase
실제 doc_id 를 그대로 기록 (L705, L773).

**진단 분류**:
- (a) 코드 하드코딩? — 아니오
- (b) 이전 Supabase 인스턴스/상태? — **예** (doc 재적재로 새 doc_id 부여, 골든셋
  rebuild 미진행)
- (c) static fixture? — 아니오 (`docs.json` 같은 fixture 없음)

**재발 방지 hook 제안 (사용자 승인 대기)**:
1. `build_golden_v2.py` L506~507 의 `pass` 를 `logger.warning + match_doc_id
   fallback` 로 교체 — stale id 발견 시 title 매칭으로 자동 fix + `doc_match_fixed`
   카운트
2. doc 재적재 SOP 에 골든셋 rebuild 트리거 추가 (Makefile target 또는 작업 문서화)
3. `build_golden_v1.py` 에 `--validate-doc-ids` 옵션 (CI 강제 가능)

본 work-log 에서는 진단·제안만 — 코드 변경은 사용자 명시 승인 후 진입.

### 4.4 numeric_lookup R@5=0.3333 — ranking 정밀도 이슈

table chunk 의 numeric query 에 대한 top-5 정밀도가 R@10 의 절반 수준. cross-encoder
reranker (S3 D4 실 모드) 진입 시 가장 우선 회복 후보.

### 4.5 prompt_version doc_id 단위 majority — D5 정밀 정정

같은 doc 안 page 별 v1/v2 혼재 시 majority 만 반영 — D5 에서는 (doc_id, chunk_idx,
page) 단위 lookup 으로 정정 예정. 현재 도구의 `label_prompt_version` 함수 시그니처
유지하면서 page 라벨 추가 옵션 만들면 hook 만으로 가능.

---

## 5. 다음 스코프 권고

### 5.1 즉시 진입 가능 (외부 cost 0)

- **D4-ext**: v1 vs v2 비교를 위해 `unlabeled` 90 row 중 doc 단위 baseline 측정 추가
  (R@10 source 추적). 단순 raw json 재가공으로 완료 가능.
- **cross_doc fail row 4건 상세 진단** — query / RRF top-5 / sparse score 출력. 추가
  도구 없이 raw json grep 으로 시작.
- **doc 매칭 fail 11 row 의 expected_doc_title 정정** — golden_v2.csv 직접 수정.

### 5.2 사용자 명시 승인 후 진입 (외부 cost 발생)

- **S4-A D5 reingest** — 5 vision-enrich PDF reingest → vision_page_cache 의 v1 row
  생성 (D1 prompt 적용) + v2 row 비교. 예상 Gemini Vision 호출 ~20~30회 / cost
  ~$0.30 / time ~3분. **승인 필수**.

### 5.3 보류

- **S4-A D3 Phase 2/3** (cross_doc 보강 + 다른 PC 자료) — 사용자 명시 진입 결정 보류
  (어제 핸드오프 문서 §11 참조).

---

## 6. 활성 한계 / 환경 가정

- 본 측정은 단일 시점 1회 — HF embed_query cold start 영향으로 row 당 latency 변동
  발생. latency 자체는 본 도구의 1차 metric 아님 (`evals/results/s3_d5_results.md`
  참조).
- `vision_page_cache` 의 v2 row 67건은 어제 S4-A D1 ship 후 sample-report reingest
  시점에 채워진 것 (S2 D5 phase 1) — 의도된 cache 자동 invalidate + 재진입.
- 본 PC (사용자 데스크탑) 에서 측정. Supabase 적재 상태는 어제와 동일 (다른 PC 자료
  미적재).

---

## 7. 산출물 경로 (최종)

| 파일 | 절대 경로 |
|---|---|
| 측정 도구 | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/evals/run_s4_a_d4_breakdown.py` |
| 단위 테스트 | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/api/tests/test_run_s4_a_d4_breakdown.py` |
| 측정 결과 md | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/evals/results/s4_a_d4_results.md` |
| 측정 raw json | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/evals/results/s4_a_d4_raw.json` |
| 본 work-log | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/work-log/2026-05-10 S4-A D4 golden_v2 R@10 3축 breakdown.md` |
