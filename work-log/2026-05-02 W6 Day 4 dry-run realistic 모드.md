# W4-Q-14 dry-run 리포트 — 새 chunk.py 정책 재청킹 시뮬레이션

> **realistic 모드** (W6 Day 4) — Storage 에서 원본 다운로드 + 파서 실행 → 실 인제스트와 동일한 입력 분포로 chunk policy 통과. DE-65 본 적용 결과 정확 예측.

- user_id: `00000000-0000-0000-0000-000000000001`
- 분석 doc 수: 8
- 현재 총 chunks: 1256
- 모드: `realistic`

## doc 별 비교

| doc | type | 현재 청크 | dry-run 청크 | Δ | 평균 len 현재 | 평균 len 신 |
|---|---|---:|---:|---:|---:|---:|
| jet_rag_sample | md | 0 | err | err | err | err |
| jrag_day4 | md | 0 | err | err | err | err |
| jet_rag_day4_sample | pdf | 6 | 6 | +0.0% | 86 | 86 |
| law sample3 | pdf | 26 | 26 | +0.0% | 206 | 206 |
| sonata-the-edge_catalog | pdf | 99 | 99 | +0.0% | 172 | 172 |
| 직제_규정(2024.4.30.개정) | hwpx | 171 | 171 | +0.0% | 106 | 106 |
| sample-report | pdf | 898 | 898 | +0.0% | 102 | 102 |
| 한마음생활체육관_운영_내규(2024.4.30.개정) | hwpx | 56 | 56 | +0.0% | 73 | 73 |

## 종합

- **현재 총 청크**: 1256
- **dry-run 총 청크**: 1256
- **총 청크 수 Δ**: +0.0%
- **doc 평균 Δ**: +0.0% (median +0.0%, min +0.0%, max +0.0%)

## 합성 시나리오 — doc 별 전체 텍스트를 단일 섹션으로 (worst case)

기존 chunks 를 doc 별로 모두 concat → 단일 ExtractedSection 으로 처리.
파서가 헤딩 구분 못한 worst case (긴 단일 섹션) 시뮬레이션.

| doc | type | 현재 청크 | concat-resplit 청크 | Δ | 평균 len 신 |
|---|---|---:|---:|---:|---:|
| jet_rag_day4_sample | pdf | 6 | 1 | -83.3% | 524 |
| law sample3 | pdf | 26 | 8 | -69.2% | 756 |
| sonata-the-edge_catalog | pdf | 99 | 22 | -77.8% | 859 |
| 직제_규정(2024.4.30.개정) | hwpx | 171 | 25 | -85.4% | 805 |
| sample-report | pdf | 898 | 121 | -86.5% | 855 |
| 한마음생활체육관_운영_내규(2024.4.30.개정) | hwpx | 56 | 6 | -89.3% | 761 |

- worst-case 종합: 1256 → 183 (-85.4%)

## DE-65 게이트

- 명세 §4 AC: 청크 수 변화 < 10% (방향성 단순 시뮬). 실제 재인제스트 전에 사용자 confirm 필요.
- 본 dry-run 한계 — 입력으로 기존 chunks 사용 → overlap 효과가 청크 수 증가에 직접 반영 (기존 chunks 가 이미 적절한 크기). 실제 재인제스트는 파서 출력 (긴 섹션 + 짧은 섹션 혼재) 에서 시작하므로 overlap 도입 효과는 더 작을 수 있음.
