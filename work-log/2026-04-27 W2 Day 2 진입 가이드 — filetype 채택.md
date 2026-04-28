# 2026-04-27 W2 Day 2 진입 가이드 — filetype 채택

> **목적**: 다른 컴퓨터에서 바로 W2 Day 2 항목 A (SLO 회복) 를 이어 진행할 수 있게 결정 사항·환경 셋업·코드 변경 계획을 한 문서에 압축.
>
> **선행 문서**:
> - `work-log/2026-04-24 오늘 작업 종합 정리.md` — Day 1 종료 시점 종합 §0 진입점
> - `work-log/2026-04-24 W2 스프린트 명세.md` v0.3 (CONFIRMED) — §3.A · §7.1 · DE-24
> - `work-log/2026-04-24 W2 Day 1.md` — Day 1 실제 진행 기록

---

## 0. 결론 (TL;DR)

| 항목 | 직전 결정 | 오늘(2026-04-27) 변경 | 사유 |
|---|---|---|---|
| 매직바이트 MIME 검증 라이브러리 | `python-magic` | **`filetype` (pure-Python)** | `python-magic` 은 OS 의 `libmagic` (brew/apt) 필수 → 시스템 영역 오염. `filetype` 은 venv 안에만 설치 |
| pending path 형식 | (미정) | **`pending/default/{uuid}.{ext}`** | W5 멀티유저 RLS 마이그레이션 회피. `user_id="default"` 상수 |
| `python-magic` 잔존 여부 | 추가됨 (0.4.27) | **제거 완료** (`uv remove python-magic`) | `pyproject.toml` · `uv.lock` 에서 빠짐 |

다른 컴퓨터에서 바로 §3 의 셋업 절차 → §4 의 코드 변경 순서대로 진행하면 된다.

---

## 1. 왜 `python-magic` 을 취소했나

### 1.1 발견한 문제
- `uv add python-magic` 으로 PyPI 패키지는 venv 에 들어가지만, 실제 import 시 **OS 의 `libmagic.dylib` (macOS) / `libmagic.so` (Linux)** 를 ctypes 로 로드해야 동작
- 사용자 머신은 `libmagic` 미설치 상태. `brew install libmagic` 필요 → `/opt/homebrew/Cellar/libmagic/...` 에 시스템 패키지로 깔림
- 사용자 요구사항: **`~/Desktop/forsit/` 등 다른 폴더 작업물에 영향 없도록 프로젝트 폴더 안에서만 설치**
- 따라서 시스템 패키지 의존하는 `python-magic` 부적합

### 1.2 검증한 사실 (forsit 영향 사전 조사)
- forsit 내 `compass-api` (유일한 Python 프로젝트) 는 `python-magic` / `libmagic` 의존성 0건 (`grep` empty)
- viralhook · triggers · nuxt-monorepo 는 Node 프로젝트라 무관
- 즉 brew install 자체는 forsit 에 직접 영향이 없으나, 사용자 정책상 시스템 영역 오염 회피 우선 → `filetype` 채택

### 1.3 `filetype` 이 명세 §3.A AC 를 충족하는 이유
명세 §3.A AC 핵심: **".png 로 rename 한 exe 차단"** = "확장자/CT 와 매직바이트 불일치 탐지".
- `filetype` 은 PDF (`%PDF`), PNG, JPEG, GIF, WebP, HEIC, ZIP 컨테이너 (DOCX/HWPX/PPTX), OLE2 컴파운드 (HWP 5.x), MP4, exe (PE/ELF/Mach-O) 등 모든 우리 포맷 시그니처 보유
- 깊이 구분 (DOCX vs HWPX, HWP vs old .doc) 은 확장자 fallback 으로 처리 — 어차피 `_ALLOWED_EXTENSIONS` 가 그 역할
- TXT/MD 처럼 시그니처 없는 평문은 `filetype.guess()` 가 None → 확장자 통과로 폴백
- 깊은 포맷 검증은 어차피 `extract` 스테이지의 파서가 수행

---

## 2. 현재 작업 상태 스냅샷 (2026-04-27 17:30 KST)

### 2.1 코드/설정
- `api/pyproject.toml` · `api/uv.lock` — `python-magic` 제거 완료 (이력은 git 에 안 남음, 추가→제거 같은 세션 내)
- `.gitignore` 미커밋 1줄 (`.mcp.json` 추가, 오늘 MCP 설정분) — Day 2 착수 전 별도 커밋 권장
- 그 외 코드 변경 없음. `git status` 는 `.gitignore` 1건만 modified

### 2.2 Day 1 까지 누적 결과 (재확인용)
- 의존성 3종 (trafilatura · python-hwpx · pyhwp) 설치 완료 — Day 1
- `VisionCaptioner` Protocol 분리 (`adapters/vision.py` + `adapters/impl/gemini_vision.py`) — Day 1
- Gemini 2.5 Flash docstring 정리 — Day 1
- `/evals/{hwpx,vision,pdf}_samples/` + `expected.schema.json` 시드 — Day 1
- 명세 v0.1 → v0.2 → v0.2.1 → v0.3 진화 — Day 1

### 2.3 Day 2 결정 확정 (2026-04-27)
- python-magic → **`filetype`** 로 전환 (이 문서 §1)
- pending path: **`pending/default/{uuid}.{ext}`** — `user_id="default"` 상수
- W5 멀티유저 도입 시 prefix 가 `pending/<actual_user_id>/...` 로 자연 이행

---

## 3. 다른 컴퓨터에서 진입 절차 (5분)

### 3.1 저장소 동기화
```bash
cd ~/Desktop/piLab/Jet-Rag        # 또는 본 컴퓨터의 Jet-Rag 경로
git status                          # working tree 상태 확인
git pull origin main                # 최신 반영
```

### 3.2 의존성 동기화
```bash
cd api
uv sync                             # pyproject.toml + uv.lock 기반 .venv 재현
                                    # python-magic 은 lockfile 에 없음 (취소됨)
                                    # filetype 추가는 §3.3 에서
```

### 3.3 `filetype` 추가 (이 문서 작성 시점에 미실행 — 다른 컴에서 첫 작업)
```bash
cd api
uv add filetype                     # ~50KB pure-Python, .venv 안에만 설치
                                    # 시스템 영역 영향 0건
```

### 3.4 import smoke 테스트
```bash
cd api
uv run python -c "
import filetype
print('filetype version:', filetype.__version__)
print('PDF guess:', filetype.guess(b'%PDF-1.7\n%test'))
print('PNG guess:', filetype.guess(b'\\x89PNG\\r\\n\\x1a\\n' + b'\\x00' * 100))
print('ZIP (DOCX/HWPX) guess:', filetype.guess(b'PK\\x03\\x04' + b'\\x00' * 100))
print('OLE (HWP 5.x/old-Office) guess:', filetype.guess(bytes.fromhex('D0CF11E0A1B11AE1') + b'\\x00' * 100))
print('EXE (Mach-O) guess:', filetype.guess(bytes.fromhex('CFFAEDFE') + b'\\x00' * 100))
"
```
→ 각 라인이 정상 객체 (None 아님) 출력하면 OK. PDF·PNG·ZIP·OLE 모두 mime 가 채워져야 함.

### 3.5 Supabase MCP 신뢰 확인 (선택)
- 처음 Claude Code 진입 시 `.mcp.json` trust prompt 가 안 뜨더라도, 도구 (`mcp__supabase-jetrag__list_tables`) 가 deferred 로 노출돼 있으면 연결됨
- 검증: Claude 한테 "`list_tables` 한 번 핑쳐줘" 요청 → public 스키마 4테이블 (documents/chunks/ingest_jobs/ingest_logs) 출력되면 OK
- 안 되면 `claude mcp list` 로 상태 확인. PAT 는 평문 노출 위험이 있으니 진작 revoke·재발급 권장 (`work-log/2026-04-24 오늘 작업 종합 정리.md` §0.3 노트)

---

## 4. Day 2 항목 A 코드 변경 계획 (실행 순서)

> 명세 v0.3 §3.A · §7.1 그대로. 본 문서는 결정 정합화·실행 표만 추가.

### 4.1 변경 범위 7건

| # | 파일 | 변경 유형 | 핵심 |
|---|---|---|---|
| 1 | `api/pyproject.toml` · `api/uv.lock` | 의존성 추가 | `uv add filetype` |
| 2 | `api/migrations/00X_documents_received_ms.sql` | 신규 | `ALTER TABLE documents ADD COLUMN received_ms INT;` |
| 3 | `api/app/routers/documents.py` | **POST 핵심 재작성** | chunk-streaming + SHA-256 + 50MB + filetype + pending path + BG 흐름 |
| 4 | `api/app/ingest/__init__.py` 또는 신규 헬퍼 | BG 흐름 변경 | `run_full_ingest(job_id, doc_id, raw_bytes, sha256, ext, content_type)` 함수 추가 — Storage upload + storage_path update + 8-stage pipeline |
| 5 | `api/app/adapters/impl/supabase_storage.py` | 시그니처 그대로 | `put()` 에 sha256·ext 인자 받도록 옵션 정리 (이미 sha256 자체 계산 → caller 전달로 변경) |
| 6 | `api/app/routers/stats.py` | `slo_buckets` 추가 | `documents.received_ms` 집계 — doc_type 별 p95_ms / sample_count / pass_rate |
| 7 | `web/src/lib/api/types.ts` | 응답 타입 보강 | `StatsResponse` 에 `slo_buckets` 추가 |

### 4.2 POST `/documents` 새 흐름 (수신 단계, < 2초)

```python
# pseudo
async def upload_document(...):
    started_at = time.perf_counter()
    file_name = file.filename
    ext = PurePosixPath(file_name).suffix.lower()
    doc_type = _ALLOWED_EXTENSIONS.get(ext) or 400

    # 1) chunk-wise stream + SHA-256 + size counter + 첫 chunk 매직바이트
    hasher = hashlib.sha256()
    buf = bytearray()
    first_chunk_mime: str | None = None
    while chunk := await file.read(64 * 1024):
        if not first_chunk_mime and len(buf) < 4096:
            # filetype 은 첫 ~262 byte 면 충분
            head = (bytes(buf) + chunk)[:4096]
            kind = filetype.guess(head)
            first_chunk_mime = kind.mime if kind else None
            _validate_magic(ext, first_chunk_mime, content_type)  # 불일치 시 400
        hasher.update(chunk)
        buf.extend(chunk)
        if len(buf) > _MAX_SIZE_BYTES:
            raise HTTPException(413, "...")
    raw = bytes(buf)
    sha256 = hasher.hexdigest()
    size = len(raw)

    # 2) Tier 1 dedup (즉시 응답 경로 — Storage 업로드 X)
    existing = ... by sha256
    if existing and not existing.flags.failed:
        return UploadResponse(doc_id=existing.id, job_id=None, duplicated=True)
    if existing and existing.flags.failed:
        # 기존 failed 자동 reingest — 기존 로직 유지
        ...

    # 3) documents insert with PENDING storage_path
    doc_uuid = uuid.uuid4().hex
    pending_path = f"pending/{settings.default_user_id}/{doc_uuid}{ext}"
    received_ms = int((time.perf_counter() - started_at) * 1000)
    doc_row = supabase.table("documents").insert({
        ...,
        "storage_path": pending_path,         # placeholder, NOT NULL 충족
        "sha256": sha256,
        "size_bytes": size,
        "content_type": content_type,
        "received_ms": received_ms,
    }).execute()
    doc_id = doc_row.data[0]["id"]

    # 4) ingest_jobs insert + BG 큐잉 (raw bytes 전달)
    job = create_job(doc_id=doc_id)
    background_tasks.add_task(
        run_full_ingest,
        job_id=job.id, doc_id=doc_id,
        raw=raw, sha256=sha256, ext=ext, content_type=content_type,
    )

    return UploadResponse(doc_id=doc_id, job_id=job.id, duplicated=False)
```

### 4.3 BackgroundTask `run_full_ingest`

```python
def run_full_ingest(*, job_id, doc_id, raw, sha256, ext, content_type):
    storage = SupabaseBlobStorage(bucket=settings.supabase_storage_bucket)
    final_path = f"{sha256}{ext}"  # 기존 SupabaseBlobStorage._build_path 와 동일 규칙
    # Storage upload (final path 로 한 번에)
    try:
        storage.put_at(path=final_path, data=raw, content_type=content_type)
        # documents.storage_path 를 final_path 로 update
        supabase.table("documents").update({"storage_path": final_path}).eq("id", doc_id).execute()
    except Exception as e:
        supabase.table("documents").update({
            "flags": {"upload_failed": True, "error": str(e)},
        }).eq("id", doc_id).execute()
        # 기존 failed 정책 경로로 통합
        return
    # 기존 8-stage 파이프라인
    run_pipeline(job_id, doc_id)
```

> `SupabaseBlobStorage` 에 `put_at(path, data, content_type)` 메서드 신설 또는 기존 `put()` 시그니처를 path-explicit 으로 확장.
>
> Storage upload 실패 시 `flags.upload_failed=true` 마킹 → `flags.failed` 정책과 통합 (재업로드 시 자동 reingest 로직이 받음).

### 4.4 매직바이트 검증 헬퍼 (filetype 기반)

```python
# api/app/routers/_input_gate.py 또는 documents.py 내부
_EXT_TO_MIMES: dict[str, set[str]] = {
    ".pdf": {"application/pdf"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"}, ".jpeg": {"image/jpeg"},
    ".heic": {"image/heic", "image/heif"},
    # ZIP 컨테이너 (DOCX/HWPX/PPTX) 는 'application/zip' 으로 동일 — 확장자 fallback
    ".docx": {"application/zip"},
    ".hwpx": {"application/zip"},
    ".pptx": {"application/zip"},
    # OLE2 (HWP 5.x) — 'application/x-ole-storage' 또는 None
    ".hwp": {"application/x-ole-storage", "application/CDFV2"},
    # TXT/MD — None 허용
    ".txt": set(),
    ".md": set(),
}

def _validate_magic(ext: str, magic_mime: str | None, content_type_header: str) -> None:
    expected = _EXT_TO_MIMES.get(ext)
    if expected is None:
        return  # 확장자 화이트리스트 통과한 경우만 호출되므로 도달 X
    if not expected:
        return  # 평문 — magic 검증 스킵
    if magic_mime is None:
        # filetype 가 식별 못 한 경우: TXT/MD 외엔 보수적으로 reject
        raise HTTPException(400, "파일 형식을 식별할 수 없습니다.")
    if magic_mime not in expected:
        raise HTTPException(400, f"확장자({ext})와 파일 내용({magic_mime})이 일치하지 않습니다.")
```

### 4.5 `/stats.slo_buckets` 집계

```python
# stats.py 의 StatsResponse 에 추가
class SloBucketStats(BaseModel):
    p95_ms: int | None
    sample_count: int
    pass_rate: float            # received_ms < 2000 비율

class StatsResponse(BaseModel):
    ...
    slo_buckets: dict[str, SloBucketStats]   # 키: pdf_50p, image, pdf_scan, hwp, url
```

집계 로직 (단순 in-Python):
1. `documents` 에서 `received_ms IS NOT NULL` 인 row 만 대상
2. 분류:
   - `pdf_50p`: doc_type=pdf · size_bytes ≥ 25MB (50% 라인)
   - `image`: doc_type=image
   - `pdf_scan`: doc_type=pdf · flags.scan=true
   - `hwp`: doc_type IN (hwp, hwpx)
   - `url`: doc_type=url
3. 각 버킷에서 `received_ms` 의 p95 계산 (numpy 없이 sorted 인덱스 `int(0.95 * (n-1))`)
4. `pass_rate = sum(received_ms < 2000) / n`

→ 기획서 §13.1 SLO KPI 산식 그대로.

### 4.6 마이그레이션 (`api/migrations/00X_documents_received_ms.sql`)

```sql
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS received_ms INT;

COMMENT ON COLUMN documents.received_ms IS
  'POST /documents 수신 단계 latency (ms). SLO: < 2000. /stats.slo_buckets 가 doc_type 별 p95 집계.';
```

> 적용: Supabase Studio SQL Editor 또는 `supabase migration up` (CLI 셋업되어 있으면). 본 프로젝트는 Studio 직접 적용 패턴이라 새 SQL 파일 추가 후 Studio 에 붙여넣기.

---

## 5. AC 체크리스트 (Day 2 항목 A 완료 기준)

명세 v0.3 §3.A 그대로:

- [ ] 50MB PDF 수신 응답 P95 < 2.0 초 (10회 연속) — `documents.received_ms` 로 측정
- [ ] 8.6MB (W1 실측 파일) 수신 응답 < 1.5 초
- [ ] 100MB 업로드 시 메모리 사용량 50MB 안에서 413 반환 — chunk-streaming 카운터로 충족
- [ ] `.png` rename 한 exe 파일 → filetype 매직바이트 검증으로 400
- [ ] 중복 업로드 수신 응답 < 2 초 내 `duplicated=true` 회신 — Storage 업로드 스킵 경로
- [ ] 수신 중 Ctrl+C → `flags.received_incomplete=true` 마킹 (orphan 아님) — except 핸들링
- [ ] 프론트 `UploadItem` 흐름 무변경 (폴링·재시도 포함)
- [ ] `/stats.slo_buckets` 응답에 `pdf_50p / image / pdf_scan / hwp / url` 5종 — `p95_ms / sample_count / pass_rate`

---

## 6. 위험·주의

| 위험 | 완화책 |
|---|---|
| `filetype.guess()` 가 .docx/.hwpx 모두 ZIP 으로 식별 → 깊이 구분 불가 | 확장자 화이트리스트로 분기. 깊이 검증은 `extract` 스테이지 파서가 수행 (이미 그렇게 됨) |
| `BackgroundTasks.add_task(raw=...)` 의 메모리 점유 | 50MB 한도 내라 OK. 다중 동시 업로드 시 N×50MB 까지 — Railway free tier 확인 필요 (W2 SLO 측정 후) |
| Storage upload 실패 → `pending/...` orphan row 잔존 | `flags.upload_failed=true` 마킹 → 재업로드 시 자동 reingest 분기에서 회복. 별도 cleanup job 은 W3 |
| 마이그레이션 적용 누락 → `received_ms` 컬럼 미존재로 INSERT 실패 | 다른 컴퓨터 진입 직후 `migrations/00X_documents_received_ms.sql` 적용 여부 확인. Supabase MCP 의 `list_tables verbose=true` 로 컬럼 존재 확인 가능 |

---

## 7. 다음 컴퓨터에서 첫 명령 묶음 (복붙용)

```bash
# 1. 동기화
cd ~/Desktop/piLab/Jet-Rag        # 본인 경로로 치환
git status
git pull origin main

# 2. 의존성
cd api
uv sync
uv add filetype

# 3. import smoke
uv run python -c "import filetype; print(filetype.guess(b'%PDF-1.7\n%test'))"

# 4. 마이그레이션 적용 (Supabase Studio SQL Editor 직접 실행)
#    파일: api/migrations/00X_documents_received_ms.sql 작성 후 SQL 붙여넣기
#    또는 Claude 에게 mcp__supabase-jetrag__apply_migration 호출 지시

# 5. Day 2 항목 A 본 작업 진입 — 위 §4 순서대로
#    Claude 에게: "2026-04-27 Day 2 진입 가이드 §4 순서대로 시작해" 요청
```

---

## 8. 회고 한 줄

오늘은 Day 2 본 작업 진입 직전에 **시스템 영역 오염 방어** 라는 사용자 정책 기준으로 의존성 1건을 되돌렸다. `python-magic` → `filetype` 전환은 명세 §3.A AC 를 그대로 만족하면서 venv-only 설치라는 정책 적합도가 더 높다. 다음 컴퓨터에서는 §3 → §4 순서로 그대로 진행 가능.
