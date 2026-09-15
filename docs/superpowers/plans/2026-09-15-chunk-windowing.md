# Edge `chunk` 단계 창 처리 — 설계 + 구현 플랜 (2026-09-15)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax.
> **배경**: `work-log/2026-09-15 서버 근본 수정 S1~S4 …md` §5 사고 1 — SK 사업보고서(1,513p, 추출물 20MB, 청크 25,872)가 `chunk` 단계에서 3회 런타임 kill. 메모리 `jetrag_edge_chunk_large_doc_hazard`.

**Goal:** 어떤 크기의 문서도 `chunk` 단계가 Edge CPU 2s·메모리 256MB 안에서 끝나게 한다. 출력(청크 경계·`chunk_idx`·flags)은 현행과 **byte-identical**.

**Architecture:** `chunk` 태스크를 extract 아티팩트 창(`from`,`count`, 기본 4개 = 40페이지) 단위로 쪼갠다. 병합(`chunk_merge.ts`)은 `buf.page === section.page` 조건 때문에 페이지를 넘지 않으므로 "page 값이 바뀌는 지점"에서 자르면 결과가 같다. 유일한 교차 창 의존(헤더/푸터 반복 카운트, `chunk_filter.ts`)은 카운트를 캐리로 누적하고 **마킹을 `load` 단계로** 옮긴다. `load` 는 이미 part 창 단위다.

**Tech Stack:** Deno/TS Edge, `ingest_artifacts` 캐리 패턴(`handlers/vision.ts` `loadCarry`, `handlers/extract.ts` `loadCarryTitle`).

---

## 0. 실측 근거 (분석 에이전트, 2026-09-15, M-시리즈 랩탑, 25,872 청크 / 9.6M자)

| 구간 | ms |
|---|---|
| `toChunkRecords`(그중 `extractEntities` 1,472) | 1,456 |
| `runChunkFilterStage`(`lineMetrics` 644 — 문자당 `WORD_RE.test`) | 950 |
| `runContentGateStage`(`detectPii` 692) | 721 |
| `mergeShortSections` | 345 |
| split / JSON.parse(22.8MB) / stripNulls+stringify | 66 / 82 / 62 |
| **합계** | **3,811** (Edge vCPU 는 더 느림 → 8~15s 추정) |

피크 RSS 405MB. O(n²) 없음 — 전부 문자 수 비례, 상수가 큼. 창 40페이지 ≈ 684청크 ≈ 95ms(랩탑).

**병합 등가성 실측** (24,020 섹션 → 7,660 병합):
- (A) page 값이 바뀌는 지점에서만 컷 → **identical**
- (B) 페이지 무시 997개씩 컷 → 다름(7,667) — 반례
- (D) extract 꼬리와 vision 첫 섹션이 같은 page 일 때 그 사이를 컷 → 다름(150 vs 149) — 반례
→ **컷 규칙: 직전·다음 섹션의 `page` 가 다른 지점에서만.** 캐리 = "마지막 page 값을 공유하는 꼬리 섹션 묶음"(한 페이지분으로 유계).

`stage='chunk'` 아티팩트 소비자는 `handlers/load.ts` 뿐. `tag_summarize`·`doc_embed` 는 `loadRawText`(extract/scan/vision 의 `raw_text`)를 읽어 청크 레이아웃과 무관.

---

## 1. 설계

### 1.1 `chunk` 태스크 (payload `{job_id, doc_id, stage:"chunk", from, count}`)

1. **소스 플랜**: `ingest_artifacts` 에서 `select("stage, seq")` 만으로(payload 없이) extract/scan/vision 행 목록을 만든다. scan 이 있으면 extract 를 대체(현행 `chunk.ts:114-134` 규칙 유지), vision 은 텍스트 전부 뒤에 붙는 순서 유지. 플랜 = 정렬된 `[{stage, seq}]` 배열. 총 창 수 `totalWindows = ceil(plan.length / count)`.
2. **창 읽기**: `plan[from … from+count)` 행만 `.select("stage, seq, payload->sections")` 로 읽는다(`raw_text` 제외 → 전송 절반).
3. **캐리 입력**: `from > 0` 이면 `stage='chunk', seq = from/count - 1`(직전 창) 아티팩트의 `payload.carry` 를 읽는다: `{ sections: Section[], nextChunkIdx: number, hfCounts: Record<string, number>, docFlags: {has_pii, has_watermark, watermark_hits…} }`. 없으면 throw(순차 계약 위반 — `vision.ts:loadCarry` 와 같은 메시지 형식).
4. **컷**: `carry.sections + 이번 창 섹션` 을 이어 붙인 뒤, 마지막 창이 아니면 **꼬리에서 마지막 섹션과 같은 `page` 값을 가진 연속 묶음**을 떼어 `nextCarry.sections` 로 남기고, 나머지만 처리한다. (page 가 `null` 인 섹션은 `null === null` 로 같은 묶음.) 마지막 창은 떼지 않는다.
5. **처리**: split → merge → `toChunkRecords({ idxOffset: carry.nextChunkIdx })` → `runContentGateStage`(청크 단위 PII/워터마크; 문서 flags 는 캐리에 **OR 누적**). `runChunkFilterStage` 는 **호출하지 않는다** — 대신 이 창의 짧은 청크 텍스트 카운트를 `hfCounts` 에 누적(`chunk_filter.ts` 의 카운트 로직을 export 해 재사용).
6. **쓰기**: `stage='chunk'`, `seq = from / count`(창 인덱스), payload `{ records, chunk_idx_from, next_chunk_idx, carry: nextCarry }`. upsert(UNIQUE(job_id,stage,seq)).
7. **다음**: 마지막 창이 아니면 `chunk` 를 `from + count` 로 enqueue. 마지막 창이면 ① `documents.flags` 를 캐리 누적값으로 **OR 머지**(현행 `mergeDocFlags` 는 덮어씀 → 고침) ② 잔존 아티팩트 삭제 `stage='chunk' AND seq >= totalWindows`(재인제스트로 창 수가 줄었을 때 옛 part 가 load 되는 것 방지) ③ `tag_summarize` enqueue(현행과 동일).

**멱등성**: 창 k 재배달 → 캐리 `seq=k-1` 재독 → 같은 offset·같은 출력 → upsert 로 행 1개.

### 1.2 `load` 태스크

- part 를 읽기 전에 **마지막 chunk 아티팩트의 `carry.hfCounts`** 를 1회 읽어 `headerFooterTexts`(≥3회)를 만든다(어느 seq 가 마지막인지는 `select seq order desc limit 1`).
- 각 청크에 `classifyChunk`(`chunk_filter.ts:118`, export 됨)를 적용해 `flags.filtered_reason` 을 채운 뒤 upsert. 나머지는 현행.
- 경고 로그(`filterRatio`)는 part 단위 → 마지막 part 에서 문서 합계 1회로 바꾼다.

### 1.3 상수·ENV

`CHUNK_ARTIFACTS_PER_TASK = 4`(40페이지). ENV `JETRAG_CHUNK_ARTIFACTS_PER_TASK` 로 오버라이드(정수, ≥1).

### 1.4 범위 밖(기록만)

`entity_extract.ts` 의 `B` lookaround pre-filter(1.4s 절감 가능)는 판정이 갈릴 수 있어 골든셋 대조 선행 필요 — 이번엔 안 한다. 창 분할만으로 예산 안에 든다.

---

## 2. 파일 구조

| 파일 | 작업 |
|---|---|
| `supabase/functions/_shared/ingest/chunk_records.ts` | `toChunkRecords`/`runChunkStage` 에 `idxOffset` (`chunk_idx = idxOffset + idx`, `overlap_with_prev_chunk_idx` 조건 `idxOffset + idx > 0`) |
| `supabase/functions/_shared/ingest/chunk_window.ts` **신규** | 순수 함수: `splitTailByPage(sections)` → `{ head, tail }`, `windowPlan(rows, count)`, `accumulateHfCounts(counts, records)`, `mergeFlagsOr(a, b)` |
| `supabase/functions/_shared/ingest/chunk_window_test.ts` **신규** | 등가성 property test: (A) page-change 컷 = 전체 처리와 identical, (B)/(D) 반례가 **다르게 나옴을 고정**(회귀 감지), 꼬리 분리, null page |
| `supabase/functions/_shared/ingest/chunk_filter.ts` | 카운트 수집(`collectShortCounts`)과 `headerFooterTexts(counts)` 를 export 로 분리. `runChunkFilterStage` 는 그대로 두되 내부에서 재사용 |
| `supabase/functions/_shared/ingest/handlers/chunk.ts` | 창 루프·캐리·`payload->sections` 셀렉트·flags OR·잔존 seq 삭제·다음 창/`tag_summarize` enqueue. **`runChunkFilterStage` 호출 제거** |
| `supabase/functions/_shared/ingest/handlers/chunk_test.ts` | 창 경계·캐리 왕복·offset 연속·마지막 창 정리·재배달 멱등 |
| `supabase/functions/_shared/ingest/handlers/load.ts` | 캐리 `hfCounts` → `classifyChunk` 마킹 |
| `supabase/functions/_shared/ingest/handlers/load_test.ts` | 마킹 이동 반영 |
| `supabase/functions/_shared/ingest/chunk_bench.ts` **신규** | `deno run --allow-read --allow-env chunk_bench.ts --sections <json> --window N` → 창별 CPU ms·피크 heap. 분석 때 쓴 `scratchpad/bench2.ts, bench3.ts, equiv.ts` 를 정리해 상시화 |
| `api/scripts/make_bench_sections.py` **신규** | 로컬 PDF → extract 섹션 JSON(`--repeat N` 으로 대형 문서 흉내) |
| `supabase/functions/api-ingest-worker/index.ts` | 사슬 주석 갱신(chunk 창 단위) |

---

## 3. Task 순서 (각 Task = 테스트 먼저 → 구현 → `deno fmt --check && deno lint && deno test` → 커밋)

- [ ] **Task 1** `chunk_records.ts` `idxOffset` + 테스트(offset 0 이면 현행과 동일 출력, offset 100 이면 `chunk_idx` 100부터·첫 청크 `overlap_with_prev_chunk_idx = 99`).
- [ ] **Task 2** `chunk_window.ts` 순수 함수 + `chunk_window_test.ts` 등가성 테스트. 테스트 데이터는 `scratchpad/sections.json`(24,020 섹션)을 `_shared/ingest/fixtures/chunk_sections_sample.json` 으로 **축소본(예: 2,000 섹션, page 경계·null page·vision seam 포함)** 만들어 넣는다. (A)=identical, (B)·(D)=다름을 고정.
- [ ] **Task 3** `chunk_filter.ts` 분리 export + 기존 테스트 통과.
- [ ] **Task 4** `handlers/chunk.ts` 창 처리 + `chunk_test.ts`(fake client 로 아티팩트 3창 시나리오: 캐리 왕복·offset·마지막 창 flags OR·잔존 seq 삭제·다음 enqueue). 기존 `chunk_test.ts` 의 단일 태스크 가정 케이스는 창 1개(전부 한 창)로 재해석해 통과시킨다.
- [ ] **Task 5** `handlers/load.ts` 마킹 이동 + `load_test.ts`.
- [ ] **Task 6** `chunk_bench.ts` + `make_bench_sections.py`. `sonata-the-edge_catalog.pdf --repeat 264`(≈SK 규모)로 **창 1개 CPU ≤ 200ms(랩탑), 피크 heap ≤ 60MB** 확인. 전체(창 없음) 대비 수치를 work-log 에 표로.
- [ ] **Task 7** 배포 + 검증: `api-ingest-worker` 배포 → **작은 문서 재인제스트 1건**(케이터링 PDF 0.3MB)으로 사슬 정상 확인(청크 수·`filtered_reason` 분포가 재인제스트 전과 동일) → **SK 보고서 재인제스트**(실패 시 `api/scripts/recover_doc_local.py` 로 즉시 복구). 성공 기준: `chunk` 태스크 kill 0, 청크 25,806 ± 30, `filtered_reason` 분포 동일, 총 소요 시간 기록.

**완료 기준**: 테스트 전부 PASS(310+), 등가성 테스트 고정, 벤치 수치 기록, SK 재인제스트 성공, work-log 갱신, 한국어 커밋·push.
