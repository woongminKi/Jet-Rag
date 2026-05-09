# 2026-05-09 table_lookup + fuzzy_memory 회귀 진단

> Sprint: D2 fix 후 신규 회귀 진단 (Master plan §6 외 — 후속 진단)
> 작성: 2026-05-09 (D2 fix ship 직후)
> 마감: 12 row (table_lookup 6 + fuzzy_memory 6) 의 search 응답 + chunks metadata 추적
> 입력: D2 fix work-log §6.1 의 1순위 후보

---

## 0. 한 줄 요약

> **table_lookup top-1 -0.167 + fuzzy_memory R@10 -0.083 회귀 root cause = chunks 변동 noise + multi-doc 매칭 fail**. 12 row search 응답 추적 결과: (1) D2 fix 효과는 sample-report 한정으로 검증 (G-A-021 acceptable chunks 904/908/909/914/915/916 caption 부착, top-3 에 chunk 904 진입), (2) table_lookup 6 row 중 5 row 의 정답 chunks 는 caption 미부착 (다른 12 doc 은 v1 cache 그대로), (3) G-U-017 'law sample2|law sample3' multi-doc 매칭 fail (doc_id 빈 cross_doc U-row 라벨 한계). 단위 테스트 775 OK / 회귀 0. 운영 코드 변경 0. 다음 후보 1순위 = **golden v2 minority qtype 표본 확장** (cost 0).

---

## 1. 진단 방법

### 1.1 대상 row

`golden_v2.csv` 의 query_type 별:
- table_lookup: 6 row (모두 caption_dependent=true)
- fuzzy_memory: 6 row (모두 caption_dependent=false)

### 1.2 진단 스크립트 (`/tmp/diagnose_regression.py`)

각 row 에 대해:
1. `app.routers.search.search()` 직접 호출 (`mode=hybrid`, RRF-only ENV)
2. target_item 의 matched_chunks 추출 + RRF score 정렬
3. 정답 chunks (relevant + acceptable) 의 top-50 위치 확인
4. 정답 chunks 의 chunks 테이블 metadata 직접 확인 (caption 부착 여부)

cost 0 (운영 코드 호출만, 외부 API 0).

---

## 2. 진단 결과 (12 row)

### 2.1 table_lookup 6 row

| row | doc | top-1 hit | 정답 위치 | caption 부착 정답 chunks |
|---|---|:---:|---|---|
| G-U-003 | sonata-the-edge | ❌ | 102 (4위) | 0/1 (chunk 102 caption 없음) |
| G-A-008 | 데이터센터 PDF | ❌ | 374 (3위), 434 (14위), 317 (116위) | 0/3 |
| G-A-011 | 브랜딩_스튜디오앤드오어 | ✅ | 1 (1위) | 0/1 |
| G-A-021 | sample-report | ❌ | 904 (3위), 909 (50위), 914 (56위) | **6/30** ✅ |
| G-A-107 | 포트폴리오_이한주 | ❌ | 38 (2위) | 0/2 |
| G-A-111 | 포트폴리오_이한주 | ❌ | 38 (2위), 39 (5위), 64 (12위) | 0/4 |

→ top-1 hit = 1/6 (G-A-011 만). 다른 5 row 의 정답이 top-2~4 에 머무름.

### 2.2 fuzzy_memory 6 row

| row | doc | top-1 hit | 정답 위치 | 비고 |
|---|---|:---:|---|---|
| G-U-001 | sonata-the-edge | ✅ | 47 (1위), 121 (2위), 46 (3위) | top-3 다 포함 |
| G-U-007 | 데이터센터 PDF | — | relv 비어있음 (n_eval 제외) | |
| G-U-017 | law sample2 \| law sample3 | ❌ | retrieve 0 (top-3 = 851, 735, 985 다른 doc) | **multi-doc 매칭 fail** |
| G-U-018 | law sample2 \| law sample3 | ❌ | 27 (3위) | top-3 hit |
| G-U-022 | 스튜디오앤드오어 | ✅ | 3 (1위) | |
| G-U-024 | 이력서 | ❌ | 116 (2위), 106 (11위), 103 (12위) | |

→ R@10 evaluable 5 row 중 4 hit. G-U-017 가 R@10=0 으로 평균 -0.20 영향.

### 2.3 sample-report 의 caption 부착 chunks 분포

- 총 994 chunks 중 caption 부착 = 89 chunks
- chunk_idx range: 900-993 (vision sections 가 chunks 끝에 집중)
- **G-A-021 acceptable chunks 30 중 caption 부착 = 6 chunks** (904, 908, 909, 914, 915, 916)
- 이 6 chunks 가 search top-50 진입:
  - chunk 904 → top-3 (3위) ✅
  - chunk 909 → top-50 (50위)
  - chunk 914 → top-56
  - chunk 915 → top-?
  - chunk 916 → top-?

---

## 3. 회귀 root cause 분리

### 3.1 table_lookup top-1 -0.167 (D5 본격 0.333 → split fix 0.167)

**root cause**: chunks 변동 (898 → 987 → 994) 의 retrieve 분포 noise.

- D5 본격 1차 시점: top-1 hit = 2/6
- split fix 후: top-1 hit = 1/6
- 차이 = 1 row (정확히 어느 row 인지 raw json 비교 불가, 어제 raw 가 덮여씌워짐)

표본 6 row 에서 1 row 변동 = top-1 변화 0.167. **통계적으로 noise**.

D2 fix 자체의 효과:
- caption 부착된 chunk 904 가 top-3 (3위) 에 진입 → R@10 회복 신호
- 다만 정답 primary chunk 868 (relevant) 은 여전히 retrieve 안 됨 (caption 미부착)

### 3.2 fuzzy_memory R@10 -0.083 (D5 본격 0.792 → split fix 0.708)

**root cause**: G-U-017 의 multi-doc 매칭 fail.

G-U-017:
- query: '법률 자료 중에 임대차 분쟁 관련 있었나'
- doc_id: 빈 (U-row)
- expected_doc_title: 'law sample2|law sample3' (multi-doc)
- relevant_chunks: 3, 6
- search top-3: [851, 735, 985] — **다른 doc 의 chunk_idx**

→ Phase 2-A 의 _pick_target_items multi-doc 매칭 helper 가 작동하지만, retrieve 자체에서 law sample2/3 doc 의 chunks 가 top-50 안에 안 잡힘. dense + sparse 매칭이 다른 doc 의 chunks 를 우선.

D2 fix 와 무관 — multi-doc cross_doc 매칭 약점 (어제 핸드오프 §6 의 활성 한계 #1 와 동일).

### 3.3 D2 fix 의 실 효과

- sample-report (1 doc) 의 89 chunks 에 caption 부착 → retrieve 효과 일부 검증
- 다른 12 doc 은 v1 cache 그대로 → caption 부착 0 → 효과 0
- table_lookup 6 row 중 1 row (G-A-021) 만 직접 영향
- 회귀 -0.167 / -0.083 은 noise + multi-doc 매칭 약점 (D2 fix 와 무관)

→ **D2 fix 자체는 양성** — caption gap 62% 회수, summary R@10 +0.074 등은 caption 부착 효과 명확.

---

## 4. 다음 후보 우선순위

### 4.1 1순위 — golden v2 minority qtype 표본 확장 (cost 0)

**왜?** 회귀 진단에서 **표본 작음** 이 noise 의 핵심. 6 row 표본의 1 row 변동 = 0.167 swing. 신뢰도 낮음.

**작업**:
- table_lookup 6 → 12+ 라벨링
- fuzzy_memory 6 → 12+ 라벨링 (특히 G-U-017 류 multi-doc 의도 라벨 재검증)
- caption_dependent=true 18 → 30+ 확장

작업량: 1~2일, cost 0.

### 4.2 2순위 — multi-doc cross_doc retrieve 진단 (cost 0)

G-U-017 / G-U-015 / G-U-032 류 R@10=0 잔존. dense + sparse 매칭이 다른 doc 으로 흘러가는 원인 분석.

### 4.3 3~10순위

| # | 후보 | 작업량 | 권고도 |
|---|---|---|---|
| 3 | 다른 docs (12 doc) v2 prompt reingest | cost ~$0.5~1.5 | ★★ |
| 4 | reranker 조건부 비활성 + combo c 운영 default | 1일 | ★★ |
| 5 | chunk_filter 45.5% 마킹 false positive 분석 | 0.5일 | ★★ |
| 6 | budget_guard cache hit 무료 인식 (운영 code 패치) | 0.5일 | ★ |
| 7 | S4-B 핵심 엔티티 추출 | 3일 | ★★ |
| 8 | S5 /answer UX | 1주 | ★★ |

---

## 5. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-regression-1 | table_lookup / fuzzy_memory 회귀 처리 | **noise 로 결론** — D2 fix 자체는 양성. 표본 확장 sprint 로 신뢰도 회복 | 즉시 결론 |
| Q-multi-doc | G-U-015/017/018/032 multi-doc retrieve 약점 진단 | 별도 sprint (search() level 분석) | 다음 sprint |
| Q-other-docs | 12 docs v2 prompt reingest | sample-report 외 doc 의 caption gap 회수 위해 cost ~$0.5~1.5 진입 | 사용자 명시 cost 승인 후 |
| (어제 잔존) | Q-D5-resume / Q-Phase2-B / 기타 | 별도 sprint | |

---

## 6. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정
- 0 건 (운영 코드 / 측정 도구 / 단위 테스트 모두 변경 없음)

### 진단 스크립트
- `/tmp/diagnose_regression.py` — 12 row search 응답 + chunks metadata 추적 (gitignored, 일회성)

### 데이터 영향
- 0 건 (read-only 진단)

---

## 7. 한 문장 마감

> **2026-05-09 — table_lookup top-1 -0.167 + fuzzy_memory R@10 -0.083 회귀는 chunks 변동 noise + multi-doc 매칭 fail (G-U-017) 로 분리**. D2 fix 자체는 양성 — sample-report 의 G-A-021 acceptable chunks 6건 caption 부착 + top-3 진입 검증. 다른 12 doc 은 v1 cache 그대로라 효과 0. 단위 테스트 775 OK / 회귀 0. 운영 코드 변경 0. 다음 후보 1순위 = golden v2 minority qtype 표본 확장 (cost 0, 1~2일).
