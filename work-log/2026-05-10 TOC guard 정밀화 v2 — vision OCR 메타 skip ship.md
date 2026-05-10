# 2026-05-10 TOC guard 정밀화 v2 — vision OCR 메타 설명 skip ship

> Sprint: TOC guard 패턴 추가 정밀화 (G-A-110 false positive 회복)
> 작성: 2026-05-10
> 마감: vision OCR 메타 설명 (`[문서] ... \n\n`) skip 후 본문 head 매칭 + 5 unit tests + ablation 재측정 → G-A-110 회복 + Overall +0.0035 도달
> 입력: 직전 sprint (TOC 정밀화 v1 — query intent skip) 의 잔존 회귀 G-A-110 / G-A-204 + chunk 77/902 text head 직접 분석

---

## 0. 한 줄 요약

> **TOC guard 정밀화 v2 ship — vision OCR 메타 설명 skip**. chunk 77 (G-A-110 FP) 직접 분석 → text head `[문서] Mugip ... 정책서 목차를 보여주는 문서\n\n사이드 Mugip 프로토타입 IA...` — "목차" 가 메타 설명에만 등장 (실제 chunk 본문 X). `_strip_vision_meta_prefix` 추가로 `[문서] ... \n\n` 제거 후 본문 매칭 → **G-A-110 false positive 완전 해결 ✅**. Ablation: TOC ON v3 R@10 0.7076 → **0.7111 (+0.0035)**, top-1 0.8521 → **0.8580 (+0.006)** — v2 (+0.003) 대비 추가 +0.0009. 잔존 1 row 회귀 (G-A-204 -0.250) — 라벨링 문제 (별도 sprint). 단위 테스트 809 → 814 (+5) / 회귀 0. 운영 default OFF 유지 (사용자 명시 결정 권고). 누적 cost 변동 0.

---

## 1. 변경 내역

### 1.1 `api/app/routers/search.py`

**추가 — `_strip_vision_meta_prefix(text_head)`**:
```python
_VISION_META_PREFIX = "[문서]"
_VISION_META_BODY_SEP = "\n\n"

def _strip_vision_meta_prefix(text_head: str) -> str:
    """vision OCR 의 `[문서] ... \\n\\n` 메타 설명 제거 후 본문 head 반환."""
    if not text_head.startswith(_VISION_META_PREFIX):
        return text_head
    idx = text_head.find(_VISION_META_BODY_SEP)
    if idx == -1:
        return text_head
    return text_head[idx + len(_VISION_META_BODY_SEP):]
```

**수정 — `_is_toc_chunk`**:
```python
# 2026-05-10 정밀화 2 — vision OCR 메타 설명 (`[문서] ... \n\n`) 제거 후 매칭.
body_head = _strip_vision_meta_prefix(meta["text_head"])
return bool(_TOC_PATTERN.search(body_head))
```

→ chunk 본문 head 만 매칭, 메타 설명에 들어간 "목차" / "차례" 무시.

### 1.2 `api/tests/test_toc_intent_pattern.py` — 5 신규 tests (StripVisionMetaPrefixTest)

- `test_strips_vision_meta_prefix` — chunk 77 패턴 강조 → 본문에 "목차" 없음 확인
- `test_keeps_text_when_no_meta_prefix` — `[문서]` 시작 없으면 원본 유지
- `test_keeps_text_when_no_double_newline` — `[문서]` 시작이지만 `\n\n` 없으면 graceful
- `test_real_toc_chunk_keeps_pattern_match` — ch 902 본문은 strip 후에도 "차 례" 매칭 (TP)
- `test_chunk_77_pattern_no_match_after_strip` — ch 77 본문은 strip 후 매칭 안 됨 (FP 차단)

### 1.3 검증

- **단위 테스트**: 809 → **814 (+5) / OK / skipped=1 / 회귀 0**
- **D4 ablation 재측정**: golden v2 178 row, BGE-M3 안정 (222s)

---

## 2. chunk text head 직접 분석 결과

### 2.1 false positive — chunk 77 (G-A-110)

```
doc_id: 9878d7bd-4766-40fa-bebb-7da45f879768 (음악 감상 doc)
page: 14
section_title: '(vision) p.14 OCR 텍스트'
text_head:
  [문서] Mugip 서비스의 프로토타입 화면, 정보 구조도, 정책서 목차를 보여주는 문서
  ↓ \n\n
  사이드 Mugip 프로토타입 IA 로그인 로그인 방법 - 이메일 로그인 - 카카오 로그인
```

**문제**: vision OCR 의 자동 메타 설명 (`[문서] X 를 보여주는 문서`) 에 "정책서 목차" 가 포함. 실제 chunk 본문은 prototype/IA 화면 설명 → TOC 아님. 기존 패턴은 text_head 첫 100자 매칭이라 메타 설명만 봄.

**Fix 후**: `[문서] ... \n\n` 제거 → 본문 "사이드 Mugip..." 매칭 시도 → "목차" 없음 → no penalty ✅

### 2.2 true positive — chunk 902 (G-A-200/204)

```
doc_id: d1259dfe-c402-4cd0-bb04-3e67d88a2773 (경제전망)
page: 5
section_title: '(vision) p.5 OCR 텍스트'
text_head:
  [문서] 경제전망 요약 보고서의 목차를 보여주는 문서
  ↓ \n\n
  차 례
  경제전망 요약
  국내외 여건 및 전망
  ...
```

**Fix 후**: `[문서] ... \n\n` 제거 → 본문 "차 례\n경제전망 요약..." → "차 례" 매칭 → penalty 적용 (TP) ✅

### 2.3 추가 true positive — chunk 920 / 952 (G-A-200 보완)

- ch 920: 본문 `국내외 여건 및 전망\n1. 주요 여건 점검\n목 차\n대외여건\n2\n대내여건\n14...` — "목 차" 매칭 ✅
- ch 952: 본문 `국내외 여건 및 전망\n2. 거시경제 전망\n목 차\n경제성장...` — "목 차" 매칭 ✅

→ 진짜 TOC chunks 모두 매칭 유지. FP 만 제거.

---

## 3. Ablation 결과 (TOC ON v3 vs v2 vs v1 vs OFF)

### 3.1 Overall

| metric | TOC OFF | TOC ON v1 | TOC ON v2 (intent skip) | **TOC ON v3 (+ meta strip)** |
|---|---:|---:|---:|---:|
| R@10 | 0.7076 | 0.7043 (-0.003 ⚠) | 0.7102 (+0.003 ✅) | **0.7111 (+0.0035 ✅)** |
| top-1 | 0.8521 | 0.8521 (0) | 0.8580 (+0.006 ✅) | **0.8580 (+0.006 ✅)** |

### 3.2 qtype 별 변화 (TOC ON v3 vs OFF, |△| ≥ 0.01)

| id | qtype | △ R@10 | top-1 변화 | 비고 |
|---|---|---:|---|---|
| G-U-010 | synonym_mismatch | +0.333 ✅ | (변동 X) | (v2 와 동일) |
| G-U-020 | summary | 0 | ✗→✓ ✅ | (v2 와 동일) |
| G-A-024 | numeric_lookup | +0.222 ✅ | (변동 X) | (v2 와 동일) |
| G-A-029 | exact_fact | +0.286 ✅ | (변동 X) | (v2 와 동일) |
| **G-A-110** | exact_fact | **0 (회복 ✅, meta strip)** | (변동 X) | **v3 신규 회복** ⭐ |
| **G-A-200** | table_lookup | **0 (회복 ✅, intent skip)** | (변동 X) | (v2 회복 유지) |
| G-A-204 | table_lookup | -0.250 ⚠ | (변동 X) | 잔존 회귀 — 라벨링 문제 |

### 3.3 잔존 회귀 G-A-204 분석

- query: "2026년 경제전망 요약표 어디 있어"
- relevant=918, acceptable=**902**, 898
- TOC ON: ch 902 (목차) penalty → top10 밖 → R@10 0.5 → 0.25
- **본질**: 사용자 의도는 "요약표" (vision content) — acceptable 에 ch 902 (목차) 가 라벨링된 게 의문
- → 라벨 정정 sprint (acceptable 902 → 916 같은 요약표 chunk 로 교체) 가 진짜 fix

**본 sprint scope 외** — 패턴 정밀화로는 더 회복 어려움. 라벨링 sprint 권고.

---

## 4. 비판적 한계 (정직 인정)

| # | 한계 | 영향 | 회복 |
|---|---|---|---|
| 1 | **G-A-204 잔존 회귀 (-0.250, table_lookup)** | TOC ON 시 1 row 회귀 | 라벨 정정 sprint (acceptable 902 → 916 같은 요약표 chunk) |
| 2 | **메타 설명 prefix 검출 = `[문서]` literal** | vision OCR 의 메타 패턴 변경 시 깨질 가능성 | adapter 측 변경 시 본 패턴도 함께 갱신 (별도 sprint) |
| 3 | **`\n\n` separator 없으면 graceful 원본 유지** | 일부 chunk 의 메타 설명이 `\n` 1개로 끝나면 strip 안 됨 | 실 chunk 분포에 0건 (vision OCR 표준 출력 형식) |
| 4 | **운영 default 변경 미결** | TOC ON 채택 시 +0.0035 R@10 / +0.006 top-1 / 단 G-A-204 1 row 회귀 | 사용자 명시 결정 (Q-toc-default-v3) |

---

## 5. 사용자 결정 보류 항목 변동

| ID | 항목 | 직전 | 갱신 |
|---|---|---|---|
| Q-toc-default-v2 | TOC default ON 채택 (v2 정밀화 후) | 사용자 결정 — 2 row 회귀 | **갱신** — v3 정밀화로 1 row 회귀만 잔존 |
| Q-toc-default-v3 | TOC default ON 채택 (v3 정밀화 후) | 신규 | **사용자 결정** — net +0.0035 R@10 / +0.006 top-1, G-A-204 1 row 회귀 (라벨링 문제) |
| Q-204-relabel | G-A-204 acceptable 902 → 요약표 chunk 정정 | 신규 | **별도 sprint** — 라벨 정정으로 G-A-204 회귀 해소 가능 |

---

## 6. 다음 후보 우선순위

### 6.1 1순위 — TOC default ON 채택 결정 (cost 0, 0.1 day, 사용자 결정)

본 sprint v3 결과 (+0.0035 R@10, +0.006 top-1, FP 2건 모두 해결, 잔존 1 row 회귀는 라벨링 문제) 기반 사용자 명시 채택.

### 6.2 2순위 — G-A-204 라벨 정정 (cost 0, 0.25 day)

acceptable 902 (목차) → 실제 요약표 chunk 식별 후 교체. 정정 후 G-A-204 회귀 해소 → TOC ON 의 잔존 회귀 0.

### 6.3 3순위 — expected_summary 정정 (cost 0, 0.5 day)

G-U-105~107 의 chunk-text 같은 expected_summary → 실 summary 정정.

### 6.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | cost 가드레일 80% 알림 절차 | 0.25 day | 0 | ★★ |
| 5 | uvicorn 좀비 모니터링 자동화 | 0.5 day | 0 | ★ |
| 6 | cross_doc qtype 자동 생성 (B 후속) | 1 day | ~$0.05 | ★★ |
| 7 | visual_grounding metric 신설 | 1 day | ~$0.05 | ★★ |
| 8 | acceptable_chunks LLM-judge 자동 보완 | 1 day | ~$0.10 | ★★ |
| 9 | S4-B 핵심 엔티티 추출 | 3 day | 0 | ★★ |

---

## 7. 핵심 변경 파일 목록

### 수정
- `api/app/routers/search.py` — `_strip_vision_meta_prefix` 추가 + `_is_toc_chunk` 가 본문 head 매칭

### 추가
- `api/tests/test_toc_intent_pattern.py` — `StripVisionMetaPrefixTest` 5 테스트
- 본 work-log

### 일회성 (gitignored, /tmp)
- `/tmp/inspect_toc_chunks.py` — chunk text head 분석 helper

### gitignored 산출 (본 work-log §3 reproduced)
- `evals/results/s4_a_d4_toc_on_v3.md`, `s4_a_d4_raw_toc_on_v3.json`

### 데이터 영향
- 0 건

### 운영 코드 변경 영향
- TOC ON 시 vision-derived chunk 의 본문 매칭 정확도 ↑
- TOC OFF (default) 시 변경 없음

### 외부 cost
- 0 (RRF-only ablation + DB 직접 query)
- 누적 (이번 세션 전체): ~$0.31 (변동 없음)

---

## 8. 한 문장 마감

> **2026-05-10 — TOC guard 정밀화 v2 ship**. `_strip_vision_meta_prefix` 추가 — vision OCR 메타 설명 (`[문서] ... \n\n`) 제거 후 본문 head 매칭. **G-A-110 false positive 완전 해결 ✅**. Ablation: TOC ON v3 R@10 0.7076 → **0.7111 (+0.0035)**, top-1 0.8521 → **0.8580 (+0.006)**. 잔존 1 row 회귀 (G-A-204 -0.250) — 라벨링 문제 (별도 sprint). 단위 테스트 809 → 814 (+5) / 회귀 0. 운영 default OFF 유지 (사용자 명시 결정 권고). 누적 cost 변동 0. 다음 1순위 = **TOC default ON 채택 결정** (사용자) 또는 **G-A-204 라벨 정정** (cost 0, 0.25 day).
