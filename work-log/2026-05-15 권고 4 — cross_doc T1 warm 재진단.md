# 2026-05-15 권고 4 — cross_doc T1 warm 재진단

## 배경

2026-05-15 세션 종합 §2.6 의 권고 4 (intent_router._DOC_NOUN 14 명사 확장 ROI) — 직전 진단은 HF cold-start timeout 으로 보류됐던 항목. 본 진단은 intent_router 의 룰 평가만 단독 수행 (네트워크/HF 불요, 비용 $0).

## 측정 방법

`api/app/services/intent_router.py::route()` 를 13 query 에 직접 호출 — 기존 cross_doc 6 query (golden_v2 잔존) + 새 3 도메인 7 query (삼성전자·SK 사업보고서·arXiv 논문 가상 query).

## 결과

| id | T1 | need | signals | query |
|---|---|---|---|---|
| G-U-015 | O | O | T1,T2 | 운영내규**랑** 직제규정에서 위원회 역할 어떻게 달라 |
| G-U-017 | O | O | T1 | 법률 **자료들**에서 원심 파기환송 사례들 어디 있었지 |
| G-U-032 | O | O | T1,T2 | 보건의료 자료**랑** 데이터센터 자료에서 데이터 활용 방식 어떻게 달라 |
| G-A-124 | O | O | T1,T2 | 운영내규**와** 직제규정에서 개정 절차가 어떻게 다른가요? |
| G-A-125 | O | O | T1,T2,T5 | 데이터센터 안내서**와** 보건의료 빅데이터 자료에서 예산 규모가 어떻게 다른가요? |
| G-A-128 | O | O | T1,T5 | law sample2**와** law sample3 두 판결에서 대법원이 내린 결정은 무엇인가요? |
| N-1 | O | O | T1,T2 | 삼성전자**와** SK 사업보고서에서 매출 규모가 어떻게 다른가요? |
| N-2 | O | O | T1,T2 | 삼성 사업보고서**랑** SK 사업보고서에서 영업이익률 비교 |
| N-3 | O | O | T1,T2 | 삼성**과** SK 보고서에서 데이터센터 투자 규모 어떻게 달라 |
| N-4 | O | O | T1,T2 | arXiv 논문**과** 삼성 보고서에서 다루는 주제 비교 |
| N-5 | O | O | T1 | 논문**들**에서 다루는 hep-th 분야 결과 무엇인가요 |
| **N-6** | **X** | **X** | T5 | **paper 1 and paper 2 differ in methodology** (영어-only) |
| N-7 | O | O | T1,T2 | arXiv paper**와** 삼성 사업보고서에서 통계 분석 방식 비교 |

| split | T1 발화 | needs_decomposition |
|---|---:|---:|
| 기존 6 cross_doc | **6/6 (100%)** | 6/6 |
| 새 3 도메인 (한국어 query 6 + 혼합 1) | **6/7 (86%)** | 6/7 |
| 새 도메인 영어-only (N-6 1건) | **0/1** | 0/1 |
| **전체** | **12/13 (92%)** | 12/13 |

## 분석

### 한국어 cross_doc — 사전 확장 ROI 낮음

- 기존 P1 보강 (`95bba0c`, S4-A) 에서 추가된 패턴 (`_T1_CROSS_DOC_PAIR` / `_T1_CROSS_DOC_PAIR2` / `_T1_CROSS_DOC_PLURAL`) 이 `_DOC_NOUN` 16 명사 (`자료|문서|보고서|안내서|규정|내규|이력서|포트폴리오|포폴|템플릿|판결|계획|사업|매뉴얼|카탈로그|논문`) 와 결합해 **새 도메인 한국어 cross_doc 까지 충분히 커버**.
- 삼성전자/SK 사업보고서 query 는 `사업`/`보고서` 명사로 매칭 (N-1, N-3).
- arXiv 학술 query 는 `논문`/`paper` 중 `논문` 으로 매칭 (N-4, N-5, N-7).
- 새 명사 추가 (`주식`, `재무`, `학술` 등) 의 한계 효과는 적으며, false-positive 증가 위험 (일반 명사일수록 `(랑|와|과)` 와 결합한 일반 query 도 cross_doc 으로 잘못 라우팅) 이 더 큼.

### 영어 cross_doc — 한국어 사전 확장으로 해결 불가

- N-6 (`paper 1 and paper 2 differ in methodology`) 는 한국어 어미 (랑|와|과) 부재 → `_T1_CROSS_DOC*` 4 regex 모두 미매칭.
- 해결책은 `_DOC_NOUN` 한국어 명사 추가가 아니라 **영어 cross_doc pattern (and/vs/between … paper/document/study) 별도 신설**.
- 사용자 corpus 가 한국어 위주 (arXiv 영어 1건 / 한국어 11건) 이므로 우선순위 낮음 → **v1.5 영어 자산 확장 시 별도 작업**.

## 결정

- **권고 4 (`_DOC_NOUN` 한국어 명사 확장) 채택 보류** — ROI 실측 낮음 (한국어 cross_doc 12/12 발화 = 100%).
- 현재 사전 (16 명사) 유지, 변경 없음.
- 영어 cross_doc pattern 추가는 별도 sprint (v1.5).

## 회귀

- 코드 변경 없음 → 회귀 0.

## 인용 / 참조

- audit 출처: `work-log/2026-05-15 세션 종합 — 데이터 정리 + audit + robustness + 새 도메인 진단 핸드오프.md` §2.6
- intent_router 위치: `api/app/services/intent_router.py:64-87`
- P1 보강 원래 commit: `95bba0c` (S4-A P1 cross_doc 커버리지 보강)
