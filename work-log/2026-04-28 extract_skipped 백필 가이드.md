# 2026-04-28 extract_skipped 백필 가이드

> W2 Day 4 이전에 업로드된 **비-PDF** (HWPX · 이미지 · URL · HWP) 들의 graceful skip 흔적을 정리하는 일회성 운영 절차. 향후 어댑터가 더 추가될 때마다 동일 절차로 재사용 가능.

---

## 1. 배경

W1·W2 Day 1~2 까지는 extract 스테이지가 **PDF 만 처리**, 그 외 포맷은 graceful skip 정책으로 다음 마킹 후 파이프라인 종료:
- `documents.flags.extract_skipped = true`
- `documents.flags.extract_skipped_reason = "doc_type=hwpx 는 아직 지원되지 않습니다 (W2 예정)."`
- `chunks` 0건

W2 Day 3~4 에서 5종 파서 (PDF · HWPX · 이미지 · URL · HWP) 디스패처 등록 → **새 업로드는 정상**, 단 기존 skipped doc 들은 수동 reingest 필요.

---

## 2. 식별 — 백필 대상 SQL

Supabase Studio SQL Editor 또는 MCP `execute_sql` 로 확인:

```sql
SELECT 
  id, title, doc_type, 
  flags->>'extract_skipped_reason' AS skip_reason,
  created_at
FROM documents
WHERE flags->>'extract_skipped' = 'true'
  AND COALESCE(flags->>'failed', 'false') != 'true'
  AND deleted_at IS NULL
ORDER BY created_at;
```

→ `flags.failed = true` 인 doc 는 자동 백필 대상에서 제외 (별도 운영 절차).

---

## 3. 자동 백필 스크립트 — 권장

`api/scripts/backfill_extract_skipped.py` 사용.

### 3.1 사전 점검 (dry-run)
대상 doc list 만 출력, 실제 reingest 호출 안 함:
```bash
cd api
uv run python scripts/backfill_extract_skipped.py
```

출력 예시:
```
백필 대상: 3 건
  - 6004fd65-...  [hwpx ] 사업계획서 v3                          reason: doc_type=hwpx 는 아직 지원되지 않습니다 (W2 예정).
  - 22b611ab-...  [image] 회의 화이트보드 캡처                   reason: doc_type=image 는 아직 지원되지 않습니다 (W2 예정).
  - 61b21b85-...  [url  ] 한국은행 보도자료                      reason: doc_type=url 는 아직 지원되지 않습니다 (W2 예정).

--dry-run (default). 실제 호출하려면 --execute
```

### 3.2 실 실행
API 서버가 실행 중인 상태에서:
```bash
cd api
uv run python scripts/backfill_extract_skipped.py --execute
```

각 doc 마다 `POST /documents/{id}/reingest` 호출 → BG 큐잉 → 8-stage 파이프라인 재실행. 호출 사이 2초 sleep (Vision API 무료 한도 보호).

### 3.3 옵션
- `--doc-id <ID>` 특정 doc 만 (반복 지정 가능): `--doc-id A --doc-id B`
- `--base-url http://...` API 호스트 변경
- `--delay-seconds 5` 호출 간격 조정 (이미지 다수면 5s 권장)

---

## 4. 수동 백필 (Studio 직접)

스크립트 사용 어려운 경우 한 건씩:

1. Supabase Studio 또는 curl 로 식별 SQL 실행 → doc_id list 확보
2. 각 doc 에 대해:
   ```bash
   curl -X POST http://localhost:8000/documents/<DOC_ID>/reingest
   ```
3. `/documents/<DOC_ID>/status` 또는 `/doc/<DOC_ID>` 페이지에서 진행 확인

---

## 5. 백필 후 검증

### 5.1 chunks count 0 → 양수 전이 확인
```sql
SELECT 
  d.id, d.doc_type, 
  d.flags->>'extract_skipped' AS still_skipped,
  COUNT(c.id) AS chunks_count
FROM documents d
LEFT JOIN chunks c ON c.doc_id = d.id
WHERE d.id IN ('<DOC_ID_1>', '<DOC_ID_2>', ...)
GROUP BY d.id, d.doc_type, d.flags;
```

기대값: `still_skipped = NULL` (성공) + `chunks_count > 0`.

### 5.2 `flags.extract_skipped` 정리 (선택)
`reingest` 후에는 `extract_skipped` flag 가 자동으로 제거되지 않을 수 있음 — 보존이 무해해서 그대로 두어도 검색·집계에 영향 없음. 정리하고 싶으면:
```sql
UPDATE documents
SET flags = flags - 'extract_skipped' - 'extract_skipped_reason'
WHERE id IN (... reingest 성공한 doc_ids ...);
```

(`-` 는 Postgres jsonb 의 key 삭제 연산자.)

---

## 6. 주의사항

| 항목 | 영향 | 대응 |
|---|---|---|
| Vision API 무료 한도 (RPD 20) | 이미지 doc 다수면 한도 초과 | `--delay-seconds 5` + 일자 분할 |
| Storage blob 재업로드 X | 기존 blob 유지 (final hash path) | 확인 필요 시 Studio Storage 탭 |
| 진행 중 job 충돌 | reingest 가 409 반환 | 진행 중인 job 완료 대기 후 재시도 |
| chunks 임베딩 비용 | BGE-M3 HF Inference 호출 비용 | 무료 티어 한도 확인 |
| 한 건당 약 15~30초 | 멀티 파일 시 누적 시간 | dry-run 으로 사전 산정 |

---

## 7. 향후 어댑터 추가 시 반복 사용

W3 의 DOCX/PPTX 등 새 어댑터가 디스패처에 등록될 때마다 본 절차를 그대로 재사용:
1. 식별 SQL 로 `doc_type=docx` AND `flags.extract_skipped=true` 인 row 들 확인
2. `--execute` 로 일괄 reingest

스크립트 자체는 doc_type 무관하게 동작 — 디스패처에 등록된 모든 type 의 skipped doc 들을 자동으로 처리.
