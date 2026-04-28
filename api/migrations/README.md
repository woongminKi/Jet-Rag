# Migrations

Supabase SQL Editor에서 순서대로 실행.

| # | 파일 | 내용 |
|---|---|---|
| 001 | `001_init.sql` | 초기 스키마 — `documents`, `chunks`, `ingest_jobs`, `ingest_logs` + pgvector/uuid-ossp extension + 인덱스 + RLS enable |
| 002 | `002_documents_received_ms.sql` | W2 Day 2 — `documents.received_ms INT NULL` 추가 (SLO 측정용, `/stats.slo_buckets` 집계 근거) |

## 실행 절차

1. Supabase 대시보드 → SQL Editor → New query
2. 파일 내용을 전체 복사·붙여넣기
3. Run
4. 완료 후 **Table Editor**에서 4개 테이블 생성 확인

## RLS 정책

현재는 모든 테이블 RLS 활성화 + 정책 없음 상태. `anon`·`authenticated` 키로는 접근 불가,
`service_role` 키만 모든 작업 가능. 백엔드 FastAPI는 `SUPABASE_SERVICE_ROLE_KEY`로 연결.

W5(인증 도입) 시점에 per-user 정책 추가 예정:
- `documents`: `user_id = auth.uid()`
- `chunks`: `doc_id IN (SELECT id FROM documents WHERE user_id = auth.uid())`
- `ingest_jobs`·`ingest_logs`: 동일 패턴

## Storage Bucket

SQL 마이그레이션 외에 대시보드에서 수동 생성 필요:

1. Storage → New bucket → name `documents` → **Private** (Public 체크 해제)
2. 기본 설정 유지 (파일 크기 제한은 입력 게이트에서 별도 검증)
