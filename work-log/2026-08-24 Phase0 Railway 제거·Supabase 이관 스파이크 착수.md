# 2026-08-24 — Phase 0 스파이크 착수 (Railway 제거 · Supabase 전면 이관)

## 결정 사항

Railway Hobby 플랜을 완전히 해지하고 백엔드를 Supabase 로 전면 이관하기로 확정(사용자 결정).
Supabase Pro 를 이미 사용 중이므로 **신규 비용 0, Railway $5/mo 가 순수 절감**, 관리 지점 4개 벤더 → 3개.

플랜 문서 2건 작성:
- `docs/superpowers/plans/2026-08-24-railway-제거-supabase-전면이관.md` — 마스터 (615줄)
- `docs/superpowers/plans/2026-08-24-phase1-기반-계층.md` — Phase 1 상세 (1,262줄, 실행 가능 코드 포함)

## 이관 규모 (실측)

| 영역 | LOC |
|---|---|
| 파서 (네이티브 의존) | 1,769 |
| 인제스트 (`ingest/`) | 3,876 |
| 검색·답변 | 2,983 |
| 기타 라우터 | 1,918 |
| services | 6,643 |
| adapters (비파서) | 2,530 |
| auth/db/config/main | 995 |
| **소스 합계** | **20,714** |
| 테스트 (pytest 127파일) | **32,176** |
| **총 재작성 대상** | **52,890** |

예상 12~17주 (1인, 테스트 재작성 포함).

## 확정된 플랫폼 제약 (문서 확인)

| 항목 | 값 |
|---|---|
| 런타임 | Deno/TypeScript 전용 (Python 미지원, 로드맵 없음) |
| **CPU time** | **2s / 요청 — 전 플랜 동일, 유료로도 완화 불가** |
| Memory | 256MB |
| Wall clock | 초기 응답 150s / 백그라운드 400s (Pro 확보) |
| 번들 | 로컬 20MB / 서버 5MB |

CPU 2s 때문에 인제스트는 "문서 1건 = 9-stage 통째" → **"작업 1건 = 큐 메시지 1개"**(페이지 단위 팬아웃, pgmq + pg_cron)로 재설계한다.

## 설계 중 발견한 함정 3건

1. **쿠키 도메인 — 그냥 옮기면 로그인이 전부 깨진다.**
   프론트는 `credentials:'include'` 로 호출하고(`web/src/lib/api/client.ts:55`) 백엔드는 `sb-<ref>-auth-token` 쿠키를 읽는다. 이 쿠키는 `web/src/lib/supabase/server.ts:43` 의 `COOKIE_DOMAIN` 으로 `.woong-s.com` 스코프 → `*.supabase.co` 로는 전송되지 않는다.
   → **Cloudflare Worker 리버스 프록시**로 `jetrag-api.woong-s.com` 유지 + 경로 매핑. 프론트 변경 0줄, Custom Domain 애드온 불필요, 경로별 점진 전환·즉시 롤백 가능.

2. **`verify_jwt` 기본값.** Edge Functions 는 게이트웨이가 Authorization JWT 를 먼저 검증한다. 쿠키만 보내는 브라우저 요청은 헤더가 없어 함수 코드에 닿기 전에 401. **전 함수 `verify_jwt = false`** 로 배포하고 인증은 `_shared/current_user.ts` 가 직접 수행해야 한다.

3. **`/auth/me` 의 `access_token` 은 장식이 아니다.** 프론트가 `realtime.setAuth()` 로 주입해 `ingest_jobs` 구독 RLS 를 통과시킨다(`009_realtime_ingest_jobs.sql`). 누락 시 **인제스트 진행률 UI 가 에러 없이 조용히 멈춘다.**

## Phase 0 진행 결과

### Task 0.1 — 스파이크 하네스 (코드 완료 / 배포 대기)

- `supabase init` 완료 → `supabase/config.toml` 생성
- `supabase/functions/spike/index.ts` 작성 — CPU 계측 하네스 (`?kind=noop`, `?kind=burn&ms=N` 자가검증 포함)
- `deno check` + `deno lint` **통과**
- **정정:** 플랜 초안의 `_spike` 는 배포 불가. Supabase 는 언더스코어 접두 디렉토리를 공유 코드로 보고 배포 대상에서 제외한다(`_shared` 관례의 근거). `spike` 로 개명.
- **배포는 `supabase login` 대기 중** — CLI 미인증 상태

### Task 0.2 Step 1 — 파서 기준선 (완료)

`api/scripts/spike_hwp_baseline.py` 작성 후 실행. 6/6 성공:

| 샘플 | source_type | chars | sections |
|---|---|---|---|
| `assets/public/law_sample1.hwp` | hwp | 985 | 36 |
| `law sample2.hwp` | **hwpml** | 8,304 | 58 |
| `직제_규정(2024.4.30.개정).hwpx` | hwpx | 18,486 | 497 |
| `한마음생활체육관_운영_내규(...).hwpx` | hwpx | 4,186 | 118 |
| `law sample3.pdf` | pdf | 5,395 | 34 |
| `승인글 템플릿1.docx` | docx | 54,407 | 322 |

기준선 산출물: `api/scripts/spike_baseline.json`

**도중 고친 것 2건:**
- 로컬 venv 의 콘솔 스크립트 shebang 이 옛 경로(`Desktop/piLab/Jet-Rag`)를 가리켜 `hwp5txt` 가 `FileNotFoundError`. `uv sync --frozen --reinstall-package pyhwp` 로 복구. (`--frozen` 단독은 이미 설치된 패키지를 건너뛰어 shebang 을 다시 쓰지 않음 — `--reinstall-package` 필요)
- **확장자를 믿으면 안 됨:** `law sample2.hwp` 는 이름만 `.hwp` 이고 실제로는 UTF-8 BOM XML = HWPML. 진짜 HWP 5.x OLE2(매직 `d0cf11e0`)는 `assets/public/law_sample1.hwp`. S1 판정은 후자로 해야 유효하다.

### Task 0.2 — S1 (HWP WASM) 로컬 프로브 결과

배포 없이 먼저 답할 수 있는 질문("Deno 에서 되긴 하는가")을 로컬에서 검증.
스크립트: `scripts/spike_wasm_probe.ts`, `scripts/spike_wasm_probe2.ts`

| 후보 | Deno import | 파싱 | 소요 |
|---|---|---|---|
| `@ohah/hwpjs` 0.1.0-rc.10 | **성공** | `toJson` 238,962자 / `toHtml` 18,530자 / `toMarkdown` 49자 | **1~2ms** |
| `@rhwp/core` 0.8.4 | **성공** | `HwpDocument` 객체 반환 (텍스트 추출 메서드 미확인) | 48.9ms |

**품질 대조 (`law_sample1.hwp`, Python 기준선 985자 대비):**

| 지표 | 값 |
|---|---|
| `toHtml` 텍스트 추출 | 830자 |
| 유사도 (공백무시 SequenceMatcher) | **0.9413** |
| 판정 기준 | 0.95 |

**격차의 원인이 특정됐다 — 전부 표(table) 셀이다.**

| 표 셀 텍스트 | 기준선 | toHtml | toJson |
|---|---|---|---|
| `D세무서장` | O | **X** | **O** |
| `2025두34754` | O | **X** | **O** |
| `수원고등법원` | O | **X** | **O** |
| `김씨` | O | **X** | **O** |
| `원 심 판 결` | O | **X** | **O** |

`toHtml` 은 표 셀을 누락하지만 `toJson`(239KB) 에는 **전부 들어 있다.** → `toJson` 위에 자체 텍스트 추출기를 얹으면 95% 기준 통과가 유력하다. `toMarkdown`(49자)은 본문을 거의 못 내므로 사용 불가.

**속도는 문제가 아니다** — 1~2ms 로 CPU 2s 예산 대비 약 1,000배 여유.

## 미검증으로 남은 것 (다음 세션 최우선)

1. **`@ohah/hwpjs` 가 Linux/Deno Edge 에서 동작하는가.** 로컬 설치 시 `@ohah/hwpjs-darwin-arm64` 가 함께 초기화됐다 — 플랫폼별 **네이티브 바이너리**라면 Edge Functions(Linux, 네이티브 애드온 불가)에서 실패한다. `@rhwp/core` 는 `initSync`/`init_panic_hook` 이 있어 진짜 wasm-bindgen 으로 보인다. **배포해서 확인해야 결론 가능 — S1 의 진짜 관문은 여기다.**
2. `toJson` 기반 텍스트 추출기의 실제 유사도 (0.9413 → 0.95 돌파 여부)
3. S2(PDF span/bbox), S3(Fernet), S4(DOCX/PPTX), S5(메모리) — 전부 미착수
4. `@ohah/hwpjs` 가 stdout 에 `DEBUG_LINESEG:` 디버그 로그를 뿜는다 — 운영 투입 시 로그 오염 확인 필요
5. 기준선의 HWP 샘플이 985자로 작다. 더 큰 실문서로 재검증 권장 (현재 유사도 수치의 신뢰구간이 넓다)

## 다음 세션 진입 조건

**`supabase login` 실행 후 `supabase link --project-ref <REF>`.** 그다음:
1. `supabase functions deploy spike --no-verify-jwt`
2. `?kind=burn&ms=500` 로 하네스 계측 정확도 확인
3. `?kind=hwp` case 추가 → **Linux/Deno 에서 hwpjs 동작 여부 판정** (미검증 #1)
4. S2~S5 순차 진행 → Task 0.6 판정표 작성 → Phase 1 착수 승인
