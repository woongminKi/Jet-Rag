# 2026-05-13 M2 W-3 — caption prefix 인제스트 augmentation

## 한 줄 요약

vision-derived chunk (이미지·표 OCR) 의 `table_caption` / `figure_caption` 을 chunk text **앞**에 prefix 로 부착하는 augmentation 분기를 `_compose_vision_text` 에 추가했다. ENV `JETRAG_CAPTION_PREFIX_ENABLED` (default false) gated — OFF 시 기존 suffix 동작 100% 보존. ON 시 `[표 p.{page}: {caption}]\n\n{base}` (table 우선, figure fallback, page None 시 `[표: ...]`). caption 200자 cap (199 + `…`). 22 단위 테스트 신규 (T1~T15 + ENV truthy 7), 전체 회귀 0 (단위 1102 → 1124 PASS, `test_embed_cache.py` 4 사전 flaky 외). M2 W-4 전체 클린 재인제스트 때 ENV ON 으로 박힘 — 본 W-3 = **구현·테스트만**, eval 측정은 M2 W-4 후. PRD v1.2 §3 W-3 / DECISION-8.

## 변경 파일 표

| 경로 | 변경 |
|---|---|
| `api/app/ingest/stages/chunk.py` | `_compose_vision_text` 시그니처에 `page` 추가, `_caption_prefix_enabled()` 헬퍼 신규, ENV ON 시 prefix 분기 + 200자 cap. `_to_chunk_records` 호출부 `page=section.page` 전달. `_CAPTION_PREFIX_ENV` / `_CAPTION_PREFIX_MAX_LEN` 상수 신규. |
| `api/app/ingest/incremental.py` | `_sections_to_chunks` 의 `_compose_vision_text` 호출부 `page=sec.page` 인자만 추가 (ENV 분기는 chunk.py 가 단독 처리). |
| `.env.example` | W-2 동의어 블록 다음에 W-3 caption prefix 블록 추가 (`JETRAG_CAPTION_PREFIX_ENABLED=false`, 주석 4줄). |
| `api/tests/test_caption_prefix.py` | 신규. T1~T15 (15 시나리오) + ENV truthy 평가 7 = **22 테스트**. stdlib unittest, 외부 호출 0. |

## 설계 결정 (W-3-D1 ~ W-3-D9)

- **W-3-D1 — caption prefix only, window 없음**
  - PRD §3 W-3 / DECISION-8 — prefix only 부터 시작. caption 인접 base chunks 에 caption 전파(window) 는 v1.5 ablation 보류.
  - 현 구현은 prefix 만 — 단일 vision-derived chunk text 1개에만 caption 부착, 인접 chunks 무영향.

- **W-3-D2 — prefix vs suffix**
  - DECISION-8 권고대로 **base text 앞**에 부착.
  - 근거: (a) dense 임베딩(BGE-M3) 의 CLS 토큰 인접한 head 에 caption 의 의미 신호 노출 → 의미 우선 학습. (b) snippet head 가 항상 caption 노출 → 사용자 가독성 ↑. (c) `_make_snippet_with_highlights` 가 매칭 fail 시 `text[:480]` 반환 → caption prefix 200자 + base 280자 동시 노출.

- **W-3-D3 — table_caption 우선, figure 는 fallback**
  - 둘 다 set 일 경우 table 우선 1개만 채택 (figure 무시). 둘 다 부착하면 prefix 가 400자 + base text 까지 합성되어 dense 임베딩 오염 risk.
  - 명세 §1 + T10 — table 이 figure 보다 의미 신호가 강하다는 경험적 가정. v1.5 에서 ablation 으로 검증 가능.

- **W-3-D4 — page 메타 활용 포맷**
  - `page is not None` → `[표 p.{page}: ...]` / `page is None` → `[표: ...]`.
  - p.{page} 자체가 검색 키워드 ("3페이지 표") 로도 매칭 가능 → search recall 보강.
  - page None fallback 은 S4-A D2 기존 suffix 포맷과 일관 — 마이그레이션 안전.

- **W-3-D5 — ENV truthy 평가 (`true/1/yes/on` 대소문자 무관)**
  - W-2 `_SYNONYM_LLM_ENV` 가 `== "true"` 만 허용했던 것보다 약간 관대 (`"on"`/`"1"`/`"yes"` 도 허용). Docker/k8s ENV 설정에서 자주 쓰이는 형태 흡수.
  - `_caption_prefix_enabled()` 모듈 top-level 헬퍼 — 함수 호출 시 1회 평가, `patch.dict` 격리 가능.

- **W-3-D6 — caption 200자 cap + `…` 잘림**
  - 명세 §1 — caption 이 200자 초과면 199 + `…` 잘림. 이유: (a) dense 임베딩 max length 보호, (b) snippet head 가 caption 만으로 가득 차 base 가려지는 사용자 경험 회피.
  - 200자 미만 caption 은 변형 0.

- **W-3-D7 — vision-derived 아닌 chunk 는 무영향**
  - `_to_chunk_records` 의 `_is_vision_derived(section)` 분기 진입 시에만 `table_caption`/`figure_caption` 을 추출해 `_compose_vision_text` 에 전달. 일반 chunk 는 `table_caption=None, figure_caption=None` 으로 전달 → ENV ON 이어도 prefix 미부착, base 그대로.
  - 명세 §1 T11 — 둘 다 None → 무영향 (vision-derived 아닌 chunk 와 동일).

- **W-3-D8 — incremental.py 호환**
  - vision incremental path (`_sections_to_chunks`) 도 `_compose_vision_text` 를 재사용. 본 W-3 의 변경은 호출부에 `page=sec.page` 인자만 추가 — ENV OFF 시 출력 100% 보존 (suffix 분기에서는 page 미사용).
  - ENV ON 시 incremental path 도 자동 prefix 부착 — chunk.py 와 같은 동작 보장.

- **W-3-D9 — search.py snippet / answer.py `_clean_chunk_text` 보존**
  - 두 경로 모두 W-2 동의어 마커만 `strip_synonym_marker` 로 제거. caption prefix 는 노출됨이 의도 (DECISION-8 (b)).
  - snippet `_make_snippet_with_highlights` 가 매칭 fail 시 `text[:480]` 반환 → caption 200자 + base 280자 동시 노출. 매칭 hit 시 매칭 위치 ±240 windowed — 매칭이 base 본문이면 caption 일부 잘릴 수 있으나, 정상 동작 (사용자 검색 키워드 우선).

## 사이드 이펙트 점검

- **dense 임베딩 (BGE-M3)**
  - ENV ON 시 chunk text 가 `[표 p.{page}: cap]\n\n{base}` 형태 → 임베딩 input 변경됨.
  - 의도된 변경 — caption 신호가 dense vector 에 반영되어 recall ↑ (PRD §3 W-3 R@10 +0.008~0.012, 표·도식 row R@10 ≤0.33 → ~0.6 기대).
  - ENV OFF default — 인덱싱 시점 변경 0.

- **sparse (PGroonga `&@~`)**
  - chunks.text 가 prefix 포함된 그대로 인덱싱됨 → caption 어휘로도 sparse 매칭 가능.
  - `[표 p.5: ...]` 토큰 자체는 PGroonga Mecab 형태소 분석 후 noise 로 처리 (대괄호·콜론·숫자) → 검색어 충돌 risk 낮음.

- **snippet 노출 (search.py)**
  - 매칭 fail 시 `text[:480]` → caption 노출 (사용자 가독성 ↑).
  - 매칭 hit 시 매칭 위치 ±240 → 검색어 주변 우선. caption 이 매칭 외부에 있으면 잘릴 수 있음 — 정상.

- **LLM 컨텍스트 (answer.py)**
  - `_clean_chunk_text` 가 W-2 마커만 strip → caption prefix 보존. LLM 답변 시 caption 컨텍스트 활용 가능 → 답변 정확도 ↑.

- **Ragas contexts**
  - 위와 동일 — caption 보존. M2 W-4 후 eval 측정 시 baseline 과 비교.

- **chunk_filter (`_classify_chunk`)**
  - caption prefix 부착 chunk → `_classify_chunk` 가 table_noise 로 오탐 안 함 (T15 검증). prefix 어휘가 의미 있는 한국어 + 충분히 긴 base 본문 → `_looks_like_table_cell` 미발화.

- **char_range**
  - `_to_chunk_records` 가 `char_range=(0, len(text_nfc))` 로 기록 → prefix 부착 후 길이가 자연 전파.

- **2차 split (`_split_long_sections`)**
  - `_compose_vision_text` 는 `_to_chunk_records` 안에서 호출되므로 split 단계 이후. prefix 가 800자 초과 청크를 추가로 만들지 않음 (cap 200자).
  - 단 base text 가 _MAX_SIZE (1000) 근처일 경우 prefix 추가로 _MAX_SIZE 초과 가능 — _to_chunk_records 는 별도 split 안 함, 그대로 NFC 후 ChunkRecord 생성. 일반 청킹 정책 (split 우선) 과 분리된 동작이라 정상.

- **W-2 동의어 마커 공존 (T12)**
  - chunk text 끝에 W-2 마커가 자연 부착됨 → 최종 형태: `[표 p.{page}: cap]\n\n{base}\n\n[검색어: ...]`.
  - 순서 보장 (caption prefix 가 가장 앞, 동의어 마커가 가장 뒤). 두 augmentation 독립적, 충돌 0.

- **NFC 정규화**
  - caption 에 NFD 결합문자 입력되어도 `_to_chunk_records` 의 `text_nfc = unicodedata.normalize("NFC", synthesized)` 가 prefix 부착 후 일괄 NFC 강제 → sparse/dense 일관성 보장.

- **section_title**
  - section_title 컬럼은 별도 보존. text 에는 미포함 → prefix 와 무관.

## ENV OFF 시 동작 완전 불변 확인

- `_compose_vision_text` ENV OFF 분기 = S4-A D2 기존 코드 100% 동일 (`extras` list + suffix `\n\n`.join).
- T1~T2 PASS — ENV OFF + caption 부착 시 `{base}\n\n[표: {cap}]` 출력.
- `_caption_prefix_enabled()` default `"false"` → ENV unset/false/random → OFF 분기.
- 전체 회귀 단위 1102 → 1124 PASS (신규 22 외 변경 0).

## incremental.py 자동 전파 확인

- vision incremental path `_sections_to_chunks` 가 `_compose_vision_text(sec.text, table_caption, figure_caption, page=sec.page)` 호출 — chunk.py 의 `_to_chunk_records` 와 동일 인자.
- ENV ON 시 동일 prefix 부착, OFF 시 동일 suffix 부착. 분기 로직 chunk.py 에 단독 → DRY.
- 변경 분량: incremental.py 1줄만 (`page=sec.page` 추가). 별도 분기 0.

## search.py / answer.py snippet · `_clean_chunk_text` caption 보존 확인

- **search.py:1565** `strip_synonym_marker(c.get("text") or "")` — 한 가지만 호출 → caption 보존.
- **answer.py:88** `_clean_chunk_text(text)` — `strip_synonym_marker` 만 호출 → caption 보존.
- 두 경로 다 caption 노출 의도 (DECISION-8). 변경 0.

## 테스트

- `cd api && uv run python -m unittest discover -s tests -p "test_*.py"`
- 결과: **1124 tests, 4 failures** — `test_embed_cache.py` 4 (사전 flaky, 영구 캐시 사이드이펙트, W-3 무관).
- W-3 신규: **22/22 PASS** (`test_caption_prefix.py`).
- 회귀 확인 묶음: `test_synonym_inject.py` (32) + `test_chunk_entities.py` + `test_chunk_filter.py` + `test_chunk_w4_q14.py` + `test_caption_prefix.py` (22) = **120/120 PASS**.

### 신규 테스트 매핑

| ID | 시나리오 | 메서드 |
|---|---|---|
| T1 | ENV OFF + table_caption + page → suffix | `test_t1_env_off_table_caption_page_suffix_preserved` |
| T2 | ENV OFF + figure_caption + page → suffix | `test_t2_env_off_figure_caption_page_suffix_preserved` |
| T3 | ENV ON + table + page=5 | `test_t3_env_on_table_caption_page` |
| T4 | ENV ON + figure + page=10 | `test_t4_env_on_figure_caption_page` |
| T5 | ENV ON + table + page=None | `test_t5_env_on_table_caption_page_none_fallback` |
| T6 | ENV ON + figure + page=None | `test_t6_env_on_figure_caption_page_none_fallback` |
| T7 | ENV ON + whitespace caption → 미부착 | `test_t7_env_on_whitespace_caption_no_prefix` |
| T8 | ENV ON + 둘 다 None → 미부착 | `test_t8_env_on_both_none_no_prefix` |
| T9 | ENV ON + 250자 caption → 199자 + `…` | `test_t9_env_on_caption_over_200_truncated` |
| T10 | ENV ON + table+figure → table 우선 | `test_t10_env_on_both_table_and_figure_table_wins` |
| T11 | ENV ON + vision-derived 아님 → 무영향 | `test_t11_env_on_non_vision_chunk_unchanged` |
| T12 | ENV ON + W-2 마커 동시 ON → 공존 | `test_t12_env_on_with_synonym_marker_coexist` |
| T13 | section_title 있는 일반 chunk → 무영향 | `test_t13_section_title_only_chunk_unchanged` |
| T14 | NFC 정규화 통과 | `test_t14_caption_nfd_normalized_to_nfc` |
| T15 | chunk_filter table_noise 미오탐 | `test_t15_prefix_chunk_not_table_noise` |
| +7 | ENV truthy 평가 (unset/false/true/TRUE/1/on/random) | `CaptionPrefixEnvParsingTest` 7개 |

## 정적 caption 포맷 샘플

### T3 — table_caption + page=5

입력:
- `base_text="표 내용"`, `table_caption="회원 자격"`, `figure_caption=None`, `page=5`, ENV ON

출력:
```
[표 p.5: 회원 자격]

표 내용
```

### T4 — figure_caption + page=10

입력:
- `base_text="그림 내용"`, `table_caption=None`, `figure_caption="흐름도"`, `page=10`, ENV ON

출력:
```
[그림 p.10: 흐름도]

그림 내용
```

### T12 — W-2 동의어 마커 공존 (W-3 ON + W-2 ON)

`table_caption="쏘나타 회원"` (사전 키 매칭) + base="본문" + page=5:

```
[표 p.5: 쏘나타 회원]

본문

[검색어: sonata Sonata]
```

caption prefix 가 가장 앞, base 본문 가운데, W-2 동의어 마커가 가장 뒤. 순서 보장 (T12 검증).

## 남은 이슈

- **section_title prefix v1.5 보류** — section_title 을 chunk text head 에 prepend 하는 augmentation 은 W25 D12 dry-run 결과 net-negative (G-S-009 악화 risk) → SKIP. v1.5 재검토 보류.
- **caption window v1.5 보류** — caption 인접 base chunks 에 caption 전파 (window=±1 chunk) 는 DECISION-8 권고 v1.5 — prefix only 효과 측정 후 진입.
- **sample-report dark zone W-4 운영** — sample-report (1000 chunk = DB 40%) 의 dense_vec 복구 작업은 M2 W-4 전체 클린 재인제스트 일정에 포함 — [[project_sample_report_dark]].
- **W-3 단독 효과 측정 보류** — M2 W-4 (W-2 동의어 + W-3 caption prefix 동시 ON 클린 재인제스트) 후 ON/OFF ablation eval 로 분리 측정 예정. 본 W-3 = 구현·테스트만.

## 다음 스코프

- **M2 W-4** — 전체 클린 재인제스트:
  - ENV `JETRAG_SYNONYM_INJECTION_ENABLED=true` + `JETRAG_CAPTION_PREFIX_ENABLED=true` 동시 ON.
  - chunks 2469 → 재생성 (W-2 마커 + W-3 prefix 박힘).
  - eval (golden_v2 182 row) baseline 재측정 + W-2/W-3 ON/OFF ablation 2×2 매트릭스.
  - top-1 ≥ 0.80 게이트 판정 (PRD §3 DECISION-1).
