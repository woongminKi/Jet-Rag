# 2026-05-11 acceptable_chunks LLM-judge 자동 보완 ship

> 프로젝트: Jet-Rag
> 작성: 2026-05-11 (`fba1316` / `c50c9bb` HEAD 기준 후속 — 2026-05-12 진입 핸드오프 §5 1순위 작업)
> 범위: 골든셋 `evals/golden_v2.csv` 의 `acceptable_chunks` empty row 를 Gemini 2.5 Flash judge 로 자동 보완 → Recall@K / R@10 / nDCG 신호 정밀화
> 흐름: senior-planner 명세 → 사용자 결정 4건 확정 → senior-developer 구현 + 단위 테스트 + dry-run → senior-qa 사전 검증 (GO) → paid 실행 → senior-qa 사후 검증 (ship GO)

---

## 0. 한 줄 요약

> golden_v2.csv 의 `acceptable_chunks` empty 37 row 중 선별 23 row 를 Gemini 2.5 Flash judge (JSON mode, temp 0.0) 로 same-doc BGE-M3 cosine top-15 + relevant 강제 포함 candidate 를 0~1 평가 → threshold 0.5·max_count 8 로 12 row 채움 (146→158 filled / 37→25 empty). 실측 cost **$0.1511** (cap $0.30 의 50.4%). 전체 R@10 **0.7088 → 0.6999** (-0.0089, ±0.02 이내 — graded recall 분모 inflation 에 의한 과대평가 보정, MRR 0.6085 불변). 단위 테스트 **930 → 951** (+21 신규, 회귀 0). `relevant_chunks` 무변경. `.bak.20260511` 자동 백업.

---

## 1. 사용자 확정 결정 (senior-planner 미결 Q 중)

| ID | 결정 |
|---|---|
| Q-2 candidate 풀 | (a) 같은 doc 의 BGE-M3 cosine top-15 + `relevant_chunks` 강제 포함 (relevant 는 보내되 결과에서 제외). BGE-M3 free, uvicorn 불필요. |
| Q-4 CSV 쓰기 | in-place 수정 + `git diff` 로 review. 실행 전 `.bak.YYYYMMDD` 자동 백업 (`--backup` default ON). atomic `os.replace`, `utf-8-sig` BOM 유지, `_V2_FIELDNAMES` 14컬럼 순서 유지. |
| Q-1 처리 범위 | 선별 룰 통과 row 전부 + `caption_dependent=true` 우선 정렬. cost cap $0.30, 초과 시 partial ship (fail-close). |
| Q-3 max_count | 8 (`--acceptable-max-count` default) — 분모 inflation 한계 #6 고려 |
| Q-5 build_golden_v2 보존 옵션 | 본 sprint 미포함 — docstring 경고만 추가 |
| Q-6 cross_doc row | doc_id 빈 값 → skip (본 sprint 미포함) |
| Q-7 인터페이스 | `make_acceptable_judge_caller` = generic `(system, user) -> raw str` (multimodal `make_llm_caller` 와 대칭). `evaluate_acceptable` entry 가 prompt build / parse / select 담당 |
| Q-8 model | `gemini-2.5-flash` (multimodal judge 와 동일) |

---

## 2. 변경 파일

### 신규
- **`evals/run_acceptable_chunks_judge.py`** — runner. argparse CLI(`--with-acceptable-judge` / env `JETRAG_ACCEPTABLE_JUDGE` / `--acceptable-judge-model` / `--acceptable-threshold` / `--acceptable-max-count` / `--candidate-top-k` / `--cost-cap-usd` / `--cost-per-row-usd` / `--max-rows` / `--input` / `--output` / `--backup`(BooleanOptionalAction, default ON) / `--dry-run`). 기본(flag 없음) = dry-run (cost 0). row 선별(empty acceptable AND non-empty relevant AND `negative != "true"` AND non-empty doc_id) + 정렬(caption_dependent desc → id asc), candidate 구성(`build_golden_v2.fetch_chunks` → same-doc cosine top-K + relevant 강제 포함, stale relevant idx 자연 제외), `CostGuard` fail-close partial ship + 80% ALERT, 연속 LLM 실패 3회 early stop(`_FailureTrackingCaller` wrapper — raw 호출 실패만 카운트, parse 실패는 정상 빈 결과로 처리), atomic CSV write(`utf-8-sig` + `_V2_FIELDNAMES` 고정), `.bak.YYYYMMDD` 백업, query NFC 정규화, `vision_usage_log` 실측 cost 합산 출력. DI 패턴(`fetch_chunks_fn`/`embed_query_fn`/`judge_call_fn`/`cost_guard` 주입), `main()` 이 `_build_real_deps` 로 실 의존성 주입.
- **`api/tests/test_run_acceptable_chunks_judge.py`** — 12 테스트 (선별 조건 / caption 정렬 / build_candidates top-K+relevant / stale relevant 제외 / run fills+exclude / stale_doc skip / cost guard partial / 연속 실패 early stop / CSV 멱등·BOM·14컬럼·bit-identical / dry-run no-write / default dry-run / source_type 등록). LLM/DB/HF 외부 호출 0 (DI + monkeypatch, env dummy).

### 수정
- **`evals/_acceptable_judge.py`** — `make_acceptable_judge_caller(*, model="gemini-2.5-flash", record_usage=True) -> Callable[[str,str],str]` (`_gemini_common.get_client()` + `with_retry()`, `temperature=0.0` + `response_mime_type="application/json"`, system 을 user content 앞 text part 로, image part 없음, 호출마다 `vision_metrics.record_call(success=True/False, source_type="acceptable_judge", usage=_parse_usage_metadata(...))` graceful, 빈 응답 → RuntimeError) + `evaluate_acceptable(*, query, candidates, judge_call_fn, threshold=0.5, max_count=8, exclude=()) -> list[int]` (candidates empty → `[]`; `build_judge_prompt` → `judge_call_fn` → `parse_judgment(expected_indices=...)` → `select_acceptable` → exclude 제거; LLM 실패/parse RuntimeError → catch → `[]` graceful) 추가. `_DEFAULT_JUDGE_MODEL` / `_USAGE_SOURCE_TYPE="acceptable_judge"` 상수. docstring scope 를 "실 적용: `evals/run_acceptable_chunks_judge.py` (2026-05-11)" 로 갱신. 기존 `JudgedChunk`/`build_judge_prompt`/`_SYSTEM_PROMPT`/`parse_judgment`/`select_acceptable` 무변경 재사용.
- **`api/app/services/vision_metrics.py`** — `_VALID_SOURCE_TYPES` 에 `"acceptable_judge"` 추가 (1줄 + 주석). 미추가 시 `_normalize_source_type` None fallback + warn → cost source_type NULL 기록되므로 필수.
- **`evals/build_golden_v2.py`** — docstring 에 "⚠ `acceptable_chunks` 는 `run_acceptable_chunks_judge.py` 로 LLM 보완됨 — 본 스크립트 재실행 시 보완분 소실, `.bak.YYYYMMDD` 또는 judge 재실행으로만 복구. `--preserve-acceptable` 미구현." 경고 추가.
- **`api/tests/test_acceptable_judge.py`** — `evaluate_acceptable` 6 + `make_acceptable_judge_caller` 3 테스트 확장 (LLM mock / config 검증(temperature 0.0 + JSON mode) / image part 없음 / record_call 1회 + source_type / graceful / 빈 응답 RuntimeError).

`git diff --stat`: `vision_metrics.py +3 / test_acceptable_judge.py +161 / _acceptable_judge.py +130 / build_golden_v2.py +8 / golden_v2.csv ±12 row` = 5 files, 309 insert / 17 delete. untracked: `run_acceptable_chunks_judge.py`, `test_run_acceptable_chunks_judge.py`, `evals/golden_v2.csv.bak.20260511`.

---

## 3. paid 실행 결과 (`cd api && uv run python ../evals/run_acceptable_chunks_judge.py --with-acceptable-judge`)

- `.bak` 백업: `evals/golden_v2.csv.bak.20260511`
- 선별 **23건** = empty 37 − (no_relevant 2 + no_doc_id 7 + negative 5). skip 분류 합 = already_filled 146 + 14 + selected 23 = 183 전수.
  - caption_dependent=true 우선 9건: G-A-011, G-A-100, G-A-200, G-A-203, G-A-205, G-A-207, G-A-209, G-A-210, G-U-016 + plain 14건 (id asc)
- 처리 23 / **채움 12 / `[empty]` 11** (judge 가 모든 candidate < threshold)
  - filled: `G-A-100=80` / `G-U-016=64,65,93,94,101,126,166` / `G-A-012=3` / `G-A-066=4,5,6,15,18,19,21,24`(max_count 8 cap) / `G-A-101=7,60,83,86,88` / `G-A-105=65` / `G-U-001=44,53` / `G-U-002=42,56` / `G-U-004=2,44,45,100,119` / `G-U-011=0,9,150,151,153,173` / `G-U-021=0` / `G-U-023=2`
  - `[empty]`: G-A-011, G-A-200, G-A-203, G-A-205, G-A-207, G-A-209, G-A-210 (전부 데이터센터 huge doc 의 table_lookup/exact_fact/vision_diagram), G-A-013, G-U-013, G-U-020, G-U-022
- `partial=False`, `consecutive_fail_stop=False`. 1회 `503 UNAVAILABLE` (G-U-023 attempt 1/3, "high demand") → `with_retry` 자동 복구.
- **실측 cost $0.1511** (`vision_usage_log` source_type=`acceptable_judge` 합산) / 추정 $0.1150 / cap $0.30 의 50.4%.
- golden_v2.csv: acceptable filled 146 → **158** (+12), empty 37 → **25** (-12).

---

## 4. 검증 (senior-qa 사후, ship GO)

| # | DoD | 결과 | 판정 |
|---|---|---|---|
| ① | acceptable 채워진 row +12 | 146→158, empty 37→25 | PASS |
| ② | R@10 ±0.02 | ΔR@10 −0.0089, ΔMRR 0.000, ΔnDCG −0.0053 | PASS |
| ③ | 단위 테스트 951 / 회귀 0 | `cd api && uv run python -m unittest discover tests` → 951 OK (930 + 신규 21) | PASS |
| ④ | cost ≤ $0.30 | 실측 $0.1511 (50.4%) | PASS |
| ⑤ | git diff = acceptable_chunks 컬럼만 + relevant_chunks 0줄 | 정확히 12 row, 7번째 컬럼만, relevant 포함 다른 13컬럼·다른 171 row 불변, BOM·14컬럼 유지 | PASS |

R@10 변동 상세 (reranker OFF, n=158, backup `.bak.20260511` vs 현 golden_v2.csv):
- BEFORE: R@10 **0.7088** / MRR 0.6085 / nDCG@10 0.6484
- AFTER:  R@10 **0.6999** / MRR 0.6085 / nDCG@10 0.6431
- 변경 12 row 중 R@10 하락 5건: G-A-066 1.000→0.500 / G-U-016 1.000→0.778 / G-U-004 1.000→0.778 / G-U-001 1.000→0.875 / G-A-012 1.000→0.667 — 나머지 7건 R@10 불변(nDCG만 소폭). caption_dependent subset R@10 거의 불변 (채워진 건 G-A-100·G-U-016 뿐, claim B 측정 보존).
- **방향 해석 (정상)**: acceptable 추가 → graded recall(relevant 1.0 / acceptable 0.5) 분모↑ → 검색이 acceptable chunk 를 top-10 에 못 넣은 row 가 더 엄격히 채점됨 = 기존 R@10 과대평가 보정. MRR 불변(relevant 무변경)이 이를 뒷받침. senior-qa 가 하락 5건 새 acceptable 값 실 chunk 텍스트로 spot-check → noise 주입 정황 없음, judge 가 약간 관대한 경향(G-A-066 max_count cap 닿음, G-U-016 marginal 개정문 chunk 포함).

---

## 5. 남은 이슈 / 한계

- **#A caption_dependent 7건 `[empty]`** — 전부 데이터센터 huge doc (~1000 chunk) 의 table_lookup/exact_fact/vision_diagram. exact_fact·table_lookup 류는 "relevant chunk = 유일 정답, 주변 acceptable 아님" 이 자연스러우나, huge doc 에서 candidate top-15 cosine 이 진짜 acceptable 후보를 놓쳤을 가능성도 배제 못 함. → 후속 개선 후보 (P3): `--candidate-top-k` 확대 (15→30+) 또는 `/search` 기반 후보 생성 (dense+sparse+RRF, cosine-only 보다 huge doc robust). 현재로는 R@10 / claim B 영향 무시 가능.
- **#B judge 관대 경향** — G-A-066 이 `max_count=8` cap 에 닿음, G-U-016 에 약한 부칙 개정문 chunk 포함. 향후 보완 라운드에서 `threshold 0.5→0.6` 상향 또는 `max_count 8→5` 검토 후보.
- **#C `golden_v2.csv` 재실행 위험** — `build_golden_v2.py` 직접 재실행 시 LLM 보완 12 row 소실. `.bak.20260511` 또는 `run_acceptable_chunks_judge.py` 재실행으로만 복구. `build_golden_v2.py` docstring 에 경고 추가됨. (영구 가드 = `--preserve-acceptable` 옵션은 미구현, Q-5 별도 sprint.)
- **#D `eval_retrieval_metrics.py` cosmetic 버그 (무관)** — 출력 markdown 헤더가 `--goldenset` 인자 무시하고 항상 `골든셋: evals/golden_v0.4_sonata.csv` 인쇄 (실제 측정은 전달 CSV). 본 작업 무관, 별도 처리 후보.
- **#E 503 UNAVAILABLE transient** — Gemini "high demand" 일시 장애 1회, `with_retry` 로 자동 복구됨. quota 문제 아님. 대규모 보완 시 retry/backoff 충분한지 모니터.

### 핸드오프 수치 정정 (2026-05-11 종합 핸드오프 §5 / §3.3 / 부속 work-log)
- golden_v2 row: ~~162~~ → **183** (origin merge `01e69e1` 로 cross_doc/safety-branch row 누적 추가됨 — `git log -- evals/golden_v2.csv` 의 `71c169b feat: golden_v2 +5 cross_doc row (G-A-124~128) — safety branch append` 등)
- acceptable empty row: ~~30~~ → **37** (judge 실행 전) → **25** (실행 후)
- 단위 테스트 skipped: ~~1~~ → **0** (현 main `fba1316` 기준 951 모두 PASS)

---

## 6. 다음 스코프 (2026-05-12+ 진입 시)

핸드오프 §5 우선순위 갱신:
1. ~~acceptable_chunks LLM-judge 자동 보완~~ → **본 문서로 완료** (12 row 채움, $0.1511, ΔR@10 −0.0089)
2. **(승격) multimodal judge 샘플 확대** — caption=true 17 row 전체 multimodal 처리, cost ~$0.03, paired t-test / effect size 로 Phase 10 효과 일반화
3. **(신규) acceptable judge 2차 라운드** — #A (huge doc candidate 풀 확대) + #B (threshold/max_count 튜닝) 적용 후 caption_dependent 7건 `[empty]` 재시도, cost ~$0.05
4. chunk text augmentation (S4-A D6 후보)
5. entity_boost factor ablation (S4-B 후)
6. G-A-208 multimodal judge timeout 진단 (한계 #14)
7. 실 BGE-reranker 측정 (D6) / S4-B 본 PC 재검증

새 미결 (사용자 확인):
- **Q-acceptable-judge-2nd** 2차 라운드 진입 시점 + candidate 풀 방식 (top-K 확대 vs /search 기반) + threshold/max_count 조정값 (paid ~$0.05 사전 승인)
- **Q-golden-bak-policy** `golden_v2.csv.bak.YYYYMMDD` 커밋 포함 vs 로컬만 (본 ship 에서는 — 아래 §7 참조)

---

## 7. ship 메모

- main HEAD: ship 전 `c50c9bb` (= `fba1316` + docs 커밋). 본 ship 후 커밋 1건 추가 예정.
- 커밋 포함: `vision_metrics.py`, `test_acceptable_judge.py`, `_acceptable_judge.py`, `build_golden_v2.py`, `golden_v2.csv`, `run_acceptable_chunks_judge.py`(new), `test_run_acceptable_chunks_judge.py`(new), 본 work-log. `golden_v2.csv.bak.20260511` 은 senior-qa 권고(롤백 안전망 + before/after baseline)에 따라 포함.
- `evals/results/_before_acceptable_judge.md` / `_after_acceptable_judge.md` 는 `evals/results/.gitignore` (`*`) 로 이미 무시됨 — 커밋 자동 제외.
- push 는 사용자 요청 시.
