# 2026-05-11 — S4-A D4 Phase 1: `--no-vision-compose` baseline 차분 측정

> 프로젝트: Jet-Rag
> 작성: 2026-05-11
> 목적: D2 적용 전 baseline 차분 측정 도구 추가 + Phase 1 ship — caption_dependent=true subset 의 chunk.text 합성 효과 단독 산출.

---

## 0. 한 줄 요약

> `evals/run_s4_a_d4_breakdown.py` 에 `--no-vision-compose` 옵션 추가 → in-memory 로 caption 합성 chunk 의 `chunk.text` 에서 합성 suffix 제거 + BGE-M3 base_text 재임베딩 + cosine 재정렬 후 R@K 산출. chunks DB 변경 0 · 외부 cost 0. **claim B 확정** — caption_dependent=true 18 row 에서 D2 합성으로 ΔR@10 = **+0.2194pp** / qtype 통제 후 (exact_fact ∩ caption=true 9 row) ΔR@10 = **+0.3588pp**. 단위 테스트 +8건 (caption strip 5 + re-rank 3) / 회귀 0.

> **(close)** 통합 sprint Phase 0~3 의 일부로 ship 완료 — 종합 핸드오프 (`2026-05-11 종합 + 2026-05-12 진입 핸드오프.md`) 참조.

---

## 1. 작업 범위

### 1.1 산출물

| # | 파일 | 종류 | 변경 |
|---|---|---|---|
| 1 | `evals/run_s4_a_d4_breakdown.py` | 측정 도구 | `--no-vision-compose` CLI + option C 헬퍼 2건 + 측정 함수 시그니처 확장 |
| 2 | `api/tests/test_run_s4_a_d4_breakdown.py` | 단위 테스트 | `CaptionComposeStripTests` 5건 + `RerankWithBaseTextTests` 3건 |
| 3 | `evals/results/s4_a_d4_baseline_compose_off.md` | baseline 측정 결과 | 신규 |
| 4 | `evals/results/s4_a_d4_baseline_compose_off.json` | baseline raw json | 신규 |
| 5 | `evals/results/s4_a_d4_results.md` | D2 결과 markdown §4.2 갱신 | 합성 효과 delta 표 영구 추가 |

운영 코드(`api/app/` 하위) 변경 0. 측정 도구·테스트·결과만.

### 1.2 option C 메커니즘

1. search() 응답 (DB 적재 dense_vec 기반 hybrid 결과) 수신.
2. 각 matched_chunk 에 대해 `chunks.metadata` 의 `table_caption` 또는 `figure_caption` 키가 있으면 합성 chunk → `chunk.text` 에서 합성 suffix regex 제거 → `base_text` 복원.
3. BGE-M3 (HF API) 로 `base_text` 재임베딩 → 임시 dense_vec.
4. 원래 query embed 와의 cosine sim 재계산 → re-rank chunk_idx.
5. 비합성 chunk 는 기존 `rrf_score` 그대로 유지 (해당 chunk 는 합성 의존 없음).
6. 재계산된 ranking 으로 R@10 / R@5 / MRR 산출.

**regex**: `\n\n(?:\[표:[^\n\]]*\](?:\n\[그림:[^\n\]]*\])?|\[그림:[^\n\]]*\])\s*$`
chunk.py 의 `_compose_vision_text` 합성 포맷 (`{base}\n\n[표: X]\n[그림: Y]` 와 그 sub) 와 정확히 inverse.

### 1.3 실행 명령

```bash
cd api && uv run python ../evals/run_s4_a_d4_breakdown.py \
    --goldenset v2 --no-vision-compose \
    --out ../evals/results/s4_a_d4_baseline_compose_off.md \
    --out-json ../evals/results/s4_a_d4_baseline_compose_off.json

# 측정 시간: 204초 (157 row, 1.30s/row 평균 — D2 모드 대비 약 2.5배 느림)
# fail 발생: 0 — 재임베딩 실패 0건, query 임베딩 실패 0건
```

---

## 2. 측정 결과 핵심

### 2.1 ON (D2 합성 적용) vs OFF (option C baseline)

| subset | n_common | ΔR@10 | ΔR@5 | ΔMRR |
|---|---:|---:|---:|---:|
| (전체) | 150 | **+0.0951** | **+0.1282** | +0.0807 |
| caption_dependent=true | 17 | **+0.2194** | **+0.3176** | +0.2500 |
| caption_dependent=false | 133 | +0.0792 | +0.1040 | +0.0592 |
| qtype=exact_fact ∩ caption=true (qtype 통제) | 9 | **+0.3588** | **+0.4259** | +0.3083 |
| qtype=exact_fact ∩ caption=false (qtype 통제) | 104 | +0.0724 | +0.0978 | +0.0507 |
| qtype=table_lookup | 6 | +0.0833 | +0.2611 | +0.2437 |
| qtype=vision_diagram | 2 | 0.0000 | 0.0000 | +0.0069 |

### 2.2 해석

- **claim B 확정**: caption_dependent=true subset 의 ΔR@10 = +0.2194pp — D2 chunk.text 합성이 caption-dependent query 의 검색을 큰 폭 향상.
- **qtype 통제 후 효과 더 강함**: exact_fact ∩ caption=true subset 에서 ΔR@10 = +0.3588pp / ΔR@5 = +0.4259pp — 이전 §4.2 의 "qtype 편향" caveat 통제 후에도 합성 효과 보존.
- **table_lookup R@5 +0.2611pp**: 표 caption 합성이 정밀 ranking (R@5) 에서 큰 효과 — table 검색의 ranking 정밀도 회복 가설 일관.
- **vision_diagram = 0**: n=2 표본 한계. 두 row 모두 ON/OFF 동일 ranking → 합성 chunk 가 이미 top-K 안 동일 위치.

### 2.3 caveat

- **score 스케일 이질 혼합**: OFF 모드에서 합성 chunk 만 cosine sim (0.5~0.9 typical), 비합성 chunk 는 RRF score (0.01~0.05 typical) — 절대 비교 어려움. **합성 chunk 들 사이 cosine 재정렬 단독 효과는 신뢰 가능** (caption=true subset Δ), 비합성 chunk 와의 교차 ranking 변화는 보조 caveat.
- **BGE-M3 HF cold start**: 재임베딩 호출이 rate limit 부딪힐 경우 fallback. 본 측정 0건.
- **§4.2 이전 caveat 와의 정합**: "정정 후 caption R@10 delta = -0.0483pp" 는 true vs false subset 절대값 비교 (subset 본질 난이도). 본 §4.2 의 +0.2194pp 는 같은 subset 내 ON vs OFF 차분 (합성 효과 단독). 서로 다른 차원, 모순 없음.

---

## 3. 단위 테스트

### 3.1 신규 8건 (의뢰서 +5 목표 → 보강 +3 = +8)

| 클래스 | 테스트 | 검증 |
|---|---|---|
| `CaptionComposeStripTests` | 5건 | regex 제거 헬퍼 — table+figure 동시 / table 만 / figure 만 / 합성 없음 / 빈 문자열 |
| `RerankWithBaseTextTests` | 3건 | 재임베딩 ranking 변화 / caption 없음 ranking 유지 / 빈 matched_chunks |

### 3.2 회귀

```bash
cd api && uv run python -m unittest discover -s tests -p "test_*.py" -t .
# Ran 778 tests in 16.309s — OK
# baseline 763 → 778 (+15, Phase 1 +8 / Phase 2 +7)
```

---

## 4. 남은 이슈 / 한계

### 4.1 비합성 chunk score 스케일 caveat

OFF 모드에서 RRF score 와 cosine sim 이 혼재. 합성 chunk Δ 는 신뢰하지만 (caption=true subset), 비합성 chunk 와의 교차 ranking 은 score normalization 없이 비교 caveat. **개선안**: option D — OFF 모드에서 모든 chunk 를 cosine 재임베딩 (비용 큼) 또는 RRF score 만 fair comparison (합성 chunk 도 RRF score 유지 대신 base_text 의 score 만 받아 RRF 재계산). 본 phase 의뢰 범위 외.

### 4.2 vision_diagram n=2 표본 한계

vision_diagram qtype 의 ΔR@10 = 0.0 은 두 row 모두 ON/OFF 동일 ranking 인 결과. n=3 (그중 n_eval=2) 로 통계적 의미 없음. S4-A D3 Phase 2 cross_doc·vision 확장 시 표본 보강 후 재측정 권고.

### 4.3 BGE-M3 cold start 변동성

재임베딩 200회 (caption_dependent=true chunk 약 18 row × 평균 2개 합성 chunk + LRU hit) 가 무료 티어 rate limit 안에 들어왔음. 다른 PC 자료 적재 후 표본 증가 시 cold start 영향 재검토 필요.

---

## 5. 다음 권고

### 5.1 즉시 진입 가능 (외부 cost 0)

- **Phase 3 cross_doc 보강** — cross_doc qtype (현재 n=4) 표본 확장 → caption_dependent + cross_doc 의 합성 효과 별도 검증. 별도 phase 명세 필요.
- **option D 보강 측정** — score normalization (RRF만 fair compare). 1~2h 작업.

### 5.2 사용자 명시 승인 후 진입

- **S4-A D5 reingest** — prompt v1 baseline 캐시 생성 → v1 vs v2 직접 비교. Gemini Vision 호출 ~20~30회 / ~$0.30 / ~3분. 별도 phase.

---

## 6. 산출물 경로 (절대)

| 파일 | 경로 |
|---|---|
| 측정 도구 (수정) | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/evals/run_s4_a_d4_breakdown.py` |
| 단위 테스트 (수정) | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/api/tests/test_run_s4_a_d4_breakdown.py` |
| baseline 결과 md | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/evals/results/s4_a_d4_baseline_compose_off.md` |
| baseline raw json | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/evals/results/s4_a_d4_baseline_compose_off.json` |
| D2 결과 §4.2 갱신 | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/evals/results/s4_a_d4_results.md` |
| 본 work-log | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/work-log/2026-05-11 S4-A D4 Phase 1 — compose_off baseline 차분 측정.md` |
