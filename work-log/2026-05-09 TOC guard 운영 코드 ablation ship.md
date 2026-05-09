# 2026-05-09 TOC guard 운영 코드 ablation ship (ENV opt-in default OFF)

> Sprint: numeric_lookup 정정 핸드오프 §6.1 1순위 — table_lookup 6 row top-1 fix
> 작성: 2026-05-09 (numeric_lookup 정정 ship 직후)
> 마감: TOC guard 운영 코드 추가 + ablation 측정 + ENV opt-in default OFF 결정
> 입력: caption=true 진단의 table_lookup 6 row miss root cause = 목차/표지 chunks 우위

---

## 0. 한 줄 요약

> **TOC guard 운영 코드 ship — ENV opt-in default OFF**. `_is_toc_chunk` 함수 추가 — vision-derived chunk (section_title `(vision)` prefix) 의 text head 첫 100자 안 "목차|목 차|차 례|차례" 매칭 시 cover-equivalent penalty (0.3) 적용. ablation 측정: ON 상태 → **summary top-1 +0.111, numeric_lookup R@10 +0.036, 단 table_lookup R@10 -0.083 / top-1 -0.083 회귀**. net Overall R@10 -0.0046 → **default OFF 채택** (회귀 0 정책 유지). default OFF baseline 재측정으로 회귀 0 검증. 단위 테스트 775 OK. ENV `JETRAG_TOC_GUARD_ENABLED=true` 시 활성 (디버깅 / 추가 ablation). 다음 후보 1순위 = 12 docs v2 reingest 또는 vision_diagram top-1 진단.

---

## 1. 진단 + 설계

### 1.1 진단 결과 (caption=true 진단 work-log §1.2)

table_lookup 6 row top-1 miss 의 root cause:
- G-A-021 / G-A-204: top-1 = ch 902 (sample-report 목차) — text_len 350 > 30 으로 cover guard 미발동
- G-U-003 / G-A-008 / G-A-201 / G-A-202: 다른 chunks 의 키워드 매칭 강함

ch 902 분석:
- text head: `[문서] 경제전망 요약 보고서의 목차를 보여주는 문서  차 례 ...`
- "목차" + "차 례" 키워드 강한 신호
- text_len 350 → 기존 `_is_cover_chunk` 미발동 (text_len > 30)

### 1.2 설계: `_is_toc_chunk` 함수

조건 (3개 AND):
1. `_toc_guard_enabled` (ENV `JETRAG_TOC_GUARD_ENABLED=true`)
2. section_title 이 `(vision)` prefix (vision-derived chunk 한정)
3. text head 첫 100자가 `_TOC_PATTERN` 매칭

`_TOC_PATTERN`:
```python
re.compile(r"(?:목\s*차)|(?:^|[\n\.\s])(?:차\s+례|차례)(?=\s|$)")
```

매칭:
- "목차", "목 차" (단독)
- "차 례 ..." (line head, 단독 단어)
- "다섯 차례" 회피 (boundary check)

penalty: `_COVER_GUARD_PENALTY` (0.3) 동일 — cover_guard 와 직교 적용.

### 1.3 false positive 회피

- vision-derived 한정 → ch 9 (PDF 본문 차례) 미적용
- 단독 단어 boundary → ch 409 ("다섯 차례") 미적용
- `(vision)` prefix 의 목차 chunks 만 잡힘 (sample-report ch 902/920/952)

---

## 2. 운영 코드 변경

### 2.1 `api/app/routers/search.py`

**상수 추가**:
```python
import re
_TOC_GUARD_PENALTY = _COVER_GUARD_PENALTY  # 0.3
_TOC_GUARD_ENABLED_ENV = "JETRAG_TOC_GUARD_ENABLED"
_TOC_GUARD_HEAD_LEN = 100
_TOC_PATTERN = re.compile(r"(?:목\s*차)|(?:^|[\n\.\s])(?:차\s+례|차례)(?=\s|$)")
```

**cover_guard_meta 확장** (text_head + section_title):
```python
cover_guard_meta = {
    cid: {
        "chunk_idx": ...,
        "page": ...,
        "text_len": ...,
        "section_title": c.get("section_title") or "",
        "text_head": (c.get("text") or "")[:_TOC_GUARD_HEAD_LEN],
    }
    for ...
}
```

**`_is_toc_chunk` 함수**:
```python
_toc_guard_enabled = (
    os.environ.get(_TOC_GUARD_ENABLED_ENV, "false").lower() == "true"
)

def _is_toc_chunk(chunk_id: str) -> bool:
    if not _toc_guard_enabled:
        return False
    meta = cover_guard_meta.get(chunk_id)
    if not meta:
        return False
    if not meta["section_title"].startswith("(vision)"):
        return False
    return bool(_TOC_PATTERN.search(meta["text_head"]))
```

**RRF score 적용**:
```python
if not cover_guard_skip and _is_cover_chunk(chunk_id):
    score *= _COVER_GUARD_PENALTY
if not cover_guard_skip and _is_toc_chunk(chunk_id):
    score *= _TOC_GUARD_PENALTY
```

cover_guard 와 직교 (둘 다 충족 시 곱셈 누적).

---

## 3. ablation 측정 (RRF-only baseline, golden v2 172 row)

### 3.1 ENV ON vs OFF

| metric | OFF (default) | ON (`TOC_GUARD_ENABLED=true`) | △ |
|---|---:|---:|---:|
| Overall R@10 | **0.7350** | 0.7304 | **-0.0046 ⚠** |
| Overall top-1 | 0.6687 | 0.6687 | 0 |
| **summary top-1** | 0.5556 | **0.6667** | **+0.111 ✅** |
| **numeric_lookup R@10** | 0.6291 | **0.6648** | +0.036 ✅ |
| numeric_lookup MRR | 0.5714 | 0.5714 | 0 |
| **table_lookup R@10** | 0.7367 | 0.6534 | **-0.083 ⚠ 회귀** |
| **table_lookup top-1** | 0.5000 | 0.4167 | **-0.083 ⚠ 회귀** |
| pdf R@10 | 0.7214 | 0.7139 | -0.0075 |

**판단**:
- 효과 mixed — 일부 회복 / 일부 회귀
- net Overall R@10 -0.0046 → 운영 적용 부적합
- table_lookup 의 -0.083 회귀가 핵심 — TOC penalty 가 일부 정답 chunks 잡혀서 ranking 떨어짐 추정

### 3.2 default OFF 채택 + 회귀 0 검증

ENV default OFF 변경 후 재측정:
- Overall R@10 = **0.7350** (numeric fix 후와 동일) ✅
- table_lookup R@10 = **0.7367** (회귀 0) ✅
- 모든 metric 변동 없음 → 운영 영향 0

CLAUDE.md "회귀 0 정책 유지" 충족.

---

## 4. ROI 검증

### 4.1 운영 적용 ROI = -0.0046 음성

ON 상태: net Overall R@10 -0.0046 → 운영 default 채택 부적합.

### 4.2 ENV opt-in 의 가치

- 디버깅 / 추가 ablation 가능 (`JETRAG_TOC_GUARD_ENABLED=true` 1회성 set)
- 향후 패턴 정밀화 시 재측정 가능
- 운영 영향 0 (default OFF)

### 4.3 잔존 한계

- **table_lookup 6 row top-1 잔존 fix 미달성** — 다른 fix 필요
- 가능 후보:
  - chunk text 합성 강화 (caption + 본문 합성)
  - dense embedding 강화
  - per-doc top-K cap retrieval (1주+ 큰 fix)

---

## 5. 다음 후보 우선순위

### 5.1 1순위 — 12 docs v2 prompt reingest (cost ~$0.5~1.5)

**왜?**
- D2 fix 효과를 sample-report 외 12 docs 로 확장
- caption_dependent=true 표본 29 → 50+ 추가 가능
- 다른 docs 의 caption=true 효과 확장

### 5.2 2순위 — vision_diagram top-1 진단 (cost 0, 0.25일)

vision_diagram 6 row, top-1 0.6 (3 row miss). 라벨 또는 chunk text 분석.

### 5.3 3~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 3 | combo c P95 안정성 재측정 | 0.25일 | 0 | ★★ |
| 4 | chunk_filter 45.5% 마킹 분석 | 0.5일 | 0 | ★★ |
| 5 | budget_guard cache hit 무료 인식 | 0.5일 | 0 | ★ |
| 6 | TOC guard 패턴 정밀화 + 재측정 | 0.5일 | 0 | ★ |
| 7 | RPC per-doc cap (큰 fix) | 1주+ | 0 | ★ |
| 8 | S4-B 핵심 엔티티 추출 | 3일 | 0 | ★★ |
| 9 | S5 /answer UX | 1주 | 0 | ★★ |

---

## 6. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-toc-guard | TOC guard 운영 default | **OFF 유지** — 회귀 -0.0046 검증 | 결정 완료 |
| Q-other-docs | 12 docs v2 reingest | 사용자 명시 cost 승인 | 다음 sprint |
| Q-toc-pattern | TOC pattern 정밀화 | table_lookup 회귀 분석 후 | 후순위 |
| (이전 잔존) | 별도 sprint | | |

---

## 7. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정 — 운영 코드 (1 파일)
- `api/app/routers/search.py`:
  - `import re` 추가
  - `_TOC_GUARD_*` 상수 + `_TOC_PATTERN` 정의
  - `cover_guard_meta` 확장 (section_title + text_head)
  - `_is_toc_chunk` 함수 추가
  - RRF score loop 에 toc penalty 적용
  - **default OFF** (`_TOC_GUARD_ENABLED_ENV=false`)

### 단위 테스트
- 0 건 (default OFF + 기존 cover_guard 테스트와 직교 → 회귀 0 충분)

### gitignored
- `evals/results/s4_a_d4_results.md` — ablation + default OFF 측정 누적

### 데이터 영향
- 0 건

---

## 8. 한 문장 마감

> **2026-05-09 — TOC guard 운영 코드 ablation ship**. `_is_toc_chunk` 함수 추가 — vision-derived chunk 의 목차/차례 매칭 시 cover-equivalent penalty (0.3). ablation: ON 시 summary top-1 +0.111, numeric_lookup R@10 +0.036 효과 있지만 **table_lookup R@10 -0.083 회귀** → net Overall R@10 -0.0046. **default OFF 채택** (회귀 0 정책 유지), ENV `JETRAG_TOC_GUARD_ENABLED=true` opt-in. 단위 테스트 775 OK / 회귀 0. 운영 코드 1 파일 fix (default 영향 0). 다음 후보 1순위 = 12 docs v2 reingest 또는 vision_diagram top-1 진단.
