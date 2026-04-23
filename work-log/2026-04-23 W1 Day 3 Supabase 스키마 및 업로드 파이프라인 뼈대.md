# 2026-04-23 W1 Day 3 Supabase 스키마 및 업로드 파이프라인 뼈대

> Day 1~2에서 스캐폴드·어댑터 프로토콜까지 끝냈고, Day 3에서는 Supabase 스키마·Storage 연동·업로드 엔드포인트·ingest 큐 뼈대를 모두 연결해 "PDF 1개 업로드 → 접수 응답 < 2초 + 상태 조회" 까지 일직선으로 흐르게 만드는 날.

---

## 1. 오늘 달성한 것

### 1.1 Supabase 초기 마이그레이션 — `api/migrations/001_init.sql`

기획서 §10.7 저장 스키마를 4개 테이블로 확정. Day 3 dedup/업로드에 필요한 필드(`sha256`·`storage_path`·`size_bytes`·`content_type`·`deleted_at`)를 추가했다.

| 테이블 | 핵심 필드 | 제약·인덱스 |
|---|---|---|
| `documents` | user_id, sha256, storage_path, doc_type, source_channel, doc_embedding(vector 1024), flags(JSONB) | `UNIQUE(user_id, sha256)` — Tier 1 dedup DB 제약 / ivfflat(doc_embedding) / GIN(tags) / GIN(flags) |
| `chunks` | doc_id, chunk_idx, dense_vec(1024), sparse_json, char_range, bbox | `UNIQUE(doc_id, chunk_idx)` / ivfflat(dense_vec) / GIN(sparse_json) |
| `ingest_jobs` | status, current_stage, attempts, error_msg, queued/started/finished_at | partial index on `(status, queued_at) WHERE status IN ('queued','running')` |
| `ingest_logs` | job_id, stage, status, duration_ms | `(job_id, started_at)` |

**RLS**: 4개 테이블 모두 enable + 정책 없음 → `anon`·`authenticated` 모두 차단, `service_role` 만 bypass. W5 auth 도입 시 per-user 정책 추가 예정.

**트리거**: `documents.updated_at` 자동 갱신용 BEFORE UPDATE 트리거 1개.

**pgvector 인덱스**: 기획서 §10.7 명시대로 `ivfflat + vector_cosine_ops + lists=100`. MVP 규모(수백 문서)에 충분. 필요 시 W5에 HNSW로 교체 가능.

### 1.2 의존성 추가

`api/pyproject.toml`:
- `supabase>=2.11` (실제 해결 버전 2.28.3) — 공식 Python 클라이언트. Storage + PostgREST + Realtime 한 번에
- `python-multipart>=0.0.19` — FastAPI `UploadFile` 전제

### 1.3 설정·클라이언트 확장

**`.env.example`**: `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_STORAGE_BUCKET=documents`, `DEFAULT_USER_ID=00000000-0000-0000-0000-000000000001` 3개 추가. 주석에 **service_role 키 유출 금지** 경고 명시.

**`app/config.py`**: `Settings` 데이터클래스에 3개 필드 추가. `lru_cache`된 `get_settings()` 패턴 유지.

**`app/db/client.py`**: `get_supabase_client()` 싱글톤. 백엔드 전용으로 항상 `service_role` 키 사용. URL·키 누락 시 즉시 `RuntimeError`.

### 1.4 어댑터 구현체 2개

`app/adapters/impl/` 신설. 인터페이스(프로토콜)는 상위 `adapters/`에 유지, 실제 서비스 연동 로직은 `impl/` 하위로 분리.

- **`supabase_storage.py`** — `SupabaseBlobStorage` : `BlobStorage` 프로토콜 구현
  - 저장 경로 규칙 `<sha256>.<ext>` — 파일 해시가 곧 식별자, 유저별 귀속은 `documents.user_id`
  - `upsert=true` 로 동일 해시 재업로드 멱등 처리
  - `put/get/delete/signed_url` 4개 전부 구현
- **`supabase_vectorstore.py`** — `SupabasePgVectorStore` : `VectorStore` 프로토콜 구현 (뼈대)
  - `upsert_chunks`: `(doc_id, chunk_idx)` 유니크 기반 upsert — JSON 직렬화 시 `char_range` 는 `[start,end)` 리터럴로 변환
  - `delete_document`: ON DELETE CASCADE 로 chunks 까지 정리
  - `search_dense/search_sparse`: **Day 4 로 미룸** — `NotImplementedError`

### 1.5 인제스트 모듈 (`app/ingest/`)

- **`jobs.py`** — `ingest_jobs` / `ingest_logs` CRUD
  - `create_job`, `start_job`, `update_stage`, `finish_job`, `fail_job`
  - `get_latest_job_for_doc` (상태 조회용)
  - `log_stage`, `list_logs_for_job`
- **`pipeline.py`** — `run_pipeline(job_id)` : `BackgroundTasks` entrypoint
  - Day 3 에는 `running → completed` 전이만 하는 placeholder stage
  - 예외는 최상위에서 잡아 `fail_job` + `log_stage(failed)` 로 기록, 2차 실패도 로깅
  - Day 4 부터 여기에 §10.2 [4]~[11] 단계 구현

### 1.6 라우터 (`app/routers/documents.py`)

**`POST /documents`** (202 Accepted)
1. `UploadFile` 수신 → 파일명·MIME 확인
2. **입력 게이트 A** (§11.3): 확장자 화이트리스트 11개 + 50MB 상한 + 빈 파일 거부
3. SHA-256 계산 → `documents.(user_id, sha256)` 조회 — **Tier 1 dedup**, 존재 시 `{doc_id, duplicated: true}` 즉시 반환
4. Supabase Storage 업로드 (`SupabaseBlobStorage`)
5. `documents` insert → `doc_id` 획득
6. `ingest_jobs` insert (`status='queued'`)
7. `BackgroundTasks.add_task(run_pipeline, job_id)`
8. `{doc_id, job_id, duplicated: false}` 반환

**`GET /documents/{doc_id}/status`**
- 해당 doc 의 최신 `ingest_jobs` 레코드 + `?include_logs=true` 시 `ingest_logs` 도 병합
- 존재하지 않으면 404

**`main.py`**: `documents_router` 등록. `/health` 그대로 유지.

### 1.7 스모크 테스트

```
GET  /health               → 200 {"status":"ok"}
GET  /docs                 → 200 (Swagger UI)
POST /documents (.xyz)     → 400 {"detail":"지원되지 않는 확장자입니다: .xyz"}
```

파이썬 import·FastAPI 기동·입력 게이트 A 단계 전부 통과. 전체 E2E (Storage 업로드·DB insert) 는 사용자가 SQL·Storage bucket 생성을 마친 뒤 수행.

---

## 2. 이번 세션 커밋 계획 (7개 + work-log)

Day 1~2 와 마찬가지로 기능 단위 분할. 본 세션 push 전 사용자 확인 필요.

1. `feat(db): Supabase 초기 스키마 마이그레이션 추가` — `api/migrations/`
2. `chore(api): supabase·python-multipart 의존성 추가` — `pyproject.toml` + `uv.lock`
3. `feat(api): service_role 기반 Supabase 클라이언트 및 설정 확장` — `config.py` + `db/` + `.env.example`
4. `feat(api): Supabase Storage 기반 BlobStorage 어댑터 구현` — `adapters/impl/supabase_storage.py`
5. `feat(api): pgvector VectorStore 어댑터 뼈대 (upsert 우선)` — `adapters/impl/supabase_vectorstore.py`
6. `feat(api): ingest_jobs/logs CRUD 및 BackgroundTasks 파이프라인 뼈대` — `ingest/`
7. `feat(api): POST /documents 업로드 엔드포인트와 상태 조회 추가` — `routers/documents.py` + `main.py`
8. `docs(work-log): W1 Day 3 스키마 및 업로드 파이프라인 작업 로그 추가`

추가 고려: 이전 세션에서 `README.md` 에 커밋 안 된 "백엔드 설치·실행 방법" 수정이 남아있음. 별도 `docs(readme): …` 커밋으로 살릴지, 본 세션 범위 외로 둘지 사용자 판단 필요.

---

## 3. 사용자가 수행할 작업 (Part 2)

코드는 이미 준비됐지만, 아래 두 가지는 Supabase 대시보드에서 직접:

### 3.1 SQL 실행
1. Supabase 대시보드 → SQL Editor → New query
2. `api/migrations/001_init.sql` 전체 복사·붙여넣기
3. Run
4. Table Editor 에서 `documents` / `chunks` / `ingest_jobs` / `ingest_logs` 4개 생성 확인

### 3.2 Storage bucket 생성
1. Storage → New bucket
2. Name: `documents` (또는 `.env` 에 다른 이름을 넣었다면 그 값)
3. **Private** (Public 체크 해제)
4. Save

### 3.3 완료 후 E2E 검증
```bash
cd api
uv run uvicorn app.main:app --reload
# 다른 터미널에서
curl -F "file=@sample.pdf" -F "source_channel=drag-drop" http://127.0.0.1:8000/documents
# → {"doc_id":"...","job_id":"...","duplicated":false}
# 같은 파일 재업로드 → duplicated=true, job_id=null
curl http://127.0.0.1:8000/documents/<doc_id>/status?include_logs=true
# → status=completed, logs=[started, succeeded]
```

---

## 4. 현재 프로젝트 상태

```
Jet-Rag/
├── .env / .env.example          ← service_role·bucket·default_user_id 추가
├── README.md                    ← (이전 세션에서 설치·실행 가이드 추가, 미커밋)
├── api/
│   ├── pyproject.toml           ← supabase, python-multipart 추가
│   ├── uv.lock                  ← 동기화
│   ├── migrations/              ← NEW
│   │   ├── 001_init.sql
│   │   └── README.md
│   └── app/
│       ├── main.py              ← routers 등록
│       ├── config.py            ← service_role·bucket·default_user_id
│       ├── db/                  ← NEW
│       │   ├── __init__.py
│       │   └── client.py
│       ├── adapters/
│       │   ├── ... (프로토콜 그대로)
│       │   └── impl/            ← NEW
│       │       ├── __init__.py
│       │       ├── supabase_storage.py
│       │       └── supabase_vectorstore.py
│       ├── ingest/              ← NEW
│       │   ├── __init__.py
│       │   ├── jobs.py
│       │   └── pipeline.py
│       └── routers/             ← NEW
│           ├── __init__.py
│           └── documents.py
└── work-log/
    ├── 2026-04-22 … (3개, 그대로)
    └── 2026-04-23 W1 Day 3 Supabase 스키마 및 업로드 파이프라인 뼈대.md   ← 이 파일
```

---

## 5. 주요 의사결정 스냅샷 (오늘 추가분)

| 항목 | 값 | 근거 |
|---|---|---|
| 저장 경로 | `<sha256>.<ext>` (유저 구분 X) | private bucket + backend-only. 동일 해시 파일을 여러 유저가 올려도 storage 는 1개 공유. 유저 귀속은 `documents.user_id` 로 분리 |
| Dedup 전략 | 애플리케이션 + DB 이중 | router 에서 SELECT 선조회 + `UNIQUE(user_id, sha256)` 제약으로 경쟁 조건 방지 |
| 파이프라인 실행 | `BackgroundTasks` (단일 프로세스) | 기획서 §10.9 MVP — v2 에 `rq + Redis` 교체 예정 |
| 업로드 동기/비동기 | Storage 업로드는 동기, 파이프라인은 비동기 | 50MB 상한 + 로컬 네트워크 속도면 < 2초 SLO 달성. 실측 결과 필요 시 W2 재검토 |
| 벡터 인덱스 | ivfflat / lists=100 | 기획서 §10.7 명시. MVP 규모 적합. HNSW 는 W5 재평가 |
| HNSW vs ivfflat | **ivfflat 유지** (핸드오프 §7 의 "HNSW/GIN" 표기는 오표기로 간주, 기획서 우선) | — |

---

## 6. 남은 이슈 (처리 시점별)

| # | 이슈 | 심각도 | 처리 시점 |
|---|---|---|---|
| 1 | `ingest_logs` 스테이지당 2행(`started` + `succeeded`) 모델 — Python `_now_iso()` vs DB `DEFAULT now()` 시계 차이로 `finished_at < started_at` 역전 가능 | 낮음 (의미 영향 없음) | Day 4 — 1행 `INSERT → UPDATE` 모델로 리팩토링 |

*Day 3 종료 시점에 블로커는 없음. 사용자 대시보드 작업(SQL 실행 + Storage bucket 생성) 직후 Day 4 파이프라인 스테이지 구현으로 진입.*

---

## 7. 다음 스코프 — Day 4~5

1. 기획서 §10.2 [4]~[11] 파이프라인 단계 구현
   - [4] 포맷별 추출 — Day 4 PDF 우선 (PyMuPDF), W2 에 나머지 포맷
   - [5]·[6] Vision·콘텐츠 게이트 — W2 로 분리 (Day 4 엔 스킵)
   - [7] 청킹 — §10.5 섹션·semantic·병합 (Day 4)
   - [8] 태그·요약·diff — Gemini Flash (Day 4 후반부 / Day 5)
   - [9] 임베딩 — BGE-M3 via HF Inference API (Day 5)
   - [10] 적재 — `VectorStore.upsert_chunks` 활용 (Day 4)
2. `search_dense` / `search_sparse` 실제 구현 (Supabase RPC 정의 필요, Day 5~W3)
3. `GET /documents`, `GET /documents/{id}` 리스트·상세 조회 엔드포인트 (Day 5)
4. W1 전체 DoD: "PDF 1개 업로드 → 키워드 검색 가능"

---

## 8. 회고 한 줄

Day 3 는 **외부 서비스 접촉 전 경계선**까지 끝낸 날. 사용자 대시보드 작업(SQL 실행 + bucket 생성) 직후 즉시 E2E 테스트 가능한 상태로 매듭지었다.
