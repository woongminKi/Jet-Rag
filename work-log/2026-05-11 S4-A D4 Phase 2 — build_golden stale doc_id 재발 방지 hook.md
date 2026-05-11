# 2026-05-11 — S4-A D4 Phase 2: build_golden stale doc_id 재발 방지 hook

> 프로젝트: Jet-Rag
> 작성: 2026-05-11
> 목적: 어제 (2026-05-10) 발견된 G-A-104~113 stale doc_id 버그의 재발 방지 — `build_golden_v2.py` 의 fallback hook 강화 + `build_golden_v1.py` 의 무결성 검증 옵션 추가.

---

## 0. 한 줄 요약

> **두 layer 방어 추가**: (1) `build_golden_v2.py` 가 stale doc_id (= CSV 에 doc_id 가 있지만 docs_index 미존재) 검출 시 경고 + title fallback auto-fix + 카운트 통계 출력. (2) `build_golden_v1.py` 에 `--validate-doc-ids` CLI 옵션 + `JETRAG_GOLDEN_VALIDATE_DOC_IDS=1` env var trigger — merged rows 의 모든 doc_id 가 Supabase `documents` 에 존재하는지 검증, 미존재 1건이라도 발견 시 exit 1. 단위 테스트 +7건 (v2 hook 3 + v1 validate 4) / 회귀 0.

> **(close)** 통합 sprint Phase 0~3 의 일부로 ship 완료 — 종합 핸드오프 (`2026-05-11 종합 + 2026-05-12 진입 핸드오프.md`) 참조.

---

## 1. 작업 배경

어제 work-log (`2026-05-10 S4-A D4 ...md` §4.3) 에서 G-A-104~113 10건 의 doc_id 가 stale (`629332ab-...` → `9878d7bd-...`) 임을 확인하고 수동 정정 + 재측정 완료. 원인:

1. `golden_v0.7_auto.csv` 생성 당시 Supabase 인스턴스의 doc 재적재로 새 doc_id 부여
2. 골든셋 rebuild 미진행
3. `build_golden_v2.py` L498~507 의 fallback (`if existing_doc_id and not in docs_index: pass`) 이 stale id 를 경고 없이 propagate

본 phase 는 위 3번 fallback 의 hook 강화 + 1·2번 재발 시 CI 가드를 위한 v1 검증 옵션 추가.

---

## 2. 작업 범위

### 2.1 산출물

| # | 파일 | 변경 |
|---|---|---|
| 1 | `evals/build_golden_v2.py` | L498~507 fallback → hook (warning + title fallback + stats) + `BuildStats` 에 stale 카운터 3개 추가 + `print_summary` 출력 |
| 2 | `evals/build_golden_v1.py` | `--validate-doc-ids` CLI flag + `JETRAG_GOLDEN_VALIDATE_DOC_IDS` env var + `validate_doc_ids()` 헬퍼 + `_fetch_valid_doc_ids_from_supabase()` |
| 3 | `api/tests/test_build_golden_v2.py` | `StaleDocIdHookTest` 3건 추가 |
| 4 | `api/tests/test_build_golden_v1.py` | **신규** — `ValidateDocIdsTest` 2건 + `CliValidationExitCodeTest` 2건 = 4건 |

### 2.2 hook 동작 (build_golden_v2.py)

```python
if existing_doc_id:
    for rec in docs_index.values():
        if rec.doc_id == existing_doc_id:
            matched_docs = [rec]
            break
    if not matched_docs:
        # NEW: stale doc_id 검출 → 경고 + title fallback 시도
        logger.warning("[%s] stale doc_id 검출: %s (title=%r) — title fallback 시도",
                       qid, existing_doc_id, title_raw)
        stats.stale_doc_id_count += 1
        recovered = match_doc_id(title_raw, docs_index) if title_raw else None
        if recovered is not None:
            logger.info("[%s]  → title 매칭 auto-fix: %s → %s",
                        qid, existing_doc_id, recovered.doc_id)
            matched_docs = [recovered]
            stats.stale_doc_id_fixed += 1
            row["doc_id"] = recovered.doc_id  # row 정정
        else:
            logger.warning("[%s]  → title fallback 실패 — stale id 보존 (수동 정정 필요)", qid)
            stats.stale_doc_id_kept.append(qid)
```

`print_summary` 끝에 hook 발동 시에만 추가 출력:
```
[stale doc_id] 검출 N건 / title fallback auto-fix M건 / 보존 K건
[WARN] 복구 실패 row id (수동 정정 필요): [...]
```

### 2.3 validation 동작 (build_golden_v1.py)

```bash
# CLI 활성
uv run python evals/build_golden_v1.py --validate-doc-ids

# 환경 변수 활성
JETRAG_GOLDEN_VALIDATE_DOC_IDS=1 uv run python evals/build_golden_v1.py

# default OFF — 기존 동작 100% 유지 (회귀 보호)
uv run python evals/build_golden_v1.py
```

내부:
1. `merge_golden()` 후 모든 row 의 doc_id 수집 (빈 칸 제외)
2. Supabase `documents.id` 전체 fetch (user_id 필터 + soft-delete 제외)
3. 차집합 (missing) 1건이라도 있으면 `exit 1` + 미존재 id 출력

---

## 3. 단위 테스트

### 3.1 신규 7건 (의뢰서 +5 목표 → 보강 +2 = +7)

`test_build_golden_v2.py`:
- `test_stale_doc_id_auto_fix_via_title_fallback` — stale id + title 매칭 성공 → row.doc_id 정정 + `stale_doc_id_fixed=1`
- `test_stale_doc_id_kept_when_title_fallback_fails` — stale id + title 매칭 실패 → 보존 + `stale_doc_id_kept=[qid]`
- `test_valid_doc_id_does_not_trigger_hook` — 정상 doc_id (회귀 보호)

`test_build_golden_v1.py` (신규):
- `test_all_valid_returns_empty_missing` — `validate_doc_ids()` mock 주입, 모두 valid
- `test_one_stale_doc_id_returns_missing` — 1건 stale → missing 리스트에 포함
- `test_main_returns_1_when_missing_doc_id` — CLI main() mock 으로 `--validate-doc-ids` + stale 1건 → exit 1
- `test_main_returns_0_when_all_valid` — CLI main() + 모두 valid → exit 0

### 3.2 회귀

```bash
cd api && uv run python -m unittest discover -s tests -p "test_*.py" -t .
# Ran 778 tests in 16.309s — OK
# baseline 763 → 778 (+15, Phase 1 +8 / Phase 2 +7)
```

---

## 4. 회귀 보호 결정 사항

### 4.1 기본 동작 변경 0

- `build_golden_v2.py`: hook 은 **stale id 발견 시에만 발동**. 정상 row 는 분기 미진입.
- `build_golden_v1.py`: validation 은 **CLI flag 또는 env var 명시 시에만 활성**. default OFF.

### 4.2 stale id 보존 정책

title fallback 실패 시 stale id 를 **삭제하지 않고 보존** — 사용자가 수동 정정할 수 있도록 후보 row id 를 stderr 에 노출. 자동 삭제 시 데이터 손실 위험 → 보수적 정책.

### 4.3 Supabase 의존성

`build_golden_v1.py --validate-doc-ids` 는 Supabase client + DEFAULT_USER_ID 필요. 환경 설정 누락 시 `[WARN] ... 검증 skip` 로 graceful degrade + exit 0 (검증 결과 없는 상태 = 차단 아님). CI 환경에서는 env 설정 필수.

---

## 5. 남은 이슈

### 5.1 cross_doc row 의 sub-doc 검증

build_golden_v1.py 의 검증은 **row.doc_id 단일 컬럼** 만 검증. cross_doc row 의 expected_doc_title `title_a|title_b` 분리된 sub-doc 들은 build_golden_v2.py 의 `match_cross_doc_ids` 안에서만 매칭되고 row.doc_id 가 빈 칸이라 검증 대상 외. 본 phase 범위 외 — v3 검증 강화 시 cross_doc 별도 column 추가 검토.

### 5.2 hook 발동 통계 영구 기록

stale 통계는 현재 stderr 출력만. CI/CD pipeline 에서 캡처해 markdown 보고서로 누적 기록하면 유익. 본 phase 범위 외.

---

## 6. 다음 권고

### 6.1 즉시 진입 가능

- **현재 골든셋 무결성 1회 검증**: `JETRAG_GOLDEN_VALIDATE_DOC_IDS=1 uv run python evals/build_golden_v1.py` — golden_v1.csv 의 모든 doc_id 가 valid 한지 확인. (어제 G-A-104~113 정정 후 모두 valid 여야 함.)
- **rebuild SOP 문서화**: doc 재적재 후 골든셋 rebuild 트리거 — Makefile target 추가 검토.

### 6.2 다음 phase

- **Phase 3 cross_doc 보강** — cross_doc 표본 확장 (현재 n=4). Phase 1 의 caption 합성 효과 측정과 별도 차원이므로 진입 가부 사용자 결정.

---

## 7. 산출물 경로 (절대)

| 파일 | 경로 |
|---|---|
| build_golden_v2 hook | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/evals/build_golden_v2.py` |
| build_golden_v1 validate | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/evals/build_golden_v1.py` |
| v2 테스트 (수정) | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/api/tests/test_build_golden_v2.py` |
| v1 테스트 (신규) | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/api/tests/test_build_golden_v1.py` |
| 본 work-log | `/Users/kiwoongmin/Desktop/documents/test-repo/image2/piLab/project/Jet-Rag/work-log/2026-05-11 S4-A D4 Phase 2 — build_golden stale doc_id 재발 방지 hook.md` |
