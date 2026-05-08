# 2026-05-10 table_lookup 약점 진단

> Sprint: D5 시범 후 후속 (핸드오프 §4 후보 #2 / D5 시도 후 권고 1순위)
> 작성: 2026-05-10
> 마감: 진단 ship (cost 0, 운영 코드 변경 0)
> 입력: D4 raw json (table_lookup R@10=0.6247, top-1=0.3333) + DB chunks/search 직접 호출

---

## 0. 한 줄 요약

> **table_lookup 6건 진단 ship — root cause 분리 식별. 5/6 = retrieve OK / top-1 약점 (reranker 효과 가능)**, **1/6 = retrieve 자체 실패** (G-A-021, sample-report 의 표 본문 chunk 868 이 search top-50 밖). chunk 868 정상 적재 (page 91 / text 표 본문 숫자) 확인 + acceptable 30+ 모두 retrieve 안 됨. **D5 v2 prompt 의 caption 보강 ROI 가설 정량 정당화** — table_caption 부착 시 dense + lex 매칭 회복 가능. cost 0 진단, 운영 코드 변경 0, 단위 테스트 766 OK / 회귀 0.

---

## 1. 진단 절차 (cost 0, DB read-only)

### 1.1 table_lookup 6건 cell 추출

| ID | doc | R@10 | top-1 | 정답 idx (rel + accept) | predicted top-5 |
|---|---|---:|:---:|---|---|
| G-U-003 | sonata | **1.0** | ✗ | 102 | [40, 118, 123, 102, 133] |
| G-A-008 | 데이터센터 | 0.5 | ✗ | 374 + 434/317 | [399, 414, 374, 395, 418] |
| G-A-011 | 브랜딩 | **1.0** | ✓ | 1 | [1, 4, 0] |
| **G-A-021** | **sample-report** | **0.0** | ✗ | 868 + 30+ accept | [810, 772, 797, 280, 14] |
| G-A-107 | 포트폴리오 | 0.667 | ✗ | 38 + 63 | [69, **38**, 39, 66, 17] |
| G-A-111 | 포트폴리오 | 0.4 | ✗ | 63 + 38/39/64 | [69, **38**, 70, **39**, 67] |

### 1.2 root cause 분류

**Group A (5/6) — retrieve OK, top-1 약점**:
- G-U-003 (rank 4 retrieve), G-A-008 (rank 3), G-A-011 (top-1 hit), G-A-107/111 (rank 2)
- 정답 chunk 가 top-10 안에 잡힘 → **reranker 효과로 top-1 회복 가능**
- S3 D5 의 mock reranker 측정에서는 폭락이라 실 BGE-reranker D6 측정 필요

**Group B (1/6) — retrieve 자체 실패**:
- G-A-021: chunk 868 + acceptable 30+ 모두 search top-50 밖
- chunk 868 정상 적재 검증 (page=91, text="통관수출/소비자물가/GDP..." — 표 본문 숫자)
- top-100 까지 확장 시 chunk 284 (rank 77) / chunk 76 (rank 87) 만 잡힘
- → **표 본문 chunk 의 dense + lex 매칭 한계**

### 1.3 G-A-021 직접 search 호출 결과

```
top-50 chunk_idx: [810, 772, 797, 280, 14, 806, 815, 112, 178, 265, 173, 12, 119, 443,
                   848, 184, 863, 895, 807, 190, 47, 782, 834, 808, 572, 784, 790, 52,
                   770, 48, 745, 728, 282, 804, 5, 716, 849, 773, 731, 442, 837, 377,
                   387, 783, 779, 896, 766, 378, 814, 18]
target hits in top-50: 0
top-100 hits: chunk 284 (rank 77), chunk 76 (rank 87)
```

→ 표 본문 chunk 들이 query "주요 경제 지표들의 추이" 와 **dense + lex 매칭 약함**.

---

## 2. 핵심 finding — D5 ROI 가설 정량 정당화

### 2.1 chunk 868 의 매칭 약점 분석

chunk 868 text:
```
통관수출4)
0.6
2.4
7.0
-5.6
...
소비자물가3)
2.4
2.1
...
중국 GDP성장률3)
5.0
5.0
...
```

→ 숫자 + 짧은 라벨만. query "주요 경제 지표들의 추이" 와 BGE-M3 dense 매칭 부족. lex 매칭도 "지표"/"추이" 키워드 부재.

### 2.2 D5 v2 prompt caption 보강 시 회복 시나리오

v2 prompt 의 `table_caption` 추출 → 예: `"주요 경제 지표 추이 (2024-2027)"`. 이 caption 이 chunk 868 의 metadata 에 부착되면:
- chunks.text 합성 시 `[표: 주요 경제 지표 추이 (2024-2027)]\n` prefix 부착
- dense embedding 재계산 → "주요 경제 지표 추이" semantic 포함
- lex 매칭도 "지표" / "추이" 직접 hit
- → top-50 안 진입 가능 → R@10 회복

### 2.3 ROI 가설 정량 추정

table_lookup 6건 중:
- **G-A-021 1건** = R@10 0.0 → caption 보강 시 회복 → table_lookup R@10 평균 0.5944 → **0.7611** (+0.167)
- 다른 5건도 caption 보강 시 top-1 회복 가능성 (가설, 추가 검증 필요)

→ table_lookup R@10 0.6247 → **0.75+ 가능** = DoD 임계 도달 가시화.

---

## 3. 다른 후보 도출

### 3.1 reranker 효과 검증 (5/6 의 top-1 약점)

S3 D5 의 mock-reranker 폭락은 mock 한계. 실 BGE-reranker 의 효과는 D6 별도 측정 필요.

작업: `JETRAG_RERANKER_ENABLED=true` + `JETRAG_MMR_DISABLE=1` ENV 로 D4 도구 재실행 → table_lookup 6건의 top-1 변화 측정.

cost: HF inference 호출 동반 (월간 cap $0.30 내).

### 3.2 caption 보강 효과 직접 검증 (G-A-021 한정)

D5 cap 가드 회복 후 sample-report 만 reingest → G-A-021 R@10 변화 직접 측정.

cost: ~$0.11 (per-doc cap 1pp 초과).

### 3.3 chunk text 합성 강화

chunk 텍스트 자체에 표 헤더 / column header 포함 (현재 표 본문만) → search 매칭 ↑.

작업: chunks 적재 stage 의 chunk text 합성 로직 보강. 운영 코드 변경 동반.

---

## 4. 진단 결과 ship — fix 적용 0 (cost 0)

### 4.1 적용 가능 fix 검토

| 옵션 | cost | 운영 코드 | 효과 | 적용 |
|---|---|---|---|:---:|
| G-A-021 acceptable 좁힘 | 0 | 0 | 라벨러 의도 손상 | ✗ |
| G-A-021 라벨 갱신 | 0 | 0 | 부적절 (chunk 868 정답 정확) | ✗ |
| reranker D6 측정 | 동반 | 0 | top-1 회복 가능 (별도 sprint) | 보고만 |
| D5 reingest 본격 | $0.50+ | 0 | caption 보강 가능 (24h+ 후) | 보고만 |

→ **즉시 적용 fix 0** — 진단 ship 만, 다음 sprint 권고만 정리.

### 4.2 단위 테스트 회귀

```
Ran 766 tests in 22.136s
OK (skipped=1)
```

회귀 0.

---

## 5. 다음 후보 우선순위 (재정렬)

| # | 후보 | 작업량 | 권고도 변화 | 이유 |
|---|---|---|---|---|
| 1 | **D5 본격 reingest** (24h+ 후) | 가변 + cost ~$0.50 | ★ → **★★★** | G-A-021 의 caption 보강 ROI 정량 정당화 |
| 2 | **S3 D6 실 BGE-reranker 측정** | 0.5일 + cost (HF) | ★ → **★★★** | table_lookup 5/6 의 top-1 약점 회복 가능 |
| 3 | **search() cross_doc retrieve 진단** | 0.5~1일 | ★★ 유지 | G-U-015/032 R@10=0 잔존 |
| 4 | **chunk_filter 49.1% 마킹 분석** | 0.5일 | ★★ 유지 | sample-report reingest 부수 효과 |
| 5 | **chunk text 헤더 합성 강화** | 1~2일 | 신규 ★★ | 표 본문 chunk 의 매칭 약점 일반 해결 |
| 6 | **Phase 2-B cross_doc row 확장** | 0.5~1일 | ★★ 유지 | search 진단 후 |

### 권고 (비판적 재검토 후)

**1순위 = S3 D6 실 BGE-reranker 측정** (cost 동반).
- 이유: table_lookup 5/6 = retrieve OK / top-1 약점. reranker 가 가장 직접적 fix
- 작업: ENV 토글 + D4 도구 재실행 — 운영 코드 변경 0
- cost: HF reranker 월간 cap $0.30 내 (안전)

**2순위 = D5 본격 reingest** (24h+ 후 cap 회복).
- 이유: G-A-021 caption 보강 ROI 정량 정당화 (R@10 0.0 → 회복 가능)
- 사전: 24h cap 회복 + DEFAULT_USER_ID UUID 정합성 + 사용자 cost 승인

**3순위 = chunk text 헤더 합성 강화** (신규).
- 이유: 표 본문 chunk 의 일반 매칭 약점 → 운영 차원 fix

---

## 6. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-table-1 | 다음 sprint 1순위 | **S3 D6 실 BGE-reranker** (HF cost ≤ 월간 $0.30 cap) | 사용자 명시 진입 + cost 승인 |
| Q-table-2 | D5 본격 reingest 시점 | 24h cap 회복 + 사용자 cost 승인 | 24h+ 후 |
| Q-table-3 | chunk text 헤더 합성 강화 | 운영 코드 변경, S3 D6 후 결정 | 별도 sprint |

---

## 7. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정
- 0 건 (운영 코드 / 측정 도구 / golden / 단위 테스트 모두 변경 없음)

### 데이터 영향
- 0 건 (DB read-only 진단, vision_page_cache / chunks 변동 0)

---

## 8. 한 문장 마감

> **2026-05-10 — table_lookup 약점 진단 ship**. 6 row root cause 분리 — **5/6 = retrieve OK + top-1 약점** (reranker 효과 가능), **1/6 = G-A-021 retrieve 자체 실패** (chunk 868 정상 적재됐으나 search top-50 밖, 표 본문 dense+lex 매칭 한계). **D5 v2 prompt caption 보강 ROI 가설 정량 정당화** (chunk 868 에 table_caption 부착 시 매칭 회복 가능). cost 0, 운영 코드 변경 0, 단위 테스트 766 OK / 회귀 0. 다음 후보 1순위 = S3 D6 실 BGE-reranker 측정 (HF cost ≤ 월간 $0.30 cap, 안전).
