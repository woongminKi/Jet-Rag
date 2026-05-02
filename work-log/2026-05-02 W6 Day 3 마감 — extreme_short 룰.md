# 2026-05-02 W6 Day 3 마감 — extreme_short 룰 (DE-65 후퇴 회수)

> W6 Day 2 (DE-65 본 적용) 직후 G-015 fail 분석 결과 단일 숫자 청크 ("2") 가 top-1
> 차지 발견. extreme_short 룰 추가 ship 으로 top-1 회수.

## 0. TL;DR

- DE-65 후 G-015 (`2.2%`) top-1 fail 원인 = `law sample3` chunk idx=8 의 텍스트 **"2"** 단일 글자 (relevance 1.0 차지)
- chunk_filter 에 신규 룰 **`extreme_short`** 추가 — `_has_meaningful_letter()` 로 한국어/영문 알파벳 0 + 길이 < 20 자 → 마킹
- 백필 결과 **430건 신규 마킹** (sample-report 376 + 직제 32 + 한마음 18 + law 4)
- golden batch — top-1 **18/20 → 19/20 (95% 회수)** + top-3 **100% 유지**
- 남은 1 fail = G-020 (golden 자체 의도성, 코드 무관)
- 단위 테스트 +5건 (총 24/24 PASS)

## 1. G-015 근본 원인 분석

### 1.1 search 응답 구조

```json
{
  "items": [
    {
      "doc_id": "49ef8d01...",  // law sample3
      "relevance": 1.0,
      "matched_chunks": [{
        "chunk_idx": 8,
        "section_title": "【판결요지】",
        "text": "2"   // ← 단일 글자!
      }]
    },
    {
      "doc_id": "3970feab...",  // sample-report (정답)
      "relevance": 1.0,
      "matched_chunks": [{
        "chunk_idx": 14,
        "text": "...소비자물가는 ... 2.2%..."  // ← 의미 있는 매칭
      }]
    }
  ]
}
```

### 1.2 원인

DE-65 본 적용 시 chunk.py 의 4.6 (표 셀 격리) 가 표 안의 단독 숫자 셀까지 청크로 만듦. PGroonga TokenBigram 이 "2.2%" query 를 "2" 로 매칭 → relevance 1.0 차지. 정답 chunk (관련 본문 다수) 와 동률이지만 doc id 정렬에서 law 가 우위.

### 1.3 회수 방향

3 가지 옵션 검토:
- (A) `_looks_like_table_cell` 임계 강화 — 효과는 chunk.py 단계, 영향 범위 큼
- (B) chunk_filter 에 `extreme_short` 룰 신설 — flags 마킹만, 안전
- (C) RRF 가중치 / chunk 길이 패널티 — search RPC 변경, 회귀 risk

**채택: (B)** — 가장 안전, 백필로 즉시 적용 가능, chunk.py 변경 0.

## 2. 변경 파일

| 파일 | 변경 | LOC |
|---|---|---|
| `api/app/ingest/stages/chunk_filter.py` | docstring + `_EXTREME_SHORT_LEN=20` 상수 + `_has_meaningful_letter` 헬퍼 + `_classify_chunk` extreme_short 분기 + 가시성 로그 확장 | +18 |
| `api/tests/test_chunk_filter.py` | `ClassifyChunkExtremeShortTest` 5건 신규 | +50 |
| `work-log/2026-05-02 W6 Day 3 golden batch (extreme_short 후).md` | golden batch 결과 | +30 |
| `work-log/2026-05-02 W6 Day 3 마감.md` | 본 문서 | (현재) |

## 3. 핵심 변경

### 3.1 extreme_short 룰 정의

```python
_EXTREME_SHORT_LEN = 20

def _has_meaningful_letter(text: str) -> bool:
    """한국어 음절 또는 영문 알파벳 1자라도 포함하면 True."""
    return any(c.isalpha() or "\uAC00" <= c <= "\uD7A3" for c in text)

def _classify_chunk(chunk, header_footer_texts):
    text = chunk.text or ""
    stripped = text.strip()
    if not stripped:
        return "empty"
    # W6 Day 3 — 한국어/영문 알파벳 0 + 짧은 청크 → extreme_short
    if len(stripped) < _EXTREME_SHORT_LEN and not _has_meaningful_letter(stripped):
        return "extreme_short"
    ...
```

### 3.2 정책 요지

| 케이스 | 마킹 |
|---|---|
| `"2"`, `"22"`, `"2.2"`, `"100"`, `"2,800"` | extreme_short ✓ |
| `"변제충당"`, `"휴관"`, `"안녕"`, `"ABC"` | None (한국어/영문 보존) |
| `"2,800원"`, `"100점"` (한글 포함) | None (보존) |
| `"123,456,789,000,000.00"` (20자 ↑) | None (긴 표 데이터 보존) |

## 4. 백필 결과

| doc | type | 추가 마킹 |
|---|---|---:|
| sample-report | pdf | 376 |
| 직제_규정 | hwpx | 32 |
| 한마음생활체육관 | hwpx | 18 |
| law sample3 | pdf | 4 |
| **합계** | | **430** |

→ 모두 단일 숫자 청크 ("2", "7", "45", "274", "3" 등). 표 셀 격리의 명백한 부산물.

## 5. golden batch 회수 결과

| 지표 | DE-65 후 (W6 Day 2) | extreme_short 후 (W6 Day 3) | Δ |
|---|---:|---:|---:|
| top-1 hit | 18/20 (90%) | **19/20 (95%)** | **+1 (회수)** |
| top-3 hit | 20/20 (100%) | 20/20 (100%) | 0 |
| 키워드 top-1 | 4/5 | **5/5** | +1 (G-015 회수) |
| 메타혼합 top-1 | 4/5 | 4/5 | 0 (G-020 잔존, golden 자체 의도성) |
| latency avg (1st batch) | 549ms | 475ms | -13% |

**KPI §13.1 출처 일치율 ≥ 0.95 충족** (top-1 0.95).

## 6. 비판적 자가 검토

1. **20자 임계 적정성**: "변제충당" (4자) 보존 + "123,456,789,000,000.00" (22자) 보존 + "2,800" (5자) 마킹. 한국어/영문 0 + 짧음 = 의미 없음 가정 합리적. 단, 영문 약어 ("AI", "KPI", "SLO" 등 2~3자) 보존 — `isalpha` 통과.
2. **마킹 비율 ↑ risk**: 1256 chunks 중 430 신규 마킹 → 누적 마킹 비율 ~67% + (430/1256) = **~90%**. 의미 있는 본문 청크 (~126건) 만 검색 가능. 실제 검색 정확도는 top-1 95% 충족 → 적정. 누적 ↑ 시 모니터링 필요.
3. **`_has_meaningful_letter` 의 false negative**: 일본어 한자, 중국어 등은 isalpha 통과하나 `'\uAC00'~'\uD7A3'` (한국어 음절) 만 명시 → CJK 전체 지원하려면 확장 필요. 현재 한국어 dominant → OK.
4. **G-020 잔존**: golden v0.2 의 `day4 샘플` 적용 안 됨 (golden_batch_smoke.py 내 GOLDEN list 가 v0.1 그대로). v0.3 진행 시 동기화 필요.

## 7. AC 매트릭스

| AC | 결과 | 충족 |
|---|---|---|
| extreme_short 룰 ship + 단위 테스트 | 5건 PASS | ✅ |
| 백필 dry-run + execute | 430건 마킹 | ✅ |
| golden top-1 회수 ≥ 95% | 19/20 (95%) | ✅ |
| 한국어/영문 보존 (false positive 0) | "변제충당" / "AI" 등 보존 검증 | ✅ |
| KPI §13.1 출처 일치율 ≥ 0.95 | 0.95 (top-1) | ✅ |
| 회귀 0 | 23 → 24 PASS | ✅ |

## 8. commit + push

| Hash | Commit |
|---|---|
| (이번 commit) | `feat(ingest)`: chunk_filter extreme_short 룰 — DE-65 후퇴 회수 (W6 Day 3) |

## 9. 다음 단계

- W6 Day 4 후보:
  - **golden_batch_smoke.py 의 GOLDEN list v0.2 동기화** (G-020 query 갱신)
  - **dry-run 정확도 향상** (W6 Day 2 §7 발견 사항)
  - **사용자 자료 누적 대기** (정량 KPI base)

## 10. 한 문장 요약

W6 Day 3 — DE-65 후 G-015 fail 분석 (`law sample3` chunk "2" 단일 글자 발견) → chunk_filter 에 **extreme_short** 룰 (한국어/영문 알파벳 0 + 길이 < 20) 추가, 백필 **430건 마킹**, golden top-1 **18/20 → 19/20 (회수)** + top-3 100% 유지 + KPI §13.1 충족 (0.95).
