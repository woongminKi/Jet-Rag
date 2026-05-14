# 2026-05-15 세션 종합 — 데이터 정리 + audit + robustness + 새 도메인 진단 핸드오프

> 프로젝트: Jet-Rag / 세션: 2026-05-14 ~ 2026-05-15 새벽 / HEAD: `1ce5465`
> **결산**: 9 commit · 사용자 데이터 정리 → over-fit audit → 권고 1 적용 → 새 3 doc 인제스트 robustness fix → 권고 3·4·5 진단까지 한 호흡. 단위 테스트 **1124 → 1194** (+70, 회귀 0). 코드 변경: production +1 신규 모듈 (query_classifier), supabase_vectorstore robustness 보강, golden_v2 182→132 row.

---

## 0. 한 줄 요약

- **사용자 요구 "어떤 doc 도 generic 대응"** 에 대한 직접 응답 sprint.
- 4 doc hard-delete (`승인글 템플릿1/3` · `sonata-the-edge_catalog` · `포트폴리오_이한주 - na Lu`) + Storage 39MB 회수.
- senior-developer audit (**종합 7.0/10**, logic generic + 휴리스틱 임계 active-corpus 튜닝) — critical 1건 (admin.py → evals 역의존) 즉시 해소.
- 새 3 도메인 doc (arXiv 영어 학술 / 삼성전자 사업보고서 / SK 사업보고서) 인제스트 중 **Postgres edge case 2건** 노출 → `_strip_null_bytes` (SQL 22P05) + batch split (SQL 57014) generic fix. audit 가 놓친 robustness 갭 실측 보완.
- 권고 3·4·5 진단: 동의어 보강 ROI 낮음 / arXiv 영어 heading 인식 실패 실증 / cross_doc warm 재진단 필요.

---

## 1. 본 세션 commit 흐름 (9 commit, `main` push 완료)

| # | commit | 한 일 | 효과 |
|---|---|---|---|
| 1 | `95ed745` | **P4**: golden_v2 의 hard-deleted 4 doc 참조 row 47건 제거 (182→135) + 단위 테스트 3 assert 갱신 | 향후 R@10 분모 정직성 회복 |
| 2 | `8cd8be7` | **위생**: `golden_batch_smoke.py` `JETRAG_API_BASE_URL` env override + `bak.20260513` untrack | port 8001 운영 호환 |
| 3 | `c231f76` | docs(work-log): P4 (135 row) | — |
| 4 | `8d7292e` | feat(evals): KPI #8 1차 자동 채점 표 + P4-b cross_doc 잔여 정리 | (revert 됨, P4-b 만 유지) |
| 5 | `8455ba2` | docs(work-log): KPI #8 자동 채점 | (revert) |
| 6 | `22f7412` | **revert**: KPI #8 작업 되돌림 (사용자 지시) — `sample_source_match.py` 제거, P4-b (132 row) 만 유지 | scope 사용자 의도 정합 |
| 7 | `3605dbb` | **audit 권고 1**: `app/services/query_classifier.py` 신규 — `admin.py` → `evals/auto_goldenset.py` lazy import 역의존 해소 | logic -179 line, 의존 그래프 단방향 회복 |
| 8 | `ab7b6de` | docs(work-log): audit 권고 1 (1171 OK) | — |
| 9 | `b70b672` | **인제스트 robustness fix**: `SupabasePgVectorStore` 의 `_strip_null_bytes` (SQL 22P05) + `upsert_chunks` batch split (SQL 57014). `JETRAG_CHUNK_UPSERT_BATCH_SIZE` env 신규 (default 50). | 새 도메인 doc 인제스트 generic 보호 |
| 10 | `1ce5465` | docs(work-log): robustness fix (1194 OK) | — |

→ 현 main HEAD = **`1ce5465`** (origin push 완료).

> 본 sprint 외 별도 mid-session: 4 doc soft-delete → hard-delete + Storage cleanup (DB 직접 작업, commit 없음, 별도 작업).

---

## 2. 핵심 결정 사항

### 2.1 데이터 정리 (사용자 명시 요청)
- **soft-delete → hard-delete 단계 진행** — chunks 460 · ingest_jobs 15 · answer_ragas_evals 2 · documents 4 = **481 row + Storage 4 파일 (39MB)** 회수.
- chunks 갯수: 2469 → 2009.

### 2.2 P4 / P4-b (hard-delete 부수 효과)
- **P4** (`95ed745`): golden_v2 의 `doc_id` 매칭 47 row 제거 → 135 row.
- **P4-b** (`8d7292e` 일부): cross_doc qtype 의 alias 형식 (`"쏘나타:25,데이터센터:344"`) 3 row 추가 정리 → 132 row. doc_id 가 비어있어 P4 에서 누락.
- 단위 테스트 `n_rows == 132`, `caption_dependent true 25 / false 107` 로 갱신.

### 2.3 KPI #8 자동 채점 revert (사용자 지시)
- 자동 채점 결과 20/50 (0.40) 까지 측정했으나, "scope 가 hard-delete 정리까지였음" 사용자 의도 확인 후 KPI #8 신규 코드 (`sample_source_match.py`) revert.
- P4·P4-b 의 golden_v2 132 row 및 test 갱신은 유지 (legitimate 후속).

### 2.4 audit 종합 (senior-developer 에이전트, **7.0/10**)
- **결론**: logic generic + 휴리스틱 임계만 active-corpus 튜닝. 어떤 doc 들어와도 인제스트·검색 logic 작동, 단 도메인 외 doc 의 검색 품질 KPI 약간 회귀 가능.
- **critical 1건**: `api/app/routers/admin.py:60-70` → `evals/auto_goldenset.py` sys.path lazy import (역방향 의존). **해소** (`3605dbb`).
- **medium 5건**: `synonym_dict` (default OFF) / `query_expansion` 사전 / `intent_router._DOC_NOUN` 14 명사 화이트리스트 / `vision_need_score` 5 임계 / `pymupdf` heading font ratio. **단계적 정리 대상** (잔여).
- **반대 증거**: 인제스트 8-stage · adapter layer · content_gate · dedup · tag_summarize 등 핵심 logic 0 doc-binding 확인.

### 2.5 인제스트 robustness 갭 발견 (audit 누락) + fix
- arXiv (LaTeX 기반) → chunk text 에 `\x00` NULL byte 흘러들어와 SQL 22P05.
- SK 사업보고서 (~300+ chunks) → 한 statement upsert 가 Supabase statement_timeout 초과해 SQL 57014.
- **fix** (`b70b672`):
  - `_strip_null_bytes` 재귀 헬퍼 (str/dict/list/tuple), `_serialize_chunk` 최종 return 단계 적용
  - `upsert_chunks` 가 `settings.chunk_upsert_batch_size` (default 50) 로 분할 upsert
  - `JETRAG_CHUNK_UPSERT_BATCH_SIZE` ENV 신규 (최소 1 clamp)
- 단위 테스트 23건 신규 (`test_supabase_vectorstore.py`).

### 2.6 권고 3·4·5 진단 (warm cache miss · cold-start 한계 안에서)
- **권고 3 (동의어)** — "삼성 매출/수익" 4/4 same_doc, 3/4 same_page → dense embedding 이 이미 동의어 처리. **default OFF 유지, 사전 외부화 보류**.
- **권고 4 (cross_doc T1)** — 진단 중 timeout (HF cold-start 영향). warm 상태에서 재진단 필요.
- **권고 5 (영어 heading)** — arXiv 의 `section_title` **94.5% (708/749)** 가 페이지 header (`arXiv:2601.00442v1 [hep-th] 1 Jan 2026`) — **ROI 실증** ✅.

---

## 3. 새 3 도메인 doc 인제스트 결과

| doc | doc_id | 크기 | chunks | unique pages | avg c/p | 평가 |
|---|---|---:|---:|---:|---:|---|
| **arXiv 영어 학술** | `bc7b4591` | 833 KB | 749 | 56 | 13.4 | section_title 94.5% 페이지 header (권고 5 실증) |
| **삼성전자 사업보고서** | `613a4c6b` | 2.2 MB | 8493 | 108 (range 1~514) | 78.6 | 표 위주 PDF — 한국어 numbered heading 정상 |
| **SK 사업보고서** | `c9d397fd` | 6.8 MB | 25806 | 116 (range 1~1432) | **222.5** | 거대 표 (1432p) — over-chunking 의심 |

→ `documents` 9 → **12** / `chunks` 2009 → **37057** (+35k). vision_chunks 모두 0 (vision_budget_exceeded flag).

### 3.1 over-chunking 평가 (재평가)
- PyMuPDF block-level 분할 + chunk.py `_looks_like_table_cell` 룰이 사업보고서 표 row 단위로 잘 잡음 — **의도된 동작**.
- **warm latency 영향 X** (warm 170ms, cache hit 115ms 정상).
- 검색 품질 영향은 별도 측정 필요 (IDF 가중치 분포 변화).

### 3.2 latency 측정
| call | latency | 해석 |
|---|---:|---|
| 1 (cold) | 1.8~20.6s | HF BGE-M3 scale-to-zero cold-start |
| 2 (warm) | 170ms ✅ | 정상 |
| 3 (warm, embed cache) | 115ms ✅ | 정상 |
| P95 (12 sample) | **29.7s** | KPI #10 게이트 3s 초과 — HF cold-start (v1.5 영역) |

→ **chunks 폭증 자체는 latency 무영향**. P95 29.7s 는 HF self-host 만 본질적 해소 가능 (v1.5).

---

## 4. 단위 테스트 추이

```
세션 시작:  1124  (전 세션 끝 시점)
↓ P4·P4-b test 갱신 (n_rows·caption_counts)
1167  (변동 0)
↓ test_query_classifier.py 신규 +11 / test_auto_goldenset.py -9 +2 (re-export)
1171  (audit 권고 1 후)
↓ test_supabase_vectorstore.py 신규 +23 (NULL byte sanitize + batch split + settings)
1194  ✅ 회귀 0 (skipped=1)
```

---

## 5. 코드 자산 (다음 세션 / v1.5 재사용)

| 자산 | 위치 | 용도 |
|---|---|---|
| **`app.services.query_classifier`** | `api/app/services/query_classifier.py` | 9 라벨 룰 분류 single source (admin · evals 둘 다 import) |
| **`_strip_null_bytes`** | `api/app/adapters/impl/supabase_vectorstore.py` | 재귀 NULL byte 정제 (str/dict/list/tuple) — Postgres TEXT/JSONB 보호 |
| **`upsert_chunks` batch split** | 동상 | `JETRAG_CHUNK_UPSERT_BATCH_SIZE` (default 50) — statement timeout 회피 |
| **assets/private/** | `assets/private/` (gitignored) | 새 도메인 fixture 보관 (arXiv·삼성·SK 3 PDF 보관 중) |

---

## 6. 현재 DB / 시스템 상태 (HEAD `1ce5465` 시점)

- **documents**: **12** (pdf 8 / hwpx 2 / pptx 1 / hwp 1)
  - 활성 9 (기존) + arXiv·삼성·SK 신규 3
- **chunks**: **37057** (effective 21913)
- **golden_v2**: **132 row** (P4+P4-b 후)
- **단위 테스트**: **1194** (회귀 0)
- **search_slo**: p50 849ms / p95 29684ms / cache_hit 16.7% — cold-start 영향
- 활성 ENV (변동 없음):
  - `JETRAG_PDF_VISION_ENRICH=true`
  - `JETRAG_SYNONYM_INJECTION_ENABLED=true` (M2 W-2)
  - `JETRAG_CAPTION_PREFIX_ENABLED=true` (M2 W-3)
  - `JETRAG_PAID_DECOMPOSITION_ENABLED` default OFF
  - `JETRAG_CROSS_DOC_SCOPED_SEARCH` default OFF
  - `JETRAG_DOC_BUDGET_USD=2.00` / `JETRAG_DAILY_BUDGET_USD=3.00` (M2 W-4 임시 상향 — default 복귀 보류 중)
  - `JETRAG_CHUNK_UPSERT_BATCH_SIZE` 미설정 (default 50)
- 활성 서버: API 8001 (uvicorn --reload) / Frontend 3001 (Next dev)

---

## 7. 잔여 후보 (다음 세션 결정 영역)

### 7.1 진단 sprint 후속 (즉시)

| 후보 | 작업량 | 효과 | 메모 |
|---|---|---|---|
| **권고 5 fix** (영어 heading pattern 보강 + arXiv reingest) | 1~2h + reingest cost | arXiv section_title 정상화 → 검색 품질 ↑ | ROI 실증 완료, 적용 가치 확인 |
| 권고 4 warm 재진단 (cross_doc T1 신호) | ~30분 | `intent_router._DOC_NOUN` 확장 ROI 결정 | warm 상태에서 timeout 회피 |
| over-chunking 품질 영향 측정 (golden_v2 132 row + 새 3 doc R@10) | ~30분 | chunks 37k 폭증의 검색 품질 영향 정량화 | 권고 외 새 sprint |

### 7.2 PRD scope 잔여 (사용자 수동)

| 후보 | 작업량 | cost | 메모 |
|---|---|---|---|
| #8 출처 일치율 50건 | 1~2h | $0 | 사용자 직접 검수 |
| #9 환각률 보강 20건 | 1h | $0 | 동일 |
| #6② Ragas Context Recall | $0.05~0.15 | paid 옵션 | 발표 카드용 |

### 7.3 audit 권고 잔여 (medium, 새 도메인 doc 들어오면 단계적)

| # | 작업 | 작업량 | ROI | 트리거 조건 |
|---:|---|---|---|---|
| 2 | `vision_need_score` 5 임계 ENV 화 | 4~6h | 중간 | 새 corpus 의 vision 미스/오버콜 측정 후 |
| 3 | `synonym_dict` + `query_expansion` 사전 JSON 외부화 | 3~4h | **낮음 (진단 결과 ROI 낮음 확인)** | 새 도메인 어휘 풍부 시 |
| 4 | `intent_router._DOC_NOUN` 14 명사 확장 + ENV | 1~2h | 중간 | warm 재진단 후 결정 |
| 5 | `pymupdf` heading pattern 보강 (영어 학술 · SW spec) | 1~2h | **높음 (실증 확인)** | arXiv reingest 비용 감수 시 즉시 |

### 7.4 v1.5 영역 (별도 sprint)

| 항목 | 영향 KPI |
|---|---|
| **HF self-host (BGE-M3 cold-start 0)** | #10 P95 29.7s → < 3s, #11 인제스트 SLO |
| golden_v3 확장 (132 → 250+, 새 3 doc 보강) | #6① R@10 + 분포 시정 |
| ColBERT / sparse self-host | #6① R@10 |
| over-chunking 검색 품질 영향 fix (필요시) | #6① R@10, IDF 가중치 |
| US-05 인수인계 추림 / US-10 주제별 타임라인 | 기획서 §3 |

### 7.5 별도 트랙 (DECISION-12)
- 인제스트 KPI #1·#2·#3 — 벤치셋 30개 별도 sprint
- 새 3 doc (arXiv·삼성·SK) 인제스트 성공으로 SLO 측정 케이스 확보

### 7.6 운영 위생 (~10분)
- `.env` 의 `JETRAG_DOC_BUDGET_USD` / `JETRAG_DAILY_BUDGET_USD` default 복귀 (P1 fix 후 안전)
- `ingest_jobs` failed 3건 → hard-delete 로 일부 해소됨, 잔여 확인

---

## 8. 인용 / 참조

### 8.1 본 세션 work-log (5건)

- `2026-05-14 P4 — hard-delete 4 doc + golden_v2 47 row 정리.md` (`c231f76`)
- `2026-05-14 audit 권고 1 — query_classifier 역의존 해소.md` (`ab7b6de`)
- `2026-05-14 인제스트 robustness fix — NULL byte + batch split.md` (`1ce5465`)
- (본 문서) — 종합 핸드오프
- (revert 된 work-log) `2026-05-14 KPI #8 1차 자동 채점 + P4-b 잔여 정리.md` — `22f7412` 에서 삭제됨

### 8.2 PRD / 마스터 / Living spec

- **PRD master**: `work-log/2026-05-12 검색 정확도 80% 달성 PRD.md` **v1.4** (본 세션 변경 없음)
- **기획서**: `work-log/2026-04-22 개인 지식 에이전트 기획서 v0.1.md`
- **Living spec**: `work-log/검색 파이프라인 동작 명세 (living).md` **v0.4**

### 8.3 코드 변경 (본 세션)

- backend production:
  - **신규**: `api/app/services/query_classifier.py`
  - `api/app/routers/admin.py` (lazy import 제거)
  - `api/app/adapters/impl/supabase_vectorstore.py` (`_strip_null_bytes` + batch split)
  - `api/app/config.py` (`chunk_upsert_batch_size` 신규)
  - `api/scripts/golden_batch_smoke.py` (env override)
- backend evals:
  - `evals/auto_goldenset.py` (re-export alias)
  - `evals/golden_v2.csv` (182 → 132 row)
- frontend:
  - `web/.env.local` (`NEXT_PUBLIC_API_BASE_URL=http://localhost:8001`)
- tests:
  - **신규**: `api/tests/test_query_classifier.py` (+11 + 2 추가 검증 = 13건)
  - **신규**: `api/tests/test_supabase_vectorstore.py` (23건)
  - `api/tests/test_auto_goldenset.py` (ClassifyQueryTypeTest 이전 + re-export sanity 2건)
  - `api/tests/test_run_s4_a_d4_compose_off.py` (n_rows·caption_counts 갱신)

### 8.4 인제스트 결과 (생성됨)

- documents 3건 신규 (assets/private/ 의 PDF 3건)
- chunks 35048건 신규 추가
- vision_chunks 0건 (vision_budget_exceeded)

---

## 9. 다음 세션 시작 시 빠른 확인 (5분 진입)

```bash
cd /Users/kiwoongmin/Desktop/piLab/Jet-Rag
git log --oneline -5
# 1ce5465 docs(work-log): 인제스트 robustness fix — NULL byte + batch split (1194 OK)
# b70b672 fix(ingest): generic robustness — NULL byte sanitize + chunks upsert batch split
# ab7b6de docs(work-log): audit 권고 1 — query_classifier 역의존 해소 (1171 OK)
# 3605dbb refactor(query_classifier): production → evals 역의존 해소 (audit 권고 1)
# 22f7412 revert(evals): KPI #8 자동 채점 작업 되돌림 (사용자 지시)

# 서버 가동 (포트 8001 / 3001)
cd api && uv run uvicorn app.main:app --reload --port 8001 &
cd web && pnpm dev &  # 또는 npm/yarn

# 현재 corpus
curl -s http://localhost:8001/stats | python3 -c "import json,sys;d=json.load(sys.stdin);print(f\"docs={d['documents']['total']} chunks={d['chunks']['total']}\")"
# docs=12 chunks=37057

# 단위 테스트
cd api && uv run python -m unittest discover 2>&1 | tail -2  # 1194 OK
```

### 다음 후보 우선순위 권고 (오늘 sprint 직접 후속)
1. **권고 5 fix** (`pymupdf_parser._HEADING_TEXT_PATTERN` 보강) — arXiv heading 인식 회복, 1~2h. ROI 실증 완료.
2. **권고 4 warm 재진단** — cross_doc T1 신호 측정, ~30분.
3. **over-chunking 검색 품질 영향 측정** — golden_v2 132 + 새 3 doc 통합 R@10 재측정, ~30분.
4. **HF self-host 검토 (v1.5 진입)** — KPI #10 P95 29.7s 가 본 sprint 의 가장 큰 미해결 이슈.

위 4건 외에는 사용자 수동 작업 (#8/#9), 별도 트랙 (DECISION-12), 운영 위생 (`.env` BUDGET) 중 선택.
