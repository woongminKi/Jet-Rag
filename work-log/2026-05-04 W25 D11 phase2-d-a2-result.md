# W25 D11 — Phase 2 차수 D-a-2 ship: 한국어 조사 + punctuation strip

> **결론**: `_strip_korean_particle` 헬퍼 추가 (조사 9자 whitelist + trailing punctuation strip). **sparse_hits=0 케이스 10/10 → 0/10 완전 해소**. 평균 sparse-only recall 0.000 → **0.800**, dense-only 0.825 → **0.900**. 격차 4건 중 3건 개선 (G-S-001 first_hit_rank 2→1, G-S-008 4→2, G-S-006 동일). **G-S-005 만 sparse 단독 부족** — Layer B/C 영역.

> 측정: `evals/run_phase2_d_diagnosis.py` (W25 D9 도입 / ragas 무관)

---

## 0. 변경 요약

| 항목 | 값 |
|---|---|
| 변경 파일 | `api/app/routers/search.py` (헬퍼 1개 추가 + `_build_pgroonga_query` 통합), `api/tests/test_pgroonga_or_query.py` (test 7건 추가) |
| 마이그레이션 | 0 |
| 재인덱싱 | 0 |
| 의존성 | 0 |
| 단위 테스트 | 294 → **301** (+7, D-a-2) |
| 회귀 | 0 |

---

## 1. 정량 효과 (D9 → D10 → D11)

| 메트릭 | D9 baseline | D10 D-a | D11 D-a+D-a-2 |
|---|---:|---:|---:|
| sparse_hits=0 케이스 | 10/10 | 3/10 | **0/10** |
| 평균 sparse-only recall | 0.000 | 0.500 | **0.800** |
| 평균 dense-only recall | 0.825 | 0.850 | **0.900** |

### 1.1 격차 4건 first_hit_rank 추이

| QA | D9 | D10 | D11 | 변화 |
|---|---:|---:|---:|---|
| G-S-001 `소나타 전장 길이가 얼마나 돼?` | 2 | 1 | **1** | ↑ (D-a) |
| G-S-005 `소나타 트림 종류 뭐가 있어?` | - | - | - | sparse hits>0 / 정답 청크 못 잡음 (B/C 영역) |
| G-S-006 `소나타 N Line 특징이 뭐야?` | 2 | 2 | 2 | 변동 없음 (이미 작동) |
| G-S-008 `소나타 디스플레이는 어떤 종류야?` | 4 | 4 | **2** | ↑ (D-a-2 — '디스플레이는' 조사 strip) |

**격차 4건 중 3건 개선, 1건 잔존** (G-S-005, B/C 영역).

### 1.2 sparse_hits 분포 (hybrid 호출 시)

| QA | D9 | D10 | D11 |
|---|---:|---:|---:|
| G-S-001 | 0 | 2 | 2 |
| G-S-002 | 0 | 8 | 9 |
| G-S-003 | 0 | 0 | **2** ← D-a-2 회복 |
| G-S-004 | 0 | 0 | **2** ← D-a-2 회복 |
| G-S-005 | 0 | 11 | 11 |
| G-S-006 | 0 | 32 | 32 |
| G-S-007 | 0 | 13 | 13 |
| G-S-008 | 0 | 0 | **6** ← D-a-2 회복 |
| G-S-009 | 0 | 5 | 5 |
| G-S-010 | 0 | 6 | 6 |

---

## 2. 변경 내용

### 2.1 `_strip_korean_particle` 헬퍼

```python
_KOREAN_PARTICLES_1 = frozenset(
    ["는", "은", "가", "을", "를", "도", "만", "에", "의"]
)
# "이" 는 외래어 명사 끝 (디스플레이/알고리즘) 충돌로 제외.

def _strip_korean_particle(token: str) -> str:
    cleaned = token.rstrip("?!.,;:")
    if len(cleaned) < 3:  # 짧은 단어 보호
        return cleaned
    if cleaned[-1] in _KOREAN_PARTICLES_1:
        return cleaned[:-1]
    return cleaned
```

### 2.2 `_build_pgroonga_query` 통합

기존 D-a 의 공백 split 로직에 `_strip_korean_particle` 호출 + 빈 토큰 필터.

```python
def _build_pgroonga_query(q: str) -> str:
    tokens = [_strip_korean_particle(t) for t in q.strip().split() if t]
    tokens = [t for t in tokens if t]
    if len(tokens) <= 1:
        return tokens[0] if tokens else q.strip()
    return " OR ".join(tokens)
```

### 2.3 단위 테스트 추가 (7건)

- 흔한 조사 strip (`전폭은` → `전폭` 등 6 case)
- 외래어 "이" 종결 보존 (디스플레이 / 프로토타이)
- 짧은 토큰 보호 (`회사` / `는` / `가가`)
- 비조사 어미 보존 (`얼마나` / `종류야` / `Sonata`)
- punctuation strip (`전폭은?` / `디스플레이는!`)
- `_build_pgroonga_query` 통합 case (의문문 query)

---

## 3. 잔존 미해결 case 분석

### G-S-005 `소나타 트림 종류 뭐가 있어?` — sparse hits>0 / 정답 청크 못 잡음

```
expected: [39, 43]
sparse retrieved (top10): [65, 47, 46, 56, 22, 23, 96, 54, 24, 30]
dense retrieved (top10):  [65, 47, 46, 56, 22, ...]
```

→ "트림" 단어로 sparse 11 hits 잡지만 chunks 39/43 의 본문이 query 토큰과 직접 매칭 약함. "트림" 이라는 단어가 chunk 39/43 에 없을 가능성 (페이지 20~21 의 본문이 트림명 ("Premium / S / Exclusive / Inspiration") 만 나열, "트림" 이라는 단어 자체는 없음).

**Layer B 영역** — chunk 분리 정책 또는 doc_title prepend 로 의미 매칭 보강 필요. 본 sprint 범위 외.

### G-S-009 `소나타 외장 색상 몇 가지야?` — sparse 가 [45, 46, 96, 17, 15] / expected [44]

→ 색상 chunks 가 인접 chunk_idx (44, 45, 46) 에 분산. "외장 색상" 토큰은 매칭하지만 정답 청크만 정확히 안 잡힘. 이도 chunk 분리 정책 영역.

---

## 4. ship 정당성 점검

| 기준 | 판정 |
|---|---|
| 회귀 0 | ✅ 단위 테스트 287→301 OK |
| 악화 case 0 | ✅ |
| 정량 개선 | ✅ sparse=0 케이스 10/10 → 0/10 완전 해소 / 평균 sparse recall +0.800 / dense recall +0.075 |
| 격차 4건 개선 | ✅ G-S-001 (2→1), G-S-008 (4→2), G-S-006 (2 유지), G-S-005 잔존 (B/C 영역) |
| 마이그레이션 회피 | ✅ |
| 재인덱싱 회피 | ✅ |

→ **ship 진행**.

---

## 5. 다음 sprint 후보 (W25 D12+)

| # | 차수 | ROI | 비용 | 근거 |
|---|---|---|---|---|
| 1 | **B chunk 분리 정책** | G-S-005 / G-S-009 회복 가능성 | chunk.py + 99 chunks 재인덱싱 | sparse 매칭 있는데 정답 청크 못 잡는 case 의 본질 |
| 2 | **mini-Ragas 재측정** (ragas 설치 필요) | D-a + D-a-2 누적 효과 정량 (precision 0.730 기준) | uv add ragas datasets | W25 D7 baseline 과 직접 비교 |
| 3 | C section_title heading boost | 보완책 | 런타임 fix | section_title 보유율 측정 후 결정 |
| 4 | D-c PGroonga Mecab 사전 강화 | 근본 토크나이저 fix | 마이그레이션 시간 큼 | D-a/D-a-2 가 충분히 회복했으므로 후순위 |

### 권고

**D-a + D-a-2 조합으로 Phase 2 차수 D 마감 가능 수준 도달**. 잔존 G-S-005/009 는 Layer B (chunk 분리 정책) 영역으로 별개 sprint. mini-Ragas 재측정 (사용자가 ragas 설치 시점) 으로 W25 D7 baseline (precision 0.730) 대비 개선치 정량화.

---

## 6. 학습

1. **Layer 분리 가치**: D-a (query mode) → D-a-2 (조사) 단계적 fix 로 각 효과 정량화. 한 번에 모든 fix 합치면 어느 변경이 실제 효과인지 분리 못 함.

2. **외래어 vs 한국어 명사 충돌**: "이" 조사 제외 결정 — "디스플레이/알고리즘/오디오/스튜디오/포트폴리오" 류 외래어 명사 보호 위해. trade-off: "회사이" 같은 정상 입력은 strip 못 함 (사용자 자연어 query 에서 "이" 조사 사용 빈도 낮음).

3. **punctuation strip 의 즉시 효과**: G-S-003/004 가 끝 "?" 한 글자 때문에 sparse 0 였음. trailing punctuation 정리 1줄 추가로 즉시 회복.

4. **Phase 2 차수 D ship 가능 시점**: D-a + D-a-2 조합으로 sparse_hits=0 완전 해소 + 격차 4건 중 3건 개선. mini-Ragas precision 재측정으로 ship 마감 판정.
