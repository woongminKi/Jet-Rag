# 2026-05-12 DB chunk 정합성 감사 — sample-report dense_vec NULL 발견

> 프로젝트: Jet-Rag
> 작성: 2026-05-12 (handoff `be26459` 직후 세션). 트리거 = 사용자 질문 "그동안 인제스트/청킹 로직이 여러 번 바뀌었는데, DB 에 올라간 chunk 중 로직 변경 전 임베딩된 stale 데이터로 eval 돌려도 괜찮나?"
> 성격: **감사(QA) 리포트 + 복구 실행 기록.** 운영 코드 변경 0. 복구 도구 신규 1 파일(`evals/_repair_sample_report_dense_vec.py`). **2026-05-12 — sample-report dense_vec 1000건 복구 `--apply` 완료 (NULL 1000→0, MCP 재확인), stale job `failed` 마킹. 다음 = golden_v2 eval 전면 재측정.**

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
| 절대 R@10/top-1/nDCG/MRR (overall) | ❌ 신뢰 불가 | sample-report 24 row(183 의 13.1%)가 0 으로 강제 유입(또는 `doc 매칭 fail` 시 분모 누락 → 반대로 상향 왜곡). DoD R@10 ≥ 0.75 미달을 검색 알고리즘 한계로 단정 금지. 추정: sample-report 정상이었으면 overall R@10 이 ~0.07~0.09pp 높았을 수 — DoD 이미 충족됐을 가능성 |
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

---

## 6. 남은 이슈 / 후속 정정 필요

- ~~**(P0) sample-report 복구**~~ ✅ 2026-05-12 완료 (§5.3 — dense_vec NULL 1000→0, stale job failed). **남음: golden_v2 eval 전면 재측정 (§7-1)** 으로 24 row 강제 miss 해소 + DoD 재판정.
- **(P0) handoff/work-log 수치 재해석 주석** — `2026-05-12 종합 마감…` handoff 및 관련 work-log 의 "R@10 0.66~0.70 / DoD 미달" 서술에 "sample-report dark 로 하향 왜곡, 복구 후 재측정 전까지 보류" 주석. DoD R@10 ≥ 0.75 미달을 검색 한계로 단정 금지.
- **(P0) `2026-05-11 D5 chunks 회귀 복구 ship.md` 정정** — "sample-report embedding 완료" 는 사실 아님(embed 스테이지 정지) 명기.
- **(P1) Phase 11 / acceptable judge worklog 오기 정정** — "데이터센터 huge doc(~1000 chunk)" → "sample-report(d1259dfe, 1000 chunk)". [empty] 7 row 원인 = "huge doc 후보 풀 부족" 이 아니라 "sample-report dense_vec NULL → cosine 후보 미생성". acceptable judge 2차 라운드는 복구 후로.
- **(P1) eval 도구 가드 추가 검토** — `eval_retrieval_metrics` 에 "정답 doc 의 chunk 중 dense_vec NULL 비율 ≥ 임계 → WARN + 별도 버킷 집계" → 같은 사고 조기 발견. `_pick_target_items` doc-매칭-fail row 수도 산출물에 노출(분모 누락 상향 왜곡 점검).
- **(P2) 위생** — old-era doc 의 entities 부분 누락(직제 등)은 backfill 도구로 채움(embed 재호출 0, 검색 영향 0 — 급하지 않음). caption_dependent=true 라벨 중 G-A-011(브랜딩 pptx)·G-U-016(직제 hwpx)은 해당 포맷에 caption 합성 경로 없음 → 라벨 자체 재검토.
- **(인지) ingest_jobs orphaned `running` 정리 정책** — 프로세스 비정상 종료 시 job 이 영영 running. 시작 시 stale `running`(예: started_at 24h+ 경과) 정리하는 startup hook 또는 watchdog 검토.

## 7. 다음 스코프 (복구 완료 후)

1. **eval 전면 재측정** — sample-report 복구 후 golden_v2 183 row R@10/top-1/nDCG/MRR 재측정 → "실제" baseline 확정 → DoD 충족 여부 재판정. v2↔v2 동일 row 비교.
2. **embed-cache 검증** (handoff 0순위 — 그대로 유효) — eval 1회로 query→vector 채움 → 2회 연속 churn 0 + HF 호출 0 확인.
3. **acceptable judge 2차 라운드** (handoff 1순위, ~$0.05) — sample-report 복구 후에야 의미. candidate top-K 15→30+ 또는 `/search` 기반 후보 + threshold 튜닝.
4. **cross_doc 약점 진단** (handoff 2순위) — R@10 0.1273 / top-1 0.0. entity_boost 레버 아님 판명 → query decomposition / multi-doc fusion / golden 라벨 재점검.

---

## 8. 검증 메모 (이 세션)

- DB 조회/변경: `mcp__supabase-jetrag__execute_sql` (읽기 전용 — 스키마/분포/sample-report 진단/NFC 실측/복구 후 재확인) + `evals/_repair_sample_report_dense_vec.py --apply` (`chunks.dense_vec` 1000 row UPDATE + `ingest_jobs` 1 row status='failed'). row 추가·삭제·text 변경 0.
- 운영 코드 변경: 0. 신규 파일: `evals/_repair_sample_report_dense_vec.py` (일회용 복구 도구).
- 단위 테스트: 997 pass / 50 subtests / 회귀 0 (운영 코드 미변경 — senior-developer 가 `uvx pytest -q` 로 확인).
- 관련 문서: senior-qa 감사 리포트(세션 내), `work-log/2026-05-11 D5 chunks 회귀 복구 ship.md`, `work-log/2026-05-11 S4-A D4 Phase 4 — D5 reingest.md`, `work-log/2026-05-11 acceptable_chunks LLM-judge 자동 보완 ship.md`, `work-log/2026-05-12 종합 마감 + 2026-05-13 진입 핸드오프.md`.
