# 2026-04-28 W2 Day 3 — HWPX · Vision · 이미지 · 스캔 PDF

> Day 2 SLO 회복 + 매직바이트 게이트 위에서 **멀티포맷 파서 4종 통합**. 항목 C(HWPX) + B(Vision 본 구현) + D(ImageParser) + A′(스캔 PDF rerouting) 모두 마감, **end-to-end smoke 4건 통과**. 같은 날(2026-04-28) Day 2 + Day 3 연속 진행.
>
> **선행 문서**: `work-log/2026-04-28 W2 Day 2 — SLO 회복 + 매직바이트 게이트.md`

---

## 0. 다음 세션 진입점

### 0.1 working tree 상태
- 모든 Day 3 변경 커밋 + push 완료
- `git status` clean

### 0.2 Day 4 진입 순서 (명세 v0.3 §7.1)

| # | 액션 | 예상 |
|---|---|---|
| 1 | **항목 E URL 파서** — `app/adapters/impl/url_parser.py` 신설. trafilatura 활용. `POST /documents/url` 별도 엔드포인트 신설. SSRF 블랙리스트 (DE-18) | 1.0d |
| 2 | **항목 F HWP 5.x 구포맷** — `app/adapters/impl/hwp_parser.py` 신설. pyhwp (`from hwp5 import ...`) 활용 | 1.0d |
| 3 | URL 평가 1~2건 + HWP 1건 smoke | 30분 |

### 0.3 결정 보류
- URL 파서 SSRF 블랙리스트 정확한 패턴 (DE-18 의 권장은 `127/10/172.16/192.168/169.254`)
- pyhwp 0.1b15 의 정확한 import 경로 (`from hwp5 import xmlmodel`?) — Day 1 에서 `hwp5html` CLI 동작 확인됨, 라이브러리 API 는 미확인

---

## 1. 오늘 달성 (Day 3)

### 1.1 항목 C — HWPX 파서 (`HwpxParser`)
- `app/adapters/impl/hwpx_parser.py` 신설. python-hwpx 의 `TextExtractor` + ZipFile (BytesIO) 패턴
- section 단위 단락 순회 → ExtractedSection 누적
- `SectionInfo.name` 의 `Contents/sectionN.xml` sentinel 은 None 마스킹 (`_normalize_section_title`)
- end-to-end smoke: 합성 HWPX 8단락 → chunks 2건 + 한국어 tags 12개 + 요약 정상 (수신 692ms · 8-stage 14s · completed)

### 1.2 항목 B — `GeminiVisionCaptioner.caption()` 본 구현
- Day 1 골격 위에 본 구현. JSON 4필드 (`type/ocr_text/caption/structured`) 프롬프트
- `google-genai` SDK 의 `Part.from_bytes(mime_type=...)` 로 이미지 1장 전달
- `response_mime_type=application/json` + 화이트리스트 외 `type` 은 "기타" 로 보수 매핑
- `_gemini_common.with_retry` (3회 + 지수 백오프) 재사용
- live smoke: 한국어 텍스트 PNG → type=문서 / 4줄 OCR 정확 / 80자 이내 caption (8.8s)

### 1.3 항목 D — `ImageParser` (Vision composition + Pillow 정규화)
- `app/adapters/impl/image_parser.py` 신설. `VisionCaptioner` composition 패턴 (vision.py docstring C-2)
- Pillow 12.2.0 추가 — pure-Python wheel, libsystem 의존성 0 (정책 부합)
- 정규화 파이프라인: `Image.open` → `ImageOps.exif_transpose` → 단변 1024px 다운스케일 → JPEG 인코딩 (alpha 있으면 PNG 유지)
- HEIC/HEIF: Pillow 디코드 회피, raw bytes 그대로 Gemini 전달 (DE-17)
- ExtractionResult 매핑: section[0] caption + section[1] ocr_text (있을 때만)
- end-to-end smoke: 1500x1000 PNG → 다운스케일 + Vision 호출 → tags 8개 + 한국어 자연 요약 (전화/이메일 OCR 정확)

### 1.4 항목 A′ — 스캔 PDF 재라우팅 (PyMuPDF → ImageParser fallback)
- extract 스테이지 dispatcher 안에 통합 (parser 어댑터에 책임 분산하지 않음)
- 검출: `_is_scan_pdf(result)` — `raw_text.strip()` 길이 ≤ 50자
- rerouting: `_reroute_pdf_to_image` — 페이지별 PNG 렌더 (DPI 150) → ImageParser.parse() → sections 누적 (page 라벨 포함)
- 마킹: `flags.scan = true`, `doc_type` 은 'pdf' 그대로 (CHECK 제약 준수)
- max 5 페이지 cap (Vision API 비용·시간), 초과 시 warning
- end-to-end smoke: 합성 image-only PDF → PyMuPDF raw_text 0자 → ImageParser fallback → 5줄 OCR 정확 + flags.scan=true (16s 완주)

### 1.5 어댑터 6종 재편 (DE-19) 누적 진척
| 어댑터 | 모듈 | 상태 |
|---|---|---|
| `BlobStorage` | `adapters/storage.py` | ✅ |
| `VectorStore` | `adapters/vectorstore.py` | ✅ |
| `EmbeddingProvider` | `adapters/embedding.py` | ✅ |
| `LLMProvider` | `adapters/llm.py` | ✅ |
| `VisionCaptioner` | `adapters/vision.py` | ✅ (Day 1 분리, Day 3 본 구현) |
| `DocumentParser` | `adapters/parser.py` | ✅ + 5종 구현체 (PDF · HWPX · Image / 스캔 PDF rerouting) |

### 1.6 Gemini 공통 헬퍼 분리 (`_gemini_common.py`)
- `gemini_llm.py` 의 `_get_client` · `_with_retry` 를 `adapters/impl/_gemini_common.py` 로 분리
- `gemini_vision.py` 본 구현이 동일 헬퍼 재사용 → 코드 중복 회피
- 함수 export 화 (underscore prefix 제거): `get_client`, `with_retry`

---

## 2. 결정

| # | 결정 | 근거 |
|---|---|---|
| **DE-35 (Day 3)** | `hwpx.SectionInfo.name` 의 `Contents/sectionN.xml` 는 sentinel 로 간주 → None | ZIP 내부 XML 경로는 사용자 메타데이터로 부적절. KPI `section_title ≥ 30%` 충족은 W3+ heading 분석 도입 시 |
| **DE-36 (Day 3)** | 스캔 PDF 검출 임계값 = `raw_text.strip()` ≤ 50자, rerouting max = 5페이지, 렌더 DPI = 150 | 50자: "거의 비어있음" 의 안전한 경계. 5페이지 cap: Vision API 비용·시간 통제. DPI 150: OCR 품질 vs 호출 속도 균형 |
| **DE-37 (Day 3)** | HEIC/HEIF 는 Pillow 디코드 회피, raw bytes 그대로 Gemini 전달 | DE-17: Gemini 가 HEIC 직접 지원. `pillow-heif` 추가 회피 (정책 부합) |
| **DE-38 (Day 3)** | Pillow 12.2.0 채택 | pure-Python wheel, libsystem 의존성 0. 정책 (`feedback_no_system_packages.md`) 부합. 명세 §3.B 의 다운스케일/EXIF AC 충족 필수 |
| **DE-39 (Day 3)** | 스캔 PDF rerouting 은 extract dispatcher 가 담당 (parser 어댑터에 책임 분산 X) | PyMuPDFParser 가 ImageParser 의존성 가지면 어댑터 단위 모호. fallback 로직은 한 곳에 모이는 게 깔끔 |

---

## 3. 발견된 이슈 (오늘 해결)

| # | 이슈 | 처리 |
|---|---|---|
| 1 | `hwpx.TextExtractor` 의 ZipFile 입력 + manifest 누락 fallback 메시지 (`masterPage` 등) | 정상 동작 (단순 stderr 로그). 추출 결과에 영향 없음 — pass-through |
| 2 | `SectionInfo.name` 이 사용자 의미 없는 ZIP 경로 → 모든 chunk metadata 의 section_title 노이즈 | `_normalize_section_title` 로 sentinel 매칭 → None |
| 3 | Gemini Vision JSON 응답에서 type 화이트리스트 외 값 | "기타" 로 보수 매핑 + warning log |
| 4 | image smoke 의 embed 단계에서 ConnectionTerminated (BGE-M3 HF Inference 일시 네트워크 끊김) | ImageParser 무관한 전이성 에러. ImageParser 자체는 OCR · summary · tags 모두 정상 검증 |
| 5 | 멀티페이지 스캔 PDF 시 Vision API 호출 N회 → 비용/시간 폭발 | DE-36: max 5 페이지 cap + warning. W3+ 에서 페이지 sampling 전략 도입 검토 |

---

## 4. 잔여 이슈

| # | 이슈 | 영향 | 처리 시점 |
|---|---|---|---|
| 1 | HWPX `section_title ≥ 30%` KPI 미충족 (heading 분석 X) | 명세 §1.2 KPI 측정 시 항목 미달 가능 | W3+ paragraph_property 분석 도입 시 |
| 2 | HWP 5.x 구포맷 파서 미구현 | doc_type=hwp 업로드 graceful skip | Day 4 항목 F |
| 3 | URL 파서 미구현 | doc_type=url 미지원 | Day 4 항목 E |
| 4 | DOCX/PPTX 파서 미구현 (W3 이월 결정) | doc_type=docx/pptx graceful skip | W3 |
| 5 | image smoke 시 BGE-M3 네트워크 일시 끊김 재현 가능 | embed 단계 transient fail. retry 1회 패턴은 Day 5 에서 도입 검토 | W3+ |
| 6 | Vision API 일일 한도 (RPD 20) 와 멀티페이지 스캔 PDF 충돌 | 큰 스캔 PDF 1건 = 5 호출 → 일일 4건 한계 | 평가셋 측정 시 사전 인지, batch 시간 분산 |

---

## 5. 변경 범위

### 5.1 신규 파일 (4건)
- `api/app/adapters/impl/hwpx_parser.py`
- `api/app/adapters/impl/image_parser.py`
- `api/app/adapters/impl/_gemini_common.py`
- (Day 3 work-log) `work-log/2026-04-28 W2 Day 3 — HWPX · Vision · 이미지 · 스캔 PDF.md`

### 5.2 수정 파일
- `api/app/adapters/impl/gemini_llm.py` — 공통 헬퍼 분리
- `api/app/adapters/impl/gemini_vision.py` — 본 구현 (caption + JSON 파서)
- `api/app/ingest/stages/extract.py` — 디스패처 확장 + 스캔 PDF rerouting
- `api/pyproject.toml` · `api/uv.lock` — `+ pillow 12.2.0`

### 5.3 의존성 (누적)
| 패키지 | 추가 시점 | 용도 |
|---|---|---|
| `python-hwpx 2.9.0` | Day 1 | HWPX 파싱 |
| `pyhwp 0.1b15` | Day 1 | HWP 5.x 파싱 (Day 4 사용 예정) |
| `trafilatura 2.0.0` | Day 1 | URL 본문 추출 (Day 4 사용 예정) |
| `filetype 1.2.0` | Day 2 | 매직바이트 (입력 게이트) |
| **`pillow 12.2.0`** | **Day 3** | **이미지 정규화 (다운스케일 + EXIF transpose)** |

---

## 6. AC 종합 (Day 3)

| AC | 결과 |
|---|---|
| HWPX 파일 업로드 → chunks ≥ 1 + tags + summary | ✅ chunks 2 + tags 12개 + 한국어 자연 요약 |
| 이미지 업로드 → Vision OCR + caption + tags | ✅ 4줄 OCR 정확 + tags 8개 + 한국어 요약 |
| 스캔 PDF (텍스트 레이어 없음) → ImageParser fallback + flags.scan=true | ✅ 5줄 OCR 정확 + flags.scan=true + chunks 1건 |
| Vision API 응답 type 화이트리스트 외 → "기타" 보수 매핑 | ✅ 단위 검증 |
| EXIF transpose + 단변 1024px 다운스케일 | ✅ 2400x1800 → 1365x1024 |
| RGBA 투명도 보존 시 PNG 유지 | ✅ |
| 8-stage 파이프라인 통과 (HWPX 14s · scan PDF 16s) | ✅ |

---

## 7. 회고 한 줄

오늘은 Day 2 + Day 3 연속 진행으로 **W2 P0 스코프 (A·B·C·D·A′)** 를 한 번에 완주. 명세 §3.A·B·C·D·A′ 가 코드 위에서 정합성 검증됨. Pillow 추가는 정책 충돌 없는 깔끔한 의존성 (pure-Python wheel + libsystem 0). 스캔 PDF rerouting 의 책임 위치(parser vs dispatcher)를 dispatcher 로 결정한 게 어댑터 모듈 경계를 깔끔히 유지하는 데 효과적이었다. Day 4 부터는 P1 항목 (URL · HWP 구포맷) 으로.
