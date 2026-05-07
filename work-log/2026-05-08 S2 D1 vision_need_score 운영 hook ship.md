# 2026-05-08 — S2 D1 ship: vision_need_score 운영 hook + 페이지 선별 작동

## §0 한 줄 요약

S1.5 D3 의 OR rule (`vision_need_score.needs_vision()`) 을 운영 코드 (`extract._enrich_pdf_with_vision` + `incremental._vision_pages_with_sweep`) 에 hook 으로 통합. needs_vision False 페이지는 ImageParser 호출 회피 → 비용·latency 절감. ENV `JETRAG_VISION_NEED_SCORE_ENABLED` (default true) 회복 토글 동시 ship. 단위 테스트 612 → **619 통과** / skipped 1 / 회귀 0.

master plan §6 S2 D1 정합 — DoD "점수 기반 페이지 선별 동작" 충족.

---

## §1 변경 파일

| 파일 | 변경 |
|---|---|
| `api/app/config.py` | `Settings.vision_need_score_enabled: bool` 신규 + `_parse_bool()` 헬퍼 신규 + ENV `JETRAG_VISION_NEED_SCORE_ENABLED` (default true) parse |
| `api/app/ingest/stages/extract.py` | `_score_page_for_vision` import + `_page_needs_vision()` module-level 헬퍼 + `_enrich_pdf_with_vision` 페이지 루프에 hook 주입 (budget cap 재검사 직후, `pix = page.get_pixmap` 직전) + 함수 종료 시 메트릭 1줄 log |
| `api/app/ingest/incremental.py` | 동일 import + `_page_needs_vision()` 헬퍼 + `_vision_pages_with_sweep` 페이지 루프에 hook + 메트릭 log |
| `.env.example` | `JETRAG_VISION_NEED_SCORE_ENABLED=true` 행 + 정책 주석 (S1.5 D3 골든셋 recall 83.3% baseline 명시) |
| `api/tests/test_extract_pdf_vision_enrich.py` | `TestVisionNeedScoreHook` 클래스 신설 — 4 테스트 신규 (skip / sweep retry 대상 X / ENV false 회복 / 점수 raise graceful fallback) |
| `api/tests/test_incremental_vision.py` | **신규 파일** — 3 테스트 (`_vision_pages_with_sweep` skip / sweep retry / ENV false) |

---

## §2 hook 흐름 + 설계 결정 6건

### 2.1 hook 흐름 (extract.py / incremental.py 공통)

```
페이지 루프 진입
  → budget cap 재검사 (S0 D4/D5, 기존)
  → page = doc[page_num]
  → if need_score_enabled and not _page_needs_vision(page, ...):
        skipped_by_need_score.append(page_num + 1)  ← sweep_idx == 1 일 때만
        completed_pages.add(page_num)               ← progress 표시 누적 (UX)
        continue                                     ← sweep retry 대상 X
  → pix = page.get_pixmap(dpi=150)
  → called_count += 1
  → page_result = image_parser.parse(...)           ← cache lookup → vision API
  → ... sections / progress / sweep retry (기존)
종료 시:
  → logger.info("vision_enrich: file=X processed=Y called=Z skipped_need_score=N pages=[..]")
```

### 2.2 설계 결정 6건

| # | 결정 | 근거 |
|---|---|---|
| 1 | **`_page_needs_vision()` 헬퍼 module-level 분리** (extract / incremental 양쪽 동일 코드 + graceful fallback) | 두 호출 지점이 같은 정책. 함수 시그니처 통일 → 단위 테스트 mock 일관 (`patch.object(mod, "_page_needs_vision", side_effect=...)` 패턴 동일) |
| 2 | **graceful fallback** — `compute_page_signals` (현 모듈 `score_page`) 가 raise → `needs_vision=True` (보수적) | 점수 시스템 깨져도 vision 호출 흐름 100% 보존 — S1.5 이전 동작과 동등. 단위 테스트 `test_score_compute_failure_falls_back_to_vision_call` 로 회귀 차단 |
| 3 | **sweep retry 대상 X** — needs_vision False 페이지는 `failed_in_sweep` 에 안 넣고 `continue`. 첫 sweep 결정 = 모든 sweep 결정 (논리 일관) | 503 random 실패와 needs_vision skip 은 **별개 사건**. skip 은 사용자 의도 회피 → retry 무의미 |
| 4 | **cache lookup 보존** — needs_vision 검사가 `image_parser.parse()` 의 cache lookup 보다 **앞**. needs_vision False 면 cache hit 도 회피 | cache 활용률 미세 회귀 가능성 인정. 그러나 일관 정책: 미래 cache 도 needs_vision True 페이지만 누적 → 손해 0. 변경 범위 ↓ (ImageParser 시그니처 변경 회피) |
| 5 | **메트릭 = log 1줄** (warnings 추가 X) | skip 은 정상 동작 — 사용자 노출 불필요. 운영자가 진단 시 `grep "vision_enrich:" log` 로 충분. flags 추가도 회피 (DB 압박 ↓) |
| 6 | **progress 표시는 skip 도 누적** — `completed_pages.add(page_num)` + `update_stage_progress(current=len(completed_pages))` | 사용자 UX — "처리 중" 인식 유지. extract.py 만 — incremental 은 progress hook 자체가 없음 (이미 sweep 안에서 누적 X) |

### 2.3 ENV 토글 (회복 가드)

- `JETRAG_VISION_NEED_SCORE_ENABLED` (`true` / `false` / `1` / `0` / `yes` / `no` / `on` / `off` 모두 지원, 대소문자 무관)
- default `true` — S1.5 D3 골든셋 recall 5/6 (83.3%) baseline 채택
- false 시 hook 자체가 비활성 → 모든 페이지 vision 호출 (S1.5 이전 100% 보존)
- invalid ENV 는 default `true` (graceful)
- 단위 테스트 `test_env_disabled_calls_all_pages` 로 회복 토글 검증 (extract / incremental 각 1건)

---

## §3 단위 테스트 결과

| 항목 | 시작 | 마감 | Δ |
|---|---|---|---|
| 통과 | 612 | **619** | +7 |
| skipped | 1 | 1 | 0 |
| 회귀 | — | **0** | — |

신규 7건 (기존 612 → 619):

**`tests/test_extract_pdf_vision_enrich.py` (+4)**
1. `test_needs_vision_false_skips_image_parser` — page 1 = False (skip), page 2,3 = True → ImageParser.parse 2회 + sections 2개 (page 2,3)
2. `test_needs_vision_false_not_in_sweep_retry` — page 1 skip + page 2 sweep 1 실패 → sweep 2 회복. parser 호출 = page 2 sweep 1 + sweep 2 = 2회 (page 1 sweep 2 retry X)
3. `test_env_disabled_calls_all_pages` — `Settings(vision_need_score_enabled=False)` mock + `_page_needs_vision` False 반환 → 모든 페이지 호출 (ENV 우선)
4. `test_score_compute_failure_falls_back_to_vision_call` — `_score_page_for_vision` raise → needs_vision=True fallback → 모든 페이지 호출

**`tests/test_incremental_vision.py` (+3, 파일 신규)**
1. `test_needs_vision_false_skips_image_parser` — `_vision_pages_with_sweep` 의 skip 동작
2. `test_needs_vision_false_not_in_sweep_retry` — sweep 동작 정합
3. `test_env_disabled_calls_all_pages` — ENV false 시 모든 페이지 호출

기존 7건 (test_extract_pdf_vision_enrich.py 의 5 + test_vision_need_score.py 의 35 일부) 모두 통과 — `_make_pdf_bytes` 가 sparse text PDF 라 `low_density` trigger → needs_vision=True 가 default → 기존 동작 회귀 0.

---

## §4 D2~D5 진입 신호 (다음 sprint day 작업)

### 4.1 prerequisite 충족

- ✅ S2 D1 ship — `extract.py` + `incremental.py` 양쪽 hook
- ✅ ENV 회복 토글
- ✅ 단위 테스트 회귀 0

### 4.2 다음 후보 (master plan §6 S2)

| sprint day | 작업 | ETA | 의존성 |
|---|---|---|---|
| **S2 D2** | **page cap + cost budget 병행** — S0 D4 의 budget_guard 와 needs_vision OR rule 의 통합 작용 검증 + 사용자 의도 cap 정책 (문서당 vision page cap n=N 추가 vs 이미 `_VISION_ENRICH_MAX_PAGES=50` 가 cap 역할 충족 → 정합 검증) | 0.5~1일 | S2 D1 (✅) |
| **S2 D3** | **운영 모드 UI 토글** — `/admin/vision-need-score` (운영자가 임계 조정 / 페이지 cap 조정) — Web 변경 필요 | 1~1.5일 | S2 D1·D2 |
| **S2 D4** | **본 PC 5 PDF 회귀 측정** — needs_vision skip 적용 후 인제스트 + 골든셋 recall 5/6 ±5pp 안 유지 검증 | 0.5일 | S2 D1 (✅) |
| **S2 D5** | **효과 측정 보고** — call 회피율 / 비용 절감 / 인제스트 latency Δ / cache hit ratio | 0.5일 | S2 D4 |

### 4.3 권고 (auto mode 진입 가능)

S2 D4 (회귀 측정) 우선 — D2/D3 운영 정책 변경 전 골든셋 recall 보호 검증 필수. 가설: 약 36% 페이지 vision skip → 골든셋 recall 5/6 유지 (vision_diagram + table_lookup 의 정답 페이지는 모두 needs_vision=True trigger 페이지 — D3 분포 분석 결과). 회귀 -5pp+ 시 stop + S1.5 v3 (multi-line table 휴리스틱) 우선.

S2 D2 는 사용자 의도 cap 정책 결정이 의존 — 사용자 확인 필요 case (의존성 있는 작업).

---

## §5 commit hash + body

`feat(vision): S2 D1 ship — vision_need_score 운영 hook + 페이지 선별 작동`

(commit + push 후 hash 기록)

---

## §6 의존성 / 사이드 이펙트

- 신규 import 만 (`vision_need_score` 는 D3 ship 모듈)
- DB 마이그레이션 0 / 외부 API 0 / 새 패키지 0
- web 변경 0 (S2 D3 에서 운영 모드 UI)
- ENV `JETRAG_VISION_NEED_SCORE_ENABLED=false` 회복 토글로 S1.5 이전 동작 100% 보존
- S0 D4/D5 budget cap, S0 D2-B vision_cache 모두 통합 상태 유지 — sweep / cache lookup / cap 흐름 변경 0
