# 2026-05-07 D3 진입 — E1 sprint 신설 + plan 작성

> 프로젝트: Jet-Rag
> 작성일: 2026-05-07 (계속 업데이트되는 마스터)
> 작성자: Claude (Explore + senior-planner 협업, senior-developer 미진입)
> 목적: 오늘(2026-05-07) Jet-Rag 작업의 종합 마스터. 추가 작업 발생 시 새 파일이 아니라 본 문서에 계속 업데이트.

---

## 0. 한 줄 요약

> **다른 컴퓨터 진입 baseline 회복 완료 (단위 테스트 460/0 OK, Supabase 마이그 3개 테이블·15컬럼 정합)** + **사용자 ETA 보고 (어제 PDF "3분" 표시 vs 실측 6~7분) 를 신규 sprint E1 으로 격상** — Explore 로 코드 정독 → senior-planner 로 plan 작성 → 별도 파일 ship. 문제 PDF 가 다른 컴퓨터에 있어 진단·구현은 다른 컴퓨터에서 진입. 본 컴퓨터에선 plan + SQL 준비까지 ship.

---

## 1. 오늘 ship 누적

### 1.1 timeline

| 시점 | 작업 | 효과 |
|---|---|---|
| 진입 | 2026-05-06 핸드오프 정독 | D1~D2 누적 마스터 (17 commit) 파악, 다음 후보 (D2-B / D4 / S1 D5) 확보 |
| baseline | `uv run python -m unittest discover tests` | **460 통과 (skipped 7) — 회귀 0** |
| baseline | Supabase MCP `execute_sql` ×2 | vision_usage_log / search_metrics_log / vision_page_cache 3개 테이블 + 15컬럼 정합 ✅ |
| 사용자 보고 | "어제 PDF 업로드 ETA 3분 → 실측 6~7분" | 신규 sprint E1 격상 결정 |
| 메모리 | `project_e1_eta_latency.md` + MEMORY.md 인덱스 1줄 | 다른 컴퓨터 진입 작업 문서마다 E1 항목 누락 방지 (사용자 명시 요청) |
| Explore | ETA + 인제스트 timing 코드 정독 | 백엔드 `eta.py:133`, 9단계 직렬, vision 페이지 순차 — 부정확 3대 가설 + latency 가설 + 측정 SQL 초안 5건 |
| senior-planner | E1 plan 작성 | 목표/DoD, 진단 SQL 5건, 개선 후보 7개 (E1-A1~A7), 1차/2차/3차 ship, 회귀·정합성, 사용자 결정 5건 |
| ship | `work-log/2026-05-07 E1 인제스트 ETA latency sprint plan.md` 신규 | 다른 컴퓨터 1장 진입용 — §10 진단 결과 칸은 reingest 후 채움 |

### 1.2 변경 파일

| 종류 | 경로 |
|---|---|
| 신규 (work-log) | `work-log/2026-05-07 E1 인제스트 ETA latency sprint plan.md` |
| 신규 (work-log) | `work-log/2026-05-07 D3 진입 - E1 sprint 신설 + plan 작성.md` (본 문서) |
| 신규 (memory, git 추적 X) | `~/.claude/projects/.../memory/project_e1_eta_latency.md` |
| 갱신 (memory, git 추적 X) | `~/.claude/projects/.../memory/MEMORY.md` |

### 1.3 코드 변경

없음 — plan 단계, 구현 미진입.

---

## 2. baseline 검증 결과

### 2.1 단위 테스트

```text
Ran 460 tests in 14.837s
OK (skipped=7)
```

D1~D2 누적 460 통과 유지. 회귀 0.

### 2.2 Supabase 마이그 정합성

```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('vision_usage_log', 'search_metrics_log', 'vision_page_cache')
ORDER BY table_name;
-- 결과: search_metrics_log, vision_page_cache, vision_usage_log (3 rows ✅)
```

```sql
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'vision_usage_log'
ORDER BY ordinal_position;
-- 결과: 15 컬럼 (call_id, called_at, success, error_msg, quota_exhausted, source_type,
--        doc_id, page, prompt_tokens, image_tokens, output_tokens, thinking_tokens,
--        retry_attempt, estimated_cost, model_used) ✅
```

마이그 005~008/014/015 모두 적용 상태 확인 → 다른 컴퓨터에서도 동일.

### 2.3 환경 변수

사용자 보고: `.env` 값 모두 추가 완료. 키 항목 (확인용):
- `JETRAG_PDF_VISION_ENRICH=true`
- `JETRAG_GEMINI_RETRY=3` (D2-C 후 503 회복용)
- 기타 SUPABASE / GEMINI / HF / OPENAI 키

---

## 3. E1 sprint 신설 — 핵심 (상세는 별도 plan 파일)

### 3.1 트리거

사용자 보고 — 어제(2026-05-05) PDF 업로드 시 화면 "남은 시간 3분" 표시 → 실측 6~7분. ETA / 실측 ratio ≈ 0.4 (2배 어긋남) + latency 자체 부담.

### 3.2 plan 핵심 요약

- **목표**: ETA 표시/실측 ratio **0.7~1.3** (50p PDF), 50p PDF p50 **≤ 3분** / p95 **≤ 5분**
- **부정확 3대 원인 가설**: ① vision 페이지 수 변동성 미반영, ② cold start fallback (extract=5000ms) 부족, ③ 5분 TTL 캐시 진부화
- **latency 가설**: vision 페이지 순차 (concurrency=0), 503 retry 백오프 누적, vision_page_cache lookup 미통합
- **개선 후보 7개**: E1-A1 (ETA 공식 분해, 1순위) / E1-A2 (페이지 동시 호출, 2순위) / E1-A3 (vision_page_cache lookup = D2-B 흡수, 3순위) / E1-A4 (TTL 단축, 5순위) / E1-A5 (fallback 정정 + sample<3 ETA 미노출, 4순위) / E1-A6 (백오프 cap, 6순위) / E1-A7 (SSE, deferred)
- **권고 ship 순서**: 1차 = 진단 + E1-A1+A4+A5 (정확도 핵심, 1.5~2일) → 2차 = E1-A3+A2 (latency, D2-B 흡수, 2~3일) → 3차 = 옵션·deferred

### 3.3 별도 plan 파일

→ [`2026-05-07 E1 인제스트 ETA latency sprint plan.md`](./2026-05-07%20E1%20%EC%9D%B8%EC%A0%9C%EC%8A%A4%ED%8A%B8%20ETA%20latency%20sprint%20plan.md)

별도 파일에 진단 SQL 5건 (S1~S5), 9단계 진입 절차, §10 진단 결과 채움 칸 포함.

---

## 4. 남은 이슈 (다른 컴퓨터에서 수행)

### 4.1 즉시 (E1 진단)

1. `git pull` 로 본 work-log + plan 파일 동기화
2. 어제 6~7분 걸린 PDF 1건 재업로드
3. **시작 직전** 화면 ETA + **시작·종료 시각** wall-clock 메모
4. T+1m / T+3m / T+5m 화면 ETA 캡처 (S5)
5. 종료 후 plan 파일 §3 의 SQL S1~S4 paste → plan 파일 §10 에 결과 기록
6. plan 파일 §10.4 의 1차 ship 진입 결정 체크리스트 진행

### 4.2 사용자 결정 필요 (1차 ship 진입 전)

| # | 항목 | senior-planner 권고 default |
|---|---|---|
| 1 | 정확도 vs latency 우선순위 | (a) 정확도 P0 |
| 5 | 첫 인제스트 ETA 미노출 + 카피 | (b) "처음에는 시간 추정이 부정확합니다" |

(2·3·4번은 ship 중 default 로 진행 후 review 시 조정)

### 4.3 후속 sprint (E1 후)

E1 1차 ship 후 권고 순서:
- E1 2차 ship (D2-B 흡수, latency 본진입)
- S1 D5 (2.5-flash vs 2.5-flash-lite 골든셋 회귀)
- D4 cost cap

---

## 5. 다음 스코프 (E1 외)

E1 진입 중에도 병렬 가능한 작업:
- master plan §6 의 S1 D2 (자동 골든셋 100+ 확장) — Gemini quota 의존, E1-A2 의 concurrency 검증과 quota 충돌 가능 → **E1 1차 ship 후 진입 권고**
- 기획서 §13 KPI 에 신규 항목 "ETA 표시/실측 ratio" 추가 (E1 1차 ship DoD)

---

## 6. 활성 한계 (sprint 진입 전 점검)

| # | 한계 | 영향 | 회복 절차 |
|---|---|---|---|
| 1 | E1 진단 미완 | 1차 ship 목표 수치 (ETA ratio 0.7~1.3) 가설값 | 다른 컴퓨터에서 PDF 1회 reingest → plan 파일 §10 채움 |
| 2 | 503 fail rate 검증 부족 | vision 인제스트 부분 실패 가능 | E1 진단 S3 (retry_attempt 분포) 동시 검증 |
| 3 | vision_page_cache lookup 미통합 (D2-B) | reingest 시 비용 0 미달성 | E1-A3 = D2-B 흡수 ship 시 해소 |
| 4 | 단가 dict 가격 변경 추적 | estimated_cost 부정확 가능 | 분기별 https://ai.google.dev/pricing 재확인 |

---

## 7. 참고 문서 우선순위

| # | 문서 | 목적 |
|---|---|---|
| 1 | **본 문서** | 2026-05-07 종합 마스터 (계속 업데이트) |
| 2 | [`2026-05-07 E1 인제스트 ETA latency sprint plan.md`](./2026-05-07%20E1%20%EC%9D%B8%EC%A0%9C%EC%8A%A4%ED%8A%B8%20ETA%20latency%20sprint%20plan.md) | E1 plan 본문 + 진단 SQL + §10 reingest 결과 칸 |
| 3 | [`2026-05-06 D1~D2 누적 + 다른 컴퓨터 종합 진입 핸드오프.md`](./2026-05-06%20D1~D2%20%EB%88%84%EC%A0%81%20+%20%EB%8B%A4%EB%A5%B8%20%EC%BB%B4%ED%93%A8%ED%84%B0%20%EC%A2%85%ED%95%A9%20%EC%A7%84%EC%9E%85%20%ED%95%B8%EB%93%9C%EC%98%A4%ED%94%84.md) | 어제까지 baseline (D1~D2 ship 누적) |
| 4 | [`2026-05-06 무료유료 모델 전략 통합 plan + 다른 컴퓨터 핸드오프.md`](./2026-05-06%20%EB%AC%B4%EB%A3%8C%EC%9C%A0%EB%A3%8C%20%EB%AA%A8%EB%8D%B8%20%EC%A0%84%EB%9E%B5%20%ED%86%B5%ED%95%A9%20plan%20+%20%EB%8B%A4%EB%A5%B8%20%EC%BB%B4%ED%93%A8%ED%84%B0%20%ED%95%B8%EB%93%9C%EC%98%A4%ED%94%84.md) | Sprint S0~S5 master plan |
| 5 | [`2026-04-22 개인 지식 에이전트 기획서 v0.1.md`](./2026-04-22%20%EA%B0%9C%EC%9D%B8%20%EC%A7%80%EC%8B%9D%20%EC%97%90%EC%9D%B4%EC%A0%84%ED%8A%B8%20%EA%B8%B0%ED%9A%8D%EC%84%9C%20v0.1.md) | 페르소나·KPI·DoD 마스터 — §13 KPI 에 "ETA ratio" 신규 추가 권고 |

---

## 8. 한 문장 요약

> 2026-05-07 D3 진입 — baseline 회복 + ETA 보고를 E1 sprint 로 격상해 plan 본문 ship. 진단·구현은 PDF 보유 다른 컴퓨터에서 진입, 본 컴퓨터에선 plan + 측정 SQL 준비까지.
