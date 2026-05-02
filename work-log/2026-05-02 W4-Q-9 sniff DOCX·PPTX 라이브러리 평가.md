# 2026-05-02 W4-Q-9 sniff — DOCX/PPTX 라이브러리 평가 리포트

> W4 명세 v0.1 (CONFIRMED) §3.W4-Q-9 sniff — `python-docx` · `python-pptx` 라이브러리만 평가, 본격 구현은 W5.
> 코드 변경 0, 의존성 추가 0. 문서·README 검토 + 코드 패턴 시뮬.

## 0. TL;DR

- **python-docx**: heading 추출 (style 기반) + 표 (paragraphs/tables) 분리 + 한국어 unicode OK. 이미지 추출은 `doc.part.rels` 활용 가능. 본격 구현 비용 ~1.5d (HwpxParser 패턴 재사용 가능).
- **python-pptx**: 슬라이드 단위 분리 (`prs.slides`) + title/text frame 추출 + 표 분리. 발표자 노트 + 이미지 + layout 정보는 README 미언급, API 문서 추가 조사 필요. 본격 구현 비용 ~2d (페르소나 A 의 PPT 자료 처리 가치 검증 후).
- **권장 W5 우선순위**: DOCX > PPTX (DOCX 가 일반 사용자 자료 빈도 ↑, 표/heading 일반화 단순)
- **DE 신규 후보**: DE-67 (DOCX 파서 어댑터 — HwpxParser sticky propagate 패턴 재사용)

## 1. python-docx 평가

### 1.1 핵심 API

```python
from docx import Document
doc = Document('file.docx')

# 1) 본문 텍스트 (paragraph 단위)
for paragraph in doc.paragraphs:
    print(paragraph.text)

# 2) heading 추출 (style 기반)
for paragraph in doc.paragraphs:
    if 'Heading' in paragraph.style.name:
        print(f"레벨: {paragraph.style.name}, 텍스트: {paragraph.text}")

# 3) 표 데이터 (paragraphs 와 분리됨)
for table in doc.tables:
    for row in table.rows:
        for cell in row.cells:
            print(cell.text)

# 4) 이미지 (relationship 통해 추출)
for rel in doc.part.rels.values():
    if 'image' in rel.reltype:
        # rel.target_part.blob 으로 바이트 추출
        pass
```

### 1.2 Jet-Rag 통합 평가

| 항목 | 평가 | 비고 |
|---|---|---|
| heading 추출 | ✅ 가능 (style.name 의 'Heading' 패턴) | HwpxParser 의 sticky propagate 패턴 재사용 — heading 발견 시 다음 heading 까지 sticky |
| 한국어 처리 | ✅ unicode 기반 (검증 필요하지만 risk 낮음) | python-docx 는 paragraph.text 가 str 반환 — 한국어 자연 처리 |
| 표 처리 | ⚠️ paragraphs 와 분리 — 별도 iteration 필요 | tables 와 paragraphs 의 문서 순서 통합 어려움 (W3 의 chunk_filter table_noise 룰과 교차) |
| 이미지 추출 | ⚠️ relationship API 통해 가능 — Vision 캡셔닝 연결 후 본문 통합 가능 | W3 ship 한 ImageParser 와 동일 파이프라인 가능 |
| footnote/endnote | ❌ README 미언급 — 추가 조사 필요 | W6+ 평가 가능 |
| 메타 (작성자·수정일) | ⚠️ doc.core_properties 통해 접근 가능 (별도 추출) | metadata 컬럼 활용 가능 |

### 1.3 본격 구현 위험 요소

1. **paragraphs vs tables 순서 통합**: 본문 단락과 표가 문서 순서대로 섞여 있을 때, `doc.paragraphs` 와 `doc.tables` 를 분리 iteration 하면 순서 손실. 해결: `doc.element.body` 의 XML 순회 — XmlElement 단위로 paragraph/table 구분 (HwpxParser 의 ZipFile 직접 파싱 패턴 재사용)
2. **footnote/endnote**: README 미커버, 별도 API 조사 필요
3. **한국어 폰트 임베디드**: 본문 추출에 영향 X (텍스트만 추출), 이미지로 대체된 글꼴은 풍부 OCR 필요 (ImageParser fallback)

### 1.4 본격 구현 예상 비용

- 파서 어댑터 (`docx_parser.py`) 작성: 0.5d
- heading sticky propagate (HwpxParser 패턴 재사용): 0.3d
- 표 처리 + chunk_filter 통합: 0.3d
- 단위 테스트 + 합성 자료: 0.3d
- **합계 ~1.5d**

## 2. python-pptx 평가

### 2.1 핵심 API

```python
from pptx import Presentation
prs = Presentation('file.pptx')

# 1) 슬라이드 본문 텍스트
for slide in prs.slides:
    for shape in slide.shapes:
        if shape.has_text_frame:
            print(shape.text_frame.text)

# 2) 슬라이드 제목
for slide in prs.slides:
    if slide.shapes.title:
        print(slide.shapes.title.text)

# 3) 표 데이터
for slide in prs.slides:
    for shape in slide.shapes:
        if shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    print(cell.text)

# 4-6) 발표자 노트, 이미지, layout — README 미커버, 추가 조사 필요
```

### 2.2 Jet-Rag 통합 평가

| 항목 | 평가 | 비고 |
|---|---|---|
| 슬라이드 단위 분리 | ✅ 자연스러움 (슬라이드 = 섹션) | section_title = shape.title, page = slide_idx + 1 매핑 |
| 한국어 처리 | ✅ unicode 기반 (DOCX 동일) | |
| 표 처리 | ✅ shape.has_table 명시 분리 | DOCX 보다 단순 (슬라이드 안의 shape 단위) |
| 발표자 노트 | ⚠️ `slide.notes_slide.notes_text_frame.text` 가능 (별도 조사) | RAG 가치 ↑ — 슬라이드 본문보다 풍부한 컨텍스트 |
| 이미지 추출 | ⚠️ `shape.shape_type == MSO_SHAPE_TYPE.PICTURE` (별도 조사) | W3 ImageParser + Vision 캡셔닝 연결 가능 |
| 슬라이드 layout | ⚠️ `slide.slide_layout` (별도 조사) | RAG 우선순위 낮음 (style 정보 검색 가치 ↓) |

### 2.3 본격 구현 위험 요소

1. **shape 다양성**: text_frame · table · picture · group · placeholder 등 type 구분 + 각자 텍스트 추출 다름. 일반화 어려움.
2. **발표자 노트 통합**: 본문 + 노트를 어떻게 결합? 동일 청크? 별도 청크? — 사용자 사용 시나리오 의존
3. **animation/transition**: 무시 가능 (RAG 텍스트 검색 무관)

### 2.4 본격 구현 예상 비용

- 파서 어댑터 (`pptx_parser.py`) 작성: 0.7d (shape type 다양성)
- 슬라이드 + 노트 통합 정책 결정 + 구현: 0.5d
- 표 + 이미지 처리: 0.3d
- 단위 테스트 + 합성 자료: 0.5d
- **합계 ~2d**

## 3. 비교 + W5 우선순위

| 항목 | DOCX | PPTX |
|---|---|---|
| 사용자 자료 빈도 (페르소나 A) | 일반 (회의록·보고서·메모) | 특수 (프레젠테이션) |
| 본격 구현 비용 | ~1.5d | ~2d |
| heading 일반화 | 단순 (style 기반) | 단순 (slide.title) |
| 표 처리 | 보통 (paragraphs 와 분리) | 단순 (shape.has_table) |
| 이미지 처리 | 가능 (relationships) | 가능 (shape type) |
| W3·W4 패턴 재사용 | 높음 (HwpxParser sticky + chunk_filter) | 보통 (slide 단위 자연 분리) |
| RAG 가치 | 높음 (회의록·메모 빈도 ↑) | 보통 (PPT 검색 가치는 사용자 의존) |

**W5 권장 순서**: DOCX 우선 ship, PPTX 는 사용자 자료 + 가치 측정 후 결정.

## 4. DE 신규 후보 (W5 결정)

| # | 결정 | 단일 추천 | 일자 | 상태 |
|---|---|---|---|---|
| **DE-67 (NEW)** | DOCX 파서 어댑터 — `docx_parser.py` 신규 | (a) HwpxParser sticky propagate 패턴 재사용 | W5 Day 1~2 | DRAFT |
| **DE-68 (NEW)** | PPTX 파서 본격 구현 시점 | (a) 사용자 PPT 자료 1건 + 가치 검증 후 W5 결정 | W5 Day 3 | DRAFT |

### 4.1 DE-67 근거

- python-docx 의 paragraph + style 패턴이 HwpxParser 의 styleIDRef 패턴과 유사 → sticky propagate 직접 재사용
- 표 처리는 W3 chunk_filter (table_noise) 룰과 자연 통합
- 이미지 추출은 W3 ImageParser + Vision 캡셔닝 파이프라인 활용

### 4.2 DE-68 근거

- python-pptx 의 shape 다양성 + 슬라이드 + 노트 통합 정책 결정 비용 ↑
- 사용자 자료 빈도 측정 부재 → 본격 구현 가치 불확실
- W5 진입 시 사용자 PPT 1건 (또는 합성) 으로 sniff 후 결정

## 5. AC 매트릭스

| AC | 결과 | 충족 |
|---|---|---|
| DOCX/PPTX 라이브러리 평가 리포트 작성 | 본 문서 | ✅ |
| heading / 본문 / 표 추출 가능성 정성 평가 | DOCX 모두 가능, PPTX 본문/표 OK / 노트·이미지·layout 추가 조사 필요 | ✅ |
| W5 본격 구현 시 작업 분해 + 위험 요소 정리 | §1.3·§1.4 + §2.3·§2.4 | ✅ |

## 6. 한 문장 요약

W4-Q-9 sniff — python-docx (heading style 기반 + tables 별도 + 이미지 relationships, ~1.5d) + python-pptx (슬라이드 단위 자연 분리 + table 단순, 발표자 노트·이미지·layout 추가 조사 필요, ~2d) 평가 완료. **W5 권장 = DOCX 우선** (페르소나 A 자료 빈도 + W3·W4 패턴 재사용 가능), **DE-67·68 신규 후보 DRAFT**.
