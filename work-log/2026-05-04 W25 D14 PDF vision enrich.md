# W25 D14 — PDF 표/그림 정확도: vision enrich stage ship (Sprint 1)

> **결론**: 일반 PDF 의 표/그림/다이어그램 정보 보강용 `_enrich_pdf_with_vision()` 함수 추가 (ENV `JETRAG_PDF_VISION_ENRICH` opt-in). **회귀 0** (default false → 기존 자료 영향 0). 단위 테스트 4건 추가. **사용자 paid tier 활성화 + 본 PDF reingest 후 Sprint 2 검증** 필요.

> Step 0 PoC + Option B PoC + 자율 비판적 재검토 (3회+) 결과 — 무료 quota 한계 인정 → 유료 Gemini Flash paid tier 결정 → 인제스트 시점 모든 페이지 vision (a) 채택. 답변 시점 multimodal (c) 은 LLM 이 이미지 활용 약함 PoC 실증 → 후순위.

---

## 0. 사용자 의도 / 진단 / 결정 흐름

| 단계 | 내용 |
|---|---|
| 사용자 보고 | 데이터센터 안내서 PDF 적재 후 "테스트베드 조성 지원 사업 체계" query → p.4 표 답변 잘림 + p.6 그림 정보 누락 |
| 진단 | (1) PyMuPDF 가 표를 raw text 로 cell 순서 뒤섞임 + 일부 누락 (chunk 17개 분리) (2) 이미지 블록 (type=1) 완전 무시 → 그림 정보 chunks 0건 |
| 비판적 재검토 (1차) | A+E (find_tables + page expansion) → 그림 미해결 |
| 비판적 재검토 (2차) | heuristic D / 모든 페이지 vision → 무료 quota 가정 잘못 |
| 비판적 재검토 (3차) | 실측 quota = **20 호출/일** (Gemini Flash 무료 RPD) — (a)/(c) 모두 무료로 비현실 |
| 사용자 결정 | 유료 Gemini Flash paid tier 비용 OK |
| 채택 | (a) 인제스트 시점 모든 페이지 vision (chunks 풍부화) — paid tier 안에서 quota 무관 |

---

## 1. Sprint 1 변경 (commit 단위)

### 1.1 `api/app/ingest/stages/extract.py`

```python
_PDF_VISION_ENRICH_ENABLED = os.environ.get("JETRAG_PDF_VISION_ENRICH", "false").lower() == "true"
_VISION_ENRICH_MAX_PAGES = int(os.environ.get("JETRAG_PDF_VISION_ENRICH_MAX_PAGES", "50"))

def _enrich_pdf_with_vision(data, *, base_result, file_name, image_parser):
    """PyMuPDF 결과 보존 + 페이지별 vision 호출 → 추가 sections 병합.
    section_title='(vision) p.N' 으로 출처 식별. cap 50 안전장치.
    """
```

**흐름 변경**:
- 일반 PDF (스캔 X) + ENV 활성 시 → `_enrich_pdf_with_vision()` 호출
- PyMuPDF sections 보존 + vision 결과 (ocr_text + structured + caption) 를 페이지별 추가 section 으로 append
- chunks 자동 생성 (chunk.py 변경 0)

### 1.2 `api/tests/test_extract_pdf_vision_enrich.py` (신규)

단위 테스트 4건 (Gemini API mock):
- `test_appends_vision_sections_with_page_meta` — sections 병합 + page 메타 + parser 호출 횟수
- `test_per_page_failure_graceful` — 페이지 단위 실패 graceful (warning 추가, 다른 페이지 계속)
- `test_max_pages_cap` — cap 초과 시 첫 N 페이지만 처리 + warning
- `test_pdf_open_failure_returns_base_result` — 잘못된 PDF bytes → base_result 보존

### 1.3 회귀

- 단위 테스트 308 → **312** (+4)
- ENV `JETRAG_PDF_VISION_ENRICH` default false → SONATA + 기존 자료 영향 0
- tsc/lint 무관 (백엔드 변경)

---

## 2. Sprint 2 (사용자 액션 후)

**사용자 액션**:
1. https://aistudio.google.com/apikey 접속
2. 본 API key 의 billing 활성화 (paid tier 전환)
3. 활성화 알림

**제가 진행**:
1. ENV 활성화 — `JETRAG_PDF_VISION_ENRICH=true` (uvicorn 환경변수 또는 .env)
2. 본 PDF reingest — `POST /documents/b218e8a1-cb35-4540-b969-f0f4fac517fa/reingest`
3. 검증 1 — chunks DB 직접 확인 (p.4 표 markdown / p.6 그림 ocr_text 들어왔나)
4. 검증 2 — 같은 query 재호출 → /search + /answer 결과 변화 측정
5. 본 PDF + SONATA 의 mini-Ragas 비교 (가능 시)
6. 효과 만족 시 SONATA 도 ENV 켜고 reingest (선택)
7. work-log + commit + push

---

## 3. 비판적 한계 (정직 인정)

1. **단위 테스트 mock 검증** — 실제 Gemini Vision 응답 구조 (특히 한국어 다이어그램) 와의 정합성 은 사용자 PDF reingest 후 실측 필요
2. **vision OCR 와 PyMuPDF text 중복** — 같은 페이지의 같은 텍스트가 두 sections 에 등장할 수 있음. chunk_filter dedup 룰이 일부 처리하지만 100% X
3. **인제스트 latency** — 페이지당 1~3초 (본 PDF 41 페이지 = 1~2분). 사용자 자료 누적 적재 패턴 검증 필요
4. **paid tier 비용** — $0.00075/페이지 추정 (실측은 사용자 billing 대시보드)
5. **cap 50 적정성 미검증** — 50 페이지 초과 PDF (정부 공고문 등) 에서 부분 누락 가능

---

## 4. 다음 sprint 후보 (Sprint 2 검증 후)

| # | 후보 | 가치 |
|---|---|---|
| **a** | Sprint 2 검증 — paid 활성화 + reingest + 측정 | 본 의도 직접 해결 |
| b | (c) 답변 시점 multimodal 보완 — 검색 ranking 후순위 case | a 효과 미달 시 |
| c | SONATA 도 ENV 켜고 reingest (선택) | 일관성 |
| d | mini-Ragas 골든셋 본 PDF 추가 | Ragas 측정 정량화 |
| e | 다른 사용자 자료 (HWPX/PPTX/이미지) 적재 → 데이터셋 확장 | 통계 의미 ↑ |

---

## 5. Karpathy 가이드라인 적용 회고

1. **Think Before Coding** — 사용자 push 받고 비판적 재검토 4회 (A+E → heuristic D → 모든 페이지 vision → 답변 시점 multimodal → quota 가설 정정 → 유료 Flash 결정). CLAUDE.md 새 원칙 (자율 N회 재검토) 추가.
2. **Simplicity First** — 별도 stage 신설 회피, 기존 `_reroute_pdf_to_image()` 패턴 재활용한 함수 추가. ENV opt-in 으로 회귀 0.
3. **Surface Assumptions** — quota 가설 (1500/일 → 실측 20/일), vision 정확도 가설 (PoC 검증), latency 가설 (실측 미정)
4. **Verifiable Success Criteria** — 단위 테스트 4건 / Sprint 2 의 본 PDF reingest + 답변 변화 측정
