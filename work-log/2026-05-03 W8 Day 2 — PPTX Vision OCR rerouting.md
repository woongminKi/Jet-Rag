# 2026-05-03 W8 Day 2 — PPTX Vision OCR rerouting (한계 #23 회수)

> W8 Day 1 §7 추천 작업 진입. 디자인 카탈로그 PPTX 의 텍스트 회수.

---

## 0. 한 줄 요약

W8 Day 2 — PptxParser 에 **슬라이드 텍스트 0 시 가장 큰 Picture → ImageParser 위임** rerouting 추가. max 5 슬라이드 cap. 단위 테스트 **181 → 186** ran (+5 Vision 회귀 보호), 의존성 추가 0, 시스템 의존성 0. 한계 #23 회수.

---

## 1. 진입 배경

W8 Day 1 사용자 PPTX (`브랜딩_스튜디오앤드오어.pptx`, 11 slides) 의 chunks 0 — 모든 콘텐츠가 Picture/GroupShape 로 렌더된 디자인 카탈로그. 한계 #23 으로 명시 후 Day 2 회수.

---

## 2. 비판적 재검토

### 2.1 옵션 비교

| 옵션 | 설명 | 장점 | 단점 |
|---|---|---|---|
| **A1** | 슬라이드 텍스트 0 시 가장 큰 Picture image_bytes → ImageParser | 의존성·시스템 의존성 0 | Picture 분리 (슬라이드 전체 캡처 X) |
| A2 | PPTX → PDF → PNG → ImageParser (LibreOffice headless) | 슬라이드 전체 캡처 | LibreOffice 시스템 의존성, 사용자 환경 영향 |

**결정 — A1 채택**. 시스템 의존성 0 + 디자인 카탈로그는 Picture 1장이 슬라이드 전체 layout 인 경우 많음 → 손실 적음.

### 2.2 Cap 정책

Gemini Flash RPD 20 무료 티어 + 디자인 PPT 첫 5장이 보통 표지·요약 → **max 5 슬라이드 × 가장 큰 Picture 1장 = max 5 호출** (`_MAX_SCAN_PAGES` PDF 패턴 일치).

---

## 3. 구현

### 3.1 변경 파일

| 파일 | 변경 |
|---|---|
| `api/app/adapters/impl/pptx_parser.py` | `__init__(image_parser=None)` composition + `_vision_ocr_largest_picture` + `_collect_pictures` (GroupShape 재귀) + `_picture_area` |
| `api/app/ingest/stages/extract.py` | `_pptx_parser = PptxParser(image_parser=_image_parser)` 주입 |
| `api/tests/test_pptx_parser.py` | `PptxVisionReroutingTest` 5 시나리오 |

### 3.2 동작

```python
# pptx_parser.py 주요 로직
for slide_idx, slide in enumerate(prs.slides):
    slide_text_parts = _extract_slide_text(slide, ...)
    if not slide_text_parts:
        if self._image_parser is not None and vision_slides_used < _MAX_VISION_SLIDES:
            ocr_text = _vision_ocr_largest_picture(slide, ...)
            if ocr_text:
                vision_slides_used += 1
                slide_text_parts = [ocr_text]
                if not slide_title:
                    slide_title = f"p.{slide_idx + 1} (Vision OCR)"
        if not slide_text_parts:
            continue
```

- `_collect_pictures` — `hasattr(shape, 'image')` duck-typing + GroupShape 재귀
- `_picture_area` — `width * height` (EMU 단위)
- `image.blob` 으로 raw bytes → `ImageParser.parse(blob, file_name=f"{file}#slide{N}.{ext}")`
- Vision 실패 시 RuntimeError 흡수 + warnings 누적

### 3.3 단위 테스트 (5 신규)

| 시나리오 | 검증 |
|---|---|
| `test_picture_only_slide_invokes_image_parser` | Picture 1장 → mock ImageParser 1회 호출, blob 전달, section_title="p.1 (Vision OCR)" |
| `test_text_slide_skips_image_parser` | 텍스트 박스 슬라이드 → Vision 호출 0회 |
| `test_max_5_slides_cap` | 6 픽처 슬라이드 → 첫 5장만 Vision (cap) |
| `test_vision_failure_graceful` | Vision raise → 슬라이드 skip + warnings "Vision OCR 실패" |
| `test_no_image_parser_disables_vision` | `image_parser=None` 기본값 → Vision 비활성 (테스트 호환) |

mock ImageParser — 외부 Gemini API 의존성 0. PIL 합성 PNG 로 Picture 슬라이드 합성 (이미 의존성).

### 3.4 회귀

- 단위 테스트 **181 → 186** ran (+5), 회귀 0
- 기존 PptxParser 7 시나리오 모두 PASS (image_parser=None default 유지)

---

## 4. 라이브 smoke (실 PPTX reingest)

### 4.1 reingest 요청

```bash
curl -X POST 'http://localhost:8000/documents/9c099607-…/reingest'
# {"doc_id":"9c099607-…","job_id":"93277140-…","chunks_deleted":0}
```

### 4.2 stage 로그 결과

```
extract       | succeeded | (Vision OCR rerouting 정상 작동)
chunk         | succeeded
chunk_filter  | succeeded
content_gate  | succeeded
tag_summarize | failed    | 429 RESOURCE_EXHAUSTED (Gemini RPD 초과 — graceful fail)
load          | succeeded
embed         | succeeded
doc_embed     | succeeded
dedup         | succeeded
```

### 4.3 결과 검증

- chunks_count: **0 → 1** (Vision OCR 회수 — 한계 #23 회수 검증)
- 9 stage 중 8 succeeded, **graceful fail 정책 작동** (tag_summarize 429 후에도 파이프라인 계속 진행, load·embed·doc_embed·dedup 모두 ship)
- tags/summary 빈 값은 Gemini quota 일시 초과 — 다음 RPD 리셋 후 reingest 시 회수
- **Vision OCR rerouting code path 정상**: extract.py 의 PptxParser → ImageParser → Gemini Vision 호출 → ExtractionResult chunks 생성 → DB load 까지 전 흐름 검증

---

## 5. 누적 KPI (W8 Day 2 마감)

| KPI | W8 Day 1 | W8 Day 2 |
|---|---|---|
| 단위 테스트 | 181 ran | **186 ran** (+5) |
| 신규 테스트 시리즈 | 8 | 8 (PptxVisionReroutingTest 추가, test_pptx_parser 동일 파일) |
| 한계 #23 (PPTX 디자인 카탈로그) | 명시 | **회수 (max 5 슬라이드 cap)** |
| 의존성 추가 | +1 (python-pptx) | **0** (Pillow 이미 의존) |
| 시스템 의존성 추가 | 0 | **0** (LibreOffice 회피) |

---

## 6. 알려진 한계 (Day 2 신규)

| # | 한계 | 회수 시점 |
|---|---|---|
| **28** | **Picture 분리 — 슬라이드 전체 layout 캡처 X** | A2 (PPTX→PDF→PNG) 검토는 시스템 의존성 검토 후 |
| 29 | Vision RPD 20 무료 티어 cap — 일일 4 PPT 한계 | 유료 또는 self-hosted 고려 시점 |
| 30 | OCR 실패 슬라이드는 영구 skip (재시도 없음) | reingest 명시 호출로 우회 |
| 31 | `_picture_area` 가 EMU 단위 — width/height None 가능 → 0 fallback | 거대 anomaly 케이스 모니터링 |

---

## 7. 다음 작업 — W8 Day 3 후보

| 우선 | 항목 | 사유 |
|---|---|---|
| 1 | **dedup Tier 3 e2e** | 한계 #26 회수, sprint 30분 |
| 2 | **monitor_search_slo CI 자동화** | GitHub Actions cron, 매일 baseline |
| 3 | **PPTX 텍스트 + Vision 혼합 슬라이드** | 한계 #28 보강 — 텍스트 + 이미지 결합 슬라이드 |
| 4 | **search debug mode mobile 노출** | W7 한계 #15 |
| 5 | **대시보드 — Vision OCR 사용량 가시성** | RPD cap 추적 (`/stats` 확장) |

**추천: dedup Tier 3 e2e (~30min) + monitor_search_slo CI (~1h)** — 둘 다 회귀 보호·운영 인프라 강화.

---

## 8. 한 문장 요약

W8 Day 2 — PptxParser Vision OCR rerouting ship (max 5 슬라이드 cap, ImageParser composition). 단위 테스트 181 → 186 ran, 회귀 0. 한계 #23 회수. 의존성·시스템 의존성 추가 0.
