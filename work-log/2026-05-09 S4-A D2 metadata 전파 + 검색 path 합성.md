# 2026-05-09 — S4-A D2 metadata 전파 + 검색 path 합성 ((a) text 합성 옵션)

> senior-planner v0.1 명세 그대로 ship. D1 의 `VisionCaption.table_caption` /
> `figure_caption` 두 필드를 chunk 단계까지 전파해 `chunks.metadata` 에 주입 +
> vision-derived chunk 한정으로 chunk.text 끝에 `[표: ...]` / `[그림: ...]` 부착.
> v1 cache row 영향 0 — caption 부재 시 합성 skip.

## 1. 작업 개요

### 1.1. 목적

S4-A 표/그림 검색 보강의 두 번째 단계. D1 에서 dataclass·prompt·cache 까지
wire 했지만 chunks 단까지 전파되지 않았음 — D2 는 vision-derived chunk 의 text
와 metadata 양쪽에 caption 을 노출시켜 dense·sparse 검색에서 표/그림 제목을
직접 매칭할 수 있게 한다.

### 1.2. ship 단위

- D2 만 — chunks.metadata 두 키 + chunk.text 합성 (vision-derived 한정)
- D3 후속: 골든셋 expected_chunk 보강
- D4~D5: 표 검색 path 평가 / 그림 보조 인덱스 / 통합

### 1.3. 옵션 선택 ((a) text 합성)

senior-planner 가 제시한 (a)·(b) 중 (a) text 합성 옵션 채택. metadata 만
넣고 검색 path 를 변경하는 (b) 보다 침습 범위가 chunk 생성 지점 1곳에 국한,
sparse 검색의 PGroonga 형태소 분석이 합성 텍스트에서 표/그림 단어를 추가
토큰으로 추출 → dense 임베딩도 동일 텍스트 사용으로 일관성 확보.

## 2. 변경 사항

### 2.1. 파일 변경 목록 (총 6 파일, +186 / -19 LOC)

| 파일 | LOC | 변경 |
|---|---|---|
| `api/app/adapters/parser.py` | +6 / -1 | `ExtractedSection.metadata: dict` 필드 추가 (frozen + factory) |
| `api/app/adapters/impl/image_parser.py` | +13 / -0 | caption section 의 metadata 에 `table_caption` / `figure_caption` 부착 (None 시 키 미주입) |
| `api/app/ingest/stages/extract.py` | +6 / -0 | `_enrich_pdf_with_vision` 가 enriched section 만들 때 metadata 보존 |
| `api/app/ingest/incremental.py` | +12 / -1 | `_sections_to_chunks` 에 caption 메타 전파 + text 합성 적용 |
| `api/app/ingest/stages/chunk.py` | +66 / -7 | `_compose_vision_text` / `_is_vision_derived` 헬퍼 + `_to_chunk_records` 분기 |
| `api/tests/test_vision_caption_propagation.py` | +91 / 0 (신규) | 4 시나리오 — 양쪽 set / table only / 양쪽 None / v1 row 호환 |

### 2.2. ExtractedSection.metadata 신규 필드

```python
@dataclass(frozen=True)
class ExtractedSection:
    text: str
    page: int | None = None
    section_title: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    metadata: dict = field(default_factory=dict)  # NEW
```

frozen 의 의미는 "필드 자체 재할당 금지" 만 — dict 인스턴스는 독립이라 안전.
hash 사용처 0 (grep 검증) 이라 frozen + dict 조합 부작용 없음.

### 2.3. ImageParser caption section 의 metadata 부착

```python
caption_metadata: dict = {}
if caption.table_caption is not None:
    caption_metadata["table_caption"] = caption.table_caption
if caption.figure_caption is not None:
    caption_metadata["figure_caption"] = caption.figure_caption
sections.append(ExtractedSection(..., metadata=caption_metadata))
```

둘 다 None 이면 빈 dict — 키 자체 미주입 → 후속 chunk 단의 `dict.get(key)`
가 None 반환 → 합성 skip.

### 2.4. extract.py / incremental.py — metadata 보존

vision section → enriched section 변환 시 `metadata=dict(sec.metadata)` 로
얕은 복사. caption 두 키만 전달, 다른 키는 ImageParser 가 넣지 않으므로
부작용 0.

### 2.5. chunk.py 의 합성 분기

```python
def _is_vision_derived(section):
    if section.metadata.get("vision_incremental"): return True
    return (section.section_title or "").startswith("(vision)")

def _compose_vision_text(base_text, *, table_caption, figure_caption):
    extras = []
    if table_caption: extras.append(f"[표: {table_caption}]")
    if figure_caption: extras.append(f"[그림: {figure_caption}]")
    if not extras: return base_text
    return base_text + "\n\n" + "\n".join(extras)
```

`_to_chunk_records` 가 `_is_vision_derived` 진입 시점에 한해
metadata 두 키 주입 + text 합성. 일반 chunk 는 기존 동작 100% 유지.

NFC 정규화는 합성된 text 에 적용 — 표/그림 한국어 자모도 NFC 보장.

## 3. 사이드 이펙트 검증

### 3.1. v1 cache row 호환

v1 row 는 `serialize` 가 caption 두 컬럼 없이 저장 → `lookup` 시
`VisionCaption(table_caption=None, figure_caption=None)` 으로 복원
(D1 ship `_deserialize` 의 default None 동작). ImageParser 의
`caption_metadata` 가 빈 dict 라 ExtractedSection.metadata 도 빈 dict →
`_compose_vision_text` 의 `not extras` 분기로 base_text 그대로 반환.

→ v1 row 의 vision-derived chunk 는 D1 이전과 chunk.text + metadata
모두 동일. 확인된 8 row 영향 0.

### 3.2. BGE-M3 embedding path 변경 0

embed.py 는 `chunk.text` 만 받으므로 합성 text 가 그대로 임베딩 입력.
embedding 입력 길이가 일부 페이지에서 ≤ 60자×2 = 120자 늘어남 →
`_MAX_SIZE` (1000자) 한도 안에서 안전.

### 3.3. retrieval path 변경 0

검색은 `chunks.text` (sparse PGroonga) + `dense_vec` (cosine) 만 사용.
metadata 키는 직접 사용처 없음 (D4 표 검색 path 후 활용 예정).
`overlap_with_prev_chunk_idx` 같은 기존 metadata 키와 충돌 0.

### 3.4. content_gate / chunk_filter / tag_summarize 영향 0

이들은 chunk.text 와 section_title 기반 — 합성 후 text 가 살짝 늘어나는
정도로 룰 트립 변동 없음 (`[표: ` 같은 마커는 한국어 룰 패턴과 무관).

## 4. 단위 테스트

### 4.1. 신규 4 시나리오 (`test_vision_caption_propagation.py`)

| 시나리오 | 입력 metadata | 예상 chunk.text 변화 | 예상 chunk.metadata |
|---|---|---|---|
| 1 | `{table_caption: "분기별 매출 추이", figure_caption: "조직도 다이어그램"}` | `+ "\\n\\n[표: ...]\\n[그림: ...]"` | 두 키 set |
| 2 | `{table_caption: "월별 사용량 표"}` only | `+ "\\n\\n[표: ...]"` 한 줄만 | table 만 |
| 3 | `{table_caption: None, figure_caption: None}` | 무변경 | 두 키 부재 |
| 4 | `{}` (v1 row 시뮬) | 무변경 | 두 키 부재 |

전부 PASS. 부정 회귀 가드 — 시나리오 2 는 `[그림:` 토큰 부재까지 assert.

### 4.2. 인접 회귀 (82건)

`test_extract_pdf_vision_enrich` / `test_chunk_w4_q14` /
`test_incremental_vision` / `test_gemini_vision_caption_5fields` /
`test_vision_cache*` — 전부 PASS, 회귀 0.

### 4.3. 전체 단위 테스트 결과

```
Ran 739 tests in 15.012s
OK
```

baseline 735 + 신규 4 = **739 PASS, 회귀 0**.

## 5. 회귀 가드 체크리스트

- [x] ExtractedSection.metadata 추가가 hash 사용처 0 (frozen 안전)
- [x] ImageParser → extract → chunk 의 metadata path 일관성
- [x] incremental_vision path 도 동일 적용 (`_sections_to_chunks` + `_compose_vision_text` 공유)
- [x] v1 cache row 시뮬 (caption 부재) → 합성 skip 검증
- [x] embedding path 변경 0 (chunk.text 풍부화만)
- [x] retrieval path 변경 0
- [x] 외부 API 호출 0
- [x] 의존성 추가 0
- [x] 마이그레이션 0
- [x] 명세 외 리팩토링 0

## 6. 사전 점검 결과 비교

D2 진입 직전 측정 (사용자 제공):
- `vision_page_cache` v1 8 row / v2 0 row
- cold-start spike 예상치 ~$0.036 → sliding 24h 잔여 $0.329 안에서 안전

D2 ship 자체는 vision API 호출 0 — cold-start spike 는 D5 reingest 시점에서
관찰될 예정. D2 까지는 v1 row 그대로 두고 v2 reingest 를 D5 별도 ship 으로
분리한 결정 정합 (sample-report PDF 1개로 spike 측정 권고).

---

## 남은 이슈

### Q-S4-A-D3 — 골든셋 expected_chunk 보강

D2 가 vision-derived chunk 의 text 와 metadata 를 풍부화했지만, 골든셋
질의의 expected_chunk 가 D1 이전 시점에서 작성됨 → D2 후 chunk.text 가
변하면 expected_chunk 의 text snippet 매칭이 깨질 수 있음. D5 reingest
후 골든셋 expected_chunk 를 v2 chunks 기준으로 보강 필요.

판단 시점: D5 reingest 후 골든셋 회귀 측정에서 expected_chunk 매칭률이
기존 baseline 대비 낮아지면 그 시점에서 보강. 현재까지는 측정 데이터 0.

### v1 row 삭제 정책 (D1 이월)

v1 8 row 그대로 보존. 감사용. D5 reingest 가 같은 (sha256, page) 키로
v2 row upsert 하면 v1 은 자동으로 leftover (lookup 은 prompt_version=v2
라 v1 row 무시). DELETE 명시는 후속 운영 결정 사항.

---

## 다음 스코프

### D3 — 골든셋 expected_chunk 보강

- D5 reingest 결과 측정 후 진입
- expected_chunk 의 text snippet 을 v2 chunks 기준으로 갱신
- 또는 expected_chunk 매칭 룰을 chunk_idx 기반으로 변경 (text snippet 의존 ↓)

### D4 — 표 검색 path 평가

- chunks.metadata 의 `table_caption` 을 sparse 검색 boost 키로 활용 검토
- PGroonga 의 `&@~` 연산자 + boost expression 으로 metadata 키 가중치 부여
- 또는 chunks.text 의 `[표: ...]` 토큰 자체가 PGroonga 형태소 추출에서 표/그림
  단어를 자연 토큰으로 인식하므로 (a) 옵션만으로 효과 측정 후 (b) 결정

### D5 — 통합 평가 + sample-report reingest

- 사용자 sample-report PDF 1개로 v1 → v2 reingest 측정
- cold-start spike 실측 (예상 $0.036 → 실측치 비교)
- 골든셋 회귀 (D3 후)
- vision_page_cache v2 row 적재 확인
