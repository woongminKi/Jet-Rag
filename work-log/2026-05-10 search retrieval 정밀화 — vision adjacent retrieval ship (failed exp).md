# 2026-05-10 search retrieval 정밀화 — vision adjacent retrieval ship (failed experiment 명시)

> Sprint: 1순위 (1~4순위 잔여) — search retrieval 단 정밀화
> 작성: 2026-05-10
> 마감: vision adjacent retrieval (ENV opt-in, default OFF) infra ship — G-A-204 ch 919 회복 효과 0 (failed experiment 정직 명시)
> 입력: 1~4순위 plan 문서 §1 + Batch 1 sprint 3 의 vision adjacent boost 후속

---

## 0. 한 줄 요약

> **vision adjacent retrieval infra ship — failed experiment 정직 명시**.
> `JETRAG_VISION_ADJACENT_RETRIEVAL=true` (opt-in, default OFF) — candidates pool 의 vision-derived chunks 의 같은 (doc, page) 인접 chunk_idx (±1) 를 DB 추가 fetch → rpc_rows + chunks_by_id + cover_guard_meta 통합. synthetic rrf_score 0.001 부여 → vision adjacent boost (factor 0.5) 와 함께 사용 시 인접 chunk 가 candidates 에 진입.
> **Ablation 결과: 0 diff rows, G-A-204 R@10 0.25 변동 X**. 원인: ch 918 (요약표 caption) 도 candidates pool 밖 → 인접 ch 919 fetch 발동 안 됨. 진짜 fix 는 retrieval pool 자체 확장 또는 query expansion (별도 sprint).
> 인프라 ship 채택 — 다른 case 효과 가능. ENV opt-in default OFF 유지. 단위 테스트 861 OK / 회귀 0. 누적 cost 변동 0.

---

## 1. 변경 내역

### 1.1 `api/app/routers/search.py` — vision adjacent retrieval logic

cover_guard_meta 구축 직후 (line ~634) 에 신규 block 추가:

```python
_vision_adj_retrieval_enabled = (
    os.environ.get("JETRAG_VISION_ADJACENT_RETRIEVAL", "false").lower() == "true"
)
if _vision_adj_retrieval_enabled and chunks_by_id:
    # 1) candidates 의 vision-derived chunks 식별
    vision_targets: dict[str, set[int]] = defaultdict(set)
    for cid, c in list(chunks_by_id.items()):
        if not (c.get("section_title") or "").startswith("(vision)"):
            continue
        page = c.get("page"); cidx = c.get("chunk_idx"); did = c.get("doc_id")
        if not did or page is None or cidx is None:
            continue
        for adj in (int(cidx) - 1, int(cidx) + 1):
            if adj >= 0:
                vision_targets[did].add(adj)
    # 2) 인접 chunks DB fetch + 통합
    for did, cidxs in vision_targets.items():
        resp = client.table("chunks").select(...).eq("doc_id", did).in_("chunk_idx", sorted(cidxs)).execute()
        for new_c in resp.data or []:
            # vision-derived 만 통합 (graceful skip)
            if not (new_c.get("section_title") or "").startswith("(vision)"):
                continue
            # chunks_by_id + cover_guard_meta + rpc_rows (rrf_score=0.001) 통합
            ...
```

### 1.2 동작

- vision-derived chunks 가 candidates 에 있으면 인접 chunk_idx 추가 fetch
- 추가된 chunks 도 vision-derived 만 채택 (caption 인접 비-vision chunk 회피)
- rrf_score=0.001 (synthetic 매우 낮음) — vision adjacent boost (별도 sprint, factor 0.5) 와 함께 사용 시 propagation 가능

---

## 2. Ablation 결과 — failed experiment

### 2.1 측정

```bash
JETRAG_TOC_GUARD_ENABLED=true \
JETRAG_VISION_ADJACENT_RETRIEVAL=true \
JETRAG_VISION_ADJACENT_BOOST=true \
uv run python ../evals/run_s4_a_d4_breakdown_eval.py
# 90.2s (BGE-M3 cache hit), R@10=0.7111, top-1=0.8580
```

### 2.2 G-A-204 (목표 case)

| 시점 | R@10 | top10 |
|---|---:|---|
| TOC ON v3 (baseline) | 0.250 | [898, 848, 248, 770, 921, 75, 900, 379, 797, 983] |
| **+va_retrieval +boost** | **0.250** | **(동일)** |

### 2.3 Overall + diff

- diff rows: **0** (전체 178 row 중 변동 0)
- Overall R@10: 0.7111 (변동 0)
- Overall top-1: 0.8580 (변동 0)

---

## 3. 원인 분석 — 왜 효과 0?

### 3.1 가설 vs 실제

**가설**: ch 918 (요약표 caption) 이 candidates pool 에 있으면 → 인접 ch 919 (요약표 데이터 part 2) DB fetch + 추가 → boost 로 propagate 하여 top-10 진입.

**실제**:
- ch 918 도 candidates pool 밖 (top-10 = [898, ...] 에 918 없음)
- candidates pool 자체 (rpc_top_k, default 50) 에 918 없을 가능성
- 인접 fetch (819-921) 발동 안 됨 → 919 추가 X

### 3.2 진짜 root cause

`/search/eval-precision` 같은 retrieval 단의 BM25 + dense 가 ch 918 / 919 를 top-50 밖으로 보냄. 즉 retrieval pool 자체 정밀화 필요:

| 옵션 | 설명 | 작업량 |
|---|---|---|
| A | rpc_top_k 상향 (50 → 100) | ENV 변경, 측정 필요 |
| B | query expansion (요약표 → "요약표 / 표 / 차트 / 도표") | 큰 변경 |
| C | 인덱스 단 augmentation — chunk 919 의 dense vector 에 caption text 가산 | embedding 재생성 |
| D | section/page-level retrieval 추가 path | 큰 변경 |

→ 모두 별도 sprint. 본 sprint scope 외.

### 3.3 ship 결정

infra 자체는 정상 동작 (ENV opt-in, 회귀 0). 다른 case 에서는 효과 가능 (예: ch 918 이 candidates 에 들어오는 query). 향후 retrieval pool 확장 sprint 와 함께 사용 시 G-A-204 회복 가능성.

ENV `JETRAG_VISION_ADJACENT_RETRIEVAL=false` (default) 유지.

---

## 4. 비판적 한계 (정직 인정)

| # | 한계 | 영향 | 회복 |
|---|---|---|---|
| 1 | **G-A-204 회복 효과 0** | 1~2 day sprint 의 핵심 목표 미달 | retrieval pool 확장 / query expansion 별도 sprint |
| 2 | **synthetic rrf_score 0.001 너무 낮음** | boost (×0.5) 로도 top-10 진입 어려움 | factor 강화 또는 절대값 부여 (별도 sprint) |
| 3 | **인접 chunks DB fetch 추가** | 매 search 마다 1~N additional query (latency ↑) | default OFF 로 운영 영향 0 |
| 4 | **vision-derived adjacent 만** | text-only doc 의 인접 chunk 는 미적용 | 의도된 동작 — text-only 는 chunk 단위 grounding 강함 |

---

## 5. 사용자 결정 보류 항목 변동

| ID | 항목 | 직전 | 갱신 |
|---|---|---|---|
| Q-vision-adjacent-retrieval | 인접 chunk retrieval | infra 가능 여부 | **infra ship + failed experiment** — default OFF |
| Q-retrieval-pool-augment | retrieval pool 확장 | 신규 | 별도 sprint (rpc_top_k 상향, query expansion) |
| Q-search-vision-table | vision 표 매칭 (G-A-204) | 별도 sprint | 본 sprint 1순위 미해결 — retrieval pool 별도 sprint 권고 |

---

## 6. 다음 후보 (잔여)

### 6.1 1순위 (이번 sprint 후속) — retrieval pool 확장

- A: `rpc_top_k` 상향 (50 → 100) — 작은 변경, 측정 필요
- B: query expansion — 큰 변경

### 6.2 2~3순위 (1~4순위 잔여)

- 3순위 — multimodal LLM judge (cost ~$0.10~$0.30)
- 4순위 — S4-B 핵심 엔티티 추출 (3 day, master plan)

### 6.3 5~10순위

(사용자 명시 결정 필요 — reingest, master plan 큰 sprint)

---

## 7. 핵심 변경 파일 목록

### 수정
- `api/app/routers/search.py` — vision adjacent retrieval block 추가 (~50 LOC, ENV opt-in default OFF)

### gitignored 산출 (본 work-log §2 reproduced)
- `evals/results/s4_a_d4_toc_on_va_retrieval.{md,json}` — ablation (효과 0 검증)

### 신규 commit 대상
- 본 work-log

### 데이터 영향
- 0 건

### 운영 코드 변경 영향
- ENV opt-in default OFF — 운영 동작 변동 X
- ENV ON 시: search latency ↑ (인접 fetch 추가 query) + chunks pool 확장

### 외부 cost
- 0
- 누적 (이번 세션 전체): ~$0.475 (변동 없음)

---

## 8. 한 문장 마감

> **2026-05-10 — search retrieval 정밀화 sprint ship (failed experiment 명시)**. vision adjacent retrieval infra (`JETRAG_VISION_ADJACENT_RETRIEVAL=true`, default OFF) 추가 — candidates pool 의 vision chunks 인접 fetch + boost propagate. **G-A-204 회복 효과 0** (ch 918 도 candidates pool 밖). 진짜 fix 는 retrieval pool 자체 확장 (rpc_top_k 상향 또는 query expansion) 별도 sprint. infra ship 채택 (다른 case 효과 가능). 단위 테스트 861 OK / 회귀 0. 누적 cost 변동 0.
