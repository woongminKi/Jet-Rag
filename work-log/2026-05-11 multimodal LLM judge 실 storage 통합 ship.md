# 2026-05-11 — Multimodal LLM judge 실 storage 통합 ship

## 0. 한 줄 요약

origin 338a2d9 시점에 stub 으로 ship 되어 있던 `evals/_multimodal_judge.py` helper 에
**실 image_fetch_fn (Supabase storage → PyMuPDF 150 DPI render)** 과
**실 llm_call_fn (Gemini 2.5 Flash multimodal API + vision_usage_log 자동 기록)**
을 추가하고, `evals/run_ragas_regression.py` 에 `--with-multimodal-judge` flag 와
`--only-qtype` 필터를 통합. vision_diagram qtype 8 row 대상 실 측정에서
**text-only faithfulness 가 잡지 못하는 거짓 양성/거짓 음성을 multimodal judge 가 식별**
함을 확인 (cost $0.011, cap $0.30 의 3.6%, 회귀 0).

---

## 1. helper 위치 + 변경 사항

### 1.1 변경 파일

| 파일 | 변경 | 라인 영향 |
|---|---|---|
| `evals/_multimodal_judge.py` | `make_image_fetcher()` + `make_llm_caller()` factory 추가 | +152 |
| `evals/run_ragas_regression.py` | `--with-multimodal-judge` / `--multimodal-judge-qtypes` / `--multimodal-judge-model` / `--only-qtype` CLI + `_pick_judge_page` / `_run_multimodal_judge` helper + `RowMeasurement` 3 필드 + markdown breakdown 섹션 | +175 |
| `api/app/services/vision_metrics.py` | `_VALID_SOURCE_TYPES` 에 `"multimodal_judge"` 추가 | +1 |
| `api/tests/test_multimodal_judge.py` | `make_image_fetcher` / `make_llm_caller` / source_type 검증 단위 테스트 9건 추가 | +210 |
| `api/tests/test_run_ragas_regression.py` | `_pick_judge_page` / `_run_multimodal_judge` / RowMeasurement multimodal field / render breakdown 단위 테스트 8건 추가 | +140 |

### 1.2 stub 시그니처 (origin 338a2d9 시점)

```python
def evaluate_multimodal(
    *,
    query: str,
    answer: str,
    doc_id: str,
    page: int,
    image_fetch_fn,  # (doc_id, page) -> bytes (PNG/JPEG)
    llm_call_fn,     # (image_bytes, system, user) -> str (raw JSON)
) -> MultimodalJudgmentResult
```

본 sprint 에서 두 callable 의 실 구현 (production storage / API 연결) 을 추가.

---

## 2. image_fetch_fn 구현

### 2.1 `make_image_fetcher(*, bucket=None, dpi=150)`

- `documents` 테이블에서 `storage_path` 조회 (`limit(1)` + `eq(id, doc_id)`)
- `SupabaseBlobStorage.get(storage_path)` 로 PDF bytes 다운로드
- `fitz.open(stream=pdf_data, filetype="pdf")` + `page.get_pixmap(dpi=150)` + `pix.tobytes("png")` 로 PNG 변환
- page 는 **1-indexed** (golden v2 / chunks.page 컬럼 규약 일치)

### 2.2 캐싱 전략 (cost 절감)

| 캐시 | maxsize | 의도 |
|---|---|---|
| `_fetch_pdf_bytes(doc_id) -> bytes` | 16 | 같은 doc 의 여러 vision_diagram row 가 동일 storage round-trip 회피 |
| `_fetch_page_png(doc_id, page) -> bytes` | 64 | 같은 row retry 시 fitz render 비용 0 |

scope: factory 인스턴스 단위 — 한 측정 run 동안만 유지 (process 종료 시 자연 소멸).

### 2.3 에러 처리

- storage_path NULL → `RuntimeError("storage_path is NULL")`
- documents row 부재 → `RuntimeError("documents row not found")`
- page out of range → `RuntimeError("page out of range")`

모두 `evaluate_multimodal` 의 `try/except` 에서 catch → `score=None` graceful.

### 2.4 DPI 선택

`_SCAN_RENDER_DPI=150` (extract.py 의 PDF scan 처리와 동일) — Gemini multimodal 입력에 충분한 해상도 + cost 절감 균형. 단위 테스트는 `dpi=72` 로 빠른 render.

---

## 3. llm_call_fn 구현

### 3.1 `make_llm_caller(*, model="gemini-2.5-flash", record_usage=True)`

- `_gemini_common.get_client()` 재사용 (기존 lazy-init singleton)
- contents 구성: `[system_text, image_bytes, user_text]` 3 part 단일 turn
- config: `temperature=0.0` (judge → deterministic), `response_mime_type="application/json"`
- `with_retry(call, label="multimodal_judge")` — 기존 retry 패턴 통합 (3회 + 지수 백오프)

### 3.2 vision_usage_log 자동 기록

success path 에서 `_parse_usage_metadata(response, model=model)` (gemini_vision.py 의 helper 재사용) 로 token usage 추출 → `vision_metrics.record_call(source_type="multimodal_judge", usage=...)` 호출.

`vision_metrics._VALID_SOURCE_TYPES` 에 `"multimodal_judge"` 추가 — 없으면 None fallback + warn.

record_call 자체 실패는 graceful swallow — judge 결과 반환에 영향 0.

### 3.3 mime_type

PyMuPDF `pix.tobytes("png")` → `"image/png"` 고정. fetch / caller 의 결합도 유지.

---

## 4. `--with-multimodal-judge` flag 통합

### 4.1 CLI 추가

```
--with-multimodal-judge          # default OFF
--multimodal-judge-qtypes vision_diagram  # 화이트리스트 (콤마)
--multimodal-judge-model gemini-2.5-flash  # ENV JETRAG_MULTIMODAL_JUDGE_MODEL
--only-qtype vision_diagram       # stratified 우회 (qtype 집중 측정)
```

`ENV JETRAG_RAGAS_MULTIMODAL_JUDGE=1` 도 동일 효과 (사용자 요구).

### 4.2 `RowMeasurement` 신규 필드

```python
faithfulness_multimodal: float | None = None
multimodal_judge_page: int | None = None
multimodal_judge_reason: str | None = None
```

기존 `faithfulness` (text-only RAGAS) 와 **별도 컬럼** — 직접 비교 가능. `_METRICS` tuple 에는 추가하지 않음 (threshold guard 결합도 회피).

### 4.3 page 결정 — `_pick_judge_page(sources)`

`/answer` 응답의 `sources[].page` 중 처음으로 valid int (>0) 인 page 선택. sources 는 score 내림차순 (`AnswerSource.score`) → 답변 근거로 가장 적합한 page 가 선택됨. page 식별 실패 시 score=None, reason="no_page".

### 4.4 markdown breakdown 섹션

multimodal row 가 1건 이상이면 `## Multimodal LLM judge (vision_diagram 한계 우회)` 섹션 자동 추가:
- 측정 row 수
- multimodal mean
- 같은 row 의 text-only mean + 회복 폭 (delta)
- per-row 표 (id / qtype / page / text faithfulness / multimodal faithfulness / reasoning)

---

## 5. 단위 테스트

### 5.1 신규 추가

| 테스트 | 검증 |
|---|---|
| `MakeImageFetcherTest.test_renders_png_from_storage_pdf` | storage.get → fitz → PNG 통합, PNG magic bytes 확인 |
| `MakeImageFetcherTest.test_raises_on_null_storage_path` | storage_path=None graceful RuntimeError |
| `MakeImageFetcherTest.test_raises_on_doc_not_found` | doc_id 부재 RuntimeError |
| `MakeImageFetcherTest.test_raises_on_page_out_of_range` | page 999 (1-page PDF) RuntimeError |
| `MakeLlmCallerTest.test_returns_text_and_records_usage` | Gemini mock + vision_usage_log record_call(source_type="multimodal_judge") |
| `MakeLlmCallerTest.test_raises_on_empty_response_text` | 빈 response 시 RuntimeError (retry exhaust 후) |
| `MakeLlmCallerTest.test_record_usage_failure_is_graceful` | record_call 실패해도 judge text 정상 반환 |
| `MultimodalJudgeSourceTypeTest.test_multimodal_judge_in_valid_source_types` | `_VALID_SOURCE_TYPES` 멤버십 확인 |
| `PickJudgePageTest` × 3 | first / null skip / all-null None |
| `RunMultimodalJudgeTest` × 2 | page=None → no_page / 정상 score 계산 |
| `RowMeasurementMultimodalFieldsTest` × 2 | default None / kwargs set |
| `RenderMultimodalBreakdownTest` × 2 | multimodal row 있음 → 섹션 출력 / 없음 → 미출력 |

총 **+17 신규 테스트**.

### 5.2 회귀 결과

```
Ran 930 tests in 16.501s

OK
```

baseline 913 → 930 (+17), **회귀 0**.

---

## 6. 실 측정 결과 (vision_diagram qtype 8 row)

### 6.1 실행 커맨드

```bash
DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' \
uv run --project api python evals/run_ragas_regression.py \
  --only-qtype vision_diagram \
  --max-rows 20 \
  --skip-context-precision \
  --with-multimodal-judge \
  --cost-cap-usd 0.30 \
  --cost-per-row-usd 0.006 \
  --out-md evals/results/ragas_multimodal_judge_run.md \
  --out-json evals/results/ragas_multimodal_judge_run.json
```

### 6.2 측정 결과 (vision_diagram 8 row)

| 항목 | 값 |
|---|---|
| sample n | 8 (vision_diagram 전체) |
| 측정 성공 | 7 (1건 G-A-208 `/search` HTTP timeout) |
| multimodal judge 측정 | 6 (G-U-016 은 page=None — sources page null 누락) |
| text-only faithfulness mean | 0.696 |
| multimodal faithfulness mean | 0.617 |
| 회복 폭 mean | -0.029 (단순 평균은 약간 하향) |
| 총 소요 | 252.8s |

### 6.3 row 별 비교 — multimodal judge 의 교정 효과

| id | text-only | multimodal | 해석 |
|---|---|---|---|
| G-U-005 | 1.00 | 0.50 | text judge 거짓 양성 — 답변이 "차량 내부 이미지 제공" 주장하나 실제 page 는 외부만. multimodal 가 식별. |
| G-U-006 | 0.88 | **1.00** | multimodal 가 다이어그램 텍스트 모두 검증. |
| G-A-206 | 0.00 | 0.50 | text judge 거짓 음성 — 도표 [그림 1.8] 일치하나 RAGAS 가 검증 불가. multimodal 가 부분 확인. |
| G-A-209 | 0.00 | 0.50 | 동일 패턴 (text 0.0 → multimodal 0.5 회복). |
| G-A-211 | 1.00 | 0.20 | text judge 거짓 양성 — 답변에 모델/램프 정보 다수, 실제 page 는 인스퍼레이션만. multimodal 정확. |
| G-A-214 | 1.00 | 1.00 | 일치 (multimodal 가 "이미지에 도표 없음" 정확 식별 — 답변도 honest). |

**핵심**: 평균 -0.029 는 multimodal 판정이 더 엄격하기 때문. **vision_diagram qtype 의 RAGAS text-only 한계 우회라는 본 sprint 목표는 달성** — G-A-206 / G-A-209 의 text 0.00 → multimodal 0.50 회복, G-U-005 / G-A-211 의 거짓 양성 교정 모두 multimodal 만이 가능한 판정.

### 6.4 활성 한계 변동

- **한계 #3 (vision_diagram faithfulness 0.0 한계)**: **부분 해소**. multimodal judge 가 row 별 회복/교정 모두 실행. 단 평균 metric 으로는 +Δ 보장 안 됨 (judge 가 더 엄격해진 결과).
- 미해소 잔여: G-A-208 HTTP timeout (uvicorn /search) — 별도 sprint.

---

## 7. Cost 실측 (cap $0.30 안쪽)

`vision_usage_log` SUM (최근 1시간 `source_type='multimodal_judge'`):

| 항목 | 값 |
|---|---|
| row 수 | 6 (모두 success) |
| 총 prompt_tokens | 3,236 |
| 총 output_tokens | 507 |
| 총 추정 cost USD | **$0.010955** |
| row 평균 cost USD | $0.001826 |

cost cap $0.30 의 **3.6%** — 매우 안전.

text RAGAS (faithfulness + answer_relevancy, judge = Gemini 2.5 Flash, embed = Gemini) 별도 cost: ~7 row × $0.006 ≈ $0.042. 합계 $0.053 (cap 의 18%).

---

## 8. 활성 한계 변동 + 다음 후보 우선순위

### 8.1 한계 해소

- **#3 (vision_diagram faithfulness 0.0)**: multimodal judge 실 storage 통합으로 우회 경로 ship. text-only judge 한계 명시 가능.

### 8.2 새로 인지된 잔여 이슈

1. **G-A-208 `/search` HTTP timeout** — vision_diagram doc_id=d1259dfe (sample-report) 의 1건. uvicorn watcher / Supabase RPC latency 확인 필요. 별도 sprint.
2. **G-U-016 page=None** — `/answer` sources 의 page 가 null (chunks.page 미설정 가능성). chunks.page backfill 점검 또는 hwpx parser 의 page 할당 검토.
3. multimodal judge mean -0.029 의 해석 — text-only judge 가 거짓 양성 다수 → 사용자에게 multimodal 가 더 정확하다는 일관 narrative 확보를 위한 sample 확대 (n=15+) 필요.

### 8.3 다음 후보 우선순위

| 순위 | 작업 | 의도 |
|---|---|---|
| 1 | multimodal judge sample 확대 (n=15+) — vision_diagram 외 table_lookup qtype 도 evaluate | text-only judge 한계의 일반화 확인 |
| 2 | G-A-208 timeout 진단 | sample-report doc 의 chunks 분포 / RPC 성능 점검 |
| 3 | hwpx parser chunks.page backfill | G-U-016 page=None 회복 |
| 4 | multimodal judge cost / accuracy trade-off — `temperature=0.2` vs `0.0` 비교 | judge 안정성 sweep |

---

## 9. 산출물

- `evals/_multimodal_judge.py` — `make_image_fetcher` / `make_llm_caller` factory 추가
- `evals/run_ragas_regression.py` — CLI flag + helper + RowMeasurement 필드 + markdown breakdown
- `api/app/services/vision_metrics.py` — source_type 멤버 추가
- `api/tests/test_multimodal_judge.py` — +9 단위 테스트
- `api/tests/test_run_ragas_regression.py` — +8 단위 테스트
- `evals/results/ragas_multimodal_judge_run.md` / `.json` — vision_diagram 8 row 실 측정 결과
- `evals/results/ragas_multimodal_judge_run.partial.jsonl` — incremental 측정 백업

---

## 10. ship 체크리스트

- [x] 단위 테스트 회귀 0 (913 → 930, +17)
- [x] cost cap $0.30 안쪽 (실측 $0.011, 3.6%)
- [x] vision_diagram qtype 외 자동 진입 금지 (qtype 화이트리스트 default)
- [x] flag default OFF strict 유지
- [x] 새 의존성 0 (fitz / supabase / google.genai 모두 기존 venv)
- [x] source_type enum 확장 (없으면 vision_metrics 가 warn 후 None fallback)
- [x] 한국어 에러 메시지 / docstring
- [x] graceful 처리 (storage 부재 / page out of range / LLM 실패 모두 score=None)
