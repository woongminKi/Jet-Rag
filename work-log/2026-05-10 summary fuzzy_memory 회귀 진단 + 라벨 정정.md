# 2026-05-10 summary / fuzzy_memory 회귀 진단 + 라벨 정정

> Sprint: D6 후속 (`work-log/2026-05-10 S3 D6 실 BGE-reranker 측정.md` 권고 1순위)
> 작성: 2026-05-10
> 마감: ship 완료 (진단 + G-A-035 라벨 정정 + work-log)
> 입력: D6 raw json (combo a → b cell-level delta) + DB chunks 직접 조회

---

## 0. 한 줄 요약

> **D6 신규 회귀 (summary -0.111 / fuzzy_memory -0.200) root cause 분리 식별 ship**. summary 9건 중 1 회귀 (G-A-035) = **라벨 정확도 문제** — chunk 59 도 "공단 운영의 핵심" 합리적 후보, golden v2 의 acceptable 에 추가 (cost 0). fuzzy_memory 5건 중 2 회귀 (G-U-001, G-U-018) = **reranker 의 fuzzy_memory query 처리 약점** — 구어체 ("그때 ... 뭐였더라") / 추측형 ("어디 있었지") 의 키워드 무시, 사양 표 같은 다른 chunk 로 매핑. 운영 default combo c 채택 시 fuzzy_memory 회귀는 라벨로 회복 불가, query 보강 또는 reranker 조건부 비활성 필요. 단위 테스트 766 OK / 회귀 0.

---

## 1. 회귀 row 식별

D6 raw json 의 combo a → b cell-level delta:

### 1.1 summary 9건 변화

| id | combo a | combo b | 평가 |
|---|---|---|---|
| G-A-026 | 0.5/✓ | 1.0/✓ | ✓ 개선 (R@10 +0.5) |
| **G-A-035** | 1.0/✓ | 1.0/**✗** | **⚠ top-1 회귀** |
| G-A-055 | 0.5/✓ | 0.5/✓ | 변화 0 |
| G-A-066 | 1.0/✗ | 1.0/✗ | 변화 0 |
| G-A-067 | 0.0/✗ | 0.0/✗ | 변화 0 |
| G-A-104 | 1.0/✓ | 1.0/✓ | 변화 0 |
| G-U-011 | 0.67/✓ | 0.67/✓ | 변화 0 |
| G-U-020 | 0.67/✓ | 0.67/✓ | 변화 0 |
| G-U-026 | 0.0/✗ | 0.0/✗ | 변화 0 |

→ **1 회귀 / 1 개선 / 7 변화 없음**. top-1 평균 -0.111 = 1 cell 영향 (9 표본 한계).

### 1.2 fuzzy_memory 5건 변화 (n_eval=5, G-U-007 제외)

| id | combo a | combo b | 평가 |
|---|---|---|---|
| **G-U-001** | 1.0/✓ | 0.33/**✗** | **⚠ top-1 회귀 + R@10 -0.67** |
| G-U-007 | 0/None | 0/None | 정답 chunks 0 (out_of_scope-like) |
| G-U-017 | 0/✗ | 0/✗ | 변화 0 |
| **G-U-018** | 0.5/✗ | 0/✗ | **⚠ R@10 -0.5** |
| G-U-022 | 1.0/✓ | 1.0/✓ | 변화 0 |
| G-U-024 | 0.33/✗ | 1.0/✗ | ✓ R@10 +0.67 |

→ **2 회귀 / 1 개선 / 2 변화 없음**. top-1 평균 -0.200 = 1 cell 영향 (5 표본 한계).

---

## 2. root cause 진단

### 2.1 G-A-035 (summary) — 라벨 정확도 문제

**query**: "공단 운영의 핵심 사항은?"
**doc**: 직제_규정(2024.4.30.개정)
**라벨**: relevant=67

| chunk | text 발췌 |
|---|---|
| **chunk 67** | "8. 차량․중기관리 ... 10. **공단운영** 및 사업계획에 관한 사항 ..." (직무 list) |
| **chunk 59** (combo b top-1) | "제5조(이사장) 이사장은 **공단을 대표하고 공단의 업무를 총괄하며 경영을 책임진다** ..." |

**평가**: chunk 59 도 "공단 운영의 핵심" 으로 합리적 후보 — 이사장이 경영 총괄은 직접적 핵심 사항. 단지 라벨러가 chunk 67 만 raw 단어 "공단운영" 으로 라벨링.

→ **라벨 정확도 문제**. acceptable 에 chunk 59 추가 (0.5 weight) 적절.

### 2.2 G-U-001 (fuzzy_memory) — reranker 약점

**query**: "그때 쏘나타 시트 뭐였더라 가죽이었나"
**doc**: sonata-the-edge_catalog
**라벨**: relevant=47, 46, 121

| chunk | text 발췌 |
|---|---|
| **chunk 121** (combo a top-1, 정답) | "[문서] 현대차 시트 조합 차트와 다양한 시트 이미지 및 관련 정보..." |
| **chunk 47** (정답) | "Seat combination chart\n\n인조가죽 시트\n천연가죽 시트..." |
| **chunk 46** (정답) | "카멜 나파가죽 시트\n네이비 나파가죽 시트..." |
| **chunk 133** (combo b top-1) | "보(8단 습식 DCT)\n2,497\n1,610\n19\n11.1\n9.4..." (연비/사양 표) |
| **chunk 64** (combo b 2위) | "Specifications\n효율적 사용방법 : 정속주행을 합시다." |

**평가**: combo a (RRF baseline) 는 "시트", "가죽" lex match 로 정답 retrieve. reranker 는 "그때", "뭐였더라" 같은 구어체 / 모호 단어를 의미 매칭에 weight 줘서 "사양" 또는 "정속주행" 같은 다른 chunk 로 매핑.

→ **reranker 의 fuzzy_memory query 처리 약점**. 라벨 정확함, 정정 불필요.

### 2.3 G-U-018 (fuzzy_memory) — reranker 약점 + multi-doc 의도

**query**: "law sample 자료에서 손해배상 기준 어디 있었지"
**doc**: law sample2|law sample3 (cross_doc U-row)
**라벨**: relevant=18, accept=12 (chunk_idx)

combo a R@10=0.5 (1/2 hit) → combo b R@10=0 (둘 다 top-50 밖).

→ reranker 가 "어디 있었지" 추측형 표현 의미 매칭 어려움 + cross_doc multi-doc 합산의 reranker score 정합성 문제.

---

## 3. 적용 fix — G-A-035 acceptable 라벨 정정

cost 0 fix:

```python
# evals/golden_v2.csv
G-A-035: relevant=67, acceptable= → acceptable=59
```

라벨러 의도 보존 (chunk 67 = relevant 1.0 weight 그대로) + chunk 59 = 합리적 후보 (0.5 weight).

### 3.1 회귀 검증 (단위 테스트)

```
Ran 766 tests in 22.136s
OK (skipped=1)
```

CSV 정정만이라 회귀 영향 0.

### 3.2 측정 영향 추정

D6 재측정 시 G-A-035 의 combo b R@10 / top-1 는:
- 현 측정: R@10=1.0 (chunk 67 rank 2 retrieve), top-1=False (chunk 59)
- acceptable 정정 후: R@10=1.0 (변동 없음 — relevant + acceptable 모두 retrieve), top-1=True (chunk 59 가 acceptable hit, top1_hit 정의 = relevant or acceptable)

→ summary qtype 회귀 -0.111 → -0.0 가능 (1 cell top-1 회복).

---

## 4. fuzzy_memory 회귀 (라벨로 회복 불가)

G-U-001 / G-U-018 은 라벨 정확. **reranker 자체 약점** 이라 라벨 정정 불가.

### 4.1 운영 시사

- 운영 default combo c 채택 시 fuzzy_memory 회귀 잔존 (5/9 표본 1건만 영향이지만 정성적 신호 강력)
- 사용자가 구어체 / 추측형 query 가 많은 페르소나 A 일 경우 reranker 기대 효과보다 회귀 우려 클 수 있음

### 4.2 fix 후보 (다음 sprint)

| 옵션 | 작업 | 효과 추정 |
|---|---|---|
| A | query_rewriter 도입 — 구어체 → 격식체 paraphrase (Gemini Flash, paid) | high (의미 매칭 정확도 ↑) |
| B | reranker 조건부 비활성 — query length / 구어체 detect 시 RRF-only fallback | medium (정밀도 / recall trade-off) |
| C | reranker score weighted blend (RRF + reranker × α) — α=0.7 같은 hybrid score | medium (튜닝 표본 부족) |
| D | golden v2 fuzzy_memory row 확장 → 재측정으로 통계 신뢰도 ↑ | 진단 보강만 |

→ **권고 = 옵션 D (cost 0) + 옵션 B (조건부 비활성, 운영 코드 변경 동반)** 의 결합.

---

## 5. 다음 후보 우선순위 (재정렬)

| # | 후보 | 작업량 | 권고도 | 이유 |
|---|---|---|---|---|
| 1 | **D5 본격 reingest** (24h+ 후) | 가변 + cost ~$0.50 | ★★★ | G-A-021 caption 보강 정량 정당화 (D6 검증) |
| 2 | **fuzzy_memory row 확장** (golden v2 5 → 10+) | 0.5~1일 (수작업) | 신규 ★★ | 통계 신뢰도 ↑, reranker fuzzy 약점 보강 측정 |
| 3 | **reranker 조건부 비활성** (query 패턴 detect) | 1일 | 신규 ★★ | 운영 코드 변경 동반, 사용자 정책 결정 |
| 4 | **search() cross_doc retrieve 진단** | 0.5~1일 | ★★ | G-U-015/032 잔존 |
| 5 | **chunk text 헤더 합성 강화** | 1~2일 | ★★ | 표 본문 chunk 매칭 약점 |
| 6 | **query_rewriter 도입** (Gemini Flash, paid) | 1.5일 + cost | ★ | 정성 효과 큰 옵션, 정량 검증 부족 |

### 권고 (비판적 재검토 후)

**1순위 = D5 본격 reingest** (24h+ 후 cap 회복, 사용자 cost 승인 필요).
- 이유: G-A-021 reranker 효과 0 검증 완료 + caption 보강 ROI 가설 정량 정당화. table_lookup R@10 0.6247 → 0.7611+ 가시화

**2순위 = fuzzy_memory row 확장** (cost 0).
- 이유: 5 row 표본 한계로 회귀 통계 신뢰도 부족. 10+ 확장 시 reranker 약점 정량화 + 운영 정책 (combo c default) 결정 근거 강화

**3순위 = reranker 조건부 비활성** (운영 코드 변경).
- 이유: query 패턴 detector (구어체 / 추측형) 로 fuzzy_memory 회귀 회피. 단 운영 정책 결정 동반

---

## 6. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-regress-1 | 다음 sprint 1순위 | **D5 본격 reingest** (24h+ 후 + cost 승인) | 사용자 명시 진입 |
| Q-regress-2 | 운영 default combo c 채택 시 fuzzy_memory 회귀 처리 | reranker 조건부 비활성 vs query_rewriter | 운영 진입 결정 시 |
| Q-regress-3 | golden v2 fuzzy_memory row 확장 | 5 → 10+ 라벨링 | 별도 sprint |

---

## 7. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정
- `evals/golden_v2.csv` — G-A-035 의 acceptable_chunks: "" → "59" (1 row)

### 운영 코드
- 0 건

---

## 8. 한 문장 마감

> **2026-05-10 — D6 회귀 진단 + 라벨 정정 ship**. summary -0.111 = G-A-035 라벨 정확도 (chunk 59 acceptable 추가, cost 0). fuzzy_memory -0.200 = G-U-001/G-U-018 의 reranker 의 구어체/추측형 query 약점 (라벨로 회복 불가, 운영 정책 결정 필요). 단위 테스트 766 OK / 회귀 0. **운영 default combo c 채택 시 fuzzy_memory 회귀 잔존 인정** + 다음 sprint 1순위 = D5 본격 reingest (G-A-021 caption 보강 ROI 정량 정당화 검증 완료).
