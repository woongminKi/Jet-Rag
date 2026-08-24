# 2026-08-24 — Phase 0 스파이크 (Railway 제거 · Supabase 전면 이관)

> **범위**: Railway 해지·Supabase 전면 이관 결정 → 플랜 2건 작성 → Phase 0 타당성 스파이크 Task 0.1/0.2(S1 = HWP 파서).
> **다음 세션 재진입**: 터미널에 `! supabase login` 실행 → 이후 `link` → `functions deploy spike --no-verify-jwt` → `?kind=env`·`hwp-import`·`hwp` 호출로 **S1 Edge 판정 마무리**. 코드는 전부 커밋·push 되어 있어 로그인만 하면 바로 이어진다.

## 0. 한눈에 보기

| 항목 | 상태 | 근거 |
|---|---|---|
| 이관 결정 (Railway 해지 → Supabase 단일화) | ✅ 완료 | 신규 비용 0, Railway $5/mo 절감, 벤더 4→3 |
| 마스터 플랜 + Phase 1 상세 플랜 | ✅ 완료 | 615줄 / 1,262줄 |
| 이관 규모 실측 | ✅ 완료 | 소스 20,714 + 테스트 32,176 = **52,890 LOC**, 12~17주 추정 |
| Task 0.1 스파이크 하네스 | 🟡 코드 완료 / 배포 대기 | `deno check`·`lint` 통과, `supabase login` 미완 |
| Task 0.2 파서 기준선 (Python) | ✅ 완료 | 6/6 성공, `api/scripts/spike_baseline.json` |
| S1 — HWP WASM 로컬 동작 | ✅ **PASS** | 순수 WASM 경로 확보, toJson 238,962자 / 15ms / RSS +35MB |
| S1 — 텍스트 품질 | ✅ **PASS** | 기준선 대비 유사도 **1.0000** (기준 0.95) |
| S1 — Edge(Linux) 실동작 | ⬜ 대기 | **차단: `supabase login`** |
| S1 — HWPX / HWPML | ⬜ 미착수 | hwpjs 는 HWP 5.x(OLE2) 전용으로 확인됨 |
| S2 PDF / S3 Fernet / S4 DOCX·PPTX / S5 메모리 | ⬜ 미착수 | — |
| Task 0.6 판정표 → Phase 1 착수 승인 | ⬜ 대기 | S1~S5 완료 후 |

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
3. S2(PDF span/bbox), S3(Fernet), S4(DOCX/PPTX), S5(메모리) — 전부 미착수
4. `DEBUG_LINESEG:` 디버그 로그가 stdout 을 오염시킨다 — 운영 투입 전 처리 필요
5. HWP 샘플이 985자 1건뿐이다. 유사도 1.0000 의 신뢰구간이 넓다 — 더 큰 실문서 필요

## 산출물 지도

| 파일 | 역할 |
|---|---|
| `docs/superpowers/plans/2026-08-24-railway-제거-supabase-전면이관.md` | 마스터 플랜 |
| `docs/superpowers/plans/2026-08-24-phase1-기반-계층.md` | Phase 1 상세 (실행 가능 코드 포함) |
| `supabase/functions/spike/index.ts` | CPU 계측 하네스. `noop`/`burn`/`env`/`hwp-import`/`hwp`/`hwp-rhwp` |
| `supabase/functions/_shared/hwp_text.ts` | **toJson 텍스트 추출기 — Phase 1 에서 그대로 쓸 코드** |
| `scripts/spike_hwp_extract.ts` | WASM 파싱 → 산출물 덤프 (`toJson`/`toHtml`/`toMarkdown`/`extracted`) |
| `scripts/spike_wasm_probe.ts` · `probe2` · `probe3` | 후보 탐색 1·2차 / WASM 경로 강제 검증 |
| `scripts/deno.json` | `nodeModulesDir: auto` + WASM 서브패키지 버전 고정 |
| `api/scripts/spike_hwp_baseline.py` | Python 파서 기준선 생성 |
| `api/scripts/spike_baseline.json` | 기준선 6샘플 |
| `api/scripts/spike_hwp_similarity.py` | **채점 스크립트 — 추출기 고칠 때마다 재실행** |

재현 명령 (2건 모두 로그인 불필요):

```bash
cd scripts && deno run -A spike_hwp_extract.ts ../assets/public/law_sample1.hwp /tmp/hwpout
python3 api/scripts/spike_hwp_similarity.py /tmp/hwpout      # → 최고 유사도 1.0000 PASS
```

## 커밋 이력 (최신순)

| 해시 | 메시지 |
|---|---|
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

## 다음 후보

| 후보 | 내용 | 근거 |
|---|---|---|
| **A (권고)** | 위 런북대로 **S1 Edge 판정 마무리** | 로컬은 전부 통과. 남은 건 Edge 런타임 정책 하나뿐이고 curl 3방이면 끝난다. 여기서 FAIL 이면 이관 계획 전체가 바뀌므로 가장 먼저 알아야 한다 |
| B | S2~S5(PDF·Fernet·DOCX/PPTX·메모리) 로컬 프로브 선행 | 로그인 없이 진행 가능. 다만 S1 결과에 따라 전제가 흔들릴 수 있다 |
| C | HWPX/HWPML Deno 경로 설계 (ZIP+XML / XML) | 이번에 새로 드러난 구멍. 공수 재산정에 필요하지만 S1 확정 후가 순서 |

**권고: A** — 로그인 1회로 즉시 진입 가능하고, 이관 가부를 좌우하는 마지막 미검증이다.
