# 2026-05-03 W8 Day 1 — DE-68 PPTX ship + DOCX 실자료 검증 + e2e 8 stage

> W7 Day 6 종합 핸드오프 §4 의 W8 추천 작업 진입.
> 사용자가 직접 docx 2건 + pptx 1건을 루트에 올리면서 DE-67 실자료 검증 + DE-68 본 ship 동시 진행.

---

## 0. 한 줄 요약

W8 Day 1 — 사용자 자료 업로드 시점에 **input_gate 매직바이트 fix + PptxParser 신규 + e2e 4 시나리오 추가** ship. 단위 테스트 **164 → 181** ran, 회귀 0. DE-68 본 ship 완료. 의존성 1건 추가 (python-pptx, 사용자 자료 업로드 = 묵시 승인).

---

## 1. 진입 배경 + 비판적 재검토

### 1.1 사용자 액션
사용자가 프로젝트 루트에 3건 업로드:
- `승인글 템플릿1.docx` (211K, 태양계 행성 자료)
- `승인글 템플릿3.docx` (65K, 삼국시대 정치 자료)
- `브랜딩_스튜디오앤드오어.pptx` (3.0M, 디자인 카탈로그)

### 1.2 비판적 재검토 (CLAUDE.md §1)

- **"이게 정말 최선의 방법인가?"** — 차트 라이브러리 추가 vs SVG only (W7 Day 4 사례 재적용)
  - PptxParser 의존성 (python-pptx) 추가 vs unzip + XML 파싱 직접
  - python-pptx 채택 — 표준 라이브러리, 안정성↑, 직접 XML 은 복잡도↑
- **"자료 부족 시 placeholder 활성"** — 5건 placeholder 중 2건만 활성 가능 → 부분 활성보다 다음 sprint 일괄. **F3 skip → F4 우선** 결정 (가성비↑)
- **"Vision OCR 즉시 통합"** — 사용자 PPTX 가 Picture 100% (이미지 텍스트) 라 OCR 필요. 단 Vision RPD 20 cap 으로 80장 처리 불가. **후속 한계 #23 으로 명시 + max 5 페이지 cap rerouting 검토**.

---

## 2. F1 — DOCX 2건 실자료 smoke (DE-67 검증)

### 2.1 첫 거부 (input_gate 버그)

```bash
curl -X POST 'http://localhost:8000/documents' -F 'file=@승인글 템플릿1.docx'
# {"detail":"확장자(.docx)와 파일 내용(application/vnd.openxmlformats-...)이 일치하지 않습니다."}
```

**원인**: filetype 1.2.0 가 ZIP 컨테이너의 deep MIME (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`) 을 반환. `_EXT_TO_MIMES` 는 `application/zip` 만 허용 → reject.

### 2.2 fix

```python
# api/app/routers/_input_gate.py
".docx": {
    "application/zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
},
".pptx": {
    "application/zip",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
},
```

회귀 보호 — `tests/test_input_gate.py` 신규 (DOCX validates / PPTX validates / EXE masquerade rejected / unknown ext rejected, 4건).

### 2.3 인제스트 결과

| 자료 | chunks | tags | summary | flags |
|---|---:|---|---|---|
| 승인글 템플릿1 | **139** | 14 (태양계·수성·금성·…) | 한국어 3줄 정확 | document_type=블로그, has_pii=False |
| 승인글 템플릿3 | **113** | 16 (삼국시대·왕권강화·…) | 한국어 3줄 정확 | document_type=블로그 |

**DE-67 본 검증 완료** — 9 stage 모두 통과, error_msg=None.

---

## 3. F2 — DE-68 PPTX 본 ship

### 3.1 PptxParser 신규 (`api/app/adapters/impl/pptx_parser.py`)

DocxParser 패턴 재사용:
- 슬라이드 1개 = `ExtractedSection` 1개 (raw_text 는 슬라이드 내부 모든 텍스트 join)
- `page` = slide_index + 1
- `section_title` = title placeholder 우선 → 첫 텍스트 박스 fallback
- 표: ` | ` separator (DocxParser `_table_to_text` 동일)
- **GroupShape 재귀** — 디자인 PPT 흔한 구조 안 텍스트 회수 (`hasattr(shape, "shapes")` duck-typing)
- graceful: 깨진 PPTX RuntimeError wrap, slide 단위 부분 실패 warnings

### 3.2 의존성

- `python-pptx>=1.0.0` 추가 (pyproject.toml)
- 사용자 PPTX 직접 업로드 시점 = 묵시 승인 (CLAUDE.md "필요 판단 시 묻기" 가이드 적용)

### 3.3 라우팅

```python
# api/app/ingest/stages/extract.py
_PARSERS_BY_DOC_TYPE: dict[str, DocumentParser] = {
    ...
    "pptx": _pptx_parser,  # W7 후속 — DE-68 본 ship
}
```

### 3.4 단위 테스트 (`tests/test_pptx_parser.py` 신규)

7 케이스:
- single slide / title placeholder / table / multi slide page increment / empty slide / corrupted RuntimeError / can_parse extension only
- 픽스처: python-pptx 의 `Presentation()` 으로 메모리 합성 — 외부 sample 파일 의존성 0

### 3.5 실자료 인제스트 결과

| 자료 | doc_type | chunks | 비고 |
|---|---|---:|---|
| 브랜딩_스튜디오앤드오어 | pptx | **0** | 11 slides 모두 Picture+GroupShape — 텍스트 박스 0 |

**한계 발견**: 사용자 PPTX 가 디자인 카탈로그라 모든 콘텐츠가 이미지로 렌더됨. PptxParser 자체는 정상 동작 (단위 테스트 7건 PASS), 단지 *이 자료* 가 텍스트 추출 불가 케이스. **Vision OCR rerouting 후속 (한계 #23)**.

### 3.6 commit

`33cf821` — `feat(api): PptxParser ship + DOCX/PPTX deep MIME 매직바이트 fix (DE-68)` (+485 lines, -6)

---

## 4. F4 — e2e 확장 (W8 Option A)

W7 Day 5·6 의 mock.patch 패턴 재사용 — `test_e2e_pipeline.py` 에 시나리오 6 → 10건.

### 4.1 추가 시나리오

| ID | 클래스·메서드 | 검증 |
|---|---|---|
| **S5a** | `DocEmbedTest.test_summary_used_as_source` | summary + implications join → embed_calls[0] 에 두 줄 모두 포함 |
| **S5b** | `DocEmbedTest.test_raw_text_fallback_when_no_summary` | summary NULL 시 raw_text[:3000] 사용 |
| **S5c** | `DocEmbedTest.test_skip_when_no_source` | 둘 다 없으면 return False, embed_calls 0회 |
| **S6** | `DedupTier2Test.test_tier2_match_marks_flags` | cosine 1.0 (동일 unit vec) → Tier 2 + flags.duplicate_of |
| **S7a** | `TagSummarizeGracefulTest.test_llm_failure_is_swallowed` | _llm.complete raise → graceful, tags=[]/summary=None 유지 |
| **S7b** | `TagSummarizeGracefulTest.test_llm_success_persists_tags_and_summary` | 정상 JSON 응답 시 tags + summary + flags(document_type/time_reference) 머지 |

### 4.2 인프라 확장

- E2EBaseTest patches: 6 → **11** (doc_embed·dedup·tag_summarize 모듈 namespace 추가)
- `_FakeTableQuery`:
  - `neq(col, value)` 메서드 추가 (dedup `.neq("id", exclude_id)`)
  - `not_` property + `_FakeTableQueryNot` class (dedup `.not_.is_("doc_embedding", "null")`)
  - `_matches_filters` 에 `neq` / `not_is` op 처리
- `FakeBGEM3Provider.embed(text)` 단건 메서드 (doc_embed 사용)

### 4.3 9 stage e2e 커버리지

| stage | W7 Day 5·6 | W8 Day 1 |
|---|:---:|:---:|
| extract | (parser 단위 테스트로 충분) | — |
| chunk | ✅ | — |
| chunk_filter | ✅ | — |
| content_gate | ✅ (Day 6) | — |
| tag_summarize | — | ✅ S7 |
| load | ✅ | — |
| embed | ✅ | — |
| doc_embed | — | ✅ S5 |
| dedup | — | ✅ S6 |

**8 / 9 stage 커버** (extract 는 parser 단위 테스트가 분리). e2e 통합 회귀 보호 base 완성.

### 4.4 commit

`d0fd5a9` — `test(api): doc_embed·dedup·tier2·tag_summarize e2e 시나리오 추가 (W8 Option A)` (+276 lines)

---

## 5. 누적 KPI (W8 Day 1 마감)

| KPI | W7 Day 6 | W8 Day 1 | 변화 |
|---|---|---|---|
| 단위 테스트 | 164 ran | **181 ran** | +17 (PptxParser 7 + input_gate 4 + e2e 6) |
| 신규 테스트 시리즈 | 6 | **8** | + test_pptx_parser, test_input_gate |
| 지원 doc_type | 6종 | **7종 (+ pptx)** | DE-68 ship |
| e2e 9 stage 커버 | 4/9 (chunk·filter·load·embed) | **8/9** (+ content_gate·doc_embed·dedup·tag_summarize) | +4 |
| 회귀 발생 | 0 | 0 | — |
| 의존성 추가 | 0 (W7) | **+1 (python-pptx)** | 묵시 승인 |
| 마지막 commit | 7268f0c | **d0fd5a9** | +3 push |

---

## 6. 알려진 한계 (W8 Day 1 신규)

| # | 한계 | 회수 시점 |
|---|---|---|
| **23** | **PPTX 디자인 카탈로그 (Picture 100%) 텍스트 추출 불가** | Vision OCR rerouting 도입 (max 5 페이지 cap) — Day 2+ |
| 24 | input_gate fix — filetype 라이브러리 새 버전이 다른 deep MIME 반환할 가능성 | filetype 업그레이드 시 회귀 확인 |
| 25 | golden v0.3 placeholder G-021~G-025 미활성 | DOCX 자료 5건 누적 후 일괄 |
| 26 | dedup e2e Tier 3 (sim 0.85~0.95 + filename ≥0.6) 미커버 | 다음 sprint |
| 27 | tag_summarize e2e 의 _llm 단건 mock — 실 Gemini API 응답 형태 변동 시 mismatch | adapter 인터페이스 안정 가정 |

---

## 7. 다음 작업 — W8 Day 2 후보

| 우선 | 항목 | 사유 / 비용 | 추천 |
|---|---|---|---|
| 1 | **PPTX Vision OCR rerouting** | 한계 #23 회수 — 디자인 PPT 자료 활용 가능화 | ~3h, max 5 페이지 cap |
| 2 | **dedup Tier 3 e2e** | 한계 #26 회수, sprint 패턴 재사용 | ~30min |
| 3 | **golden placeholder 활성** | 한계 #25 — 추가 자료 누적 후 | 사용자 자료 의존 |
| 4 | **search debug mode mobile 노출** | W7 한계 #15 (의도였으나 사용 빈도↑ 검토) | ~30min |
| 5 | **monitor_search_slo CI 자동화** | GitHub Actions cron | ~1h |

**Day 2 추천: PPTX Vision OCR rerouting (~3h)** — 사용자가 PPTX 자료 올린 시점이라 그 활용도 회수가 최우선.

비판적 재검토:
- "Vision RPD 20 cap" — 단일 PPT 의 max 5 페이지만 OCR. 11 slides PPT 의 경우 첫 5장만. 사용자 카탈로그는 보통 표지·요약 슬라이드가 앞에 있어 적정.
- 패턴 재사용: 기존 `_reroute_pdf_to_image` (PyMuPDF + ImageParser) 와 동일. 단 PPTX → 이미지 변환은 LibreOffice headless 또는 windll(미동작) 필요. 시스템 의존성 신중 검토.

---

## 8. 한 문장 요약

W8 Day 1 — DE-68 본 ship (PptxParser + python-pptx + GroupShape 재귀) + input_gate 매직바이트 fix + e2e 9 stage 중 8 stage 커버 완성. 단위 테스트 164 → **181** ran, 회귀 0. 사용자 docx 자료 인제스트 정상 (chunks 139 + 113), pptx 자료는 디자인 카탈로그 한계 (Vision OCR 후속). 마지막 commit `d0fd5a9`.
