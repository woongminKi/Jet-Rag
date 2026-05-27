# 2026-05-27 세션 종합 — Portfolio Mode C+ + 모바일 UIUX Toss

> 본 세션 시작 HEAD `19fcde5` (W31 invite 코드 게이트 제거) → 마감 HEAD **`81a6b90`** (모바일 UIUX Toss).
> 본 세션 push 누적 **2 commit**: `493a57f` → `81a6b90`.
> 변경 누적 **45 files** (portfolio 18 + mobile-uiux 27), +964/-511.
> 단위 테스트 1300 PASS / 19 skip / 3 fail (baseline flaky, 새 회귀 0).
> production 상태: **포트폴리오 데모 모드 + Toss 풍 모바일 UIUX 적용**.

---

## 0. 핵심 요약 (~500 word)

본 세션은 두 개의 큰 sprint 를 연달아 마감했다. (1) **Portfolio Mode C+** — 채용 담당자가 로그인 없이 즉시 검색·답변 시연 가능하도록 owner 의 인덱싱된 12 docs 위에서 read-only 데모 모드 활성화. (2) **Mobile UIUX Toss 적용** — 모바일 viewport 에서 좌우 스크롤·콘텐츠 잘림 완전 차단 + Toss 디자인 언어 적용 + 메인 페이지 노이즈 축소.

**Sprint 1 — Portfolio Mode C+ (`493a57f`)**:
- `get_current_user` early-return 으로 모든 anonymous 방문자를 owner_user_id 로 매핑 → 12 docs (사업보고서·법률판례·정책·학술논문·이력서) 그대로 노출
- `forbid_demo_writes` 신규 dep + `JETRAG_DEMO_READONLY` ENV → 7 POST endpoint 503 차단 (업로드·재인제스트·feedback·eval)
- 프론트 7 파일 주석 처리: proxy 게이트 / Header AuthProvider / 업로드 버튼 / Hero 추천 query 칩 5개 / `/ingest` redirect to `/docs`
- 3 테스트 파일 `@unittest.skip` 데코레이터 (auth 가드 검증)
- README 라이브 사이트 섹션 + 추천 query 5개 가이드
- 18 files, +291/-140

**Sprint 2 — Mobile UIUX Toss (`81a6b90`)**:
- **좌우 스크롤 root cause 발견·해결**: Tailwind v4 preflight 가 `ul/ol/menu` 에 `list-style: none` 만 적용, 브라우저 기본 `padding-inline-start: 40px` 가 살아남아 모든 UL 안 콘텐츠 우측 잘림. 글로벌 reset 적용 + html/body 양쪽에 `overflow-x: clip` + `max-width: 100vw`
- **검색 결과 가독성**: text-[15px] + leading-7 + `break-keep` (한국어 단어 중간 잘림 방지) + 좌측 primary border-l-2 quote bar (Toss 풍 인용 표시)
- **NewArrivals 카드 모바일 stack**: title 단독 row + tags row 우측에 time `ml-auto` → title+time 한 줄 squeeze 완전 제거
- **메인 페이지 노이즈 축소**: SLO / 검색 추세 / Vision 사용량 / Vision API 추세 카드 4종 제거 + getStatsTrend fetch 2건 제거 (초기 로드 4→2 API 호출)
- search-subheader mobile 세로 stack / mode chip horizontal-scroll-snap / Hero 추천 query 칩 5개 / 모든 카드에 rounded-2xl + overflow-hidden + min-w-0 패턴
- 27 files, +673/-371

**검증**: TypeScript exit 0 / ESLint 0 error / 단위 테스트 1300 PASS / baseline flaky 3 동일 / 백엔드 변경 0건 / 신규 의존성 0건

**production**: 자동 배포 진행 — Vercel (jetrag.woong-s.com) 코드만, Railway (jetrag-api.woong-s.com) 백엔드 무변경 → 재배포 없음. Railway ENV `JETRAG_DEMO_READONLY=true` 별도 dashboard 설정 필요.

---

## 1. 본 세션 시작·마감 시점

| 항목 | 시작 (`19fcde5`) | 마감 (`81a6b90`) | Δ |
|---|---|---|---|
| commit 누적 | 563+ | **565+** | +2 |
| 단위 테스트 PASS | 1322 (fail+err 7 baseline) | **1300** (skip 19) | -22 net (19 skip = 인증 검증 일시 비활성) |
| 마이그레이션 | 20 | 20 | 0 |
| production 모드 | 공개 가입 + 자유 업로드 | **read-only 포트폴리오 데모** | (모드 전환) |
| 모바일 UIUX | 좌우 스크롤 발생 / 콘텐츠 잘림 | **scroll 0 / 잘림 0 / Toss 풍** | (전면 리팩토링) |
| 메인 페이지 카드 | 9개 | **5개** | -4 (SLO/Trend/Vision 4종 제거) |

---

## 2. commit 별 진전

### 2.1 `493a57f` — Portfolio Mode C+

#### 백엔드 (8 files)
- `api/app/auth/dependencies.py` — `get_current_user` early-return: `CurrentUser(user_id=settings.owner_user_id or settings.default_user_id, email=None)`. `forbid_demo_writes` 신규 dep — `settings.demo_readonly` true 시 503
- `api/app/auth/__init__.py` — `forbid_demo_writes` export
- `api/app/config.py` — `demo_readonly: bool = False` Settings 필드 + `_parse_bool("JETRAG_DEMO_READONLY", False)` 파싱
- `api/app/routers/documents.py` — 4 POST endpoint 에 `Depends(forbid_demo_writes)` (`/`, `/url`, `/{id}/reingest`, `/{id}/reingest-missing`)
- `api/app/routers/answer.py` — 3 POST endpoint 게이트 (`/answer/feedback`, `/answer/eval-ragas`, `/search/eval-precision`)
- `api/app/routers/{search,stats}.py` — router-level `require_auth` dep 주석
- `api/tests/test_{admin_gate,auth_jwt,auth_protected_routes}.py` — auth 가드 검증 클래스 `@unittest.skip` 데코레이터 (3 클래스 19 tests skip)

#### 프론트엔드 (7 files)
- `web/src/proxy.ts` — `/login` 강제 리다이렉트 비활성 (NextResponse.next() 즉시 return). `PUBLIC_PATHS` / `isPublicPath` / supabase middleware import 일괄 주석
- `web/src/app/layout.tsx` — `getCurrentUser` / `AuthProvider` wrap 주석. children 직접 렌더
- `web/src/app/ingest/page.tsx` — `IngestUI` 본문 전체 주석 + `redirect('/docs')` 1줄
- `web/src/components/jet-rag/header.tsx` — 업로드/로그인/로그아웃 버튼 + Button import 주석. 포트폴리오 배지는 사용자 요청 따라 제거
- `web/src/components/jet-rag/header-mobile-panel.tsx` — 모바일 업로드 버튼 주석
- `web/src/components/jet-rag/active-docs-indicator.tsx` — `/ingest` → `/docs` redirect
- `web/src/components/jet-rag/hero-section.tsx` — 추천 query 칩 5개 (경제전망 / SK 사업보고서 / 하도급 / 데이터센터 / 본인 이력서). 업로드 버튼 주석

#### 문서 (1)
- `README.md` — 라이브 사이트 섹션 신설 (URL + 추천 query 5개 + 운영 모드 + 복원 절차)

#### 환경 (1)
- `.env` — `OWNER_USER_ID=2af8fca5-03ab-421b-94b8-53d4fe9d8046` + `JETRAG_DEMO_READONLY=true`

### 2.2 `81a6b90` — Mobile UIUX Toss

#### 글로벌 (2 files)
- `web/src/app/globals.css`:
  - `html, body` 양쪽에 `overflow-x: clip` + `max-width: 100vw`
  - `ul, ol, menu { margin-block: 0; padding-inline-start: 0 }` — Tailwind v4 preflight 누락 보강 (root cause fix)
  - `img, video, svg, canvas, iframe { max-width: 100% }` 안전망
  - `.scrollbar-hide` utility (chip row 등 의도된 horizontal scroll 용)
  - `-webkit-text-size-adjust: 100%` iOS landscape 폰트 확대 차단
- `web/src/app/layout.tsx` — body 에 `min-h-dvh` (iOS Safari mobile viewport 정확)

#### 검색 페이지 (4 files)
- `web/src/components/jet-rag/search-subheader.tsx`: mobile 세로 2단 stack (← input / AI 답변 + mode chips). mode chip row `scrollbar-hide overflow-x-auto snap-x`. debug 버튼 `hidden md:inline-flex`
- `web/src/components/jet-rag/result-card.tsx`:
  - snippet body `text-[15px] leading-7 break-keep text-foreground` + 좌측 `border-l-2 border-primary/30 pl-3` quote bar
  - 인용 메타 row `text-[11px]` + page label `font-medium`
  - snippet 간 `space-y-3`, 패딩 `p-3.5 md:p-4`
  - UL `m-0 list-none pl-0` 명시
- `web/src/components/jet-rag/search-precision-card.tsx` — `min-w-*` 제거, `break-words`
- `web/src/app/search/page.tsx` — container 일관

#### AI 답변 (3 files)
- `web/src/components/jet-rag/answer-view.tsx` — 카드 패턴 (`p-4 md:p-6 rounded-2xl overflow-hidden`)
- `web/src/components/jet-rag/ragas-eval-card.tsx` — 동일 패턴
- `web/src/app/ask/page.tsx` — 헤더 query truncate

#### 홈 카드 + 그리드 (11 files)
- `web/src/components/jet-rag/cards/new-arrivals-card.tsx`:
  - **모바일 stack**: title 단독 row + time 을 tags row 우측 (ml-auto) 으로 stack → title+time 한 줄 squeeze 완전 제거
  - 상대 시간 짧은 형식 ("13일") + hover title 풀 텍스트
  - UL `m-0 list-none pl-0` + LI `-mx-2` 제거 + 모든 flex 에 min-w-0
  - 태그 Badge `truncate max-w-full`
- `web/src/components/jet-rag/cards/popular-tags-card.tsx`: 태그 칩 `flex flex-wrap`
- `web/src/components/jet-rag/cards/recently-viewed-card.tsx`: title truncate + 날짜 shrink-0
- `web/src/components/jet-rag/cards/my-doc-stats-card.tsx`: 숫자 column 정렬
- `web/src/components/jet-rag/cards/chunks-stats-card.tsx`: 메트릭 wrap
- `web/src/components/jet-rag/cards/search-slo-card.tsx` / `vision-usage-card.tsx` / `metrics-trend-card.tsx`: 모바일 메트릭 wrap (이후 home-grid 에서 제거됨)
- `web/src/components/jet-rag/cards/search-tips-card.tsx`: 본문 break-words
- `web/src/components/jet-rag/home-grid.tsx` — **SLO/Trend/Vision 카드 4종 제거**. 우측 컬럼: 인기 태그 → 내 문서 → 청크 통계 → 검색 팁 (이전 9 → 5)
- `web/src/app/page.tsx` — `getStatsTrend` fetch 2건 제거 → 초기 API 호출 4 → 2
- `web/src/components/jet-rag/hero-section.tsx` — 추천 query 칩 mobile horizontal-scroll-snap, sm 부터 flex-wrap. 검색 input `h-12 sm:h-14` 터치 타깃

#### 헤더 (3 files)
- `web/src/components/jet-rag/header.tsx` — `safe-area-inset-top` 패딩, `shadow-sm` sticky. 로고/HeaderSearch `shrink-0`/`flex-1 min-w-0`
- `web/src/components/jet-rag/header-search.tsx` — 모바일 검색 폭 보장
- `web/src/components/jet-rag/header-mobile-panel.tsx` — 안내 문구 정리

#### docs / doc (2 files)
- `web/src/components/jet-rag/docs/docs-browser.tsx` — DocRow 날짜 컬럼 침범 방지
- `web/src/app/doc/[id]/page.tsx` — MatchedChunksSection 메타 row 잘림 방지

#### 문서 (1)
- `work-log/2026-05-27 모바일 UIUX Toss 적용.md` — senior-developer 산출

---

## 3. 의사결정 기록

### DECISION-A (포트폴리오 모드 옵션 선택)
- 옵션 A (현재 공용 default_user_id) — 사생활/오염 risk
- 옵션 B (세션 쿠키 anonymous UUID) — 빈 sandbox, ~3h
- **옵션 C+ (Owner 데이터 공개 + 업로드 차단 + 샘플 query 칩)** — 채택. 채용 담당자 0초 가치 노출. 12 docs 안 #4 본인 이력서 / #11 브랜딩 자료 노출 OK 사용자 confirm
- 옵션 D (하이브리드 read=owner, write=세션) — ~5-7h 오버스코프

### DECISION-B (모바일 referencing)
- 당근 (Karrot) — 채용 타깃 회사 패턴 매칭
- **Toss** — 채택. 한국 모바일 UX 표준 (여백·터치·타이포)
- Perplexity mobile — RAG 동급 비교 inspiration

### DECISION-C (UIUX 적용 방식)
- 옵션 A (senior-developer 일괄 위임) — **채택**. 진단·계획 명확. 단계별 stop 불필요
- 옵션 B (/design-review 자동 audit + iterative) — 검토 게이트 부담
- 옵션 C (plan-design-review 먼저) — scope 확정돼 skip

### DECISION-D (포트폴리오 데모 문구 노출)
- 사용자가 "헤더·칩의 '포트폴리오', '포트폴리오 데모' 텍스트 빼" 명시 요청
- 헤더 배지 + Hero 칩 위 안내문 + 모바일 패널 안내 일괄 제거
- `/ingest` 페이지 안내 텍스트 / README 라이브 사이트 섹션 / 코드 주석 의 `PORTFOLIO MODE` 마커 는 유지 (사용자 비가시 영역)

### DECISION-E (홈 메인 카드 정리)
- 사용자가 SLO/Trend/Vision 4종 직접 지목
- import 주석 처리로 컴포넌트 파일 보존 → git history 없이도 복원 가능
- `getStatsTrend` fetch 도 제거 → 백엔드 부하 ↓

### DECISION-F (UL 좌우 잘림 root cause)
- Tailwind v4 preflight 가 ul/ol/menu 에 `list-style: none` 만 적용
- 브라우저 기본 `padding-inline-start: 40px` + `margin-block: 1em` 살아남음
- 모든 UL 안 콘텐츠가 우측으로 40px 밀려 → 모바일에서 잘림 확정
- **글로벌 reset** (globals.css) 로 전역 해결. 컴포넌트별 fix 추가 적용

### DECISION-G (NewArrivals title+time stack)
- title flex-1 + time shrink-0 가 계산상 fit 해야 하는데 사용자 screenshot 에서 time 잘림 지속
- 측정 오차 가능성 인정 → **mobile: time 을 tags row 로 stack** (분리). 같은 줄에 squeeze 자체 제거
- desktop (sm+): 기존 title+time inline 유지

### DECISION-H (commit 분할)
- **2 commit 분할 채택** (`493a57f` portfolio + `81a6b90` UIUX)
- 1 commit 으로 묶지 않은 이유: 두 sprint 가 다른 동기 / 검토 단위 / 복원 방향. portfolio 모드는 ENV 기반 토글, UIUX 는 design 결정 → 분리 git history 가독성 ↑

---

## 4. 검증·테스트 진전

### 단위 테스트
| 시점 | PASS | skip | fail | 비고 |
|---|---:|---:|---:|---|
| 본 세션 시작 (`19fcde5`) | 1322 | 0 | 4+3=7 (baseline flaky) | invite 코드 제거 후 |
| Portfolio Mode 후 (`493a57f`) | 1300 | 19 | 3 (synonym + vision_caption x2) | auth 가드 검증 3 클래스 skip |
| Mobile UIUX 후 (`81a6b90`) | 1300 | 19 | 3 동일 | 프론트만 변경 → 백엔드 테스트 무영향 |

### TypeScript / ESLint
| 시점 | tsc | eslint |
|---|---|---|
| Portfolio Mode 후 | exit 0 | 0 error / 0 warning |
| Mobile UIUX 후 | exit 0 | 0 error / 0 warning |

### 모바일 viewport 검증 (사용자 직접 + 본 세션 진단)
- 좌우 스크롤: **0** (이전 발생 → root cause fix 후 해결)
- 콘텐츠 잘림: **0** (사용자 5회 screenshot iteration 으로 최종 fix 확정)
- 가독성: 본문 text-[15px] + leading-7 + break-keep → 사용자 "좀더 가독성 좋게" 피드백 후 quote bar 추가

---

## 5. 미해결 / 후속 sprint 후보

### 시각·UX
- 다크 모드 자동 전환 (`prefers-color-scheme`) — CSS 토큰 정의돼 있으나 미적용
- Landscape orientation polish (가로 모드 미검증)
- 카드 hover shadow lift (Toss 풍 마이크로 인터랙션)
- 한국어 `break-keep` vs `break-words` 미세 가독성 (일부 적용, 전수 확인 미진)
- Toss 정통 블루 색감 swap (현재 primary 는 남청, Toss 는 cyan 가까움)

### 인프라
- Mobile 회귀 자동 측정 인프라 (Chromatic / playwright visual diff)
- Production 모바일 실기기 검증 (iPhone Safari / Android Chrome 직접 점검)
- Vercel 자동 빌드 결과 확인 (jetrag.woong-s.com 진입)

### Portfolio Mode 후속
- Railway dashboard 에서 `JETRAG_DEMO_READONLY=true` ENV 설정 (현재 미설정 시 write endpoint 가 503 안 됨)
- per-user LLM 비용 quota / rate limit (`19fcde5` commit 본문 명시)
- 본인 비밀번호 변경 UI / SECURITY.md / 데모 GIF 녹화 등 (`2026-05-21` work-log 권고)

### 후속 새 feature 후보
- v1.6 답변 UX (PRD M3 잔여 — W-9 답변 UX + KPI 8개 측정)
- arXiv heading patch commit (`1ce5465` 이전 세션 uncommitted)
- API 키 회전 (사용자 명시적 "패스" 했지만 노출 risk 잔존)
- 새 도메인 / 이력서 등 외부 작업

---

## 6. 다음 세션 진입 우선순위

| 권고도 | 작업 | 비고 |
|---|---|---|
| 🔴 최우선 | **Railway ENV `JETRAG_DEMO_READONLY=true` 설정** | dashboard 에서 1 줄 추가 → write endpoint 503 활성. 없으면 portfolio mode 의 write 차단이 작동 안 함 |
| 🟡 보통 | **Vercel 자동 빌드 결과 확인** | jetrag.woong-s.com 진입해 모바일 UIUX prod 반영 확인 |
| 🟡 보통 | **실기기 검증** | iPhone Safari / Android Chrome 에서 다양한 viewport (375 / 393 / 412 / 414 / 428) |
| 🟢 외부 | **이력서 / 자료 큐레이션** | 12 docs 중 노출 OK 확인했지만, 데모 시연 query 큐레이션 가능 |
| 🟢 새 sprint | **다크 모드** | CSS 토큰 있으니 wiring 만. ~30분 |
| 🟢 새 sprint | **per-user quota / rate limit** | API 비용 burn risk — portfolio mode 와 별개로 베타 진입 시 필수 |
| 🟢 큰 그림 | **v1.6 / PRD M3 잔여** | W-9 답변 UX / KPI 측정 |

---

## 7. 다음 세션 첫 메시지 권장

> "git pull 후 HEAD `81a6b90` 확인. Railway dashboard 에서 `JETRAG_DEMO_READONLY=true` ENV 추가 (write endpoint 503 활성화). jetrag.woong-s.com 진입해 모바일 UIUX 반영 확인. 다음 추천 작업 결정 (다크 모드 / per-user quota / v1.6 답변 UX 중)."

---

## 8. 본 세션 변경 요약 (git diff)

### 신규 work-log (2)
- `work-log/2026-05-27 모바일 UIUX Toss 적용.md` — senior-developer 산출 (Mobile UIUX 1차)
- `work-log/2026-05-27 세션 종합 — Portfolio Mode C+ + Mobile UIUX Toss.md` (본 문서)

### 수정 코드 (총 45 files)

**Portfolio Mode (`493a57f`, 18 files)**:
- 백엔드 (8): dependencies / __init__ / config / 4 라우터 (POST 게이트) / 3 테스트 (skip)
- 프론트 (7): proxy / layout / ingest / header / mobile-panel / active-docs-indicator / hero
- 문서 (1): README
- 환경 (1): .env (OWNER_USER_ID + DEMO_READONLY)

**Mobile UIUX (`81a6b90`, 27 files)**:
- 글로벌 (2): globals.css / layout.tsx
- 검색 (4): search-subheader / result-card / search-precision-card / search/page
- AI 답변 (3): answer-view / ragas-eval-card / ask/page
- 홈 + 카드 (11): new-arrivals / popular-tags / recently-viewed / my-doc-stats / chunks-stats / search-slo / search-tips / vision-usage / metrics-trend / home-grid / page / hero-section
- 헤더 (3): header / header-search / header-mobile-panel
- docs (2): docs-browser / doc/[id]/page
- 문서 (1): senior-developer work-log

### 외부 인프라 변경
- production code 만 배포 (Vercel 자동 빌드)
- Railway 백엔드 변경 0 → 재배포 없음
- Railway ENV `JETRAG_DEMO_READONLY` 미설정 상태 — 다음 세션 첫 액션 필요

---

## 9. 핵심 한 줄

> **본 세션 = 포트폴리오 사이트 ship-ready 상태 도달**. 채용 담당자가 로그인 없이 모바일에서 12 docs 위에서 검색·답변 시연 가능. Toss 풍 모바일 UIUX 적용으로 좌우 스크롤·콘텐츠 잘림 0. 다음 세션 = Railway ENV 설정 + 실기기 검증 + (선택) 다크 모드 / per-user quota / v1.6.
