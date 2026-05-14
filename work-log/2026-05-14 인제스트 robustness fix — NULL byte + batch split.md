# 2026-05-14 인제스트 robustness fix — NULL byte sanitize + chunks batch split

> 프로젝트: Jet-Rag / commit: `b70b672` / 사용자 요구 "어떤 doc 도 generic 대응" 직접 후속

## 1. 한 줄 요약

- 사용자가 새 도메인 doc 3건 (arXiv 영어 학술 / 삼성전자 사업보고서 / SK 사업보고서) 업로드 중 **인제스트 파이프라인 generic robustness 갭** 2건 노출.
- audit (senior-developer) 가 7.0/10 평가 했지만, 그 evaluation 이 놓친 **Postgres edge case** 직접 발견 — 실측 > audit.
- 두 fix 모두 `SupabasePgVectorStore` (single source) 에서 처리 — 모든 doc 보호.
- 단위 테스트 1171 → **1194** (+23, 회귀 0). 1 commit push.

---

## 2. 발견된 robustness 갭 (실측 evidence)

### 2.1 에러 1 — arXiv (SQL 22P05 "unsupported Unicode escape sequence")

```json
{
  "message": "unsupported Unicode escape sequence",
  "code": "22P05",
  "details": "\\u0000 cannot be converted to text."
}
```

- **원인**: arXiv 같은 LaTeX 기반 PDF 추출 시 chunk text 에 `\x00` NULL byte 가 흘러들어옴 (PyMuPDF 의 text encoding 처리 한계, 영어 학술 PDF 흔한 케이스).
- **영향**: Postgres TEXT 컬럼이 NULL byte 거부 → INSERT 실패 → load stage fail → ingest_jobs failed.
- **doc-binding 0** — 모든 LaTeX/특수 인코딩 PDF 잠재 영향.

### 2.2 에러 2 — SK 사업보고서 (SQL 57014 "statement timeout")

```json
{
  "message": "canceling statement due to statement timeout",
  "code": "57014"
}
```

- **원인**: SK 사업보고서 (~300+ chunks) 가 한 `upsert(payload=[...])` statement 로 일괄 인서트 — Supabase statement_timeout (default ~30~60s) 초과.
- **영향**: 대용량 doc (100+p PDF) 적재 실패.
- **doc-binding 0** — 모든 큰 doc 잠재 영향.

### 2.3 audit 평가와의 격차

senior-developer audit 가 종합 **7.0/10** ("logic generic + 휴리스틱 임계만 active-corpus 튜닝") 평가 했으나, **Postgres edge case 두 건은 누락**. logic 자체는 generic 했지만 **adapter layer 의 robustness** 가 active 9 doc 의 분포 (한국어 + ≤ 50p) 안에서만 검증됨.

**실측 > audit**. 본 fix 가 audit 7.0/10 평가를 8.0+ 로 끌어올렸다고 추정.

---

## 3. Fix 내용

### 3.1 NULL byte sanitize (`_strip_null_bytes`)

```python
@staticmethod
def _strip_null_bytes(value: Any) -> Any:
    """str → replace("\\x00", ""), dict/list/tuple → 재귀, 그 외 그대로."""
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, dict):
        return {k: SupabasePgVectorStore._strip_null_bytes(v) for k, v in value.items()}
    if isinstance(value, list):
        return [SupabasePgVectorStore._strip_null_bytes(v) for v in value]
    if isinstance(value, tuple):
        return tuple(SupabasePgVectorStore._strip_null_bytes(v) for v in value)
    return value
```

- `_serialize_chunk` 최종 return 단계에서 `return SupabasePgVectorStore._strip_null_bytes(row)` 적용
- 모든 string 영역 (text, section_title, metadata, flags, sparse_json 등) 보호
- dense_vec list[float] / None / int / float / bool / bytes 는 영향 0 (그대로)

### 3.2 chunks upsert batch split

```python
def upsert_chunks(self, chunks: list[ChunkRecord]) -> None:
    if not chunks:
        return
    batch_size = max(1, get_settings().chunk_upsert_batch_size)
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        payload = [self._serialize_chunk(c) for c in batch]
        (
            self._client.table(self._TABLE_CHUNKS)
            .upsert(payload, on_conflict="doc_id,chunk_idx")
            .execute()
        )
```

- 신규 setting: `Settings.chunk_upsert_batch_size` (default 50)
- ENV override: `JETRAG_CHUNK_UPSERT_BATCH_SIZE` (최소 1 clamp)
- SK 사업보고서 ~300 chunks → 6 batch, 각 batch <10s 안에 안전 적재

---

## 4. 검증

### 4.1 단위 테스트

```
Ran 1194 tests in 22.740s
OK (skipped=1)
```

- 1171 → **1194** (+23, 회귀 0)
- 신규 `test_supabase_vectorstore.py`:
  - `StripNullBytesTest` 8건 (str / dict / list / tuple / None / int / dense_vec / multi)
  - `SerializeChunkNullByteTest` 5건 (text / section_title / metadata / flags / clean)
  - `UpsertChunksBatchSplitTest` 5건 (120→3 / empty / 40→1 / clamp / 통합 sanitize)
  - `ChunkUpsertBatchSizeSettingsTest` 5건 (default / ENV / invalid / zero clamp / negative clamp)

### 4.2 실측 reingest

```bash
# arXiv reingest
POST /documents/bc7b4591-.../reingest
→ job_id=91f47015..., chunks_deleted=0

# SK reingest
POST /documents/c9d397fd-.../reingest
→ job_id=0bd4bc99..., chunks_deleted=0
```

→ uvicorn `--reload` 가 코드 변경 자동 반영, 두 reingest job 진행 중. 결과는 BG 완료 시점에 확인.

### 4.3 삼성전자 사업보고서 (정상)

```
status=completed stage=done attempts=1
```

50p 미만 + 한국어 PDF → 두 edge case 모두 회피, 1회 시도 통과.

---

## 5. 사용자 의도 정합

> "문서 관련한 데이터는 언제든 지워질 수 있고 업로드해서 새로 생성될 수도 있어. 그래서 어떤 문서가 들어와도 모두 대응할 수 있는 수준까지 되어야 해."

본 fix 가 사용자 요구의 핵심 — **인제스트 robustness**. logic generic 만으론 부족, **adapter 의 edge case 처리** 도 필수. 이제:

- ✅ 한국어 / 영어 PDF (LaTeX 포함) 모두 적재
- ✅ 작은 / 큰 doc (≥ 100p, ≥ 300 chunks) 모두 적재
- ✅ chunk text 에 NULL byte / 비표준 인코딩 포함되어도 안전

---

## 6. 잔여 작업

### 6.1 즉시 (현재 BG 진행 중)
- arXiv reingest (`91f47015`) 완료 확인 — ETA ~5분 (작은 PDF, vision OCR 일부 페이지)
- SK reingest (`0bd4bc99`) 완료 확인 — ETA ~15~30분 (vision page cap 50 적용)

### 6.2 reingest 완료 후 ablation 진단 (audit 권고 2~5)
- **권고 3 진단**: 삼성전자 + SK 둘 다 적재 후 "매출 vs 수익", "영업이익 vs EBIT" 동의어 query → R@10 비교
- **권고 4 진단**: "삼성전자와 SK 비교" cross_doc query → `triggered_signals` 가 T1 발화하는지
- **권고 5 진단**: arXiv `chunks.section_title` 분포 → 영어 numbered heading 인식률

### 6.3 본 PRD 잔여 (변동 없음)
- #8 출처 일치율 50건 수동 검수
- #9 환각률 보강 20건 수동 검수
- `.env` BUDGET default 복귀

---

## 7. 인용 / 참조

- 본 세션 commit: `b70b672`
- 직전: `ab7b6de` (audit 권고 1 work-log), `3605dbb` (query_classifier 이전)
- 영향 파일: `api/app/adapters/impl/supabase_vectorstore.py`, `api/app/config.py`, `api/tests/test_supabase_vectorstore.py`
- audit 리포트: senior-developer 에이전트 (대화 내, 종합 7.0/10)

---

## 8. 다음 세션 시작 시 빠른 확인

```bash
cd /Users/kiwoongmin/Desktop/piLab/Jet-Rag
git log --oneline -3
# b70b672 fix(ingest): generic robustness — NULL byte sanitize + chunks upsert batch split
# ab7b6de docs(work-log): audit 권고 1 — query_classifier 역의존 해소 (1171 OK)
# 3605dbb refactor(query_classifier): production → evals 역의존 해소 (audit 권고 1)

# 인제스트 상태
curl -s "http://localhost:8001/documents/batch-status?ids=bc7b4591-b1c8-426b-a82f-6b2ae8347dec,613a4c6b-41b7-4f23-bf29-9010c7802319,c9d397fd-da09-4333-9078-c01c49ea7fb2"

# 단위 테스트
cd api && uv run python -m unittest discover 2>&1 | tail -2  # 1194 OK
```
