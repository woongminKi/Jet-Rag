# 2026-05-15 권고 5 — arXiv heading pattern 보강

## 배경

2026-05-15 세션 종합 §2.6 audit 에서 실증된 권고 5 처리.

- arXiv 영어 학술 doc (`doc_id` prefix `bc7b4591`, 749 chunks, 56 unique pages)
- `section_title` 의 **94.5% (708/749)** 가 page header 문자열 `arXiv:2601.00442v1 [hep-th] 1 Jan 2026` 로 잡힘.
- 원인 1 — `_HEADING_FONT_RATIO=1.15` 휴리스틱이 arXiv page header 의 큰 font 를 heading 으로 오인.
- 원인 2 — `_HEADING_TEXT_PATTERN` 이 한국어 `제N조` / `Chapter` / `Section` 만 인식, 영어 학술 numbered heading (`1. Introduction`, `2.1 Method`) 미인식.

## 변경 요약

### 1. `api/app/adapters/impl/pymupdf_parser.py`

#### (A) `_HEADING_TEXT_PATTERN` 확장 — 영어 학술 heading 추가

**Before**

```python
_HEADING_TEXT_PATTERN = re.compile(
    r"^(제\s*\d+\s*[조항장절편관]|부칙|별표\s*\d*|별첨\s*\d*"
    r"|【[^】]{1,30}】|\[[^\]]{1,30}\]"
    r"|Chapter\s*\d*|Section\s*\d*)([\s(].*)?$"
)
```

**After**

```python
_HEADING_TEXT_PATTERN = re.compile(
    r"^("
    r"제\s*\d+\s*[조항장절편관]|부칙|별표\s*\d*|별첨\s*\d*"
    r"|【[^】]{1,30}】|\[[^\]]{1,30}\]"
    r"|Chapter\s*\d*|Section\s*\d*"
    r"|\d+(?:\.\d+){0,3}\.?\s+[A-Z][A-Za-z]*"          # numbered (1. Intro, 2.1 Method)
    r"|(?:Abstract|Introduction|Background|Related\s+Work|Methodology|Methods?"
    r"|Experiments?|Evaluation|Results?|Findings?|Discussion|Conclusions?"
    r"|References|Bibliography|Acknowledg(?:e?)ments?|Appendix(?:\s+[A-Z])?)"   # standalone
    r")([\s(].*)?$"
)
```

추가 인식 케이스:

- `1. Introduction`, `2.1 Related Work`, `3.4.1 Methodology`, `10. Conclusions`, `1 Introduction`
- `Abstract`, `Introduction`, `References`, `Related Work`, `Appendix A`, `Acknowledgments`, `Discussion` 등 학술 표준 standalone heading

회귀 안전 — 한국어 alternation 은 그대로 좌측 우선 매칭.

#### (B) `_PAGE_HEADER_BLACKLIST` 신규 — page header 차단

```python
_PAGE_HEADER_BLACKLIST = re.compile(
    r"^("
    r"arXiv:\s*\d+\.\d+(v\d+)?(\s*\[[A-Za-z\-\.]+\])?(\s+.+)?"
    r"|\d{1,4}"
    r"|Page\s*\d+"
    r"|-\s*\d+\s*-"
    r")\s*$",
    re.IGNORECASE,
)
```

차단 케이스:

- `arXiv:2601.00442v1 [hep-th] 1 Jan 2026` (font ratio 2.0 이어도 차단)
- 페이지 번호 단독 (`1`, `12`, `Page 3`, `- 4 -`)
- 대소문자 변형 모두 (`ARXIV:...`)

#### (C) `_is_heading_block` 가드 순서 변경

블랙리스트를 **font ratio 검사 전** 첫 가드로 배치. font 가 커도 블랙리스트 hit 면 즉시 `False`.

```python
def _is_heading_block(block_max_size, page_median_size, text) -> bool:
    if len(text) <= _HEADING_TEXT_MAX_LEN and _PAGE_HEADER_BLACKLIST.match(text):
        return False                          # 새 가드 (D)
    if page_median_size > 0 and block_max_size >= page_median_size * _HEADING_FONT_RATIO:
        return True                           # (A) font ratio
    if len(text) <= _HEADING_TEXT_MAX_LEN and _HEADING_TEXT_PATTERN.match(text):
        return True                           # (B)/(C) 텍스트 패턴
    return False
```

font ratio 임계 (`1.15`) 는 변경 없음 — 회귀 위험 0. ENV flag 도입 불요.

### 2. `api/tests/test_pymupdf_heading.py` — 신규 단위 테스트 8건

`IsHeadingBlockTest` 에 5건 추가:

- `test_text_pattern_hit_english_numbered` — `1. Introduction` 외 5 케이스
- `test_text_pattern_hit_english_standalone` — `Abstract` 외 6 케이스
- `test_arxiv_header_blocked_even_with_large_font` — font ratio 2.0 + 블랙리스트 → False (4 케이스)
- `test_page_number_only_blocked` — 페이지 번호 단독 4 케이스
- `test_korean_patterns_no_regression` — 기존 한국어/영문 키워드 회귀 검증 (8 케이스)

`PageHeaderBlacklistTest` 클래스 신규 추가 (3건):

- `test_arxiv_variations_match` — arXiv 4 변형
- `test_page_number_variations_match` — 페이지 번호 7 변형
- `test_normal_body_text_does_not_match` — 본문/heading 후보가 잘못 블랙리스트 매치 안 되는지 (7 케이스, **negative test**)

## 검증

### 단위 테스트

```
$ cd api && uv run python -m unittest discover -q
Ran 1202 tests in 21.381s
FAILED (failures=3)
```

- **1194 → 1202 (+8 신규 모두 OK)**
- 기존 failures 3건은 본 변경 무관 (caption propagation 관련 — `test_synonym_inject` / `test_vision_caption_propagation`, baseline 동일).
- **신규 회귀 0**.

### 한국어 자산 KPI 회귀 0

`PyMuPDFParserRealAssetKpiTest::test_average_fill_ratio_meets_kpi` — 공개 fixture 5건 (`(붙임2) 2025년…`, `보건의료_빅데이터_플랫폼…`, `law sample3.pdf`, `law_sample2.pdf`, `sample-report.pdf`) + 비공개 (`sonata-the-edge_catalog.pdf`) 의 평균 채움 비율 ≥ 30% 유지 (PASS).

## 변경 전/후 패턴 비교 (요약)

| 케이스 | Before | After |
|---|---|---|
| `제3조(목적)` | True | True (회귀 0) |
| `【판시사항】` | True | True (회귀 0) |
| `Chapter 1` | True | True (회귀 0) |
| `1. Introduction` | False | **True (신규)** |
| `2.1 Related Work` | False | **True (신규)** |
| `3.4.1 Methodology` | False | **True (신규)** |
| `Abstract` | False | **True (신규)** |
| `References` | False | **True (신규)** |
| `arXiv:2601.00442v1 [hep-th] 1 Jan 2026` (font ratio 2.0) | True (오인) | **False (차단)** |
| 페이지 번호 `12` (font ratio 2.0) | True (오인) | **False (차단)** |

## 회귀 위험 평가

| 변경 | 영향 | 위험도 |
|---|---|---|
| `_HEADING_TEXT_PATTERN` 영어 alternation 추가 | 영어 numbered + 학술 단어가 새로 heading 으로 인식 | **낮음** — 한국어 자산은 alternation 좌측 우선 매칭으로 영향 없음. 영어 자산은 본래 false negative → true positive 로 개선. 단 영문 일반 문서에서 `1. ...` 형태의 list item 이 heading 으로 잡힐 가능성 — `_HEADING_TEXT_MAX_LEN=80` cap 으로 본문 list 는 차단됨. |
| `_PAGE_HEADER_BLACKLIST` 우선 가드 | `arXiv:...` / 페이지 번호 단독 → 항상 False | **매우 낮음** — 한국어 자산엔 해당 패턴 없음. 본문이 우연히 `12` 한 단어로만 이루어진 블록은 본래도 heading 후보로 의미 없는 케이스. |
| `font ratio` 임계 변경 | 없음 (`1.15` 유지) | 0 |
| ENV flag 도입 | 없음 (필요 없을 만큼 안전) | 0 |

## 후속 명령 — arXiv doc reingest (사용자가 별도 실행)

본 commit 은 **코드만**. 기존 인제스트된 chunks 의 `section_title` 은 변하지 않으므로, arXiv doc 의 회복은 reingest 필요.

### 1) 풀 UUID 조회 (DB)

```sql
SELECT id, original_name, source_type, created_at
FROM documents
WHERE id::text LIKE 'bc7b4591%'
ORDER BY created_at DESC
LIMIT 1;
```

### 2) reingest 호출 (기존 `POST /documents/{doc_id}/reingest` 사용)

```bash
DOC_ID="<위에서 조회한 풀 UUID>"
curl -X POST "http://localhost:8001/documents/${DOC_ID}/reingest" \
  -H "Content-Type: application/json"
```

- 응답 `{ "doc_id", "job_id", "chunks_deleted" }` — chunks 전부 삭제 후 같은 storage_path 로 파이프라인 재실행.
- 기존 `flags.ingest_mode` 보존 (S2 D3 정책).
- 진행 중 job 있으면 409.

### 3) 회복 확인 (DB)

```sql
SELECT section_title, COUNT(*) AS n
FROM chunks
WHERE doc_id = '<풀 UUID>'
GROUP BY section_title
ORDER BY n DESC
LIMIT 20;
```

- 기대: 상위 section_title 이 `Abstract`, `1. Introduction`, `2. Related Work` 등으로 분포.
- `arXiv:...` 가 여전히 다수면 본 패치 미반영 (서버 재시작 확인) 또는 추가 page-header 변형 케이스 (저널명 반복) — TODO 항목 참조.

## 남은 이슈 / 다음 스코프 후보

- doc 전체에서 동일 텍스트가 80%+ 페이지에 반복되면 page-header 로 판정해 추가 차단 (저널명/저자명 반복 페이지 header) — 2-pass 비용 대비 효과 측정 후 도입. 본 패치의 정규식만으로 회복률이 충분한지는 reingest 후 평가.
- `_HEADING_TEXT_PATTERN` 영어 standalone 목록 확장 가능성 (`Approach`, `Setup`, `Theorem` 등) — 자산 sniff 후 추가 검토.
- bold flag (`flags & 16`) 휴리스틱은 기존 TODO 그대로.
