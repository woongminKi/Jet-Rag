# 2026-08-24 — Phase 0 스파이크 (Railway 제거 · Supabase 전면 이관)

> **범위**: Railway 해지·Supabase 전면 이관 결정 → 플랜 2건 작성 → Phase 0 타당성 스파이크 Task 0.1/0.2(S1 = HWP), Task 0.3(S2 = PDF).
> **다음 세션 재진입**: S1·S2 **둘 다 PASS**. 남은 스파이크는 S3(Fernet)·S4(DOCX/PPTX)·S5(메모리)와 HWPX/HWPML 경로. 배포·인증은 이미 뚫려 있어 `supabase functions deploy spike --no-verify-jwt` 한 줄이면 바로 이어진다. 자세한 후보는 맨 아래 "다음 후보" 참조.

## 0. 한눈에 보기

| 항목 | 상태 | 근거 |
|---|---|---|
| 이관 결정 (Railway 해지 → Supabase 단일화) | ✅ 완료 | 신규 비용 0, Railway $5/mo 절감, 벤더 4→3 |
| 마스터 플랜 + Phase 1 상세 플랜 | ✅ 완료 | 615줄 / 1,262줄 |
| 이관 규모 실측 | ✅ 완료 | 소스 20,714 + 테스트 32,176 = **52,890 LOC**, 12~17주 추정 |
| Task 0.1 스파이크 하네스 | ✅ 배포 완료 | `burn&ms=500` → `cpuMs 500.07` 로 계측 정확도 확인 |
| Task 0.2 파서 기준선 (Python) | ✅ 완료 | 6/6 성공, `api/scripts/spike_baseline.json` |
| S1 — `@ohah/hwpjs` | ❌ **Edge FAIL** | emnapi 가 shared memory·Worker 요구 → Edge 는 1페이지도 불가 |
| S1 — **`@rhwp/core`** | ✅ **Edge PASS** | `getTextFileText()` 유사도 **1.0000**, 총 72ms (예산 2s) |
| S1 — 최종 판정 | ✅ **PASS** | HWP 5.x 경로 확정: `@rhwp/core` + HTML 엔티티 디코딩 |
| S1 — HWPX / HWPML | ⬜ 미착수 | hwpjs 는 HWP 5.x(OLE2) 전용으로 확인됨 |
| **S2 — PDF (`mupdf` 1.27.0)** | ✅ **PASS** | 페이지당 CPU 최대 **100.8ms**, 유사도 **1.0000**×7p, 다운스트림 **7/7 동등** |
| **S3 — Fernet (Web Crypto)** | ✅ **PASS** | Python↔Deno **양방향 복호**, 변조·오키 거부, 복호 1.7~2.0ms |
| **S4 — DOCX/PPTX (ZIP+XML 직접)** | ✅ **PASS** | 섹션·(text,title) 쌍 **완전일치**, 유사도 **1.0000**, CPU 10~72ms |
| S5 — 메모리 | ⚠️ **방법 막힘** | Edge `Deno.memoryUsage()` 가 0 반환 — 직접 계측 불가 |
| Task 0.6 판정표 → Phase 1 착수 승인 | ⬜ 대기 | S5 판정 방법 결정 + HWPX/HWPML 잔여 |

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

## 2차 세션 — 미검증 #1·#2 처리 (커밋 `155cd92`, `a1e8dc9`)

### 미검증 #1 (네이티브냐 WASM 이냐) — **로컬에서 결론 남. 배포 불필요했다.**

`@ohah/hwpjs` 는 **napi-rs 패키지**다. 패키지 실측(0.1.0-rc.10):

| export 조건 | 진입점 | 정체 |
|---|---|---|
| `node` ← **Deno 가 기본 선택** | `dist/index.js` | NAPI 로더 → `hwpjs.<platform>.node` 네이티브 |
| `browser` | `dist/browser.js` | `export * from '@ohah/hwpjs-wasm32-wasi'` |

즉 1차 세션에서 본 `-darwin-arm64` 는 **기본 경로가 네이티브라서** 뜬 것이고,
optionalDependencies 에 `@ohah/hwpjs-wasm32-wasi`(순수 WASM, 910KB) 가 **따로 있다.**
서브패키지를 직접 지목하면 네이티브 없이 돈다 — 로컬 Deno 2.8 실측:

| 지표 | 값 |
|---|---|
| import | 22ms |
| `toJson` | 238,962자 / 15ms — **네이티브 경로와 완전히 동일** |
| `toHtml` | 18,530자 / 9ms — 동일 |
| RSS 증가 | import +13MB, 파싱 후 +35MB (총 88.6MB) |

**속도·메모리 모두 Edge 예산(CPU 2s / 256MB) 안에 들어온다.**

### 미검증 #2 (toJson 추출기 유사도) — **1.0000 PASS**

`supabase/functions/_shared/hwp_text.ts` 신설. 기준선 대비 유사도 **1.0000** (기준 0.95).

| 후보 | chars | 유사도 | 판정 |
|---|---|---|---|
| `extracted(toJson)` | 950 (36문단) | **1.0000** | PASS |
| `toHtml` | 1,166 | 0.9413 | FAIL — 표 셀 8토큰 누락 |

**도중 잡은 함정:** `ctrl_header` 의 `paragraphs` 는 `children` 의 **평탄화 사본**이다.
둘 다 순회하면 문서 전체가 정확히 두 번 나온다(985자 → 2,141자, 중복 문단 36건).
ctrl_header 19건 전부에서 `children` 텍스트 == `paragraphs` 텍스트임을 확인하고
children 우선 + 빈 경우만 paragraphs 폴백으로 해결.

채점 스크립트를 저장소에 남겼다: `api/scripts/spike_hwp_similarity.py`
(`python3 api/scripts/spike_hwp_similarity.py <추출디렉토리>`)

### 새로 드러난 것 — **hwpjs 는 HWP 5.x(OLE2) 전용이다**

같은 추출기를 다른 확장자에 걸어본 결과:

| 샘플 | 결과 |
|---|---|
| `law_sample1.hwp` (OLE2 `d0cf11e0`) | OK |
| `law sample2.hwp` (실제로는 HWPML/XML) | `Invalid CFB file (wrong magic number): [ef,bb,bf,3c...]` |
| `직제_규정...hwpx` (ZIP) | `Invalid CFB file (wrong magic number): [50,4b,3,4...]` |

기준선 6샘플 중 **HWPX 2건 + HWPML 1건은 hwpjs 로 처리 못 한다.**
Deno 이관 시 HWP 계열만 **3경로**(hwpjs / ZIP+XML / XML)가 필요하다 — Phase 1 공수 재산정 대상.

### 스파이크 함수 확장 (배포 1회로 판정 끝나게 묶음)

`?kind=env` / `hwp-import` / `hwp` / `hwp-rhwp` 추가. `deno check`·`deno lint` 통과.
`env` 는 실패 시 원인을 가르는 대조군이다 — emnapi 부트스트랩이 요구하는
SharedArrayBuffer / Worker / `WebAssembly.Memory({initial:4000, shared:true})`(250MB **예약**)를 각각 시험한다.

### git 정리

로컬 `main` 이 origin 보다 3커밋 앞서고 11커밋 뒤처져 있었다(1차 세션 `306399c` 가 미푸시 상태).
`origin/main` 위로 rebase 후 push 완료 (백업 ref `backup/pre-rebase-20260824`).

## 미검증으로 남은 것

1. **Edge 런타임이 emnapi 부트스트랩을 허용하는가** — SAB / `fetch(file:)` 로 번들 내 .wasm 읽기 / Worker 생성.
   로컬 Deno 는 셋 다 되지만 Edge 는 더 제한적이다. **`?kind=env` + `?kind=hwp-import` 한 번이면 판정된다.**
2. HWPX/HWPML 경로 (위 참조) — 미착수
3. ~~S2(PDF span/bbox)~~ ~~S3(Fernet)~~ ~~S4(DOCX/PPTX)~~ → **2026-09-04 전부 PASS**.
   S5(메모리)만 남았고 방법부터 막혀 있다 — Edge 의 `Deno.memoryUsage()` 가 0 을 반환한다.
4. `DEBUG_LINESEG:` 디버그 로그가 stdout 을 오염시킨다 — 운영 투입 전 처리 필요
5. HWP 샘플이 985자 1건뿐이다. 유사도 1.0000 의 신뢰구간이 넓다 — 더 큰 실문서 필요

## 3차 세션 (2026-08-25) — **S1 Edge 판정 완료. PASS, 단 라이브러리가 바뀌었다**

`supabase login` 해소 → `link` → `functions deploy spike --no-verify-jwt` 성공(Docker 불필요 확인).
`?kind=burn&ms=500` → `cpuMs 500.07` 로 하네스 계측 정확도 검증됨. 인증 헤더 없이 200 응답.

### `@ohah/hwpjs` — Edge **FAIL** (로컬 통과가 아무 의미 없었다)

```
Error: Context is currently not supported
    at new Context (node:wasi:6:11)
    at .../@ohah/hwpjs-wasm32-wasi/0.1.0-rc.10/hwpjs.wasi.cjs:19:16
```

`?kind=env` 로 원인이 정확히 갈렸다:

| 능력 | 로컬 Deno 2.8 | **Edge Runtime 1.74.3 (Deno 2.1.4 호환)** |
|---|---|---|
| SharedArrayBuffer 존재 | true | true |
| `Worker` | true | **false** |
| shared memory 250MB | OK | **불가** |
| shared memory **1페이지** | OK | **불가** (`Creating a shared memory is not supported`) |

1페이지도 안 된다 → 크기 문제가 아니라 **정책적 전면 차단**이다.
emnapi 부트스트랩이 `WebAssembly.Memory({shared:true})` + Worker 를 요구하므로
**napi-rs/emnapi 계열 WASM 패키지는 Edge 에서 전부 불가**로 봐야 한다. 우회로 없음.

### `@rhwp/core` — Edge **PASS**

| 지표 | 값 |
|---|---|
| import | 41.9ms |
| `new HwpDocument(bytes)` | 66.9ms |
| `getTextFileText()` | **2.8ms** |
| 합계 | 약 **72ms** / CPU 2s 예산 대비 **28배 여유** |
| 유사도 (기준선 대비) | **1.0000** (엔티티 디코딩 후) |

wasm-bindgen 계열이라 SAB·Worker 를 요구하지 않는 것이 갈린 지점이다.

**메서드 탐색 결과** (411개 메서드 중):

| 메서드 | 결과 |
|---|---|
| `getTextFileText()` | **1,041자 — 표 셀 포함 전문. 채택** |
| `getTextFileUnicode()` | 1,085자(JSON 이스케이프 포함), 내용 동일 |
| `getPageText()` | 빈 문자열 — 페이지네이션 전이라 무효 |
| `exportHml()` | `HML_SOURCE_REQUIRED` — HML 원본 문서만 가능 |

**필수 후처리 1건**: 출력이 특수문자를 HTML 숫자 엔티티로 낸다(`&#65378;` = `｢`).
디코딩 전 0.9637 → **디코딩 후 1.0000**. 샘플에서 6개 발견.

### 이번 판정이 바꾼 것

- 2차 세션의 `_shared/hwp_text.ts`(toJson 추출기, 로컬 유사도 1.0000)는 **Edge 에서 무용지물**이다.
  의존 파서가 로드조차 안 된다. 파일 상단에 경고를 달아두고 남겼다 — toJson 구조 분석
  (children↔paragraphs 이중 노출 함정)은 재활용 가치가 있어서다.
- **교훈: 로컬 Deno 통과는 Edge 통과의 근거가 되지 못한다.** 남은 스파이크(S2~S5)는
  로컬 프로브를 건너뛰고 **처음부터 배포해서 재는 편이 빠르다.** 배포는 20초면 된다.

## 4차 세션 (2026-09-04) — **S2(PDF) 판정 완료. PASS**

S1 교훈대로 로컬 프로브를 건너뛰고 바로 배포해서 쟀다. 배포 6회, 총 소요 1세션.

### 관문 — `mupdf`(mupdf.js) Edge **로드 성공**

| 지표 | 값 |
|---|---|
| import | 43~56ms |
| exports | 38개 (`Document`, `StructuredText`, `Page` …) |
| shared memory·Worker 요구 | **없음** — Emscripten 단일 스레드 빌드라 S1 을 죽인 조건에 안 걸린다 |

대안 후보 `unpdf`(pdfjs 서버리스, 순수 JS)도 로드된다(3.7ms). 폴백이 실재한다.

### 판정을 두 층으로 나눴다

구조가 같다고 이관 가능이 아니다. 제품이 실제로 읽는 건 **섹션**과 **vision 호출 여부**다.
그래서 채점기를 둘 만들었다.

**① 필드 단위 대조** (`spike_pdf_compare.py`) — 기준선 PyMuPDF 1.27.2 대비

| 샘플 | block | span | bboxΔ(pt) | 유사도 | cpuMs |
|---|---|---|---|---|---|
| law sample3 p0 | 11/11 | 34/34 | 0.205 | **1.0000** | 70.3 |
| law sample3 p1 | 15/15 | 27/27 | 0.205 | **1.0000** | 63.8 |
| 데이터센터 안내서 p0 | 10/10 | 10/10 | 0.966 | **1.0000** | 46.0 |
| 데이터센터 안내서 p39(표) | 35/35 | 96/96 | 0.360 | **1.0000** | 78.6 |
| sample-report p0(이미지) | 7/7 | 7/**9** | 0.004 | **1.0000** | 152.7 |
| 삼성 사업보고서 p0 | 46/46 | 46/46 | 0.012 | **1.0000** | 90.8 |
| 삼성 사업보고서 p100(표) | 70/70 | 105/**97** | 0.008 | **1.0000** | 84.0 |

**② 다운스트림 동등성** (`spike_pdf_downstream.py`) — 양쪽 dict 를 프로덕션 함수에 그대로 투입

`pymupdf_parser._extract_dict_blocks` + `vision_need_score.score_page` 결과:

| 항목 | 결과 |
|---|---|
| 섹션 수·제목·본문 | **7/7 완전일치** |
| 섹션 bbox | **7/7** (Δ ≤ 1pt) |
| `needs_vision` | **7/7 동일** — vision 호출 비용 변화 없음 |
| `triggers` | **7/7 동일** |
| `composite_score` | 최대 편차 **0.0006** |

즉 **span 수가 어긋난 2페이지에서도 산출물은 같다.**

### 도중 잡은 함정 4건

1. **`asJSON()` 은 bbox 를 정수로 반올림한다** — 기준선 대비 최대 1.93pt 편차. `walk()` 는 float 원본을 준다.
2. **`preserve-spans` 는 블록 분할 자체를 바꾼다** — `sample-report` p0 에서 7블록(text 5) → 6블록(text 4).
   line 층·span 층을 따로 불러 인덱스로 짝지으려던 최초 설계가 여기서 깨졌다.
   처음 확인한 2페이지에서 우연히 성립했을 뿐이다 — **2개 표본으로 구조 가정을 세우면 안 된다.**
3. **`walk` 의 `font` 는 글자마다 새 JS 래퍼다.** 객체 동일성(`!==`)으로 span 경계를 재면
   모든 글자가 경계가 되어 span 수 = 글자 수(1,543)가 된다. `pointer` 값으로 비교해야 한다.
4. **메서드를 변수에 담아 호출하면 `this` 가 끊긴다** — mupdf 내부에서
   `Cannot read properties of undefined (reading 'pointer')`. 수신자를 붙여 호출해야 한다.

### 버전이 갈림돌이었다

`mupdf@1.28.0` 은 블록을 병합해 기준선과 어긋났다(데이터센터 p0 10→9, 삼성 p100 70→62).
기준선이 PyMuPDF 1.27.2(MuPDF 1.27.2)라서 **같은 계열인 `1.27.0` 으로 내리자 block 7/7 일치**.
→ `deno.json` 에 `npm:mupdf@1.27.0` 고정. Phase 1 에서 올릴 때는 이 채점기를 다시 돌려야 한다.

### 처리 규모 (573p 삼성 사업보고서 · 한 요청 안에서 연속 처리)

| 페이지 수 | 총 CPU | 페이지당 max | 페이지당 avg | 결과 |
|---|---|---|---|---|
| 50p | 296ms | 78.4ms | 5.7ms | OK |
| 200p | 725ms | 79.4ms | 3.6ms | OK |
| 400p | **1,421ms** | 100.8ms | 3.5ms | OK |
| 573p(전체) | — | — | — | **`WORKER_RESOURCE_LIMIT`** |

9MB/93p 이미지 문서(`sample-report`)는 50p 에 1,212ms 로 가장 무겁다.
41p 데이터센터 안내서는 전체 392ms.

**먼저 걸리는 건 메모리가 아니라 CPU 2s 다.** 메모리는 직접 계측하지 못했다 —
Edge 의 `Deno.memoryUsage()` 가 0 을 반환한다(**미검증**). 다만 400페이지를 한 워커에서
연속 처리하고도 죽지 않았으므로 페이지 해제(`destroy()`)는 동작하는 것으로 본다.

### 남은 잔차 — 합성 공백 (닫음, 영향 0으로 측정)

PyMuPDF 는 MuPDF 가 **간격 때문에 끼워 넣은 공백**을 독립 span 으로 둔다
(`'52,966,362' / ' ' / '20,138,323'` — 표 컬럼 신호). `walk` 의 6개 인자
(char, origin, font, size, quad, color)에는 그 플래그가 없어 우리 쪽은 한 span 으로 합친다.
반대로 줄 끝 실공백은 우리가 더 쪼갠다(`'경제전망 '` → `'경제전망'` + `' '`).

- "공백이면 무조건 분리" 규칙을 세워 검증했더니 **7페이지 전부 불일치**했다(진짜 공백까지 쪼갠다). 규칙이 아니다.
- `color` 를 span 키에 추가해도 두 페이지의 span 수는 그대로였다 — 원인이 색이 아니라는 뜻이다.
- 실측 영향: 다운스트림 7/7 동일. **지금 더 파지 않고 닫되**, Phase 1 에서 표 페이지
  회귀 테스트로 고정한다(`_is_table_like_block` 이 span 수를 직접 읽으므로).

### 기준선 커밋 범위

`assets/private/` 는 `.gitignore` 대상이라 그 본문이 저장소로 새면 안 된다.
기준선 JSON 은 **공개 자산 3건(5페이지)만** 담고, 삼성 사업보고서는
`--include-private` 옵트인으로 분리했다. 위 표의 7페이지는 로컬에서 잰 값이다.
공개 세트만으로 재현하면 5페이지가 나오고 결과는 동일하다(다운스트림 5/5 PASS).

## 5차 세션 (2026-09-04) — **S3(Fernet) · S4(DOCX/PPTX) 판정 완료. 둘 다 PASS**

### S3 — Fernet 호환 (`_shared/fernet.ts`)

Web Crypto 만으로 Python `cryptography.fernet` 과 호환된다. **한 방향만 보면 안 된다** —
이관 도중에는 Python 과 Deno 가 `subscriptions.billing_key` 를 함께 읽고 쓴다.

| 표본 | ① Py→Deno 복호 | ② Deno→Py 복호 | ③ 변조 거부 | 복호 | 암호 |
|---|---|---|---|---|---|
| `TEST_SID_1234567890` | O | O | O | 1.72ms | 1.18ms |
| `S1234567890abcdefghij` | O | O | O | 1.77ms | 1.21ms |
| `한글 SID 테스트 · 2026` | O | O | O | 1.81ms | 1.08ms |
| 빈 문자열(패딩 경계) | O | O | O | 2.04ms | 1.25ms |
| 4KB(다중 블록) | O | O | O | 1.95ms | 2.53ms |

④ 다른 키로 복호 시도 → 거부(`HMAC 불일치`).

**플랜 초안의 스파이크 코드는 HMAC 검증을 건너뛴다.** 그대로 두면 "복호는 되는데 위조도
통과"하는 구현을 PASS 로 오판한다. `crypto.subtle.verify` 로 검증하도록 구현하고,
채점기에 변조 토큰·오키 거부를 넣었다(직접 바이트 비교는 타이밍 공격 표면이라 verify 에 맡김).

키는 **매 실행 일회용 생성** — 운영 키(`JETRAG_BILLING_KEY_ENCRYPTION_KEY`)와 운영 SID 는 쓰지 않았다.

### S4 — DOCX/PPTX (`_shared/ooxml_text.ts`)

**라이브러리 대신 ZIP+XML 을 직접 읽는다.** `mammoth` 는 deps 6개(jszip/bluebird/…)를 끌고
오면서 **style 이름을 그대로 주지 않는다.** 현행 `DocxParser` 는 `paragraph.style.name`
정규식으로 heading 을 판정하는데 실자산에 Title 16 · Heading2 99 · Heading1 1 = **116건**이
쓰여 있다 — 이름이 없으면 `section_title` sticky propagate 가 통째로 죽는다. PPTX 는 쓸 만한
후보가 아예 없다. OOXML 은 ZIP+XML 이라 직접 읽는 편이 의존성·정확도 모두 낫다.

기준선은 **프로덕션 파서(`DocxParser`/`PptxParser`)를 그대로 돌려** 떴다.

| 샘플 | 섹션 | 문자 | (text,title) 쌍 | 제목 집합 | 유사도 | cpuMs |
|---|---|---|---|---|---|---|
| `승인글 템플릿1.docx` | 322/322 | 54,086/54,086 | **322/322** | O | **1.0000** | 71.6 |
| `승인글 템플릿3.docx` | 254/254 | 40,095/40,095 | **254/254** | O | **1.0000** | 50.1 |
| `브랜딩_...pptx` | 0/0 | 0/0 | 0/0 | O | 1.0000 | 54.7 |
| fixture `spike_sample.docx` | 12/12 | 328/328 | **12/12** | O | **1.0000** | 32.5 |
| fixture `spike_sample.pptx` | 4/4 | 287/287 | **4/4** | O | **1.0000** | 10.2 |

### 도중 잡은 것 3건

1. **`firstDescendantInner` 가 이름과 무관하게 depth 를 줄였다.** `w:body` 를 찾을 때 첫
   `</w:p>` 에서 0 이 되어 곧바로 반환 → 본문 전체 유실 → 섹션 0개. 첫 배포에서 전 샘플 FAIL 로 드러났다.
2. **Python `len()` 은 코드포인트, JS `.length` 는 UTF-16 코드유닛.** 섹션 쌍이 322/322
   완전일치인데 문자 수만 54,086 vs 54,101 로 나왔다 — 📌 15개 때문이다. 텍스트는 동일했다.
   **판정이 이상하면 자[尺]를 먼저 의심**한 게 맞았다. 계측 단위를 코드포인트로 맞췄고,
   fixture 에 📌 를 넣어 회귀로 고정했다.
3. **저장소의 유일한 실제 PPTX 는 텍스트가 0자다** — 11슬라이드 전부 이미지(`<a:t>` 0개, media 106개).
   운영에서 Vision OCR 로 가는 자산이라 "텍스트 추출이 되는가"를 판정할 수 없다.
   → `spike_ooxml_fixture.py` 로 title/본문/**그룹 도형**/표를 담은 fixture 를 만들어 판정했다.
   (이 자산의 Vision 경로는 Gemini API 호출이라 런타임이 바뀌어도 영향 없음)

### 기준선 커밋 범위 (S2 와 같은 정책)

실자산 `승인글 템플릿*.docx` · `브랜딩_*.pptx` 는 `.gitignore` 의 `/*.docx`·`/*.pptx` 대상이다.
본문을 기준선 JSON 으로 커밋하면 자산을 우회 커밋하는 것과 같아서, **커밋되는 기본 세트는
합성 fixture 뿐**이고 실자산은 `--include-local` 로 붙인다. 위 표의 실자산 행은 로컬 측정치다.

### 알려진 한계 (Phase 1 이관 시 확인)

- `w:gridSpan`/`w:vMerge`: python-docx `row.cells` 는 병합 셀을 grid 열 수만큼 **반복** 반환한다.
  이번 구현은 `w:tc` 를 그대로 세므로 **병합표에서 열 수가 갈린다.**
  대상 문서 실측으로는 gridSpan·vMerge 가 **0건**이라 이번 판정에 영향이 없었다.
- `w:hyperlink` 안의 run: python-docx `Paragraph.text` 는 직계 `w:r` 만 본다. 같은 규칙으로
  맞췄으므로 동작은 일치하지만, 결과적으로 하이퍼링크 텍스트는 **양쪽 다** 빠진다(기존 동작 유지).
- 대상 문서 실측: 하이퍼링크 0 · `w:tab` 0 · `w:sdt` 0 — 위 경로가 실제로 안 밟혔다는 뜻이다.

## 산출물 지도

| 파일 | 역할 |
|---|---|
| `docs/superpowers/plans/2026-08-24-railway-제거-supabase-전면이관.md` | 마스터 플랜 |
| `docs/superpowers/plans/2026-08-24-phase1-기반-계층.md` | Phase 1 상세 (실행 가능 코드 포함) |
| `supabase/functions/spike/index.ts` | CPU 계측 하네스. `noop`/`burn`/`env`/`hwp*`/`pdf-import`/`pdf`/`pdf-walk`/`pdf-dict`/`pdf-pages`/`pdf-unpdf` |
| `supabase/functions/_shared/hwp_text.ts` | toJson 텍스트 추출기 — **Edge 에서는 무용지물**(경고 주석 참조) |
| `supabase/functions/_shared/pdf_dict.ts` | **mupdf → PyMuPDF dict 변환기 — Phase 1 에서 그대로 쓸 코드** |
| `api/scripts/spike_pdf_baseline.py` · `.json` | PDF 기준선 생성기 / 공개 자산 3건 5페이지 |
| `api/scripts/spike_pdf_compare.py` | **필드 단위 채점기 — mupdf 버전 올릴 때 반드시 재실행** |
| `api/scripts/spike_pdf_downstream.py` | **다운스트림 동등성 채점기 (섹션·needs_vision)** |
| `supabase/functions/_shared/fernet.ts` | **Fernet 호환 (HMAC 검증 포함) — Phase 4 에서 그대로 쓸 코드** |
| `supabase/functions/_shared/ooxml_text.ts` | **DOCX/PPTX ZIP+XML 추출기 — Phase 1 에서 그대로 쓸 코드** |
| `api/scripts/spike_fernet_check.py` | **S3 채점기 — 양방향 복호 + 변조·오키 거부** |
| `api/scripts/spike_ooxml_fixture.py` | 합성 fixture 생성기 (docx/pptx) |
| `api/scripts/spike_ooxml_baseline.py` · `.json` | 프로덕션 파서 기준선 (기본=fixture, `--include-local`=실자산) |
| `api/scripts/spike_ooxml_compare.py` | **S4 채점기 — 섹션·(text,title) 쌍 대조** |
| `scripts/spike_hwp_extract.ts` | WASM 파싱 → 산출물 덤프 (`toJson`/`toHtml`/`toMarkdown`/`extracted`) |
| `scripts/spike_wasm_probe.ts` · `probe2` · `probe3` | 후보 탐색 1·2차 / WASM 경로 강제 검증 |
| `scripts/deno.json` | `nodeModulesDir: auto` + WASM 서브패키지 버전 고정 |
| `api/scripts/spike_hwp_baseline.py` | Python 파서 기준선 생성 |
| `api/scripts/spike_baseline.json` | 기준선 6샘플 |
| `api/scripts/spike_hwp_similarity.py` | **채점 스크립트 — 추출기 고칠 때마다 재실행** |

재현 명령 (2건 모두 로그인 불필요):

```bash
# S1 (로그인 불필요)
cd scripts && deno run -A spike_hwp_extract.ts ../assets/public/law_sample1.hwp /tmp/hwpout
python3 api/scripts/spike_hwp_similarity.py /tmp/hwpout      # → 최고 유사도 1.0000 PASS

# S2 (배포된 spike 함수 호출 — anon key 는 .env 에서 자동으로 읽는다)
api/.venv/bin/python api/scripts/spike_pdf_baseline.py       # 기준선 재생성
python3 api/scripts/spike_pdf_compare.py --dump /tmp/edge    # 필드 단위 대조
api/.venv/bin/python api/scripts/spike_pdf_downstream.py /tmp/edge   # → FAIL 0

# S3 / S4 (키는 스크립트가 일회용으로 생성 — 운영 키 불필요)
api/.venv/bin/python api/scripts/spike_fernet_check.py               # → FAIL 0
api/.venv/bin/python api/scripts/spike_ooxml_fixture.py              # fixture 재생성
api/.venv/bin/python api/scripts/spike_ooxml_baseline.py             # 기준선 재생성
python3 api/scripts/spike_ooxml_compare.py                           # → FAIL 0
```

## 커밋 이력 (최신순)

| 해시 | 메시지 |
|---|---|
| `783f8c6` | feat(spike-s3,s4): Fernet 양방향 호환 + DOCX/PPTX 추출 PASS — 전부 FAIL 0 |
| `e1c2b1d` | docs(work-log): S2(PDF) Edge 판정 PASS 기록 |
| `9226328` | feat(spike-s2): PDF Edge 판정 PASS — mupdf 1.27.0, 다운스트림 7/7 동등 |
| `b7ad26c` | feat(spike-s1): Edge 실측으로 HWP 파서 확정 — @rhwp/core, 유사도 1.0000 |
| `ae2c59f` | docs(work-log): 2026-08-24 세션 종합 — Phase 0 S1 스파이크 재진입 런북·산출물 지도·다음 후보 |
| `d528aa3` | docs(work-log): S1 2차 세션 — WASM 경로 확보·유사도 1.0000·HWPX 미지원 발견 기록 |
| `a1e8dc9` | chore(spike-s1): deno.lock 커밋 — WASM 후보 버전 고정 |
| `155cd92` | feat(spike-s1): HWP 순수 WASM 경로 확보 + toJson 텍스트 추출기 유사도 1.0000 |
| `3b06eca` | feat(migration): Railway 제거·Supabase 전면 이관 플랜 + Phase 0 스파이크 착수 |

전부 `origin/main` push 완료. rebase 백업 ref: `backup/pre-rebase-20260824`.

미커밋 잔여(이번 세션과 무관, 손대지 않음): `docs/superpowers/plans/2026-07-07-w5-6-kakaopay-subscription.md`, `workers/email-ingest/package-lock.json`.

## 차단 요인

| 요인 | 성격 | 해소 방법 |
|---|---|---|
| `supabase login` 미완 | **사용자 액션 필요** | 터미널에 `! supabase login` → 브라우저 Authorize |

그 외 외부 대기 없음. Docker 불필요(CLI 2.111.0 은 Edge Function 배포에 Docker 를 쓰지 않는다).

## 다음 세션 재진입 런북

로그인 후 아래를 순서대로. 각 단계의 **기대 결과**를 먼저 적어둔다.

```bash
# 1. 프로젝트 연결 → 기대: "Finished supabase link."
supabase link --project-ref mpmtydudhojpukuuadrd

# 2. 함수 배포 → 기대: "Deployed Functions on project mpmtydudhojpukuuadrd: spike"
supabase functions deploy spike --no-verify-jwt

# 3. 하네스 계측 정확도 → 기대: cpuMs 가 500 근처
curl -s "https://mpmtydudhojpukuuadrd.supabase.co/functions/v1/spike?kind=burn&ms=500"

# 4. 런타임 능력 조사 → hwp 실패 시 원인을 가르는 대조군
curl -s "https://mpmtydudhojpukuuadrd.supabase.co/functions/v1/spike?kind=env"

# 5. S1 관문 → 기대: exports 에 toJson 포함, error null
curl -s "https://mpmtydudhojpukuuadrd.supabase.co/functions/v1/spike?kind=hwp-import"

# 6. 실파싱 + CPU 계측 → 기대: jsonChars 238962, cpuMs << 2000
curl -s -X POST --data-binary @assets/public/law_sample1.hwp \
  "https://mpmtydudhojpukuuadrd.supabase.co/functions/v1/spike?kind=hwp"
```

3~6번이 **401** 이면 게이트웨이가 apikey 를 요구하는 것이다 — `-H "Authorization: Bearer $NEXT_PUBLIC_SUPABASE_ANON_KEY"` 를 붙여 재시도한다(값은 `.env` 에 있다). 함수 자체는 `--no-verify-jwt` 로 배포하므로 코드 수정은 불필요.

> ⚠️ `link` 이후 **`supabase db push` 계열은 절대 쓰지 말 것.** 이 프로젝트는 마이그레이션을
> SQL Editor 로 직접 적용해와서 `supabase_migrations` 추적 테이블이 비어 있다(대시보드에도
> "No migrations" 로 표시된다). CLI 가 미적용으로 오판해 전체를 다시 밀 수 있다.
> 이번 작업은 `functions deploy` 만 쓴다.

**5번이 실패하면** 4번 결과로 원인을 가른다:

| `env` 결과 | 해석 | 다음 수 |
|---|---|---|
| `sharedMemory250MB` 실패 | 250MB 예약이 Edge 메모리 정책에 막힘 | `@rhwp/core`(`?kind=hwp-rhwp`) 로 전환 — wasm-bindgen 계열이라 SAB 불필요 가능성 |
| `hasWorker: false` | emnapi 비동기 워커 풀 생성 불가 | 동기 호출만 쓰면 우회 가능한지 확인 |
| 전부 true 인데 import 실패 | `fetch(file:)` 로 번들 내 .wasm 읽기 실패 | .wasm 을 base64 인라인하거나 Storage 에서 받아 `WebAssembly.instantiate` |

## 다음 후보 (2026-09-04 갱신 — S1·S2·S3·S4 완료 후)

| 후보 | 내용 | 근거 |
|---|---|---|
| **A (권고)** | **HWPX/HWPML Edge 경로** (ZIP+XML / XML) | 파서 중 유일하게 남은 미판정. 기준선 6샘플 중 **3건**이 여기 걸린다. S4 에서 만든 OOXML ZIP+XML 스캐너를 거의 그대로 재사용할 수 있어 비용이 낮다 |
| B | S5 메모리 판정 방법을 정하고 Task 0.6 판정표 작성 | `Deno.memoryUsage()` 가 0 이라 직접 계측이 막혔다. 간접 지표(대량 처리 생존 · `WORKER_RESOURCE_LIMIT` 유형)로 대체할지 결정하면 Phase 1 착수 승인까지 간다 |
| C | Phase 1 착수 (Cloudflare Worker 리버스 프록시부터) | 파서 4종이 다 통과해 "물리적으로 불가"라는 중단 조건은 벗어났다. HWPX 는 Phase 1 중 병렬 처리 |

**권고: A** — HWPX/HWPML 은 샘플 6건 중 3건이 걸리는데 아직 아무 근거가 없다.
S4 의 XML 스캐너 재사용으로 싸게 닫을 수 있고, 그래야 Task 0.6 판정표가 빈칸 없이 채워진다.

> S1 이 남긴 절차 변경(S2 에서 재확인됨): 로컬 Deno 통과는 Edge 통과의 근거가 아니다.
> S3~S5 도 로컬 프로브 없이 `spike` 함수에 case 를 추가해 배포(약 20초)하고 실측한다.
>
> S2 가 덧붙인 것: **라이브러리 출력의 구조를 상상해서 파서를 쓰지 말 것.** 원본을 1회
> 덤프해 읽고 나서 매핑을 정한다. 표본 2개로 세운 구조 가정은 3번째 표본에서 깨졌다.
