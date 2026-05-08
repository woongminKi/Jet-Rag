# 2026-05-09 S3 D2 ship — confidence 안전망 + meta filter fast path

## 1. 요약

S3 D2 (planner v0.1) ship 완료. D1 의 `intent_router.route()` 룰을 `/answer` 라우터에
와이어해 confidence 안전망(`meta.low_confidence`)을 활성화하고, 새 `meta_filter_fast_path`
서비스를 만들어 `/search` 진입 직후 임베딩·RPC·reranker 호출을 건너뛰는 분기를 추가했다.
외부 API 호출 0, 의존성 추가 0, 마이그레이션 0. 단위 테스트 698 → 710 (+12), 회귀 0.

## 2. baseline

- 단위 테스트 시작 시점: **698 / skipped 1 / 회귀 0**
- 12 commits ahead origin/main
- 직전 ship: S3 D1 (`5a41735`) — `IntentRouterDecision` + `route()` 룰 단독, 어디서도 import 0

## 3. 변경 파일

| 파일 | LOC | 종류 |
|---|---|---|
| `api/app/services/meta_filter_fast_path.py` | 440 (신규) | service |
| `api/app/routers/search.py` | +95 | router 분기 |
| `api/app/routers/answer.py` | +20 | router 분기 |
| `api/tests/services/test_meta_filter_fast_path.py` | 152 (신규) | 단위 테스트 |
| `api/tests/test_answer_router_confidence.py` | 107 (신규) | 단위 테스트 |

총 합계 ≈ 814 LOC (코드 555 + 테스트 259).

## 4. D1 schema adapt 결과

planner v0.1 명세는 `decision.intent` / `decision.confidence` 표현을 사용했지만 D1 에서
실제 ship 된 dataclass 는 다른 이름이라 다음과 같이 adapt 했다.

| planner 명세 | D1 실제 | adapt |
|---|---|---|
| `decision.intent` | (없음) | 응답 meta 에는 `triggered_signals` 전체를 노출 (`router_signals`) |
| `decision.confidence` | `confidence_score` | 그대로 사용 — 응답 meta 의 `router_confidence` 필드명만 달리 |

threshold 는 명세 그대로 0.75. answer router 안의 `_LOW_CONFIDENCE_THRESHOLD` 상수로 분리.

## 5. /search 라우터 분기 diff 요약

```python
# 진입 직후 (clean_q 정규화 후) — RAG 흐름 직전.
if doc_id is None and mode == "hybrid":
    plan = meta_filter_fast_path.is_meta_only(clean_q)
    if plan is not None:
        return _run_meta_fast_path(...)
if response is not None:
    response.headers["X-Search-Path"] = "rag"
```

- `doc_id` 명시 또는 `mode != hybrid` 시 fast path 차단 — 사용자가 명시한 의도(단일 문서 스코프 / ablation) 우선.
- fast path 진입 시 응답 헤더 `X-Search-Path: meta_fast`, 응답 본문 `meta = {"path": "meta_fast", ...}`.
- `_run_meta_fast_path` 헬퍼 — documents SELECT 1회 + SearchHit 변환 (matched_chunks=[], relevance=1.0).

## 6. /answer 라우터 분기 diff 요약

```python
router_decision = intent_router.route(clean_q)
answer_meta = {
    "low_confidence": router_decision.confidence_score < _LOW_CONFIDENCE_THRESHOLD,
    "router_signals": list(router_decision.triggered_signals),
    "router_confidence": router_decision.confidence_score,
}
# TODO(S3-D3): if low_confidence and "T1_cross_doc" in signals: call decomposer
```

- 본문 RAG 흐름은 그대로. `AnswerResponse.meta` 필드 신규 추가 (Optional — 기존 클라이언트 호환).
- `_gather_chunks` 결과 0건 path / 정상 응답 path 양쪽에 `meta=answer_meta` 전달.

## 7. meta_filter_fast_path 룰 핵심

### detector — `is_meta_only(query: str) -> MetaFilterPlan | None`

| 신호 | 룰 |
|---|---|
| 절대 날짜 | `YYYY-MM-DD` / `YYYY.MM.DD` / `YYYY년 M월 D일` |
| 월 단위 | `YYYY년 M월` → `[from, next-month-from)` |
| 상대 날짜 | 오늘 / 어제 / 그저께 / 이번주(7일) / 지난주 / 이번달(30일) / 지난달 |
| 태그 | `#한글영문숫자_-` 1자 이상 |
| doc 제목 | doc-suffix(문서·보고서·자료·회의록·기획서·파일·리포트·요약·공문·계약서) |

판정 흐름:
1. 의문/서술 동사구(어떻게·왜·언제·뭐·얼마·할까 등) 잔존 → None.
2. 잔여 토큰이 명령형 stopword(보여줘·찾아·열어·줘 등)만 + 조사 strip → meta-only 확정.
3. (date | tag | title) 중 1개 이상 매칭 시 plan 반환.

회귀 가드 핵심: **doc-suffix 가 없는 한글 명사 단독/구는 fast path 진입 차단**.
기존 단위 테스트의 `q="결론" / "시트" / "소나타 시트 종류" / "공사대금 합의해지" / "테스트" / "테스트쿼리" / "없는단어"`
등은 모두 None 반환 — RAG path 그대로 동작.

### executor — `run(plan, *, user_id) -> list[dict]`

`SELECT id, title, doc_type, tags, summary, created_at FROM documents`
`WHERE user_id=? AND deleted_at IS NULL [+date_range +tags +ilike] ORDER BY created_at DESC LIMIT 20`.

임베딩·reranker·RPC 호출 0. 한 번의 supabase chain 호출.

## 8. 단위 테스트 final count

```
Ran 710 tests in 15.039s
OK
```

698 (S3 D1 baseline) + 12 (S3 D2 신규) = **710 / skipped 1 / 회귀 0**.

신규 12 cases 분포:
- `test_meta_filter_fast_path.py` (10) — detector 6 + regression guard 3 + executor 1.
- `test_answer_router_confidence.py` (2) — low_confidence True / False.

명세 목표 706 (8건) 대비 +4 — regression guard 3 + executor 1 보너스. 모두 pass.

## 9. 회귀 가드 결과

- 단위 테스트 회귀 0 — 698 → 710 모두 OK.
- search 관련 핵심 8 파일 (`test_search_*` + `test_doc_level_rrf` + `test_pgroonga_migration` + `test_reranker`) 단독 실행 85 cases OK.
- 골든셋 R@10 측정은 D5 통합 측정 시점에 진행 — 본 D2 단계에서는 **측정 도구 미가용** 상태로 work-log 에 남기고, 다음 스코프에서 일괄 측정.

## 10. 외부 API / 의존성 / 마이그 영향

| 항목 | 변경 |
|---|---|
| 외부 API 호출 | 0 (intent_router 룰 + 정규식만) |
| 의존성 추가 | 0 (표준 라이브러리만) |
| 마이그레이션 | 0 (documents 테이블 기존 컬럼만 사용) |
| Supabase RLS | 변경 0 (service_role 그대로) |

## 11. 발견 이슈

- `meta_filter_fast_path.run` 의 `get_supabase_client` 를 처음에 함수 안 lazy import 로 두니 `patch.object(meta_filter_fast_path, "get_supabase_client", ...)` 가 안 먹어서, 모듈 상단 import 로 변경. 다른 service 모듈도 동일 패턴이므로 일관성 OK.
- `/search` 시그니처에 `response: Response` 추가 — FastAPI dep 으로 자동 주입. 단위 테스트는 직접 호출 시 None 전달 가능하도록 default 값 부여 (헤더 set 분기에서 None 가드).
- `AnswerResponse.meta` 는 Optional dict — 기존 프론트가 무시해도 호환. 향후 D3 에서 `decomposed_subqueries` 필드 추가 검토.

## 12. 남은 이슈

1. **골든셋 R@10 회귀 측정** — 도구 미가용. D5 통합 측정에서 RAG path 우회 비율과 함께 확인 필요.
2. **fast path 매칭률 텔레메트리 부재** — `meta_filter_fast_path` 진입 횟수·매칭 종류별 카운터를 `search_metrics` 같은 채널에 기록할지 D5 에서 결정.
3. **doc-suffix 화이트리스트 커버리지** — 현재 10개. 운영 query 로그가 쌓이면 누락 suffix 추가.
4. **`/answer` 의 router decision 결과를 `/search` 와도 공유할지** — 본 D2 는 `/answer` 단독 호출. `/search` 도 동일 marking 이 필요한지 D3 에서 검토.

## 13. 다음 스코프 (S3 D3)

- planner v0.1 §D — decomposition. low_confidence + T1_cross_doc 발화 시 외부 LLM 으로 sub-query 분해.
- 본 D2 가 `# TODO(S3-D3)` 주석으로 진입점만 미리 박아둠 (`api/app/routers/answer.py`).
- 외부 API 호출 시작 — Gemini quota 영향 측정 필요. 캐시 정책 사전 정리.
- 정량 baseline (R@10 ±0.01) 도 D3 ship 시점에 함께 측정 (도구 정비).

## 14. 핸드오프

- 본 PC 절대경로: `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag`.
- 다른 PC 진입 시: `git pull` 후 `cd api && uv run python -m unittest discover -s tests -t . -q` 로 710 OK 재확인.
- 커밋 미수행 — 사용자 명시 요청 시에만 commit.
