# W25 D8 — Phase 2 메뉴 footer 가드 시도 → **롤백** + 후속 sprint 신호

> **결론**: 런타임 메뉴 footer score 패널티는 **본 PDF 카탈로그 구조에서 부적절** — 모든 본문 페이지에 메뉴 footer 가 등장하여 정답 청크와 노이즈 청크가 동일 비율로 가드 trigger. 단순 매칭 / 비율 기반 / 정밀 패턴 모두 실패. **chunk 분리 정책 (Phase 2 차수 B)** 또는 **PGroonga 한국어 sparse 회복 (D)** 으로 근본 해결 필요.

---

## 0. 진입 컨텍스트

- W25 D7 mini-Ragas Phase 1 측정 — Recall 100% / Precision 0.730, 4건 격차
- SQL 진단 (D7~D8 직전) — SONATA 카탈로그 99 chunks 중 ~70% 청크에 메뉴 footer 반복
  ("Home Intro Exterior Interior Function Performance Hyundai SmartSense Hybrid N Line Features Colors Customizing Specifications")
- 가설: 메뉴 footer 가 변별력 부족 → 모든 검색에 고르게 매칭 → ranking 격차 4건의 공통 원인
- 결정: 표지 가드 (W25 D4) 와 동일 패턴으로 런타임 메뉴 footer 가드 도입 (재인덱싱 회피)

## 1. 시도한 안

### 1차 — 단순 패턴 매칭 (`Home\s+Intro\s+Exterior\s+Interior\s+Function\s+Performance`)

```python
if _MENU_FOOTER_PATTERN.search(text):
    score *= 0.5  # _MENU_FOOTER_PENALTY
```

mini-Ragas 측정 (1차 시도 ship 직후):

| QA | Phase 1 | Phase 2 1차 | 변화 |
|---|---|---|---|
| G-S-001 전장 | 0.50 | 0.50 | — |
| G-S-005 트림 | 0.05 | 0.06 | 미세 개선 |
| G-S-006 N Line | **0.50** | **0.03** | **악화** |
| G-S-008 디스플레이 | 0.25 | 0.33 | 개선 |
| **평균** | **0.730** | **0.692** | **악화** |

악화 원인 — 정답 청크 chunk_idx 37/38/43 등 본문 + 메뉴 합산 청크도 같이 깎임.

### 2차 — 비율 기반 가드 (`ratio >= 0.30 일 때만 패널티`)

```python
match = _MENU_FOOTER_PATTERN.search(text)
if match and (len(match.group(0)) / len(text)) >= 0.30:
    score *= 0.5
```

SQL 분석 (실제 청크 비율):

```
idx   text_len  match_len  ratio   유형
4     253       49         0.19    정답 (본문+메뉴) — 보호 대상
8     125       49         0.39    메뉴 단독 — 패널티 대상
37    214       49         0.23    정답 (N Line 본문+메뉴) — 보호 대상
38    191       49         0.26    정답 (N Line 본문+메뉴) — 보호 대상
43    232       49         0.21    정답 (트림 본문+메뉴) — 보호 대상
```

→ ratio 0.30 임계값으로 두 그룹 분리 가능해 보였음.

**문제**: `[^\n]*` 으로 패턴을 확장하니 매칭이 본문까지 흡수 → 단위 테스트 fixture 의 ratio 가 0.5 초과 → 임계값 의도 깨짐.

### 3차 — 정밀 패턴 (130자 고정 시퀀스)

```python
_MENU_FOOTER_PATTERN = re.compile(
    r"Home\s+Intro\s+Exterior\s+Interior\s+Function\s+Performance"
    r"\s+Hyundai\s+SmartSense\s+Hybrid\s+N\s+Line\s+Features"
    r"\s+Colors\s+Customizing\s+Specifications"
)
```

매칭 길이 ≈ 130자 — 본문 + 메뉴 청크 (text_len 200~250) 내 비율 0.5~0.65 → **임계값 0.30 으로는 정답 청크 보호 불가**.

## 2. 본질적 한계 — 왜 런타임 가드가 부적절한가

| 청크 유형 | text_len | menu_len (130자) | ratio | 패널티 의도 |
|---|---|---|---|---|
| 메뉴 단독 | 125 | 125 | 1.00 | ✅ 적용 |
| 본문 + 메뉴 (정답) | 200~250 | 130 | 0.52~0.65 | ❌ X (보호) |
| 본문 + 메뉴 (정답, 길이 짧음) | 191 | 130 | 0.68 | ❌ X (보호) |

→ 모든 페이지의 본문 청크가 메뉴를 포함하기 때문에 ratio 임계값으로는 두 그룹을 분리할 수 없음. 메뉴 단독 청크와 본문+메뉴 청크 모두 ratio >= 0.50 영역에 분포.

**근본 원인**: chunk 단위가 PDF 페이지 끝에서 잘리지 않고 메뉴 footer 가 본문에 항상 합쳐짐. 즉 **chunk 분리 정책 자체** 문제.

## 3. 롤백 결정

- 가드 코드 / 패턴 / 상수 / 단위 테스트 모두 제거
- search.py 는 W25 D7 종료 시점으로 정확히 회귀
- mini-Ragas 재측정 → Phase 1 결과 (0.730) 정확 복원

검증 (재측정):

```
G-S-001 전장        0.50  (회복)
G-S-005 트림        0.05  (회복)
G-S-006 N Line      0.50  (회복) ← 1차 시도에서 0.03 까지 악화한 케이스
G-S-008 디스플레이  0.25  (회복)
평균 Precision     0.730  (회복)
```

회귀 0 — `api` 287 tests OK.

## 4. 후속 sprint 진입 신호 (강력)

본 시도가 **Phase 2 차수 (A) 런타임 가드 부적절** 을 정량 증명. 따라서 다음 차수로 진입:

### 차수 (B) — chunk 분리 정책 (재인덱싱 필요) **← 1순위 후보**

`api/app/ingest/stages/chunk.py` 가 메뉴 footer 패턴을 detect 하여 boundary 로 분리 + 본 청크에서 제외:

- 효과: 정답 청크가 메뉴 없이 본문만 남게 됨 → 임베딩 변별력 회복
- 비용: 재인덱싱 1회 (SONATA 99 chunks → 약 60~70 chunks 로 축소 예상)
- 위험: 다른 PDF 카탈로그 (메뉴 footer 패턴 다양) 에 일반화 어려움 — 사용자 doc 별 학습 필요

### 차수 (C) — section_title heading boost (런타임)

검색어가 청크의 `section_title` 과 매칭되면 score 가산 (예: q="N Line" + section_title="N Line" 매칭 시 ×1.5).

- 효과: 정답 청크가 heading 보유 시 후순위에서 회복 가능
- 비용: 런타임 가드, 마이그레이션 0
- 위험: section_title 추출 누락 청크는 효과 X. SONATA 카탈로그 청크 중 section_title 보유율 측정 필요.

### 차수 (D) — PGroonga 한국어 sparse 회복 **← 근본**

현재 `소나타 N Line` 같은 의문문에 PGroonga 0건 매칭 사례 다수. dense 단독 ranking 의존 → 노이즈 청크 우세. PGroonga Mecab 토크나이저 / 인덱스 갱신으로 sparse 매칭률 복원 시 RRF 가 자동으로 정답 청크를 끌어올림.

- 효과: 본 mini-Ragas 4건 격차 모두 해결 가능성
- 비용: 마이그레이션 신규 / 형태소 사전 튜닝 (한 sprint 이상 소요 가능)
- 위험: PGroonga 설정 시간 비용

### 권고

1. **차수 (D) PGroonga 회복** 을 먼저 진단 — sparse_hits 로그 분석 (4건 격차 케이스에서 sparse 가 0인지 확인)
2. PGroonga 가 정상 작동 하는데도 정답 청크가 후순위면 차수 (B) chunk 분리 정책 진입
3. 차수 (C) heading boost 는 (B) 와 병행 가능

## 5. 변경 파일 (롤백 후)

- `api/app/routers/search.py` — W25 D7 시점으로 정확 회귀. 단, 메뉴 가드 시도 결과를 주석으로 남김 (재시도 방지):

```python
# W25 D8 Phase 2 — 메뉴 footer 가드: 1차 시도 실패 → 롤백 / 후속 sprint 신호로 보존.
# 시도 결과 (work-log/2026-05-04 W25 D8 Phase 2 메뉴 footer 가드.md):
#   - 단순 패턴 매칭 → 정답 청크 (idx 37/38/43) 도 함께 깎임 → G-S-006 0.50→0.03 악화
#   - 비율 기반 (ratio >= 0.30) → 본문 + 메뉴 합산 청크 ratio 0.5~0.65 로 정답 보호 실패
#   - 정밀 패턴 (130자 고정 시퀀스) 도 동일 — 모든 페이지에 메뉴가 등장하여 변별력 부족이 본질
# 결론: 런타임 score 패널티로는 해결 불가. chunk 분리 정책 (Phase 2 차수 B) 또는 PGroonga
#       한국어 sparse 회복 (D) 으로 근본 해결해야 함.
```

- `work-log/2026-05-04 ragas-mini-result.md` — 자동 갱신됨 (Phase 1 결과로 회복, 평균 0.730)
- `work-log/검색 파이프라인 동작 명세 (living).md` — §9 변경 이력에 W25 D8 한 줄 추가 (시도 → 롤백 / 본문 §1~§8 변경 0)

## 6. 학습

1. **mini-Ragas Phase 1 (W25 D7 ship) 가 즉시 진단 도구로 작동** — 시도 직후 G-S-006 악화 캐치, 추측이 아닌 정량 신호. mini-Ragas 가 없었으면 본 가드를 ship 한 채 모르고 운영했을 위험.
2. **표지 가드 패턴 재사용 무리수** — 표지 청크는 1/99 (1.0%) 의 명확한 outlier 였음. 메뉴 footer 는 70/99 (70%) 분포 → 본질적으로 다른 문제. 같은 패턴 적용 부적절.
3. **재인덱싱 회피 우선 원칙의 한계** — 인덱스 후처리 (런타임 score) 로 해결 못하는 분포 존재. 차수 (B) chunk 분리 정책 같은 인덱싱 시점 개입이 필요한 케이스 인지.
4. **PDF 카탈로그 구조 이해 필요** — 메뉴 footer 가 모든 페이지에 등장하는 PDF (마케팅 카탈로그 등) 와 메뉴 없는 PDF (논문, 보고서) 는 다른 처리 정책이 필요할 수 있음.

---

## 남은 이슈

- W25 D8 Phase 2 차수 (A) 런타임 가드 — **부적절 판정 / closed**
- mini-Ragas precision 0.730 격차 4건 — **미해결 / 후속 sprint 진입 필요**

## 다음 스코프 (W25 D9 / D10 후보)

- **D2-search (P1)** — PGroonga 한국어 sparse 진단 + 회복 시도 (차수 D)
- chunk 분리 정책 (차수 B) — `chunk.py` 메뉴 footer detect + boundary 분리, dry-run 후 재인덱싱
- section_title heading boost (차수 C) — 런타임 추가 가드, (B) 와 병행 가능
