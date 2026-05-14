# 2026-05-14 Audit 권고 1 — `query_classifier` 역의존 해소

> 프로젝트: Jet-Rag / commit: `3605dbb` / audit 후속 (senior-developer 리포트)

## 1. 한 줄 요약

- 사용자 질의 "DB 데이터 변동해도 generic 대응 가능?" → senior-developer audit (종합 **7.0/10**, "logic generic + 휴리스틱 임계만 active-corpus 튜닝") 결과 **critical 1건** (admin.py → evals lazy import 역의존) 만 즉시 해소.
- `classify_query_type` 본체 + 의존 상수를 `api/app/services/query_classifier.py` 신규 모듈로 이전. evals 측은 alias re-export 로 backward-compat 유지.
- 권고 2~5 (vision_need_score 임계 ENV / synonym 사전 외부화 / intent_router 명사 확장 / heading pattern 보강) 는 단계적 잡일, 본 sprint 외.
- 단위 테스트 1167 → **1171** (+4 순증가, 회귀 0). `/admin/queries/stats` smoke OK. 1 commit push.

---

## 2. 변경 요약

| 파일 | 변경 |
|---|---|
| `api/app/services/query_classifier.py` | **신규** — single source. `QueryType` + `QUERY_TYPE_LABELS` + `classify_query_type` + 룰 상수 전부 이전. DB·LLM 호출 0, stdlib re+typing 만. |
| `api/app/routers/admin.py` | `_import_classify_query_type()` lazy import 제거. `from app.services.query_classifier import QUERY_TYPE_LABELS, classify_query_type` 직접 import. `sys`/`Path` import 정리. `error_code='classify_unavailable'` path 는 deprecated (Literal 후방호환 유지). |
| `evals/auto_goldenset.py` | `QueryType` / `_QUERY_TYPE_LABELS` / `classify_query_type` 본체 117 line 제거. `from app.services.query_classifier import ...` 후 alias re-export. `from auto_goldenset import classify_query_type` 호출 패턴 그대로 호환. |
| `api/tests/test_query_classifier.py` | **신규** — 9 라벨 룰 분류 검증 11건 (production 모듈 직접). `QUERY_TYPE_LABELS` 9 element + classifier output domain 정합 2건. |
| `api/tests/test_auto_goldenset.py` | `ClassifyQueryTypeTest` 9건 → 본체 test_query_classifier.py 로 이전. `ClassifyQueryTypeReExportTest` 2건만 유지 (alias 동일 객체 sanity). |

> 총 +307 line / -222 line = **+85 line 순증가** (테스트 신규 + alias 패턴 추가). logic line 자체는 -179 (중복 제거).

---

## 3. audit 결과 종합 (senior-developer 리포트 핵심)

### 3.1 종합 판정 **7.0 / 10**

- **로직 generic** (8.5/10) — 인제스트 8-stage / search RRF / adapter layer / content_gate / dedup / tag_summarize 등 핵심 logic 은 doc_id/sha256/title 분기 **0**. **어떤 doc 들어와도 작동**.
- **휴리스틱 임계·사전은 active corpus 튜닝** (5.5/10) — synonym 사전 36 entry, query_expansion 사전, intent_router `_DOC_NOUN` 14 명사 화이트리스트, vision_need_score 5 임계, pymupdf heading font ratio 가 9 doc ablation 기반.
- **그러나 대부분 opt-in default OFF 또는 graceful degrade** → production 실제 영향은 적음.
- **eval ↔ production 결합 1건** (admin.py → evals) 만 critical — **본 sprint 에서 해소**.

### 3.2 심각도별 발견 (audit 표 요약)

| 심각도 | 항목 | 본 sprint 처리 |
|---|---|---|
| ❌ critical (1) | `admin.py:60-70` evals lazy import (역의존) | ✅ **해소** (이번 commit) |
| ⚠️ medium (5) | synonym_dict / query_expansion 사전 / intent_router `_DOC_NOUN` / vision_need_score 5 임계 / pymupdf heading pattern | ⏳ 사용자 결정 후 단계적 (권고 2~5) |
| ℹ️ low (다수) | entity_extract docstring 예시 / content_gate 워터마크 / chunk.py 법령 마스킹 등 | 무영향, 보류 |

### 3.3 분명히 generic 한 영역 (반대 증거)

- `api/app/ingest/stages/*` 8개 stage — 모든 파일 doc_id 분기 0
- `api/app/adapters/impl/*` — Gemini LLM / BGE-M3 / Supabase / PyMuPDF 어댑터 전부 Protocol 기반, W6 OpenAI 스왑 가능 유지
- `api/migrations/*` — schema/RPC/RRF 가 doc 종류 무관 (PGroonga incremental reindex)
- `api/app/services/entity_extract.py:43-75` — 정규식 (날짜·금액·%·ISBN 등) 전부 generic
- 모든 ENV 토글이 default OFF/conservative (TOC_GUARD 만 default ON, 2026-05-10 사용자 명시 승인)

→ **결론**: 데이터 자체가 묶이지 않음. 새 doc 들어오면 검색 품질 KPI (R@10) 가 active corpus 평균보다 약간 낮을 수는 있으나 **기능 회귀 아닌 KPI 회귀**.

---

## 4. 검증

### 4.1 단위 테스트

```
Ran 1171 tests in 31.170s
OK (skipped=1)
```

- 1167 → 1171 (+4 순증가)
  - test_query_classifier.py: +11 신규
  - test_auto_goldenset.py: -9 (ClassifyQueryTypeTest 이전) +2 (re-export sanity)

### 4.2 Smoke

```bash
curl -s "http://localhost:8001/admin/queries/stats?range=7d"
# error_code=None
# total_queries=1000
# distribution keys=['exact_fact', 'fuzzy_memory', 'synonym_mismatch',
#                    'numeric_lookup', 'table_lookup', 'vision_diagram',
#                    'summary', 'cross_doc', 'out_of_scope']
```

이전 `error_code='classify_unavailable'` graceful path 사라짐. 9 distribution key 모두 정상 노출.

---

## 5. 효과

### 5.1 의존성 그래프 정합

```
[before]
  api.app.routers.admin → sys.path.insert("evals/") → import evals.auto_goldenset

[after]
  api.app.routers.admin → app.services.query_classifier  (정방향, production-internal)
  evals.auto_goldenset → app.services.query_classifier   (정방향, evals 가 production import)
```

→ evals 디렉토리 이동/이름변경 시 production 영향 0. 단방향 의존 (`evals → app`) 유지.

### 5.2 결합도 감소

- 본 sprint 이전: production code 의 한 모듈 (admin.py) 이 evals/ 의 함수에 sys.path 의존
- 이후: evals/ 가 production 모듈에 의존 (자연스러운 방향). production 코드는 evals 디렉토리 존재 여부와 무관.

### 5.3 후방호환

- `from auto_goldenset import classify_query_type` 패턴 (기존 호출자: evals 스크립트 다수, 단위 테스트) 그대로 작동
- `auto_goldenset.QueryType`, `auto_goldenset._QUERY_TYPE_LABELS` alias 도 유지

---

## 6. 잔여 audit 권고 (사용자 결정 영역)

| # | 작업 | 작업량 | ROI | 우선순위 |
|---:|---|---|---|---|
| 2 | `vision_need_score` 5 임계 ENV 화 (`Settings` 노출) | 4~6h | 중간 | 새 corpus 들어오면 |
| 3 | `synonym_dict` + `query_expansion` 사전 JSON 외부화 (`data/synonyms_ko.json` + `JETRAG_SYNONYM_DICT_PATH`) | 3~4h | 중간 | 사용자 자료 도메인 확장 시 |
| 4 | `intent_router._DOC_NOUN` 14 명사 화이트리스트 확장 + ENV override | 1~2h | 중간 | 동일 |
| 5 | `pymupdf_parser._HEADING_TEXT_PATTERN` 보강 (영어 학술·SW spec) | 1~2h | 낮음 | 영어 doc 늘어나면 |

권고: **지금은 보류**. 4건 모두 opt-in default OFF 또는 graceful degrade 영역이라 새 doc 들어와도 기능 영향 0. 새 도메인 doc 인제스트 후 KPI 회귀가 실제로 측정되면 그 시점에 ablation 후 단계적 정리.

---

## 7. 본 PRD 잔여 (변동 없음)

- #8 출처 일치율 50건 (~1~2h, 사용자 수동)
- #9 환각률 보강 20건 (~1h, 사용자 수동)
- #6 ② Ragas Context Recall (옵션, paid $0.05~0.15)
- DECISION-12 인제스트 KPI 별도 트랙
- v1.5 영역 (golden_v3 확장 등)
- `.env` BUDGET default 복귀 (~5분 사용자 수동)

---

## 8. 인용 / 참조

- 본 세션 commit: `3605dbb`
- 직전 핸드오프: `22f7412` (KPI #8 revert), `c231f76` (P4)
- audit 리포트: senior-developer 에이전트 (대화 내, 1500 단어, file:line 다수 인용)
- 신규 코드: `api/app/services/query_classifier.py`, `api/tests/test_query_classifier.py`
- PRD master: `work-log/2026-05-12 검색 정확도 80% 달성 PRD.md` **v1.4** (변경 없음)

---

## 9. 다음 세션 시작 시 빠른 확인

```bash
cd /Users/kiwoongmin/Desktop/piLab/Jet-Rag
git log --oneline -3
# 3605dbb refactor(query_classifier): production → evals 역의존 해소 (audit 권고 1)
# 22f7412 revert(evals): KPI #8 자동 채점 작업 되돌림 (사용자 지시)
# 8cd8be7 chore(repo): 위생 — golden_batch_smoke API_BASE env override + bak.20260513 untrack

# 단위 테스트
cd api && uv run python -m unittest discover 2>&1 | tail -2  # 1171 OK

# /admin/queries/stats
curl -s "http://localhost:8001/admin/queries/stats?range=7d" | python3 -c "import json,sys;d=json.load(sys.stdin);print(f\"error_code={d.get('error_code')}\")"
# error_code=None
```
