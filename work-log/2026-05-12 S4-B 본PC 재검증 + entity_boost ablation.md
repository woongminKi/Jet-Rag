# 2026-05-12 — S4-B 본 PC 재검증 + entity_boost factor ablation

## 요약 (TL;DR)

- **chunks DB = entity 포함 상태로 영구 변경됨** — `metadata.entities` 보유 chunk **441 / 2469** (백필 전 377 → +64 신규 백필).
- `_ENTITY_BOOST_FACTOR` → `JETRAG_ENTITY_BOOST_FACTOR` ENV 화 (A안, `search.py` 내 `_parse_factor_env` 헬퍼 신규, default 1.10 / [0.5, 3.0]). **동작 변경 0** (ENV 미설정 시 1.10).
- 단위 테스트 **+8** → 전체 **981 OK / skipped 0 / 회귀 0**.
- ablation (baseline OFF + f110 + f150 + f200, golden_v2 183 row, in-process search()) → **4 설정 전부 metric 동일**: R@10 0.6553 / top-1 0.7443 / nDCG@10 0.5975 / MRR 0.5616. qtype별 Δ 전부 0.0000. cross_doc subset R@10 0.1273 (전부 동일).
- 원인 = **entity-매칭 가능 모수 ≈ 0**: golden_v2 183 query 중 `extract_entities(query)` non-empty = **1건** (`G-U-019` "12%"), 그 1건조차 정답 chunk entities 와 교집합 **0건**.
- **production default 권고: `JETRAG_ENTITY_BOOST` OFF 유지 확정** (결정 트리 케이스 ③ "모든 factor 개선 0 → OFF 유지 확정, 다음 단계 = LLM 보강(persons/orgs/products) 통합"). `_ENTITY_BOOST_FACTOR` ENV 화 자체는 default 1.10 유지라 ship 무방.
- **paid cost = $0** (entity 추출 = 룰 기반 정규식, 검색 측정 = BGE-M3 무료 임베딩, LLM 호출 0).

---

## 1. 백필 (`evals/backfill_chunk_entities.py --apply`) — 사용자 승인 후 실행

### a. dry-run (실행 전 모수)

```
total processed: 2469
skipped (entities 이미 있음): 377
empty (entities 0건 추출): 2028
updated: 64 (dry-run, DB 변경 0)
=== 추출 entities 분포 (cumulative) ===
dates: 65 / percentages: 51 / amounts: 1 / identifiers: 0
```

- 백필 전 이미 377 chunk 가 `metadata.entities` 보유 (새 ingest 자동 통합분 + 이전 세션 부분 백필 추정).
- 64 chunk 가 신규 백필 대상 (entities 추출 성공 + 키 미존재). 2028 chunk 는 entities 0건 추출(빈) → 스킵 (단건 텍스트에 날짜/금액/% /식별자 패턴 없음 — 정상).

### b. `--apply` (background 실행)

```
=== 종료 요약 ===
total processed: 2469
skipped (entities 이미 있음): 377
empty (entities 0건 추출): 2028
updated: 64 (applied to DB)
```

- updated 64 = dry-run 예측치와 **일치**. flush: `flushed 64 updates (final)` 1회.

### c. 멱등 재확인 (`--dry-run` 재실행)

```
total processed: 2469
skipped (entities 이미 있음): 441   ← 377 + 64 (백필 반영)
empty (entities 0건 추출): 2028
updated: 0 (dry-run, DB 변경 0)
```

- `updated: 0` → 멱등 확인. `empty 2028` 은 매번 재추출되어 다시 잡힘 (DB 미기록, 정상 동작 — 빈 entities 는 키 미주입 설계).

### d. DB count 확인 (service_role client — Supabase MCP `--read-only` 라 동등 read 검증)

```
total chunks: 2469
chunks with metadata.entities: 441
```

기존 키 보존 육안 확인 (entities 보유 chunk 중 metadata 키 ≥ 3 인 58건 표본):

```
chunk 30b88b16... keys: ['entities', 'overlap_with_prev_chunk_idx', 'table_caption']
chunk de890125... keys: ['entities', 'overlap_with_prev_chunk_idx', 'table_caption']  (entities.dates 18건, table_caption 보존)
metadata 키셋 분포 (entities 보유 chunk): 382×(entities,overlap), 23×(entities,figure_caption,overlap),
  16×(entities,overlap,table_caption), 14×(entities,figure_caption,overlap,table_caption),
  3×(entities,overlap,watermark_hits), 1×(entities,overlap,table_caption,watermark_hits), 1×(entities,overlap,pii_ranges) ...
```

→ `table_caption` / `figure_caption` / `overlap_with_prev_chunk_idx` / `watermark_hits` / `pii_ranges` 전부 `entities` 와 공존 — **기존 키 손실 0**. (`backfill_chunk_entities.py` 의 `new_metadata = dict(metadata); new_metadata["entities"] = ...` 로직대로.)

### 사이드 이펙트 (영구 변경)

- **chunks DB 상태가 entity 포함으로 영구 변경** — 이후 모든 검색·측정의 baseline 이 (entities 키 추가된) 현 상태 기준. entity_boost OFF 일 때는 검색 동작 영향 0 (entities 키는 boost 분기에서만 읽음). ON 시에만 영향 (현재 default OFF).

---

## 2. `_ENTITY_BOOST_FACTOR` → `JETRAG_ENTITY_BOOST_FACTOR` ENV 화 (A안)

`api/app/routers/search.py` (L123~162):

- 신규 헬퍼 `_parse_factor_env(name, default=1.10, lo=0.5, hi=3.0)` — env get → float 변환 실패/음수/[lo, hi] 범위 밖 → default. `config._parse_float` 와 달리 **상·하한 클램프(범위 밖 → default)** 포함 (극단 factor × cover/toc guard ×0.3 ranking 깨짐을 import 시점 차단). silent fallback (운영 graceful, config 헬퍼와 일관).
- `_ENTITY_BOOST_FACTOR = _parse_factor_env("JETRAG_ENTITY_BOOST_FACTOR")` — **모듈 import 시 1회 parse** (현재 상수처럼 사용). 사용 지점(L965 `score *= _ENTITY_BOOST_FACTOR`) **무변경**.
- 상수 추가: `_ENTITY_BOOST_FACTOR_ENV` / `_ENTITY_BOOST_FACTOR_DEFAULT=1.10` / `_ENTITY_BOOST_FACTOR_MIN=0.5` / `_ENTITY_BOOST_FACTOR_MAX=3.0`. 주석 갱신 (2026-05-12 라인 + "production default 변경은 사용자 사인오프 후").
- **동작 변경 0** — ENV 안 주면 1.10 (현 하드코딩값과 동일).
- `_entity_match_chunk` 로직 / `JETRAG_ENTITY_BOOST` (활성 토글) / cover·toc·reranker 가드 **무변경**.

### 단위 테스트 — `api/tests/test_search_entity_boost_factor.py` (신규, 8 케이스)

- `_parse_factor_env` / 상수 직접 import 해 함수 호출 테스트 (모듈 reimport 불필요).
- 케이스: 미설정→1.10 / 빈문자열→1.10 / "1.5"→1.5 / "2.0"→2.0 / "0.5"·"3.0" 경계 포함 / "99"·"3.01" 상한밖→default / "0.1"·"0.49" 하한밖→default / "-1"·"-0.5" 음수→default / "abc"·"1.1x" 비숫자→default / custom default·lo·hi 재사용 검증.
- `cd api && uv run python -m unittest discover tests` → **Ran 981 tests / OK / skipped 0** (기존 973 + 신규 8). search.py 편집 후 / 신규 테스트 추가 후 둘 다 회귀 0.

---

## 3. entity-매칭 가능 모수 파악 (golden_v2, ablation 사전)

- golden_v2.csv 183 row 각각 `extract_entities(query)` non-empty row: **1건** — `G-U-019` ("경제전망에서 12% 증가 그게 어디였지", qtype=numeric_lookup) → `{"percentages": ["12%"]}` 만 추출.
- 그 1건의 정답 chunk(들) entities ∩ query entities: **0건** — G-U-019 의 relevant_chunks=[2] (entities `["2%"]`), acceptable=[911, 981] (`["2.0%","0.3%",...]` 등) — 어느 것도 `12%` 미포함.
- 참고: G-U-019 의 doc(d1259dfe..., sample-report) 내 `12%` entity 보유 chunk = chunk_idx **481** 1건뿐 — 정답 chunk 아님.
- **모수 < 10** → 명세대로 "측정 모수 작음 — 방향성 참고용" 명시. 실질 모수 ≈ **0** (검색 ranking 에 entity_boost 가 영향 줄 수 있는 query·chunk 페어가 사실상 없음). golden_v2 는 룰 기반 entity(날짜/금액/%/식별자) 보다 자연어 fuzzy/exact_fact 위주라 본 측정셋에서 entity_boost 레버는 거의 안 걸림.

---

## 4. ablation 결과 (baseline OFF + f110 + f150 + f200)

- 도구: `run_s3_d5_search_stack_eval.py --combo a --goldenset v2` (RRF-only baseline + cross_doc sub-report + 7 metric + raw JSON). entity ENV 는 외부에서 주입 (`_apply_combo_env` 가 `JETRAG_ENTITY_BOOST*` 미설정 → 외부값 생존). golden_v2.csv = **read-only** (literal `v2` 인자, 쓰기 0).
- 출력: `evals/results/_s4b_ablation_{baseline,f110,f150,f200}.{md,json}` — **gitignored, commit 안 함**.
- HF embed cold-start: 첫 run(baseline) 만 cold (P95 33.6s, avg 3.0s). f110/f150/f200 은 warm cache 재사용 (P95 ~0.6s). metric 영향 0, 시간만.

### 전체 metric (golden_v2 183 row, n_eval=176)

| 설정 | ENV | R@10 | nDCG@10 | MRR | top-1 | ΔR@10 | Δtop-1 | 회귀? |
|---|---|---:|---:|---:|---:|---:|---:|:---:|
| baseline | `JETRAG_ENTITY_BOOST=false` | 0.6553 | 0.5975 | 0.5616 | 0.7443 | — | — | — |
| f110 | `ON` `FACTOR=1.10` | 0.6553 | 0.5975 | 0.5616 | 0.7443 | +0.0000 | +0.0000 | no |
| f150 | `ON` `FACTOR=1.50` | 0.6553 | 0.5975 | 0.5616 | 0.7443 | +0.0000 | +0.0000 | no |
| f200 | `ON` `FACTOR=2.00` | 0.6553 | 0.5975 | 0.5616 | 0.7443 | +0.0000 | +0.0000 | no |

(회귀 가드 = R@10 또는 top-1 baseline 대비 −0.02 초과 하락 — 해당 없음.)

### qtype별 R@10 (n) — baseline | f110Δ | f150Δ | f200Δ

| qtype | n | baseline | f110Δ | f150Δ | f200Δ |
|---|---:|---:|---:|---:|---:|
| cross_doc | 10 | 0.1778 | +0.0000 | +0.0000 | +0.0000 |
| exact_fact | 115 | 0.6957 | +0.0000 | +0.0000 | +0.0000 |
| fuzzy_memory | 8 | 0.7022 | +0.0000 | +0.0000 | +0.0000 |
| numeric_lookup | 7 | 0.5890 | +0.0000 | +0.0000 | +0.0000 |
| summary | 9 | 0.6481 | +0.0000 | +0.0000 | +0.0000 |
| synonym_mismatch | 8 | 0.6998 | +0.0000 | +0.0000 | +0.0000 |
| table_lookup | 12 | 0.6910 | +0.0000 | +0.0000 | +0.0000 |
| vision_diagram | 7 | 0.5842 | +0.0000 | +0.0000 | +0.0000 |

(qtype 합 n=176 = n_eval. out_of_scope/false 등 relevant_chunks 없는 row 는 chunk-level metric 미산출.)

### cross_doc sub-report (top-5 distinct doc_id ≥ 3, n_subset=15 / n_eval=11)

| 설정 | R@10 | nDCG@10 | MRR | top-1 |
|---|---:|---:|---:|---:|
| baseline | 0.1273 | 0.0874 | 0.1136 | 0.0000 |
| f110 | 0.1273 | (동일) | (동일) | 0.0000 |
| f150 | 0.1273 | (동일) | (동일) | 0.0000 |
| f200 | 0.1273 | (동일) | (동일) | 0.0000 |

→ S3 D6 의 cross_doc 최약 (R@10 0.1273 / top-1 0.0) **entity_boost 로 변화 없음** — entity_boost 는 cross_doc 약점의 레버가 아님 (cross_doc query 들이 룰 기반 entity 를 거의 안 가짐).

### per-cell 변동 — f200 vs baseline

- predicted top-10 이 바뀐 cell: **1건** (`G-U-019`) — 나머지 182 row 전부 동일 (f110/f150 은 0건 변동, factor 작아 boost 후에도 순위 역전 없음).
- `G-U-019` baseline top10 = `[986,990,988,321,310,260,916,317,991,914]` (R@10=0.0) → f200 top10 = `[481,986,990,988,321,310,260,916,317,991]` (R@10=0.0). chunk 481(`12%` entity 보유) 이 ×2.0 boost 로 1위 진입, 기존 10위 chunk 914 를 밀어냄. **정답 chunk(2/911/981) 는 양쪽 다 top-10 밖** → metric 영향 0. ── 명세의 "큰 factor × guard 무력화 / 표지·목차 침투" 엣지의 축소판 (여기선 침투한 게 표지가 아니라 단순 % 보유 chunk라 무해). 실제 운영 데이터에서 query 가 날짜/금액을 자주 포함하면 이 boost 가 의미를 가질 수 있으나, 현 골든셋으론 검증 불가.

---

## 5. production default 권고 (결정 트리)

결정 트리 케이스 ③ **"모든 factor 개선 0 또는 회귀 → OFF 유지 확정"** 적중:

- **권고: `JETRAG_ENTITY_BOOST` = OFF 유지 (변경 X)**. `.env`·코드 default 자동 변경 안 함 (CLAUDE.md ENV 정책 + 사용자 명시).
- 근거: 룰 기반 entity(날짜/금액/%/식별자)만으론 현 골든셋 ranking 개선 0 — 측정 가능한 query·chunk 매칭 모수가 사실상 없음. cross_doc 약점의 레버도 아님.
- **S4-B 다음 단계 우선순위 = LLM 보강 통합** — `entity_extract.py` 의 `extract_entities_with_llm` / `parse_llm_entities` (persons/orgs/products, 현재 미통합) 를 ingest 에 통합하면 자연어 query("쏘나타 디 엣지 가격", "한국은행 보고서")가 entity 매칭에 걸려 boost 가 의미를 가질 가능성. 단 Flash-Lite LLM 호출 비용 발생 → 사용자 사인오프 필요. (남은 작업 plan 의 entity_boost 항목 갱신 권고.)
- `_ENTITY_BOOST_FACTOR` ENV 화는 default 1.10 유지라 동작 변경 0 — ship 무방. 향후 ON 전환 결정 시 factor 튜닝은 ENV 로 즉시 가능.

### Q-entity-1~4 결정안

| ID | 질문 | 결정 |
|---|---|---|
| Q-entity-1 | entity_boost production default ON/OFF? | **OFF 유지** (룰 기반 모수 ≈ 0, 개선 0). LLM 보강 통합 후 재평가. |
| Q-entity-2 | factor production 값? | 현 ENV default **1.10 유지**. (ON 전환 시점에 재측정 — 룰만으론 차이 없음.) |
| Q-entity-3 | `_entity_match_chunk` 로직(카테고리 OR 매칭) 변경? | **변경 안 함** — 현 로직 정상, 한계는 모수 부족이지 로직 아님. |
| Q-entity-4 | 기존 chunks backfill `--apply` 실행? | **실행 완료** (사용자 승인, cost 0, 멱등). chunks DB = entities 포함 상태 영구 변경 (441/2469). |

---

## 6. DoD 체크

- (a) ✅ 백필 완료 (updated 64) + 멱등 재확인 (updated 0) + DB count (441/2469) + 기존 키 보존 육안.
- (b) ✅ factor ENV 화 (`_parse_factor_env`) + 단위 테스트 8건.
- (c) ✅ ablation 측정 (OFF + 3 factor) → 표 (전체/qtype/cross_doc).
- (d) ✅ production default 권고 (OFF 유지, 트리 케이스 ③) + Q-entity-1~4.
- (e) ✅ 단위 테스트 981 OK / skipped 0 / 회귀 0.
- (f) ✅ golden CSV 무변경 / 마이그레이션 무변경 / `_entity_match_chunk` 무변경 / 운영 `.env` 무변경 / cover·toc·reranker 가드 무변경 / ingest 무변경.
- (g) ✅ work-log 작성 (본 문서). paid cost **$0**. chunks DB = **entity 포함 상태 (entities 보유 chunk 441개)**.

---

## 7. 명세 ↔ 실제 코드 차이

- 명세: "eval_retrieval_metrics.py (qtype breakdown 포함) 메인" → 실제 `eval_retrieval_metrics.py` 에는 qtype breakdown 없음. `run_s3_d5_search_stack_eval.py` 가 cross_doc sub-report + raw JSON(per-cell query_type) 보유 → senior-developer 재량으로 **run_s3_d5 메인 채택** + raw JSON 후처리로 qtype별 R@10 산출 (인라인 python, commit 안 함). 명세도 이 선택을 허용("처음부터 그걸 써도 됨").
- 명세: "`--goldenset ../evals/golden_v2.csv`" → 실제 `run_s3_d5` 의 `--goldenset` 은 literal `{v1,v2}` 만 받음 (`v2` → 내부적으로 golden_v2.csv read-only 로드). literal `v2` 사용. (`eval_retrieval_metrics.py` 였다면 path 인자 가능했으나 qtype breakdown 없음.)
- 명세: dry-run 표본 "updated 10 / empty 20" (2026-05-10 30-chunk 표본) → 본 PC 전체(2469) dry-run = updated 64 / empty 2028 / skipped 377. 백필 전 이미 377 chunk 가 entities 보유 (명세 "본 PC 백필 미실행 추정" 과 일부 상충 — 새 ingest 자동 통합 + 이전 부분 실행분으로 보임). 백필로 +64 → 441.
- 명세: "단위 테스트 973 OK" → 본 PC HEAD `0648053` 도 **973 OK**, 추가 8 → 981. 일치.
- 명세: "golden_v2 183 row, n_eval ~176" → 일치 (183 row, chunk-level metric 산출 가능 176).
- 명세: factor ENV 화 위치 = A안 (`search.py` 내 헬퍼) → 그대로 적용. 테스트 위치 = 명세 "test_search.py 또는 entity 테스트 인접" → `test_search_entity_boost_factor.py` 신규 (기존 `test_search_cover_guard.py` 등 네이밍 일관).
- f125 는 시간 여유로 생략, f200 은 추가 (명세 "여유 시 f125·f200" — guard interaction 관찰이 더 유의미해 f200 선택). 어차피 모수 0 이라 f125 추가해도 결과 동일했을 것.

---

## 8. 미해결 / 다음 스코프

- **entity_boost 효과 = 룰 기반 한계로 현 골든셋에선 측정 불가** (모수 ≈ 0). LLM 보강(persons/orgs/products) 통합 후 재측정해야 진짜 효과 판단 가능 — 비용 발생, 사용자 사인오프 필요.
- cross_doc qtype R@10 0.1273 / top-1 0.0 — 여전히 최약. entity_boost 는 레버 아님으로 판명. 별도 접근 필요 (query expansion / multi-doc fusion 튜닝 / golden cross_doc row 라벨 재점검).
- `evals/results/_s4b_ablation_*.{md,json}` 8개 파일 = gitignored, 로컬 보존 (재현용). 정리 불필요(`results/` 통째 gitignored).
- golden_v2 자동 추출 이슈: doc 매칭 fail 1건 (expected_doc_title partial match 실패) — 본 작업 범위 밖, golden CSV 정정은 별도 (쓰기 금지 준수).
- production default 변경(`JETRAG_ENTITY_BOOST=true`) 은 **권고만** — 사용자 사인오프 전까지 OFF 유지. `_ENTITY_BOOST_FACTOR` ENV 화는 동작 변경 0 이라 즉시 ship 가능.

### senior-qa 검증 후 보강 (2026-05-12)

- **D6(2026-05-11) baseline vs 본 측정 baseline 차이** — D6 combo a R@10 0.6539 / top-1 0.7386 → 본 측정 R@10 0.6553 / top-1 0.7443 (집계 ±0.005). 단 **per-cell 로는 183 row 중 ~92건의 `predicted_top10` 가 바뀜** (대부분 top-10 내 인접 스왑, 일부 경계 in/out — 집계값만 우연히 상쇄). 원인 = **HF BGE-M3 embed query API 비결정성** (모델 서버 인스턴스·배치·fp 정밀도 차이 → dense query 벡터 미세 변동 → dense_rank → RRF 재정렬 전파; cold/warm 서버 차이 가능). **S4-B 코드 변경(import-time ENV parse)·백필 때문 아님** — entity_boost OFF 면 `entities` 키는 검색 점수에 영향 0 (`_entity_match_chunk` 가 `JETRAG_ENTITY_BOOST` true 일 때만 도달). **회귀 아님.** 같은 세션 내 baseline↔f110 은 byte-identical(0 diff) — 세션 내 결정적, 세션 간 비결정적.
- **별건 트래킹**: HF embed query 비결정성 = 평가셋 측정 재현성 결함 (D6 #A HF embed cold-start 와 연결). 향후 ablation·회귀 측정 신뢰도 위해 query→vector 영구 캐시 또는 self-host embed 검토 — S4-B 범위 밖, 별도 후보.
- **백필 revert 방법** (필요 시): `UPDATE chunks SET metadata = metadata - 'entities' WHERE metadata ? 'entities';` (Supabase SQL). 단 새 ingest 자동 통합분(377)도 함께 지워지므로 주의 — 사실상 영구로 간주.
- `run_s3_d5_search_stack_eval.py` 산출 JSON 의 `env` 필드가 `_COMBO_ENV[combo]` 만 echo (라인 938) → ablation 의 `JETRAG_ENTITY_BOOST*` ENV 가 산출물에 안 찍힘. factor 실제 적용 증거 = f150·f200 의 G-U-019 `predicted_top10` 에 chunk 481(query "12%" ∩ chunk 481 entities)이 top-1 진입 (baseline 엔 없음). 도구가 ablation ENV 도 기록하도록 개선은 별도 후보.
- 정정: f110 = per-cell 변동 0건 / **f150·f200 = 각 1건** (chunk 481 이 ×1.5 부터 이미 top-10 진입 — §3 의 "f200 1건" 은 f150 부터 시작). metric 영향 0.
- `_parse_factor_env` 의 범위 밖 처리 = **default fallback** ("99" → 1.10, 클램프 아님). 단위 테스트 정합.
- production 에서 entity_boost 를 켤 경우 factor 운영 가이드 = `[1.05, 1.20]` 권장 (3.0 × cover guard 0.3 = 0.9 → 가드 무력화 영역. ENV 상한 [0.5,3.0] 은 ablation 용, 코드 변경 불요).
- **사용자 사인오프 권고** (Q-entity 시리즈): "S4-B 룰 기반 entity_boost = 현 골든셋 효과 없음 확정, default OFF 유지, LLM 보강(persons/orgs/products) 통합은 우선순위 낮음 (cross_doc 레버 아님 — query decomposition 강화 / multi-doc fusion 튜닝 / cross_doc golden 라벨 재점검 쪽 ROI 높음). 동의?"

---

## 변경 파일

- `api/app/routers/search.py` — `_parse_factor_env` 헬퍼 + 상수 + `_ENTITY_BOOST_FACTOR` ENV 화 (default 1.10, 동작 변경 0). 주석 갱신.
- `api/tests/test_search_entity_boost_factor.py` — 신규, 8 케이스.
- (DB) chunks.metadata.entities 백필 — 64 chunk 신규 → 441/2469 보유. 마이그레이션 파일 없음 (데이터 변경, 스키마 변경 아님).
- `evals/results/_s4b_ablation_*` — gitignored 측정 산출물 (commit 안 함).
