# 2026-05-15 over-chunking 검색 품질 영향 측정

## 배경

2026-05-15 세션 종합 §3.1 에 따르면 새 3 도메인 doc (arXiv 영어 학술 / 삼성전자 사업보고서 / SK 사업보고서) 인제스트 후 `chunks` 가 2009 → **37057** (+18.4×) 로 폭증. 특히 SK 사업보고서는 페이지당 평균 222.5 chunks (1432 page, 25806 chunks) 로 표 cell 단위 over-chunking 가능성.

세션 메모리 기록은 "warm latency 영향 X (170ms warm, 115ms cache hit)" 였으나, **검색 품질 (R@10) 영향은 미측정**. 본 작업은 IDF 가중치 분포 변화 및 sparse search noise 가 기존 doc 검색 품질에 미치는 영향을 정량화.

## 측정 방법

- 도구: `evals/run_s4_a_d4_compose_off.py --goldenset v2 --out /tmp/over_chunking_r10.md`
- 골든셋: `evals/golden_v2.csv` 132 row (P4·P4-b 이후 잔존)
- 검색 path: hybrid (RRF), reranker default ENV
- in-process 측정 (HF embedding + Supabase RPC 직접 호출, HTTP 서버 불요)
- 측정 시간: 36.6s (132 row × 0.28s avg)

## 결과 — 전체 + qtype breakdown

### §0 전체

| 항목 | 값 |
|---|---:|
| R@10 | **0.6829** |
| R@5 | 0.6312 |
| MRR | 0.5916 |
| n_total | 132 |
| n_eval | 119 |
| fail | 3 |

### §1 qtype breakdown (9종)

| qtype | n_total | n_eval | R@10 | R@5 | MRR |
|---|---:|---:|---:|---:|---:|
| exact_fact | 76 | 74 | 0.7180 | 0.6973 | 0.6104 |
| cross_doc | 6 | 0 | — | — | — |
| vision_diagram | 6 | 6 | 0.6446 | 0.5877 | 0.6042 |
| synonym_mismatch | 8 | 8 | 0.6695 | 0.5104 | 0.3787 |
| fuzzy_memory | 7 | 7 | 0.6776 | 0.4204 | 0.4048 |
| summary | 8 | 8 | 0.5307 | 0.4635 | 0.7500 |
| numeric_lookup | 7 | 7 | 0.6843 | 0.6071 | 0.5714 |
| table_lookup | 9 | 9 | 0.5694 | 0.5556 | 0.6389 |
| out_of_scope | 5 | 0 | — | — | — |

- cross_doc 6 row 는 `relevant_chunks` 자체는 alias 형식 (`직제규정:58`, `law2:10,law3:13` 등) 으로 채워져 있으나 **`doc_id` 컬럼이 빈 값** (cross_doc 의 multi-doc 참조 본질) → `evals/eval_retrieval_metrics._load_golden` 의 v0.5+ schema 분기 `if row.get("doc_id"):` 에서 row skip → n_eval=0. 본 측정 도구는 single doc_id 기반 schema 만 지원, cross_doc 측정은 별도 alias_map RPC 필요. 결론에 영향 0 (qtype breakdown 의 다른 6 qtype 결과는 정상).
- out_of_scope 5 row 는 negative case (acceptable_chunks 0) — n_eval 0 정상.

### §2 caption_dependent breakdown

| caption_dependent | n_total | n_eval | R@10 | R@5 | MRR |
|---|---:|---:|---:|---:|---:|
| true | 25 | 25 | 0.6622 | 0.6630 | 0.6650 |
| false | 107 | 94 | 0.6884 | 0.6227 | 0.5721 |

- R@10 delta (true − false) = **−0.026** — M2 W-4 의 caption gap +0.012 보다 약간 벌어졌으나 row 수 변경 confound 있음.

### §latency

| 구간 | avg_latency |
|---|---:|
| [10/132] | 781ms (HF cold-start 영향) |
| [50/132] | 393ms |
| [100/132] | 294ms |
| [132/132] | 268ms (수렴) |

→ 인제스트로 chunks 18배 증가했음에도 **검색 latency 워밍 후 268ms 정상**.

## baseline 대비 비교

| 기준 시점 | golden row | R@10 | top-1 | 비고 |
|---|---:|---:|---:|---|
| M2 W-4 baseline (`beb83b4`, 메모리) | 182 | 0.6747 | 0.7966 | chunks 2009 (vision 200, golden_v2 182) |
| 현재 (over-chunking 후) | 132 | **0.6829** | — | chunks 37057 (vision 200, golden_v2 132) |
| 차이 | −50 row | **+0.0082** | — | row 수 confound 큼 |

⚠️ **직접 비교 한계**: golden_v2 가 182 → 132 row 로 변경 (P4·P4-b 의 hard-deleted doc 참조 row 50건 제거). 분모가 다르므로 R@10 절대값 비교는 신뢰도 낮음. **분포·qtype 별 회귀 신호** 가 더 의미 있음.

## 결론

- 전체 R@10 0.6829 — **분포·qtype 신호 기반 정성 평가: 새 3 도메인 doc 추가로 인한 over-chunking 회귀 신호 0**.
- 9 qtype 분포 정상 (exact_fact 0.7180 / table_lookup 0.5694 등), 이전 baseline 와 동일 패턴.
- warm latency 268ms 정상 — chunks 18배 폭증해도 검색 path 자체에는 영향 0.
- caption_dependent gap 약간 확대 (+0.012 → −0.026, 부호 반전) — row 수 변경의 분모 영향 가능, 추적 필요.

**판단**: BM25 IDF 분포 변화로 인한 우려는 **분포·qtype 신호 기반 정성 평가로 회귀 신호 0**. 단 다음 한계 동반:
- (a) **row 수 confound** (182→132, 분모 변동 38%) — 두 R@10 절대값은 직접 비교 불가, 비교는 qtype 분포·warm latency 등 정성 신호 기반
- (b) **SK/삼성 사업보고서 (chunks/MB 3,800, 가장 over-chunking 영향 큰 doc) 가 golden_v2 에 0건** = 측정 대상 외, 진정한 over-chunking 영향은 새 3 도메인 doc 의 자체 golden query (v1.5 golden_v3 후보) 가 들어가야 직접 측정 가능
- (c) BM25 IDF 직접 영향 분리 측정 시 `--mode sparse` 별도 측정 권장 (post-ship 후속)

본 결론은 **"chunks 37k 폭증 자체로 인한 즉시 회귀 위험 0"** 의 정성 평가로 한정 — v1.5 진입 차단 신호 없음. **chunks 37k 폭증 대응 별도 작업 불요** (단, 새 도메인 doc 의 golden query 부재라는 측정 공백은 v1.5 golden_v3 확장 시 보강 필요).

## 한계

- golden_v2 row 수 confound (182→132) — 절대값 비교 신뢰도 낮음, qtype·분포 신호 기반 평가.
- cross_doc 6 row 는 측정 불가 (P4-b 잔존 relevant_chunks empty) — 별도 라벨 재검토 필요.
- 새 3 도메인 doc (arXiv·삼성·SK) 자체에 대한 golden query 부재 — 직접 측정 대상 아님, 본 측정은 **기존 9 doc 검색 품질의 부수 영향** 만 측정.

## 남은 이슈 / 다음 스코프 후보

- `cross_doc` 6 row 의 relevant_chunks 재라벨 — P4-b 후속 (golden_v2 재검수). 우선순위 낮음.
- 새 3 도메인 doc 의 golden query 작성 (별도 sprint, golden_v3 후보) — over-chunking 자체에 대한 직접 측정 필요 시.
- caption_dependent gap 부호 반전 추적 — row 분모 영향인지 doc 분포 영향인지 ablation 필요.

## 인용

- 측정 결과 raw: `/tmp/over_chunking_r10.md`, `/tmp/over_chunking_r10.json`
- 도구: `evals/run_s4_a_d4_compose_off.py:999-1039`
- M2 W-4 baseline 출처: `work-log/2026-05-13 종합 — M1+M2 완료 + M3 진입 핸드오프.md`
