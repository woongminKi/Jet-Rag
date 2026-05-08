# 2026-05-09 S3 D1 intent_router ship

## 작업 내용

senior-planner v0.1 Part B (§3 7 trigger 표 + §7 단위 테스트 9건 매트릭스) 그대로 구현.
룰 기반 query 의도 분류기 모듈 신규 생성. **외부 API 호출 0 / DB 0 / 의존성 추가 0 /
검색 path import 0** — D2 (`/search` `/answer` 통합) 와 D3 (외부 API 보조 라우터)
선행 인프라.

### 변경 파일 (3 신규)

| 파일 | LOC | 역할 |
|---|---|---|
| `api/app/services/intent_router.py` | 253 | `route()` + `IntentRouterDecision` |
| `api/tests/services/test_intent_router.py` | 121 | 9 단위 테스트 |
| `api/tests/services/__init__.py` | 6 | services 서브패키지 marker |

총 380 LOC 신규. 기존 파일 수정 0.

### 7 trigger 매핑

명세 §3 표 그대로 — 본 ship 의 매핑 테이블.

| # | Trigger | 신호 식별자 | 룰 |
|---|---|---|---|
| T1 | cross-doc | `T1_cross_doc` | regex `(자료\|문서\|보고서).{0,15}(랑\|와\|과\|및).{0,15}(자료\|문서)` |
| T2 | 비교 | `T2_compare` | 키워드 OR — 차이 / 비교 / vs / 달라 / 대비 |
| T3 | 인과 | `T3_causal` | 키워드 OR — 왜 / 이유 / 때문 / 원인 / 어째서 |
| T4 | 변경점 | `T4_change` | 키워드 OR — 달라진 / 바뀐 / 변경 / 수정된 / 업데이트 |
| T5 | 긴 query | `T5_long_query` | char ≥ 40 또는 token ≥ 12 |
| T6 | low confidence | `T6_low_confidence` | 모호 표현 — 그거 / 그때 / 그 (trailing space) / 어디였더라 / 뭐였지 / 어떻게 됐더라 |
| T7 | 복수 대상 | `T7_multi_target` | T1 미발화 + `count("랑") + count("과") >= 2` |

`needs_decomposition = (T1 or T2 or T3 or T7) or (T5 and T6)` — T4 / T5 / T6 단독은
분해 불필요.

`confidence_score = max(0.0, min(1.0, 1.0 - 0.15 * len(signals)))` 후
T6 발화 시 추가 `-0.3` cap.

### 단위 테스트 9건 (명세 §7 표 그대로)

| # | 테스트명 | 검증 |
|---|---|---|
| 1 | `test_t1_cross_doc_regex_fires` | "작년 보고서랑 올해 자료 비교해줘" → T1 + decomp True |
| 2 | `test_t2_compare_keyword_fires` | "두 모델의 차이가 뭐야" → T2 + matched("차이") + decomp True |
| 3 | `test_t3_causal_keyword_fires` | "매출이 떨어진 이유 알려줘" → T3 + matched("이유") + decomp True |
| 4 | `test_t4_change_keyword_fires_without_decomposition` | "이번 분기 업데이트 내역 정리" → T4 단독, decomp False |
| 5 | `test_t5_long_query_fires` | 43자 fixture → T5 단독, decomp False |
| 6 | `test_t6_low_confidence_fires_with_penalty` | "그거 어떻게 됐더라" → T6 + confidence ≈ 0.55 |
| 7 | `test_t7_multi_target_fires` | "랑/과" 합 ≥ 2 + T1 미매치 → T7 + decomp True |
| 8 | `test_edge_clean_keyword_returns_no_signals` | "안녕하세요" → 신호 0, confidence 1.0 |
| 9 | `test_edge_empty_query_raises_value_error` | "" / whitespace → ValueError |

#### 테스트 fixture 정정 메모 (구현 중 발견)

초안 fixture 가 키워드 부분매칭으로 인접 trigger 와 동시 발화 → 의도된 단일
신호 검증 실패. 다음 3건 fixture 교체.

1. **T4** — "이번 분기 달라진 점" 의 "**달라**진" 이 T2 키워드 "달라" 부분매칭 →
   "이번 분기 **업데이트** 내역 정리" 로 교체. `assertNotIn("T2_compare", ...)` 가드 추가.
2. **T5** — "...정리해 알려**달라**" 가 T2 "달라" 부분매칭 →
   "...깔끔히 정리해 주세요" (43자) 로 교체. `assertEqual(triggered_signals, ("T5_long_query",))` 로 단독 보장.
3. **T7** — 명세 카운트는 `count("랑") + count("과")` 만 — "민수**와**" 의 "와" 는 카운트 X.
   "사과랑 배 그리고 책상과 의자" 로 교체 (랑 2 + 과 2).

### 회귀

- 단위 테스트 **689 → 698 (+9)** — `unittest discover tests` 14.8s.
- 실패 0, error 0, skip 1 (기존 그대로).
- 운영 코드 수정 0 — `intent_router` 는 어떤 모듈에서도 import 되지 않음 (D2 책임).
- 외부 API 호출 0, DB write 0, 마이그레이션 0.
- 의존성 추가 0 (`re` / `unicodedata` / `dataclasses` 표준 라이브러리만).

### DoD 체크

- [x] `intent_router.py` 신규 + import 가능
- [x] 9 단위 테스트 100% pass
- [x] 단위 테스트 689 → 698 (회귀 0)
- [x] work-log 작성

---

## 남은 이슈

### Q-S3-D1-1 — T6 "그 " trailing space 매칭의 demonstrative 노이즈

명세 §3 의 T6 키워드에 "그 " (trailing space) 가 포함. demonstrative pronoun
"그것 / 그 사람" 같은 정상 발화도 hit 위험. 현재 NFC 정규화 + 다중 공백 단일화
후 substring 매칭으로 처리 — false positive 측정값 없음.

**다음 액션** — D2 통합 후 골든셋 query 60건 + 실 사용 query 100건에 대해 T6
발화율을 측정해 임계 조정 또는 키워드 제거 판단.

### Q-S3-D1-2 — T7 substring 이중 카운트

`count("랑")` / `count("과")` 가 단순 substring 카운트라 "사과" 의 "**과**" 도
카운트. 명세 그대로 구현했지만, 다음 케이스에서 false positive 가능.

- "사과 좋아" — "과" 1회. T7 발화 안 함 (≥2 임계). 위험 0.
- "사과와 배" — "과" 2회 + "와" 0회. T7 발화 — 의미상 정상 (복수 대상).
- "효과적이고 강력한" — "과" 1회 + "력" 무관. 위험 0.

현재까지 발견된 명백한 false positive 없음. 골든셋 측정 후 word boundary 정밀화
필요 시 D3 에서 보완.

### Q-S3-D1-3 — T3 "?" 가산점 미구현

명세 §3 T3 표에 "말미 '?' 가산점" 명시. 본 ship 에서는 신호 발화 자체에는
영향 없음 (가산점만 향후 활용) 으로 처리하고 별도 score 필드 미도입. D3
외부 API 보조 라우터에서 confidence 계산에 통합 시 함께 정의 예정.

### Q-S3-D1-4 — `tests/services/` 서브패키지 분리 영향

기존 `api/tests/` 가 flat 구조 (61 파일) 였는데 본 ship 에서 `services/`
서브패키지 신규. `unittest discover tests` 가 재귀 탐색해 정상 수집되지만
다른 service 테스트 (`test_vision_need_score.py` 등) 는 여전히 flat. **본 분리는
S3 sprint 부터의 신규 service 테스트만 적용**, 기존 파일 이동 0 (회귀 위험 회피).
S5 종료 시 일괄 이전 여부 재검토.

---

## 다음 스코프 (D2 — `/search` `/answer` 통합)

- `app/routers/search.py` / `app/routers/answer.py` 에서 `intent_router.route(query)`
  호출 → `IntentRouterDecision.needs_decomposition` 분기.
- `needs_decomposition=True` 시 sub-query 생성 경로 진입 (현재는 stub —
  D3 에서 외부 API 보조 도입 후 결합).
- 결정 결과 (`triggered_signals`, `confidence_score`) 를 `SearchMetric` /
  `AnswerMetric` 에 logging 필드로 추가 — D5 의 분포 분석 기반 데이터 수집.
- 회귀 — 기존 `/search` `/answer` 응답 schema 변경 0, 동작 변경은 logging only.
- DoD — D1 단위 테스트 698 유지 + D2 통합 후 회귀 0 + 골든셋 60건 baseline 보존.
