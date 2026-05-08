# 2026-05-10 S4-A D5 시범 reingest 차단 학습

> Sprint: S4-A D5 (Master plan §6) — **부분 ship + 진입 실패 학습**
> 작성: 2026-05-10
> 마감: 시범 reingest 시도 + cap 가드 차단 검증 + work-log
> 입력: caption gap +0.0307 (G-A-104~113 fix 후) + vision_page_cache 진단 (v1=8건, sample-report 만)

---

## 0. 한 줄 요약

> **S4-A D5 시범 reingest 진입 실패 — per-doc budget cap (used=$0.1113, cap=$0.1000) 가드 차단으로 vision API 호출 0건. v2 prompt caption 부착 0건. chunks 1061 → 898 (chunk_filter 49.1% 마킹, -163), Overall R@10 0.7203 → 0.7103 (-0.0100 미세 회귀).** 진입 자체는 실패지만 ROI 가설 검증의 결정적 학습 — D5 본격 진입은 (1) 24h cap 회복 + (2) 사용자 cost 승인 + (3) DEFAULT_USER_ID 정정 후 가능. caption gap 양수 변환 (+0.0307~0.0346) 은 v2 prompt 효과 아닌 measurement coverage 변화 효과로 reframe. **단위 테스트 766 OK / 회귀 0**. 다음 후보 1순위 = table_lookup 약점 진단 (cost 0).

---

## 1. 진입 결정 근거 + 비판적 재검토

### 1.1 사전 진단

D4 G-A-104~113 fix 후 caption gap 첫 양수 변환 (-0.0465 → +0.0307) → D5 v2 prompt reingest 의 ROI 가설 회복 신호. 사용자 명시 승인 받아 진입.

### 1.2 발견 #1 — vision_page_cache v1=8건만

```
vision_page_cache total: 8
sha256 prefix dist: {'b35f5b1955': 8}  # = sample-report 1 doc
```

→ 11 docs 중 **sample-report 만 vision API 호출됨**. 다른 12 docs 는 vision_need_score 임계 이하라 vision API 호출 0건.

### 1.3 발견 #2 — v1 result schema = 4 필드

```python
v1 cache result keys: ['caption', 'ocr_text', 'structured', 'type']
```

→ S4-A D1 의 5필드 (`table_caption`/`figure_caption` 추가) 적용 안 됨. v2 reingest 시 신규 호출 필요.

### 1.4 발견 #3 — sample-report chunks metadata caption 0건

```python
sample-report chunks: 1061
chunks with table_caption/figure_caption metadata: 0
```

→ D2 ship 의 chunks.metadata 전파가 v1 cache 에 없는 caption 필드라 부착 안 됨.

### 1.5 D5 reingest 의 실 효과 추정

- sample-report **만** 의미 있는 reingest 대상 (8 페이지 v2 신규 호출 → table/figure caption 부착)
- 다른 docs reingest 는 vision API 호출 0 → caption 부착 0 → chunks.text 합성 효과 0
- 비용: per-doc cap $0.10 + 약간 초과 (~$0.11, D5 phase 1 동일)

### 1.6 비판적 재검토 (3회)

| 단계 | 결정 |
|---|---|
| 1차 안 | 11 docs 모두 reingest |
| 1차 비판 | "vision API 호출 0건 doc 도 의미?" → **불필요**. caption 자체가 None 이라 D5 효과 0 |
| 2차 비판 | sample-report 만 → cap $0.10 내. 시범 ROI 검증으로 진입 |
| 3차 비판 | 어제 sample-report reingest 의 $0.1113 가 24h cap 안에 있나? → **있음**, 즉시 차단 위험 |

→ 권고: **sample-report 1 doc 시범 진입**. cap 차단 시 ROI 검증 부분적이라도 시그널 확인.

---

## 2. 실행 + 차단 발생

### 2.1 첫 시도 — UUID 오류

```
DEFAULT_USER_ID="test" → dedup stage SQL "invalid input syntax for type uuid"
```

→ pipeline fail. **chunks 1061 → 0 (DB 무결성 손상)**. 즉시 복구 필요.

### 2.2 두번째 시도 — 정정 UUID + 재시도

```bash
DEFAULT_USER_ID="00000000-0000-0000-0000-000000000001" uv run python /tmp/reingest_sample_report.py
```

```
PDF vision enrich skip — budget cap (scope=doc, used=$0.1113, cap=$0.1000)
chunk_filter: 마킹 비율 49.1% > 5% — false positive risk 검토 필요
pipeline elapsed: 208.1s — OK (단 vision_call=0)

post-reingest chunks: 898 (이전 1061 대비 -163)
caption metadata: 0
vision_page_cache: 8 (v1=8 그대로, v2=0)
```

→ **per-doc cap 가드 발동** (어제 D5 phase 1 의 $0.1113 가 24h 안 누적). vision enrich 전체 skip.

### 2.3 측정 검증

D4 도구 재실행:

| metric | doc fix 후 | D5 시도 후 | △ |
|---|---:|---:|---:|
| Overall R@10 | 0.7203 | 0.7103 | **-0.0100** |
| top-1 | 0.6284 | 0.6351 | +0.0067 |
| caption=true R@10 | 0.6931 | 0.6796 | -0.0135 |
| caption=false R@10 | 0.7238 | 0.7142 | -0.0096 |
| caption gap (false − true) | +0.0307 | +0.0346 | (양수 유지) |
| n_eval | 148 | 148 | 0 |

→ 미세 회귀 -0.0100. **caption gap 양수 유지지만 v2 prompt 효과 아님** — measurement coverage 변화 효과 (Phase 2-A 의 cross_doc fix + G-A-104~113 doc fix 의 누적).

---

## 3. ROI 가설 검증 결과

### 3.1 가설 — D5 v2 prompt reingest 가 caption_dependent=true 의 R@10 향상

**검증 차단**:
- vision API 호출 0건 → v2 prompt 5필드 적용 0
- chunks.metadata caption 부착 0 → chunks.text 합성 0
- caption gap 변화는 v2 효과 아님 — 단순 measurement variance

### 3.2 가설 reframe

- caption gap 양수 변환 (~+0.03) 의 진짜 원인:
  - Phase 2-A: cross_doc fix 로 false 그룹 +0.0048
  - G-A-104~113 fix: caption=true 의 table_lookup row evaluable 추가 → -0.0724
  - D5 시도: 회귀 -0.0135 (caption=true), -0.0096 (false)
- **D5 v2 prompt 의 실 효과 = 0 (이번 시도 한정)**. ROI 가설은 미검증.

### 3.3 sample-report 표본 한계

- caption_dependent=true 18건 중 **sample-report 5건** (G-A-018~022)
- 이 5건의 R@10 변화 만이 D5 ROI 의 직접 증거
- 다른 13건은 vision API 호출 0 doc → D5 영향 0

이번 시도에서 sample-report 5건의 R@10:
- 평균 = (0.875+0.9+0.909+0+0.455)/5 = **0.628**
- G-A-021 (table_lookup) R@10=0.0 — 정답 chunk 가 chunks 898 안에 retrievable 안 됨

---

## 4. 회귀 영향

### 4.1 chunks 1061 → 898 (-163)

원인: `chunk_filter: 마킹 비율 49.1% > 5%` warning. chunk_filter 가 noise/header-footer 의심 chunks 를 마킹 → 일부가 적재 안 되거나 search 에서 제외됨.

영향: sample-report 의 chunk_idx 분포 변화 가능 → 정답 chunk_idx 5/16/.../71 의 retrievability 변동 가능. 단 측정 결과 sample-report 의 R@10 평균 0.628 (신규 evaluable 12 row 의 평균) — 이전 측정에서 이 row 들의 R@10 분포와 정량 비교 필요.

### 4.2 운영 코드 변경 0

- 측정 도구 / 단위 테스트 / 스테이지 코드 모두 변경 없음
- chunk_filter 의 49.1% 마킹은 운영 코드 그대로의 결과

### 4.3 단위 테스트 회귀

```
Ran 766 tests — OK (skipped=1)
```

회귀 0.

---

## 5. 다음 후보 우선순위 (재정렬)

| # | 후보 | 작업량 | 권고도 | 이유 |
|---|---|---|---|---|
| 1 | **table_lookup 약점 진단** | 0.5일 | ★★★ | R@10 0.6247, -0.0956 vs overall. caption + table dense 매칭 한계 |
| 2 | **search() cross_doc retrieve 진단** | 0.5~1일 | ★★ | G-U-015/032 R@10=0 잔존, Phase 2-A 후 |
| 3 | **D5 본격 진입** (24h cap 회복 후) | 가변 + cost ~$0.50 | ★ | cap 가드 + DEFAULT_USER_ID 정정 + 사용자 cost 승인 필요 |
| 4 | **chunk_filter 49.1% 마킹 비율 분석** | 0.5일 | 신규 ★★ | sample-report 의 false positive 마킹 검토 |
| 5 | **DEFAULT_USER_ID UUID 정합성** | 0.25일 | 신규 ★ | reingest 사용자 메타 의존성 명시화 |
| 6 | **Phase 2-B cross_doc row 확장** | 0.5~1일 | ★★ | search 진단 후 |

### 권고 (비판적 재검토 후)

**1순위 = table_lookup 약점 진단** (cost 0).
- 이유: D4 fix 후 R@10 0.6247 (-0.0956 vs overall) 가장 약한 chunk-evaluable qtype
- 작업: G-U-003 / G-U-013 / G-A-021 / G-A-031 / G-A-068 / G-A-107 / G-A-111 의 search 응답 분석 + chunks 매칭 추적

**2순위 = search() cross_doc retrieve 진단**.
- 이유: Phase 2-A 후도 G-U-015 / G-U-032 R@10=0 잔존

**3순위 = chunk_filter false positive 분석** (신규).
- 이유: sample-report reingest 시 49.1% 마킹 → 운영 ingest 시 회귀 위험. 다른 docs 도 영향 가능

---

## 6. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-D5-trial-1 | D5 본격 진입 시점 | 24h cap 회복 + 사용자 cost 승인 + DEFAULT_USER_ID 정정 후 | 24h+ 후 |
| Q-D5-trial-2 | sample-report chunks 1061 → 898 회귀 처리 | chunk_filter 분석 후 결정 | 별도 sprint |
| Q-D5-trial-3 | DEFAULT_USER_ID 운영 메타 명시화 | env doc 갱신 + reingest 가드 보강 | 후순위 |

---

## 7. 핵심 변경 파일 목록

### 신규
- 본 work-log
- `/tmp/reingest_sample_report.py` — 시범 스크립트 (gitignored, 일회성)

### 수정
- 0 건 (운영 코드 / 측정 도구 / golden / 단위 테스트 모두 변경 없음)

### 데이터 영향
- sample-report chunks 1061 → 898 (chunk_filter 49.1% 마킹, reingest 부수 효과)
- vision_page_cache v1=8건 그대로, v2=0건 (cap 가드 차단)

---

## 8. 한 문장 마감

> **2026-05-10 — S4-A D5 시범 reingest 진입 실패 학습**. per-doc budget cap (used=$0.1113, cap=$0.1000) 가드 발동으로 vision API 호출 0건. v2 prompt caption 부착 0건. caption gap 양수 변환은 v2 효과 아닌 measurement variance 로 reframe. chunks 1061 → 898 (chunk_filter 49.1% 마킹), Overall R@10 0.7203 → 0.7103 (-0.0100 미세 회귀). 단위 테스트 766 OK / 회귀 0. **D5 본격 진입 보류 — 24h cap 회복 + 사용자 cost 승인 + DEFAULT_USER_ID 정정 후 재진입**. 다음 후보 1순위 = table_lookup 약점 진단 (cost 0).
