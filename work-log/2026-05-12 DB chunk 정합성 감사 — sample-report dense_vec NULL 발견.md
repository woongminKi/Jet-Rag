# 2026-05-12 DB chunk 정합성 감사 — sample-report dense_vec NULL 발견

> 프로젝트: Jet-Rag
> 작성: 2026-05-12 (handoff `be26459` 직후 세션). 트리거 = 사용자 질문 "그동안 인제스트/청킹 로직이 여러 번 바뀌었는데, DB 에 올라간 chunk 중 로직 변경 전 임베딩된 stale 데이터로 eval 돌려도 괜찮나?"
> 성격: **감사(QA) 리포트 + 복구 실행 기록 + 복구 후 eval 재측정.** 운영 코드 변경 0. 복구 도구 신규 1 파일(`evals/_repair_sample_report_dense_vec.py`). **2026-05-12 — ① sample-report dense_vec 1000건 복구 `--apply` 완료 (NULL 1000→0, MCP 재확인 — 전체 DB dense_vec NULL 0건), stale job `failed` 마킹. ② golden_v2 eval 전면 재측정 완료 (§5.4) — sample-report 24 row 회복(R@10 0.5968→0.6946, top-1 0.5833→0.8333, 0점 row 0건) + embed-cache 검증 ✅(churn 0, embed_query_cache 183 row 채움) — 그러나 overall R@10 0.6844 < 0.75 = DoD 미달 = 검색의 실제 한계 확정. 다음 = cross_doc 약점 진단.**

---

## 0. 한 줄 결론

> **chunk 분할(청킹) 로직 자체는 `51e4f62`(2026-05-02) 이후 변경 0 — DB 의 가장 오래된 문서(05-04)부터 전부 현재 청킹 로직으로 들어갔다. 그 이후 `chunk.py` 변경 3건(`aa92d25`/`d1ce3e5`/`1d1bdfc`)은 vision-PDF caption 합성 + metadata 추가뿐이라 일반 chunk·비-PDF 텍스트는 byte-identical, vision-PDF 영향분은 05-11 재인제스트로 반영됨. NFC 정규화(`e417b45`)도 실측상 기존 데이터엔 no-op. → "stale chunk" 는 사실상 없음. 단 별개로, 05-11 D5 회복 reingest 중 `sample-report`(doc_id `d1259dfe…`, 1000 chunk = 전체 DB 의 40.5%)가 embed 스테이지에서 멈춰 `dense_vec`·`sparse_json` 1000건 전부 NULL 상태로 방치 = 완전 dark. `ingest_jobs` 에 stale `running` job(`cd912e70…`, 05-11 05:53~ finished_at NULL). 이게 `2026-05-11 D5 chunks 회귀 복구 ship.md` 의 "sample-report embedding 완료" 기록과 모순. golden_v2 183 row 중 24 row(13.1%)·caption_dependent=true 31 row 중 17 row 가 이 doc 정답 → eval R@10/top-1 등 하향 왜곡 중. Phase 11 acceptable judge 의 "[empty]" 7 row 도 이 doc(데이터센터 아님 — worklog 오기). → 결정: 전체 wipe·재업로드 X(멀쩡한 1469 벡터 손실 + 재업로드 시 doc_id churn → golden_v2 전부 stale), sample-report 1000 chunk 한정 재임베딩(+필요시 in-place 재인제스트, doc_id 유지)으로 처리. 사용자 = sample-report 한정 파괴적 작업 + vision paid(~$0.07~$0.63) 사전 승인.**

---

## 1. 트리거 / 감사 범위

- 사용자 우려: 작업 중 로직이 여러 번 바뀌었으니 DB 에 stale 임베딩이 섞여 있을 것 → eval(golden_v2 R@10·top-1·nDCG·MRR) 신뢰성 의심.
- 감사 진행: senior-qa (코드·git 이력 분석 + golden_v2.csv 대조) + 메인 (Supabase MCP 로 `chunks`/`documents`/`ingest_jobs` 실조회).
- **직접 수정 없음** — 본 문서는 감사 결과 + 복구 착수 기록.

---

## 2. 발견 — chunk(청킹) 로직 변경 이력 vs DB 인제스트 시점

`api/app/ingest/stages/chunk.py` 전체 이력 (oldest→newest): `920a669`(§10.5 3단계) → `d844e77`(W4-Q-14 정책) → `f6a831b`(4.3 따옴표/괄호 보호, 05-02) → `51e4f62`(4.6 표 청크 격리 — `_merge_short_sections` 병합 차단, 05-02) → `e417b45`(D1~D5 sprint, 05-05) → `aa92d25`(D2 caption 합성, 05-08) → `d1ce3e5`(D2 split/merge metadata 전파, 05-09) → `1d1bdfc`(S4-B entity, 05-10).

| 커밋 | 날짜 | chunk.py 변경 내용 | 이미 인제스트된 chunk 영향? |
|---|---|---|---|
| `f6a831b` 4.3 / `51e4f62` 4.6 | 05-02 | **실제 segmentation 변경** (분할 경계 / 표 청크 병합 차단) | ❌ 영향 없음 — DB 최古 문서가 05-04 → **전부 이 로직 포함** |
| `e417b45` D1~D5 sprint | 05-05 20:32 | `_to_chunk_records` 에 **NFC 정규화** (`text=unicodedata.normalize("NFC", section.text)`) — 일반 chunk text 를 건드리는 유일한 변경 | ⚠️ 이론상 05-05 오전 인제스트된 HWP/HWPX 영향. **실측: 직제 171·한마음 56·law2 30 = 257 chunk 전부 이미 NFC (`text <> normalize(text,NFC)` 0건) → no-op 확정** |
| `aa92d25` D2 caption 합성 | 05-08 | `_is_vision_derived` chunk(`(vision) p.N` 타이틀 또는 `metadata.vision_incremental`) 한정 caption 텍스트 합성 + metadata 전파. 일반 chunk 분기 미진입 | ❌ 일반 chunk·비-PDF 텍스트 byte-identical. pptx/hwpx/hwp/docx 는 caption 합성 경로 자체 없음 |
| `d1ce3e5` D2 split/merge 보강 | 05-09 | `_split_long_sections`/`_merge_short_sections` 에 `metadata` 전파만 추가 (**경계·텍스트 불변**, caption metadata 보존용) | ❌ 비-vision doc 사실상 no-op |
| `1d1bdfc` S4-B entity | 05-10 | `chunks.metadata.entities` 추가 | ❌ `entity_boost` default OFF + 룰 기반 매칭 모수 ≈ 0 (S4-B ablation `b3f7a64` 확정) → 검색 영향 0 |

→ **청킹 로직은 안정. DB 의 514 old-era chunk(05-04/05 인제스트 6 doc: 브랜딩 pptx·law2 hwp·직제 hwpx·한마음 hwpx·승인1/3 docx) + 929 good chunk(05-09/11 인제스트 PDF 6 doc)를 다시 만들어도 결과가 사실상 동일.** caption 합성 영향을 받는 vision-PDF chunk 들은 이미 05-11 재인제스트됨.

### 부수 발견 — DB document별 인제스트 era (13 doc / 2469 chunk)

| doc_id(prefix) | title | type | chunk_created | n_chunks | dense_vec NULL | metadata.entities | 비고 |
|---|---|---|---|---:|---:|---:|---|
| 430e9fbe | 브랜딩_스튜디오앤드오어 | pptx | 05-04 | 5 | 0 | 0 | old-era, caption 비대상 |
| 80b48fa9 | law sample2 | hwp | 05-05 | 30 | 0 | 19 | old-era, NFC 실측 OK |
| a5f3cfae | 직제_규정 | hwpx | 05-05 | 171 | 0 | 112 | old-era, NFC 실측 OK. entities 부분 보유(backfill) |
| 2e083cb0 | 한마음생활체육관_운영_내규 | hwpx | 05-05 | 56 | 0 | 26 | old-era, NFC 실측 OK |
| 88920c9e | 승인글 템플릿1 | docx | 05-05 | 139 | 0 | 16 | old-era |
| 75c74e4c | 승인글 템플릿3 | docx | 05-05 | 113 | 0 | 0 | old-era |
| 0ed5a024 | law sample3 | pdf | 05-09 | 26 | 0 | 12 | mid-era (aa92d25/d1ce3e5 후) |
| 9878d7bd | 포트폴리오_이한주 | pdf | 05-11 03:06 | 79 | 0 | 28 | new-era. ⚠️ 별개 이슈: `chunk.page` 일부 DB NULL (handoff #G-A-107/111) |
| 51dd2cc0 | 기웅민 이력서 | pdf | 05-11 03:06 | 103 | 0 | 25 | new-era |
| 2303ef36 | 보건의료_빅데이터 | pdf | 05-11 03:08 | 175 | 0 | 9 | new-era |
| 92c38315 | sonata-the-edge_catalog | pdf | 05-11 05:40 | 129 | 0 | 4 | new-era (D5 회복 reingest) |
| b218e8a1 | (붙임2) 데이터센터 산업 활성화 지원 사업 안내서 | pdf | 05-11 05:45 | 443 | 0 | 27 | new-era (D5 회복 reingest) |
| **d1259dfe** | **sample-report** | pdf | **05-11 05:56** | **1000** | **1000 (전부)** | 163 | ⚠️⚠️ **dense_vec·sparse_json 전부 NULL — §3** |

원본 파일 13개 전부 `documents.storage_path` non-null (Storage 에 살아 있음 → 재인제스트 시 재업로드 불필요, doc_id 유지 가능).

---

## 3. 핵심 발견 — `sample-report` 완전 dark (handoff 기록과 모순)

- `chunks` WHERE `doc_id=d1259dfe-c402-4cd0-bb04-3e67d88a2773`: 1000건 전부 `dense_vec IS NULL` + `sparse_json` NULL/'{}'. `text` 는 채워져 있음(평균 165자, 1~1038). `flags.extract_skipped`/`low_quality` 0건. → **extract→chunk→(load 일부)까지 완료, embed(+sparse) 미완료.** dense·sparse 양쪽 검색 불가 = 완전 dark.
- `ingest_jobs` WHERE `doc_id=d1259dfe…` 최신 row: `id=cd912e70-9773-4bc3-b1ac-80ed229b3d9d`, `status=running`, `current_stage=embed`, `attempts=1`, `error_msg=NULL`, `queued_at=2026-05-11 05:53:59`, `started_at=2026-05-11 05:53:58`, `finished_at=NULL`. → **embed 단계에서 멈춘 채 영영 running.** 예외(`error_msg`) 없음 → 프로세스가 그냥 사라짐(외부 kill / 비정상 종료) → `_cleanup_failed_doc` 미발동 → chunk 1000건만 잔존. 그 직전 job `015536c8…` 는 05-11 03:14 completed (= 회복 reingest 가 두 번 큐잉됐고 두 번째가 정지).
- → `work-log/2026-05-11 D5 chunks 회귀 복구 ship.md` 의 "sample-report 1000 chunk + 모든 page embedding 완료" 기록은 **사실이 아님.** chunk 수(1000) 복구만 확인하고 dense_vec 미검증 → 성공으로 기록한 것으로 보임.
- 추정 직접 원인: HF BGE-M3 Inference **free-tier scale-to-zero cold-start** (503 "model is loading", P95 ~67s, 3-retry 백오프 ~95s worst). 1000 chunk = batch(16) ~63회 → cold-start 반복 시 embed 가 stall.

### eval 신뢰성 영향 (senior-qa golden_v2.csv 대조)

| 측정·판정 | 신뢰 가능? | 근거 |
|---|---|---|
| 단위 테스트 997 | ✅ OK | DB 무관 (fixture/mock, `tests/__init__.py` 가 embed-cache OFF 강제) |
| eval v2↔v2 회귀 판정 (reranker on/off, entity_boost ablation 등 Δ) | ⚠️ 조건부 OK | 같은 row 양쪽 동일 miss → Δ 상쇄. **S3 D6 "reranker net-negative" 결론 유지 가능.** 절대값은 무의미 |
| 절대 R@10/top-1/nDCG/MRR (overall) | (복구 전 한정) ❌ 신뢰 불가 | sample-report 24 row(183 의 13.1%)가 0 으로 강제 유입(또는 `doc 매칭 fail` 시 분모 누락 → 반대로 상향 왜곡). ~~추정: sample-report 정상이면 overall R@10 ~0.07~0.09pp 상승 — DoD 충족 가능~~ **← 실측 반증 (§5.4): 복구 후 overall R@10 0.6819→0.6844 = +0.0025pp 에 그침. sample-report 가 복구 전에도 PGroonga FTS 로 부분 recall 이 있어 0 강제가 아니었고, non-target row 의 HF embed churn 이 상쇄. DoD 미달은 검색의 실제 한계로 확정.** |
| caption_dependent=true subset R@10 ("claim B") | ❌ 신뢰 불가 | true 31 row 중 17 row(55%)가 sample-report |
| acceptable judge 1차 (Phase 11, 7 row "[empty]") | ⚠️ 일부 무효 | G-A-200/203/205/207/209/210 [empty] 의 정답 doc = sample-report(데이터센터 443 chunk 아님 — worklog "huge doc(~1000 chunk)" 오기). cosine 후보가 dense_vec NULL 이라 안 생긴 게 진짜 원인. **acceptable judge 2차 라운드는 복구 후로 미뤄야 함** |
| 인제스트 개선 효과 측정 (S4-A caption 합성 등) | ❌ 신뢰 불가 | 효과 최대 doc 이 dark |
| old-era doc(05-04/05, 514 chunk) | ✅ 사실상 OK | 본문 byte-identical (caption 합성 비대상 포맷). entities 키 유무만 차이 — 검색 영향 0 |

---

## 4. 결정 — 전체 wipe·재업로드 X, sample-report 한정 복구 O

후보 검토:
- **전체 wipe + 재업로드** → ❌ ① 멀쩡한 1469 벡터 손실 → HF free-tier 로 2469개 전부 재임베딩 = sample-report 에서 이미 멈춘 그 경로, 백업 없음 ② **재업로드 = 새 doc_id → golden_v2 183 row doc_id 컬럼 전부 stale → 골든셋 매핑 재작성** (handoff 의 "build_golden stale doc_id 재발 방지 hook" 이 이 사고용) ③ vision/tag_summarize paid 전면 재발생 ④ D5 재인제스트 회귀(chunks -185, budget_guard 차단으로 vision chunk 손실) 전례 — blast radius 확대.
- **sample-report 한정**:
  - A. embed-only 재실행 — 기존 1000 chunk 보존, `dense_vec IS NULL` embed + sparse 재적재. 최소 위험, doc_id 불변. 단 그 1000 chunk 가 budget 차단으로 text-only 면 vision 정보 미반영.
  - B. in-place 재인제스트 — `chunks` 1000건 delete, **`documents` row 유지(→ doc_id 불변 → golden_v2 무영향)**, 기존 Storage 객체에 인제스트 재트리거 + budget 우회 ENV + vision v2. vision 정보·현 청킹·embed 보장. vision paid 재발생(~$0.07~$0.63).
  - C. delete + 재업로드 — 새 doc_id → golden 깨짐 → `documents` row 손상 시만.
  - 공통: stale `running` job `cd912e70…` 정리.
- **선택 = A 또는 B (senior-developer 가 "현재 1000 chunk 에 vision-derived chunk 가 있나" 조사 후 결정).** delete + 재업로드(C)는 안 함.

**사용자 승인** (2026-05-12): "C로 진행 (도구 설계 + work-log). 네가 보고 필요하다면 sample-report 관련 데이터 모두 지우고 다시 업로드해도 괜찮아." → sample-report 한정 파괴적 작업 + vision paid 사전 승인. 단 실제 `--apply` 실행은 메인 리뷰 후.

---

## 5. 복구 — 도구 + 실행 결과 (✅ 완료)

### 5.1 senior-developer 조사 — 결손 성격 정정
- **결손은 "완전 dark" 가 아니라 dense 경로만.** `chunks.sparse_json='{}'` 은 **설계상 정상** — 인제스트 파이프라인(`load.py`→`embed.py`)은 `sparse_json` 을 안 채우고, sparse/키워드 검색은 PGroonga FTS 가 `chunks.text` 직접 인덱싱(마이그 004). `bgem3_hf_embedding.embed_batch()` 도 `EmbeddingResult(dense=..., sparse={})` 반환(HF API 가 BGE-M3 lexical weights 미노출). → sample-report 는 **이미 키워드/PGroonga 경로로 검색 가능**, 망가진 건 `dense_vec IS NULL` → `<=>` cosine 후보 미생성뿐. `search_hybrid_rrf` RPC 의 dense CTE 는 `WHERE c.dense_vec IS NOT NULL` 로 NULL chunk 를 자연 배제 → 검색 자체는 에러 없이 돌되 sample-report 의 dense 기여 0.
- **1000 chunk 안에 vision-derived chunk(`section_title LIKE '(vision) p.%'`) 102건 포함** (dry-run 확인). → 2026-05-11 D5 회복이 vision 정보 포함 1000 chunk(898→1000, vision 102p)를 정상 적재했고 직후 embed 가 cold-start 로 멈춘 것. **콘텐츠 정상 → 재인제스트 이점 없음 → embed-only 가 정답.**
- budget 우회 ENV(재인제스트 안 가지만 기록): `JETRAG_BUDGET_GUARD_DISABLE=1`(코드 상수 `_DISABLE_ENV_KEY` — handoff 의 `..._DISABLED` 는 오타) / `JETRAG_DOC_BUDGET_USD=0.15` / `JETRAG_VISION_PROMPT_VERSION=v2` / `JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS=2`(default).

### 5.2 도구 — `evals/_repair_sample_report_dense_vec.py` (신규, 일회용)
- 운영 코드 변경 0 — `embed.py`/`pipeline.py`/`bgem3_hf_embedding.py` 미변경. embed.py 로직을 cold-start 내성 있게 재구성한 standalone.
- 기본 dry-run, `--apply` 로만 DB 쓰기. `--fix-stale-job` 으로 orphaned `running` job 정리. `--batch-size`(default 16) 조정 가능.
- `--apply` 흐름: warmup ping(`embed_query("warmup")`) → `dense_vec IS NULL` chunk 를 `chunk_idx` 순 조회 → batch 16개씩 `embed_batch` → 각 row `update({"dense_vec":...}).eq("id",...)` (embed.py 와 동일 단건 update, supabase upsert NULL 컬럼 회피) → batch 실패 시 3-retry(5/10/20s) 후 그 batch skip → batch 사이 0.5s sleep → 끝에 stale job `status=failed` 마킹 → 검증 쿼리 출력. **chunks row 절대 삭제·text 수정 안 함**, `dense_vec` 컬럼만 채움. 멱등·resumable(부분 실패 후 재실행 시 이미 채운 chunk skip).
- 사용법: `cd api && uv run python ../evals/_repair_sample_report_dense_vec.py [--apply --fix-stale-job] [--batch-size 8]`
- 단위 테스트: 운영 코드 미변경 → 997 pass / 50 subtests / 회귀 0. (참고: 이 venv 에 `pytest` 미설치라 senior-developer 는 `uvx pytest -q` 로 확인 — 의존성 추가 아님, 실행 방식만 다름. 기존 handoff 의 `uv run pytest -q` 는 환경에 따라 실패할 수 있음 — 별도 인지사항.)

### 5.3 실행 결과 (2026-05-12, `--apply --fix-stale-job`)
- warmup OK (25.0s) → 63 batch / **batch skip 0건** / 1000건 채움 / 총 ~496s (~8.3분, HF free-tier batch 당 5~40s 가변).
- stale job `cd912e70-9773-4bc3-b1ac-80ed229b3d9d` → `status=failed` 마킹.
- 도구 검증: `dense_vec NULL: 1000 → 0` ✅
- **MCP 재확인**: `chunks` WHERE doc_id=sample-report → 1000건 / dense_vec NULL 0건. 전체 DB `chunks` dense_vec NULL **0건** (2469 chunk 전부 벡터 보유). `ingest_jobs.cd912e70….status='failed'`.
- 추가 paid 비용 **$0** (HF 무료 티어, vision/Gemini 호출 0). doc_id 불변 → golden_v2 무영향.

### 5.4 복구 후 eval 전면 재측정 (2026-05-12, senior-qa)

- **스크립트**: `evals/run_s4_a_d4_breakdown_eval.py` (RRF-only — 내부에서 `JETRAG_RERANKER_ENABLED=false`+`JETRAG_MMR_DISABLE=1` 강제, paid LLM 0). `app.routers.search.search()` in-process 직접 호출(서버 미기동). `JETRAG_EMBED_QUERY_CACHE=1`. 2회 실행. 산출: `evals/results/s4_a_d4_post_sample_report_repair_run{1,2}.{md,json}`.
- **Overall (golden_v2 183 row, n_eval 174 / doc-match-fail 3 / 정답없음 6):** R@10 **0.6844** / nDCG@10 0.6350 / MRR 0.6041 / top-1 **0.7989**. (복구 전 2026-05-11 post-recovery: R@10 0.6819 / top-1 0.7471 — row 셋 동일이나 non-target row 가 HF embed churn 으로 ±변동해 "복구 효과"만 깔끔히 분리 안 됨.)
- **핵심 검증 — sample-report 정답 24 row subset:** R@10 0.5968→**0.6946** / top-1 0.5833→**0.8333** (14→20 row) / **R@10==0 인 row: 2건(G-U-019, G-A-214) → 0건.** predicted top-10 에 chunk_idx 9xx 대역(sample-report 영역) 정상 노출 — dense cosine 후보 복귀 확인. → **복구 효과 확정.** (단 G-A-021/023/204/214 는 여전히 R@10 ≤ 0.33 — 표/도식 split 별개 약점, dense 와 무관.)
- **subset:** caption_dependent=true 31 row(17 이 sample-report) R@10 0.6695→0.7197 / cross_doc qtype 10 row R@10 **0.3333**(복구 전후 불변 — dense 와 무관, 최대 약점) / qtype top-1: numeric_lookup 1.00, exact_fact 0.86, table_lookup 0.83, fuzzy_memory 0.86 ↔ synonym_mismatch 0.375, cross_doc 0.30.
- **embed-cache 검증 (handoff 0순위):** run1↔run2 predicted_top10 churn **0 row** (byte-identical 183/183) / 집계 metric 소수점까지 완전 동일 / run2 실행 41.6s = persistent `embed_query_cache` hit (HF 호출 0). **MCP 확인: `embed_query_cache` 183 row (model_id `BAAI/bge-m3`, dim 1024, 채워진 시각 2026-05-12 03:26~03:43 = run1).** → S4-B ablation 때 ~92 row churn 완전 해소, eval 재현성·cold-start 면역 확정.
- **DoD 재판정:** R@10 0.6844 < 0.75 → **미달 (격차 −0.066)**, 검색의 실제 한계로 확정 (handoff/본 문서 §3 의 "복구하면 충족 가능" 추정 반증). top-1 0.7989 ≈ 0.80 경계.
- **새 발견:** ① doc-match-fail 3 row(G-U-018/G-U-027 = `|` multi-doc 라벨인데 검색이 정답 doc 를 top-50 에 못 띄움 / G-N-004 = 의도적 무정답) 는 "fail"이 아니라 "검색이 못 찾음" → 분모에서 빠져 overall 을 약 +0.01pp 상향 왜곡 (174→177 분모면 ~0.673). ② out_of_scope 5 row 는 retrieval metric 산출 불가 — negative-recall 측정 메커니즘 부재(RAGAS/judge 단계 필요). ③ 복구 전↔후 non-target ~20 row 가 R@10 ±0.1~0.5 흔들림 = HF embed 비결정성 — embed_query_cache freeze 로 향후 사라짐(run1↔run2 churn 0 이 증거).

---

## 6. 남은 이슈 / 후속 정정 필요

- ~~**(P0) sample-report 복구 + eval 재측정**~~ ✅ 2026-05-12 완료 (§5.3 / §5.4 — dense_vec NULL 1000→0, sample-report subset R@10 0.5968→0.6946, embed-cache 183 row, DoD 재판정 = 여전히 미달·검색 한계 확정).
- **(P0→진행) handoff/work-log 수치 재해석 정정** — `2026-05-12 종합 마감…` handoff 및 관련 work-log 의 "R@10 0.66~0.70 / DoD 미달" 서술 + **본 문서 §3·§6 의 "복구하면 DoD 충족 가능" 추정**에 "실측 반증: 복구 후 overall R@10 0.6844 — 여전히 DoD 미달, 검색 한계로 확정" 정정. (본 문서 §0·§3·§5.4 는 이미 반영. handoff·D5 worklog 는 별도 정정 필요.)
- **(P0) `2026-05-11 D5 chunks 회귀 복구 ship.md` 정정** — "sample-report embedding 완료" 는 사실 아님(embed 스테이지 정지 → 2026-05-12 별도 복구) 명기.
- **(P1) Phase 11 / acceptable judge worklog 오기 정정** — "데이터센터 huge doc(~1000 chunk)" → "sample-report(d1259dfe, 1000 chunk)". [empty] 7 row 원인 = "huge doc 후보 풀 부족" 이 아니라 "sample-report dense_vec NULL → cosine 후보 미생성". **복구 완료 → acceptable judge 2차 라운드 의미 있어짐** (단 paid ~$0.05, cross_doc 진단보다 후순위).
- **(P1) eval 도구 가드 + doc-match-fail 처리** — `eval_retrieval_metrics`/`run_s4_a_d4_breakdown_eval` 에 "정답 doc 의 chunk 중 dense_vec NULL 비율 ≥ 임계 → WARN + 별도 버킷" 가드. 그리고 doc-match-fail 3 row(G-U-018/G-U-027 `|` 멀티-doc 라벨)는 "fail"(분모 제외) 대신 "R@10=0"(분모 포함)으로 — overall 이 ~0.673 으로 약간 내려가지만 정직. 또는 골든셋 라벨을 단일 doc 으로 분할.
- **(P2) 위생** — old-era doc 의 entities 부분 누락(직제 등)은 backfill 도구로 채움(embed 재호출 0, 검색 영향 0 — 급하지 않음). caption_dependent=true 라벨 중 G-A-011(브랜딩 pptx)·G-U-016(직제 hwpx)은 해당 포맷에 caption 합성 경로 없음 → 라벨 자체 재검토.
- **(인지) ingest_jobs orphaned `running` 정리 정책** — 프로세스 비정상 종료 시 job 이 영영 running. 시작 시 stale `running`(예: started_at 24h+ 경과) 정리하는 startup hook 또는 watchdog 검토.

## 7. 다음 스코프

- ~~eval 전면 재측정~~ ✅ §5.4. ~~embed-cache 검증~~ ✅ §5.4. ~~cross_doc 약점 진단~~ ✅ §9. ~~cross_doc 골든셋 라벨 정정(P0)~~ ✅ §10. ~~cross_doc chunk cap + eval 라운드로빈 + intent_router 커버리지(P1)~~ ✅ §11 (commit `8f16f32`, cross_doc R@10 0.2856→0.4424 +55%).
- ~~P2 $0 기계 분해~~ ❌ §12 (net-negative — cross_doc R@10 0.4424→0.3610, 코드 미머지).
1. **(보류 — paid 승인 필요) P2-(b) paid LLM query decomposition** (§9 P2 / §12 — $0 기계 분해 실패의 진짜 대안) — `query_decomposer`(현 `/answer` 전용)를 `/search` 옵션 노출 + LLM 분해. ~$0.05/eval. intent_router 발화 9/9 로 게이트 준비됨. **또는** doc-scoped RPC 마이그(doc 내 RRF 계산하는 새 함수 — $0 이나 마이그·중규모). 둘 다 §11.2 baseline(cross_doc R@10 0.4424) 대비.
3. **doc-match-fail 3 row 처리** (§6 P1) — G-U-018/G-U-027(qtype=cross_doc 아님 — fuzzy_memory/exact_fact) 의 `|` 멀티-doc 라벨 정정 또는 eval 도구 R@10=0 분모 포함.
4. **acceptable judge 2차 라운드** (~$0.05, paid 승인) — 복구로 sample-report 후보 정상화 → 의미 있어짐. cross_doc 작업보다 후순위(분자 보정일 뿐 근본 약점 미해결).
5. **chunk augment** (handoff 3순위, 5~7일) / **handoff·D5·Phase11 worklog 수치·오기 정정** (§6 P0).
6. **(인지) RPC 후보 풀 `_RPC_TOP_K=50` sweep** — cross_doc 정답 chunk 절반 이상이 50 풀 밖(옛 진단). 100/150 sweep + p95 ≤ 3s SLO 점검 (효과 제한적 예상, P1 후순위). + `run_s4_a_d4_breakdown_eval.py` `_format_markdown` "157 row" 하드코딩 정리(§10.4).

---

## 8. 검증 메모 (이 세션)

- DB 조회/변경: `mcp__supabase-jetrag__execute_sql` (읽기 전용 — 스키마/분포/sample-report 진단/NFC 실측/복구 후 재확인/`embed_query_cache` row 수/cross_doc chunk 텍스트 검증) + `evals/_repair_sample_report_dense_vec.py --apply` (`chunks.dense_vec` 1000 row UPDATE + `ingest_jobs` 1 row status='failed') + eval 실행이 `embed_query_cache` 183 row insert. `chunks`/`documents` row 추가/삭제/text 변경 0.
- 운영 코드 변경: `api/app/services/retrieval_metrics.py` set 원소 타입 generic 화(로직 불변). 신규: `evals/_repair_sample_report_dense_vec.py`(복구 도구), `evals/cross_doc_alias_map.json`(alias 매핑), `evals/results/s4_a_d4_post_sample_report_repair_run{1,2}.*`·`s4_a_d4_cross_doc_relabel_run{1,2}.*`(eval 산출, .gitignore). 정정: `evals/golden_v2.csv`(cross_doc 10 row)·`evals/run_s4_a_d4_breakdown_eval.py`·`evals/build_golden_v2.py`.
- 단위 테스트: 997 → **1022** pass / 50 subtests / 회귀 0 (`.venv/bin/python -m unittest` — `uvx pytest` 는 격리환경이라 PIL 등 미설치 collection error / full-discover 시 test_embed_cache LRU 4건 flaky 는 pre-existing 테스트-격리 이슈, 격리 실행 시 pass).
- 커밋: `c2e0f06`(sample-report 복구 + 감사) / `da9f958`(eval 재측정 §5.4) / `82424d2`(cross_doc 진단 §9) / `09df535`+`037a596`(cross_doc 라벨 정정 P0 §10) / `8f16f32`+`95bba0c`(cross_doc cap+라운드로빈+intent_router P1 §11). P2(§12) 는 net-negative 라 미머지(코드 폐기) — 테스트 1022 유지. main HEAD = 본 work-log 커밋.
- 관련 문서: senior-qa 감사·eval 재측정·cross_doc 진단 리포트 + senior-planner 스키마 설계 리포트(세션 내), `work-log/2026-05-11 D5 chunks 회귀 복구 ship.md`, `work-log/2026-05-11 S4-A D4 Phase 4 — D5 reingest.md`, `work-log/2026-05-11 acceptable_chunks LLM-judge 자동 보완 ship.md`, `work-log/2026-05-12 종합 마감 + 2026-05-13 진입 핸드오프.md`.

---

## 9. cross_doc 약점 진단 (2026-05-12, senior-qa — $0 분석 + RRF-only ablation)

> **⚠️ 본 §9 의 수치(R@10 0.3333 / chunk-cap ablation 0.51 / per-doc union 시뮬 0.43)는 모두 "옛 라벨(doc-바인딩 없는 bare chunk_idx, false-positive 섞임)" 기준 — §10 P0 정정으로 무효화. 새 baseline = §10 (cross_doc 9 row R@10 0.2856 / top-1 0.3333 / doc-match-fail 0). 이후 cross_doc 측정·비교는 §10 baseline 기준. §9 는 진단 *근거*(병목 A/B/C 식별)로만 유효.**

DoD 미달의 최대 약점 = golden_v2 `qtype=cross_doc` 10 row R@10 **0.3333** / top-1 0.30 (dense 복구·entity_boost 둘 다 무관, 불변). 진단 결과 **3중 복합 원인**:

| # | 병목 | 판정 | ROI / 비용 |
|---|---|---|---|
| **A** | **골든셋 라벨 스키마 결함** — cross_doc `relevant_chunks` 가 doc-바인딩 없는 bare chunk_idx 평탄 연결(`A_idx,B_idx`), eval `recall_at_k` 는 bare set 비교 → ① false positive(다른 doc 의 같은 chunk_idx 도 카운트, 예 G-A-124 운영내규#22=정답 ↔ 직제규정#22=메타noise) ② `0,0`→`set={0}` dedup 부작용 ③ cross_doc 만 sub-doc당 BGE cosine top-1 1개만 "relevant"(인간 검수 0) → 라벨 noise(예 G-A-128 law2#13=무관 사실관계가 정답으로) | **주범 (측정 노이즈)** — cross_doc 10 중 ≥4 row(G-A-124/126/128, G-U-015)의 0.0 은 "검색 실패"보다 라벨 결함. 0.33 자체가 하향 추정치 | $0 (라벨 재검수 + 스키마) |
| **B** | **list 모드 per-doc chunk cap** `_MAX_MATCHED_CHUNKS_PER_DOC=3` (`search.py` L56) — non-doc-scope 응답에서 doc당 RRF top-3 만 `matched_chunks` 에 → 정답이 RPC top-50 풀엔 있어도 그 doc 의 top-3 밖이면 `predicted_top10` 에서 탈락. 응답 길이 실측 3·5·6 (10칸 절반도 안 참) | **부분 주범 (실제 검색 손실)** — cap 3→50 monkeypatch ablation: R@10 0.33→**0.51**, 단 일부 row 하락(G-U-032 0.33→0.22 등 — 첫 doc 독식) → doc별 라운드로빈 quota 필요 | $0 (상수/파라미터, UX 회귀 주의) |
| **C** | **단일 query 로 여러 doc 의 정답 chunk 동시 회수 불가** — RPC `_RPC_TOP_K=50` 글로벌 풀에 cross_doc 라벨 chunk 절반 이상 미포함(예 G-A-126 기웅민#7·#11 = 50 밖 / G-A-128 law2#13 = 50 밖). generic query 라 풀 확대만으론 한계 | **근본 한계** — per-doc 검색 union $0 시뮬: 0.33→**0.43** (단 G-A-124/125/126/128 4 row 여전히 0 — 라벨 결함). query decomposition(LLM) 또는 기계 분해 필요 | decomposition LLM = paid ~$0.05/eval / 기계 분해·per-doc union = $0 |

**무관 확정** (재확인): ① meta_filter / intent_router 가 단일 doc 으로 좁히는 경로 — cross_doc 10 query 전부 메타 fast path 미발동, doc-scope 강제 없음 ② doc_embedding RRF 가중(`JETRAG_DOC_EMBEDDING_RRF`) — 켜도 cross_doc R@10 불변(doc 페이지 순서만 바꿀 뿐 chunk 후보 풀 미변경) ③ S4-B entity_boost ④ sample-report dense 결손(target doc 에 없음).

**부수 발견**: `query_decomposer.decompose` 는 `/answer` 전용(`answer.py` L229~), `/search`(=golden eval) 미배선. 게이트 `JETRAG_PAID_DECOMPOSITION_ENABLED=true` AND `intent_router.route(q).needs_decomposition=True` — cross_doc 10 중 `needs_decomposition=True` 3건뿐(intent_router 가 "다른가요" 어미·"내규와 규정에서" 패턴 미발화).

**권고 실행 순서**: P0 라벨 정정($0, 전제) → 재측정으로 "라벨 noise분" vs "진짜 retrieval 손실분" 분리 → P1 chunk cap 라운드로빈 + intent_router 커버리지($0) → 재측정 → 부족하면 P2 decomposition($0 기계 분해 먼저, 효과 보고 paid LLM 판단) → P2 RPC 풀 sweep(latency 여유 시). **§7 의 1~3·7 항목이 이에 대응.** paid 필요한 건 P2-(b) decomposition LLM(~$0.05/eval) 뿐.

---

## 10. cross_doc 골든셋 라벨 doc-bound 정정 — 구현 + 새 baseline (2026-05-12, P0, commit `09df535`)

§9 진단의 주범 A(라벨 결함) 해소. 사용자 사인오프: 스키마 (a) 인라인 doc-prefix / 비대칭 라벨 유지 / G-A-075 qtype 정정.

### 10.1 변경 (commit `09df535`, 7 파일, 단위 테스트 997→1002 / 회귀 0)
- **신규 `evals/cross_doc_alias_map.json`** — alias → `{doc_id, title_prefix}` 11개 (운영내규/직제규정/데이터센터/보건의료/기웅민이력서/이한주포폴/승인1/승인3/law2/law3/쏘나타). build/eval 단일 출처.
- **`evals/golden_v2.csv`** — 정확히 10 row 편집(나머지 173 무변경, CRLF+BOM 보존). cross_doc 9 row 의 `relevant_chunks`/`acceptable_chunks` 를 `alias:idx` + DB chunk 텍스트로 검증한 재라벨로 교체(아래 표). G-U-015/031/032 는 두 번째 doc 에 진짜 답 없음 → 비대칭 라벨 + `source_hint` 주석. G-A-075(실은 단일 doc 내 금성vs지구 비교) `query_type` `cross_doc`→`exact_fact`(라벨 무변경) → cross_doc 모수 10→9.
- **`api/app/services/retrieval_metrics.py`** — `recall_at_k`/`mrr`/`ndcg_at_k`/`_relevance_score` 의 relevant/acceptable set 원소 타입 `int`→`int|tuple[str,int]`(`ChunkKey` alias) generic 화. 로직 불변, `int` 입력 하위호환.
- **`evals/run_s4_a_d4_breakdown_eval.py`** — `alias:idx` 파서(미등록 alias raise) + cross_doc cell 은 `alias_map.doc_id` 로 target item 직접 선별(`_pick_cross_doc_items` — 깨져 있던 title-12자-prefix 매칭 폐기, doc_id = ground truth, 미등록 doc_id item skip, 둘 다 응답에 없으면 `note="doc 매칭 fail"`) → 각 item `doc_id→alias` 로 matched_chunks 에 `(alias,idx)` 부여(MatchedChunk DTO 가 doc_id 미보유 → 부모 item 의 doc_id 주입) → RRF desc merge → metric. `predicted_top10` 도 cross_doc 은 `(alias,idx)` (raw JSON `["law2",10]`). single-doc cell 무변경.
- **`evals/build_golden_v2.py`** — `query_type=="cross_doc"` early return(인간 라벨 보존, embed/chunk fetch 미호출, `cross_doc_processed`+1). 자동 cosine 라벨 폐기.
- **tests** — `test_retrieval_metrics.py` 튜플 키 케이스 추가 / `test_build_golden_v2.py` cross_doc 테스트를 skip 동작 확인으로 갱신. (참고: `uvx pytest` 는 격리환경이라 PIL 등 미설치 collection error 22건 — `.venv` 직접 실행 시 1002 pass.)
- **무변경**: `evals/eval_retrieval_metrics.py`(golden_v1 만 사용 + cross_doc(doc_id 빈 row) skip → 영향 0, retrieval_metrics generic 화로 하위호환).

### 10.2 cross_doc 9 row 새 라벨 (DB chunk 텍스트로 검증)
| id | relevant (전→후) | acceptable (전→후) | 근거 |
|---|---|---|---|
| G-A-124 | `22,58`→`운영내규:22,직제규정:58` | ``→`운영내규:21` | 운영내규#22=제13조 운영시간변경 사전고지 / 직제규정#58=제3조 직제개편 이사회의결 |
| G-A-125 | `15,9`→`데이터센터:45,보건의료:9` | ``→`데이터센터:15,데이터센터:16,보건의료:151` | 데이터센터#45=총50.65억·과제당5~25.33억 / 보건의료#9=2018사업예산개요(플랫폼46억·네트워크24억·연구19억) |
| G-A-126 | `7,11`→`기웅민이력서:7,이한주포폴:11` | ``→`기웅민이력서:2,이한주포폴:14` | 기웅민#7=기술스택(JS·Node·React·TS…)/#2=소개 / 이한주#11=기획디자인·데이터분석 스킬·ESTJ /#14=ABOUT ME |
| G-A-127 | `0,0`(→`{0}` dedup)→`승인1:0,승인3:0` | (빈값) | 승인1#0="선택한 주제: 태양계 구조와 특징" / 승인3#0="선택한 주제: 삼국시대 정치 구조" |
| G-A-128 | `10,13`→`law2:10,law3:13` | ``→`law2:27,law2:29,law3:24` | law2#10=각파기·대구고법환송·상고기각 / law3#13=주위적청구파기·인천지법환송 (#9·#12 는 "【주문】" 헤더만 — 제외) |
| G-U-015 | `102`→`직제규정:58` | `0,15`→`직제규정:59` | **운영내규엔 "위원회" 콘텐츠 전무** → 운영내규 측 라벨 없음(비대칭). 직제규정#58=직제개편 이사회의결·#59=구성원 |
| G-U-017 | `10,13`→`law2:10,law3:13` | `29,27`→`law2:27,law2:29,law3:24` | law2#10=대구고법환송, law3#13=인천지법환송(source_chunk_text 주석 일치). 현 accept #27 은 law3 범위(0~25) 밖이라 오류였음 |
| G-U-031 | `113,397`→`쏘나타:25,데이터센터:344` | `112,129`→`쏘나타:11,쏘나타:26,쏘나타:29,쏘나타:112,데이터센터:346` | 쏘나타#25=스마트센스 안전보호 / 데이터센터#344·346=AI고위험군 정의의 원자력안전·국민안전(데이터센터 자체 안전 아님 — 비대칭 약라벨) |
| G-U-032 | `10,441`→`보건의료:10,데이터센터:409` | `151,155,385,387,409`→`보건의료:155,보건의료:13,보건의료:25,데이터센터:14` | 보건의료#10=빅데이터 중요성·활용 / 데이터센터#409="데이터센터 활용 지원"(="데이터 활용" 아님 — 비대칭 약라벨, 최근접). 데이터센터 안내서엔 '데이터 활용 방식' 콘텐츠 사실상 없음 |

(+ G-A-075: `query_type` `cross_doc`→`exact_fact`, relevant `20`·accept `18,16,12,100` 그대로.)

### 10.3 재측정 — **새 baseline** (`JETRAG_EMBED_QUERY_CACHE=1`, RRF-only, `evals/results/s4_a_d4_cross_doc_relabel_run{1,2}.*` — run1==run2 churn 0)
- **cross_doc 9 row**: R@10 **0.2856** / nDCG **0.2371** / MRR **0.2981** / top-1 **0.3333** / doc-match-fail **0**. → 옛 "0.3333"(10 row, doc-바인딩 없어 false-positive inflation)보다 낮음 = **더 정직한 수치, cross_doc retrieval 이 실제로 약함 확인.** row 별: G-U-017 0.667→0.714(top-1 F→T) ✓, G-A-125 0.0→0.143(F→T) ✓, G-A-126 0.0→0.167 ✓, G-A-128 0.0→0.286 ✓ (새 라벨 = 검색이 실제로 찾는 정답 chunk) ↔ G-A-127 1.0→0.5(`{0}`→2원소라 둘 다 찾아야), G-U-015 0.0(직제규정:58 미노출), G-U-031 0.167→0.111·G-U-032 0.333→0.25(데이터센터 약라벨), G-A-124 0.5→0.4.
- **overall**: R@10 0.6839 / nDCG 0.6353 / MRR 0.6079 / top-1 0.8046 (이전 0.6844/0.6350/0.6041/0.7989 — cross_doc 라벨 변경 + G-A-075 qtype 이동 효과뿐, R@10 ≈동일, MRR/top-1 소폭↑). **단일-doc 174 row metric·predicted_top10 회귀 0**(동일 환경 비교, bit-identical). DoD R@10 ≥ 0.75 여전히 미달.
- ⚠️ 이전 `results/*.json` 의 cross_doc cell 과 직접 비교 금지(라벨 의미 변경 = 새 baseline). eval 산출물 `results/s4_a_d4_cross_doc_relabel_run{1,2}.*` 는 `.gitignore` 됨(로컬).

### 10.4 알려진 사소 이슈 (scope 밖)
- `run_s4_a_d4_breakdown_eval.py` `_format_markdown` §1 제목이 "157 row baseline" 하드코딩 — 실제 183 row. 코스메틱(측정값 영향 0). 다음에 정리.

### 10.5 다음 — P1 ✅ §11. 그 다음 = P2 decomposition. 새 baseline = §11.2(P1 이후 — §10.3 은 P0 직후 중간 baseline).

---

## 11. cross_doc P1 — chunk cap 8 + eval 라운드로빈 + intent_router 커버리지 (2026-05-12, commit `8f16f32`)

§9 진단의 병목 B(`_MAX_MATCHED_CHUNKS_PER_DOC=3` 가 cross_doc 응답에서 정답 chunk 탈락) + intent_router cross_doc/compare 신호 부족 해소. cross_doc-class query 한정 변경 — non-cross_doc 회귀 0.

### 11.1 변경 (6 파일, 단위 테스트 1002→**1022** / 회귀 0)
- **`api/app/services/intent_router.py`** — T1 보조 regex 3종 추가(기존 `_T1_CROSS_DOC` 와 OR): `_T1_CROSS_DOC_PAIR`("NP1 (와|과|랑) NP2 …0~15자… 문서류명사" — 예 "law sample2와 law sample3 두 판결", "기웅민 이력서와 이한주 포트폴리오"), `_T1_CROSS_DOC_PAIR2`("문서류명사 (와|과|랑) NP2" — 예 "승인글 템플릿1과 템플릿3", "운영내규랑 직제규정"), `_T1_CROSS_DOC_PLURAL`("문서류명사들 (에서|에|중…)" — 예 "법률 자료들에서 …사례들"). `_DOC_NOUN` 화이트리스트(자료/문서/보고서/안내서/규정/내규/이력서/포트폴리오/포폴/템플릿/판결/계획/사업/매뉴얼/카탈로그/논문). T2: `_T2_COMPARE_STEM = re.compile(r"다르[게지]|다른[가지]|다릅|상이")` 추가("다른 사람"(공백) 제외 — FP 최소화), 매칭 시 `matched_keywords` 기록.
- **`api/app/routers/search.py`** — `_MAX_MATCHED_CHUNKS_PER_DOC_CROSS_DOC=8`·`_CROSS_DOC_CLASS_SIGNALS=frozenset({"T1_cross_doc","T2_compare","T7_multi_target"})` 신규 + `_is_cross_doc_class_query(query)` 헬퍼(`intent_router.route().triggered_signals ∩ 신호`, graceful). 응답 조립부: `is_cross_doc_resp = (not is_doc_scope) and _is_cross_doc_class_query(clean_q)` → `chunk_cap` 3분기(doc-scope 200 / cross_doc-resp 8 / else 3), cross_doc-resp 면 `matched_chunks` RRF desc 정렬. **non-cross_doc list 모드(idx asc, cap 3)·doc-scope 무변경.** schema/필드 추가 없음.
- **`evals/run_s4_a_d4_breakdown_eval.py`** — cross_doc cell re-merge 를 "전체 merged chunk RRF desc 정렬" → `_round_robin_cross_doc_chunks(target_items)`(doc 순서 alias 사전순 고정·결정적, 각 doc 내 RRF desc, rank0부터 인터리브, alias_map 미등록 doc skip) 로 교체. cap 8 만으론 RRF desc 시 첫 doc 가 top-10 독식 가능(옛 ablation "일부 row 하락" 증상) → 라운드로빈으로 doc 균형. single-doc cell 무변경.
- **tests**: `test_intent_router.py` P1 패턴 11건 + `test_search_cross_doc_chunk_cap.py` 신규 4건 + `test_run_s4_a_d4_breakdown_crossdoc_roundrobin.py` 신규 6건. (`.venv/bin/python -m unittest` — full-discover 시 `test_embed_cache` LRU 4건 flaky 는 P1 무관 pre-existing 테스트-격리 이슈, 격리 실행 시 9/9 pass.)

### 11.2 재측정 — **새 baseline** (`JETRAG_EMBED_QUERY_CACHE=1`, RRF-only, `evals/results/s4_a_d4_p1_crossdoc_cap_run{1,2}.*` — run1==run2 churn 0)
- **cross_doc 9 row**: R@10 **0.2856→0.4424** (+0.157, +55% rel) / nDCG **0.2371→0.3213** / MRR **0.2981→0.3472** / top-1 0.3333→0.3333(G-A-126 F→T ↔ G-A-125 T→F net 0 — 후자는 라벨 비대칭(데이터센터 약라벨) 한계, R@10 불변) / **doc-match-fail 0**. row 별 R@10: G-U-015 0.0→0.667 ✓(직제규정:58/59 가 cap 8+RRF desc 로 노출) / G-U-017 0.714→1.0 ✓(라운드로빈으로 law3 chunk top-10 진입) / G-U-032 0.25→0.375 ✓ / G-A-126 0.167→0.5 ✓ / 나머지 5 row 불변.
- **overall 183 row**: R@10 0.6839→**0.6920**(+0.008) / top1_rate **0.8046 동일** / doc-match-fail 3 동일 / error 0. p95 latency ~229→287ms(cold-state 노이즈, intent_router 룰 기반 0ms — SLO 무관).
- **단일-doc 174 row 회귀 0**: non-cross_doc 중 변동 = G-U-028(out_of_scope) 1건 — `predicted_top10` 만 미세 변동, recall/top1 None→None(정답 chunks 없는 latency-only row) → metric 영향 0. 원인 = 이 query 가 `_T1_CROSS_DOC_PLURAL`("자료들에") 로 cross_doc-class 오판정(검색 카드에 chunk 8개 노출 — UX 만, 정확도 0). 다른 173 row predicted bit-identical.
- ⚠️ §11.2 = P1 이후 새 baseline. §10.3(P0 직후) 은 중간 baseline. 이후 P2 등은 §11.2 기준 비교.

### 11.3 intent_router 발화율 / FP
- cross_doc 9 query `needs_decomposition` 발화: before **2/9** → after **9/9** (전부 — P2 decomposition 게이트 통과 준비). (이전 핸드오프 "3/9" 는 부정확 — 실측 2/9.)
- golden_v2 단일-doc 174 query FP(분해 불필요인데 `needs_decomposition=True`): before 9/174(5.2%) → after **11/174(6.3%)** — <10% 임계 내. 신규 FP 2건: "이 자료들에 환경 인증 절차 나와있어?"(out_of_scope, PLURAL) / "체육관 이용 시 금지 행위와 행사 규정은?"(exact_fact, PAIR — "행위와 행사…규정"). regex 미축소(수용). 더 좁히려면 `_T1_CROSS_DOC_PAIR` 한글 토큰 최소 길이 3+ 또는 PLURAL 제거 검토 가능.

### 11.4 알려진 한계 (scope 밖)
- G-A-125 top-1 T→F (R@10 불변) — 데이터센터 측 라벨 비대칭(약라벨) 한계, P1 결함 아님.
- `_is_cross_doc_class_query` 가 응답 경로에서 query 당 `intent_router.route()` 1회 호출(룰 기반 0ms) — MMR 분기 `_is_cross_doc_query`(T1 전용)와 중복이나 통합 안 함(의도 분리: MMR=T1, chunk cap=T1/T2/T7).
- `run_s4_a_d4_breakdown_eval.py` `_format_markdown` "157 row" 하드코딩(§10.4) 여전 — 다음에.

### 11.5 다음 = §12 (P2 $0 기계 분해 — 시도했으나 net-negative). 새 baseline 유지 = §11.2.

---

## 12. cross_doc P2 — $0 기계 분해 시도 → **net-negative, 미머지** (2026-05-12)

§9 진단의 병목 C(단일 query 로 여러 doc 정답 chunk 동시 회수 불가) 에 대한 $0 시도(LLM 없이 doc-제목 토큰 검출 → per-doc scoped 검색 → union). senior-developer 가 `search.py` 에 ENV 게이트(`JETRAG_CROSS_DOC_DECOMP`, default OFF) 뒤로 구현·재측정 → **net-negative 확정 → 코드 미머지**(net-negative 인 ~242줄 dormant 코드는 `간결하게` 원칙상 보존 안 함, 진단만 본 절에 기록).

### 12.1 측정 결과 (decomp ON, §11.2 baseline 대비)
- **cross_doc 9 row: R@10 0.4424→0.3610 (−0.0815) / nDCG 0.3213→0.2507 / MRR 0.3472→0.2620 / top-1 0.3333→0.2222.** doc-match-fail 0 유지. overall R@10 0.6920→0.6878 (하락분 전부 cross_doc 에서 유입, 단일-doc 174 row 회귀 0). 9 row 중 7개 분해 발동(한↔영 미지원으로 G-U-017 0 검출 / G-U-031 1 검출 미발동). doc 검출 정밀도 100%(단일-doc 174 중 분해 FP 0).
- 회귀 row: G-A-124 0.4→0.0, G-A-126 0.5→0.167, G-A-127 top-1 T→F. 개선 row: 없음(G-A-128 nDCG 미세↑, R@10 불변). latency: cross_doc query ~2배(추가 RPC 2개), overall p95 무동요(cross_doc ≈5%).

### 12.2 근본 원인 (왜 net-negative)
- **doc-scoped 검색 경로는 "doc 내 RRF" 가 아님.** `search_hybrid_rrf` RPC 는 `doc_id` 인자가 없음 → doc-scoped = 글로벌 dense/sparse top-200 RPC + 사후 `doc_id` 필터. 더 깊은 200-pool 에서는 dense+sparse 양쪽 hit chunk 가 RRF 합산 boost 를 받아 ranking 이 재배치됨 → 얕은 50-pool 에서 상위였던 정답 chunk(예 G-A-124 운영내규#22)가 200-pool 재배치로 밀려남. 즉 per-doc "scoped" 검색이 오히려 정답 회수율을 낮춤.
- ⇒ **진짜 fix 후보**: (a) **paid LLM query decomposition** — `/answer` 의 `query_decomposer` 를 `/search` 옵션 노출 + LLM 분해(query 를 의미 단위 sub-query 로, doc-제목 토큰 아님). ~$0.05/eval. intent_router 발화 9/9 로 게이트 준비. (b) **doc-scoped RPC 마이그** — `search_hybrid_rrf` 에 `doc_id` 인자 추가해 doc 내에서 dense/sparse top-K + RRF 계산하는 새 함수($0 이나 마이그+RPC 변경=중규모, search.py doc-scope 경로 전체 영향). 둘 다 P2 1차 scope 밖 → 사용자 판단(특히 (a)는 paid 승인 필요).

### 12.3 산출/코드 처리
- 코드: `search.py` 의 P2 변경 + `test_search_cross_doc_decomp.py` 19 케이스 → **`git checkout`/`rm` 으로 폐기**(커밋 안 됨). 단위 테스트는 P1 시점 1022 로 복귀.
- eval 산출물 `evals/results/s4_a_d4_p2_decomp*.*` → 로컬 삭제(.gitignore 였음).
- 교훈: cross_doc 의 doc-간 정답 분산 문제는 "per-doc 검색"으로 안 풀림(doc-scoped 가 진짜 doc-RRF 가 아니라서). 다음 시도는 (a) 또는 (b).
