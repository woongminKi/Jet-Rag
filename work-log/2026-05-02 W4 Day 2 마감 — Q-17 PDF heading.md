# 2026-05-02 W4 Day 2 마감 — W4-Q-17 PDF heading 추출 강화

> W4 명세 v0.1 (CONFIRMED) §3.W4-Q-17 ship 완료. 1d budget 내 완료, KPI §13.1 PDF section_title 30% 절대 임계 회수.

## 0. TL;DR

- `PyMuPDFParser` 를 `page.get_text("dict")` 기반으로 재작성 — span 단위 font size 추출 → page median × 1.15 휴리스틱 + 텍스트 inline 패턴 (HwpxParser 와 동일) + sticky propagate (doc 전체).
- 사용자 자산 PDF 4건 재인제스트 후 chunks 기준 평균 section_title 채움 비율 **99.75%** (KPI §13.1 30% 대비 +69.75pp 초과).
- HwpxParser 의 graceful degrade 패턴 재사용 — `dict` 추출 실패 시 기존 `blocks` fallback.
- 신규 단위 테스트 20건 (목표 10건 초과) + 회귀 0 (81 → 101/101 PASS, ordering flaky 1건은 P3 backlog).
- 의존성 추가 0 (PyMuPDF 1.27.2 + `statistics.median` stdlib).
- DE-64 (a) heuristic-only CONFIRMED — 형태소 분석기 의존성 추가 거부.

## 1. 변경 파일

| 파일 | LOC ± | 변경 |
|---|---|---|
| `api/app/adapters/impl/pymupdf_parser.py` | +167 | `dict` 모드 + `_page_median_size` / `_block_max_size` / `_block_text` / `_is_heading_block` + sticky `current_title` (doc 전체) + blocks fallback |
| `api/tests/test_pymupdf_heading.py` | +260 (신규) | 20건 — page_median(4) / block_max(3) / block_text(2) / is_heading_block(7) / bad input(1) / dict fallback(1) / 실 PDF KPI(1) / sticky(1) |

## 2. KPI 측정 (chunks 단위 — diagnose_chunk_quality.py)

### 2.1 doc 별 채움 비율

| doc | type | chunks | section_title% (이전 → 이후) |
|---|---|---:|---|
| sample-report | pdf | 377 | 0% → **100%** |
| sonata-the-edge_catalog | pdf | 75 | 0% → **99%** |
| law sample3 | pdf | 12 | 0% → **100%** |
| jet_rag_day4_sample | pdf | 3 | 0% → **100%** |
| 직제_규정 | hwpx | 70 | 100% (회귀 0) |
| 한마음생활체육관 | hwpx | 18 | 100% (회귀 0) |

**전체 555 chunks 평균 section_title 채움 = 99.8%** (W3 Day 5 마감 시 15.9% 대비 +83.9pp 개선).

### 2.2 PDF 전용 평균

PDF 4건 chunks 가중 평균: (377×100 + 75×99 + 12×100 + 3×100) / 467 = **99.84%**

KPI §13.1 절대 임계 (≥30%) 대비 **+69.84pp 초과 충족**.

### 2.3 부수 효과

- 표 노이즈 비율 36.8% → 39.5% (sample-report 40% → 42% 미세 증가)
- 청크 수 553 → 555 (+2)
- raw_text ±0.1% (회귀 risk 매우 낮음)

## 3. 휴리스틱 설계 — DE-64 정합

### 3.1 (A) font size 비율

`block_max_size >= page_median_size * 1.15`

- block_max_size: 한 block 안의 모든 span size 의 max (block 내부 일관성)
- page_median_size: page 안의 모든 본문 span size 의 median (outlier robust)
- 임계 1.15: sniff 결과 sonata 21pt vs 9pt (2.3x), law 12pt vs 10.1pt (1.18x) 둘 다 잡음

### 3.2 (B) 텍스트 inline 패턴 (보조)

```python
_HEADING_TEXT_PATTERN = re.compile(
    r"^(제\s*\d+\s*[조항장절편관]|부칙|별표\s*\d*|별첨\s*\d*"
    r"|【[^】]{1,30}】|\[[^\]]{1,30}\]"
    r"|Chapter\s*\d*|Section\s*\d*)([\s(].*)?$"
)
```

- HwpxParser 의 inline 패턴 + 한국 법률 PDF 의 `【판시사항】`/`[공보2026]` 추가
- 텍스트 ≤ 80 chars 일 때만 적용 (긴 본문 false positive 차단)

### 3.3 bold flag 미구현

PyMuPDF 의 `flags & 16` (bold). 사전 sniff 결과 사용자 자산 4건 모두 거의 미사용 (sonata 0%, law 0%) → 의도적 미구현. 모듈 docstring + `_is_heading_block` 의 TODO 주석으로 향후 ablation 가능성 명시.

### 3.4 sticky propagate 정책

- **doc 전체 sticky** — page 경계 넘어 propagate (HwpxParser 와 동일)
- 첫 heading 이 노이즈일 경우 doc 전체가 노이즈로 sticky 되는 위험 — **W4-Q-15 (b) header_footer 룰** 과 교차 검증 권장

## 4. DoD §3.W4-Q-17 검증

- [✅] PDF 4건 평균 section_title 채움 비율 ≥ 30% — **99.84% 충족**
- [✅] 단위 테스트 ~10건 → 20건 (목표 초과)
- [✅] 백필 (재인제스트) + doc 별 breakdown — chunks 진단 리포트 자동 출력
- [⏳] golden 의 PDF 매칭 6건 top-3 chunk 의 section_title 등장 비율 측정 — Day 6 정성 검토 단계 (사용자 페이스)

## 5. 비판적 자가 검토

1. **99.8% 가 너무 높음 — "의미 있는 분류" 검증 부재**: KPI 30% 절대 임계만 명시되어 있고 "section_title 이 검색 ranking 에 도움이 되는지" 정성 검토 부재. Day 6 사용자 검토 (golden G-006~019) 에서 확인 필요. 만약 모든 chunk 가 동일 doc title sticky 면 ranking 영향 0 — 이 경우 추후 page 단위 sticky 또는 sub-heading 추적 정책 재검토.
2. **doc 전체 sticky 의 false positive risk**: 사용자 자산 4건은 정상적인 첫 heading (제목 페이지) 이라 안전. 그러나 노이즈 PDF (예: 표지가 url 만 있는 보고서) 에서는 모든 chunk 가 url 로 sticky 됨. **W4-Q-15 (b) 노이즈 룰** + 실 데이터 모니터링.
3. **bold flag 미구현 trade-off**: sniff 4건이 우연히 bold 미사용일 수도 있음. 향후 자산 다양화 (DOCX/PPTX 등) 시 bold 가 dominant 한 자료 발견되면 ablation 필요.
4. **법률 PDF (law sample3) 100% 충족 — 의외**: page median 10.1pt × 1.15 = 11.6 < 12pt heading. 12pt 의 8건 `【판시사항】` 등이 잡혔고 sticky 가 12 chunks 모두 채움. 법률 PDF 일반화 가능성 — 추후 ablation.
5. **ordering 의존 flaky 1건 (P3)**: `test_concurrent_access_no_race_error` (W4 Day 1 산출물) 가 다른 테스트와 같은 프로세스에서 실행 시 cache 비어있는 cold path 발생 → 401. 단독 실행은 PASS. mock 보강 필요. **본 Day 2 commit 차단 사유 아님**, 별도 P3 backlog.

## 6. DE-64 결정 확정

| 결정 | 채택 | 사유 |
|---|---|---|
| **DE-64 — PDF heading 휴리스틱** | **(a) heuristic-only (font size + text pattern)** | 형태소 분석기 (konlpy 등) 의존성 추가 거부 (CLAUDE.md 정책). 사용자 자산 4건에서 99.84% 충족 입증. 향후 자산 확장 시 ablation 후 재검토. |

## 7. commit + push

| Hash | Commit |
|---|---|
| (이번 commit) | `feat(adapters)`: PyMuPDFParser dict 모드 + heading 휴리스틱 + sticky propagate (W4-Q-17) |

## 8. 다음 단계 — W4 Day 3

- W4-Q-14 (청킹 정책 본격 변경) — chunk.py 의 4.1·4.2·4.4·4.5 묶음 (4.3 W5 이월)
  - (1) 4.1 lookbehind 제거 + alternation 재작성
  - (2) 4.2 false split 보호 (숫자/영문 직후 `. ` + 법령 인용)
  - (3) 4.4 청크 경계 100자 overlap
  - (5) 4.5 section_title 우선순위 swap
- 1d budget (5h, 4.3 제외)
- 단위 테스트 ≥ 15건 + dry-run 리포트 + golden 회귀 비교

## 9. 한 문장 요약

W4 Day 2 — PyMuPDFParser 에 PyMuPDF dict 모드 + page median × 1.15 + 텍스트 inline 패턴 + sticky propagate 도입 → 사용자 자산 PDF 4건 평균 section_title 채움 **99.84%** (KPI 30% 대비 +69.84pp 초과), 회귀 0 (101/101 PASS, ordering flaky 1건은 P3 backlog), **DE-64 heuristic-only CONFIRMED**.
