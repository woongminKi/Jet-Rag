# Jet-Rag 디자인 시스템 (design.md)

**Base**: Wanted Design System (Community) `.fig` — 토큰 실측: `wanted_ds_tokens.json`
**레퍼런스 UX**: 토스(Toss)
**범위**: `web/` (Next.js 16 + React 19 + Tailwind v4)
**상태**: 현행(2026-05-27 Toss 풍 1차 적용분) 정착 + 갭 정의. 이 문서는 코드 변경 지시서가 아니라 **판단 기준**이다.

---

## 0. 왜 이 문서인가

Jet-Rag 는 한국 공공기관 문서(PDF/HWP/이미지)를 모아두고 어렴풋한 기억으로 검색하는 **개인 지식 검색 에이전트**다. 사용 맥락은 대부분 **모바일에서, 짧게, 집중해서** — 지하철에서 문서 제목이 기억 안 날 때 툭 검색하고 결과를 확인하는 식이다. 이 문서는:

1. Wanted DS 의 원자 토큰(atomic)을 Jet-Rag 시맨틱 토큰으로 재정의하고,
2. 이미 코드에 있는 Toss 풍 패턴(rounded-2xl 카드, quote bar, break-keep 등)을 존중하며,
3. 현재 비어있거나 임의값(arbitrary value)으로 흩어진 부분 — 컬러 변수 체계, 다크 모드, 타이포 스케일 — 을 하나의 기준으로 수렴시킨다.

---

## 1. 디자인 원칙

Toss 의 5 원칙을 Jet-Rag 맥락으로 구체화한다.

| 원칙 | Toss 원문 의도 | Jet-Rag 적용 |
|---|---|---|
| 한 화면 한 가지 일 | 화면마다 사용자가 할 행동은 하나 | 검색 결과 화면의 1차 행동은 "결과 훑기"뿐. AI 답변·전체 보기·디버그는 보조 진입점으로 격리(`/ask`, `?debug=1`) |
| 큰 타이포 + 넉넉한 여백 | 정보 밀도보다 인지 부하 최소화 | 검색 결과 카드는 snippet 1~2개만 우선 노출, 나머지는 "+N개 더 매칭" 뒤로 |
| 명확한 단일 CTA | 화면당 주요 버튼 1개, 나머지는 시각적으로 후순위 | Hero 의 "검색"이 primary, "파일 업로드"·"전체 문서 보기"는 outline/secondary |
| 존댓말 + 간결한 마이크로카피 | 친절하지만 말이 길지 않음 | "잠시 후 다시 시도해주세요", "요약 미생성" 같은 짧은 존댓말 유지 |
| 바텀시트/풀스크린 모달 우선 | 모바일에서 네이티브 앱처럼 | 현재 Dialog/Sheet 컴포넌트 없음 — §6.8 에서 표준 정의 |

추가로 이 프로젝트 특유의 원칙 두 개:

- **기억 보조**: 사용자는 "정확한 키워드"가 아니라 "느낌"으로 검색한다. UI는 확신 없는 검색 행동을 부담 없게 만들어야 한다 (예: 매칭 강도 라벨은 `?debug=1` 뒤로 숨김 — 일반 사용자에게 점수를 들이밀지 않음).
- **AI 답변은 보조, 원본은 주역**: 결과 카드의 quote(인용) 표현이 AI 요약보다 먼저 눈에 들어와야 한다 — 이미 `border-l-2 border-primary/30` quote bar 로 구현되어 있음, 유지.

---

## 2. 컬러 토큰

### 2.1 Wanted DS 아토믹 팔레트 — 참조

**coolNeutral** (Jet-Rag 배경/라벨/라인의 기반 스케일 — atomic `neutral` 램프와는 별개 계열이니 혼용 금지):

| step | hex | step | hex | step | hex |
|---|---|---|---|---|---|
| 99 | `#F7F7F8` | 60 | `#878A93` | 20 | `#292A2D` |
| 98 | `#F4F4F5` | 50 | `#70737C` | 17 | `#212225` |
| 97 | `#EAEBEC` | 40 | `#5A5C63` | 15 | `#1B1C1E` |
| 96 | `#E1E2E4` | 30 | `#46474C` | 10 | `#171719` |
| 95 | `#DBDCDF` | 25 | `#37383C` | 7 | `#141415` |
| 90 | `#C2C4C8` | 23 | `#333438` | 5 | `#0F0F10` |
| 80 | `#AEB0B6` | 22 | `#2E2F33` | | |
| 70 | `#989BA2` | | | | |

**Primary — blue**: 50(normal) `#0066FF` · 45(strong) `#005EEB` · 40(heavy) `#0054D1` · 60(inverse용) `#3385FF`

**상태(status)**: positive(green-50) `#00BF40` · cautionary(orange-50) `#FF9200` · negative(red-50) `#FF4242`

나머지 accent 램프(lime/cyan/violet/purple/pink)는 태그·데이터 시각화 등 저빈도 용도이므로 필요 시 원본 JSON(`wanted_ds_tokens.json`)의 `atomic` 블록을 그대로 참조 — 이 문서에 전량 재수록하지 않음.

### 2.2 시맨틱 토큰 — 라이트

| 시맨틱 토큰 | 베이스 atomic | 값 | 비고 |
|---|---|---|---|
| static-white | common-100 | `#FFFFFF` | |
| static-black | common-0 | `#000000` | |
| primary-normal | blue-50 | `#0066FF` | 버튼/링크/포커스 링 |
| primary-strong | blue-45 | `#005EEB` | hover/active |
| primary-heavy | blue-40 | `#0054D1` | pressed |
| label-normal | coolNeutral-10 | `#171719` | 본문 텍스트 |
| label-strong | common-0 | `#000000` | 제목 등 최고 대비 |
| label-neutral | coolNeutral-22 | `rgba(46,47,51,0.88)` | 부제/보조 텍스트 |
| label-alternative | coolNeutral-25 | `rgba(55,56,60,0.61)` | 캡션/타임스탬프 |
| label-assistive | coolNeutral-25 | `rgba(55,56,60,0.28)` | placeholder |
| label-disable | coolNeutral-25 | `rgba(55,56,60,0.16)` | disabled 텍스트 |
| background-normal-normal | common-100 | `#FFFFFF` | 페이지 배경 |
| background-normal-alternative | coolNeutral-99 | `#F7F7F8` | 섹션 구분 배경 |
| background-elevated-normal | common-100 | `#FFFFFF` | 카드 |
| background-elevated-alternative | coolNeutral-99 | `#F7F7F8` | 카드 내부 서브 블록(snippet 박스 등) |
| interaction-inactive | coolNeutral-70 | `#989BA2` | 비활성 아이콘/토글 |
| interaction-disable | coolNeutral-98 | `#F4F4F5` | 비활성 배경 |
| line-normal-normal | coolNeutral-50 | `rgba(112,115,124,0.22)` | 강조 구분선 |
| line-normal-neutral | coolNeutral-50 | `rgba(112,115,124,0.16)` | 일반 구분선 |
| line-normal-alternative | coolNeutral-50 | `rgba(112,115,124,0.08)` | 옅은 구분선 |
| line-solid-normal | coolNeutral-96 | `#E1E2E4` | 카드 border(불투명) |
| line-solid-neutral | coolNeutral-97 | `#EAEBEC` | 옅은 카드 border |
| line-solid-alternative | coolNeutral-98 | `#F4F4F5` | 가장 옅은 border |
| status-positive | green-50 | `#00BF40` | |
| status-cautionary | orange-50 | `#FF9200` | fallback/warning badge |
| status-negative | red-50 | `#FF4242` | destructive/에러 |
| inverse-primary | blue-60 | `#3385FF` | 다크 표면 위 primary |
| inverse-background | coolNeutral-15 | `#1B1C1E` | 다크 표면 배경 |
| inverse-label | coolNeutral-99 | `#F7F7F8` | 다크 표면 위 텍스트 |

### 2.3 시맨틱 토큰 — 다크 (적용 시 기준)

Wanted DS 추출본에는 **다크 테마 전체 시맨틱 표가 별도로 없다** — `inverse-*` 세 토큰과 `semantic_opacity_dark`(라벨/라인 alpha) 만 확보됨. 아래 표는 그 두 자료를 조합해 구성한 **잠정 기준**이며, `(추정)` 표시된 행은 Figma 다크 프레임을 재추출해 검증이 필요하다.

| 시맨틱 토큰 | 값 | 근거 |
|---|---|---|
| background-normal-normal | `#1B1C1E` | inverse-background (확정) |
| background-elevated-normal | `#171719` (추정) | coolNeutral-10, 베이스보다 한 단계 더 어둡게 — 다크에서는 elevated 가 더 밝은 게 일반적이나 원본 미확정. **재검증 필요** |
| label-normal | `#F7F7F8` | inverse-label (확정) |
| label-neutral | `rgba(247,247,248,0.88)` (추정) | base 를 inverse-label 로 치환, alpha 는 `semantic_opacity_dark` 그대로(88%) |
| label-alternative | `rgba(247,247,248,0.61)` (추정) | 위와 동일 로직 |
| label-assistive | `rgba(247,247,248,0.28)` (추정) | 위와 동일 로직 |
| label-disable | `rgba(247,247,248,0.16)` (추정) | 위와 동일 로직 |
| primary-normal | `#3385FF` | inverse-primary (확정) |
| line-normal-normal | `rgba(112,115,124,0.32)` | `semantic_opacity_dark.line-normal-normal` (확정 — 베이스 색은 라이트와 동일, alpha 만 32%) |
| line-normal-neutral | `rgba(112,115,124,0.28)` | 확정 |
| line-normal-alternative | `rgba(112,115,124,0.22)` | 확정 |
| line-solid-normal | `#2E2F33` (추정) | coolNeutral-22 — 원본에 다크 solid 라인 표 없음, `line-normal-normal` 근사값으로 대체 제안 |
| status-positive/cautionary/negative | 라이트와 동일 hex | 채도가 높아 다크 배경(`#1B1C1E`)에서도 대비 충분 — 별도 다크 변형 불필요 |

**다크 모드는 2026-07-19 적용됨** (globals.css `.dark` 블록 + 시스템 감지·수동 토글). 위 표의 `(추정)` 값은 그대로 시작점으로 채택 — Figma 다크 프레임 재추출 검증은 여전히 후속(§8-2).

### 2.4 Tailwind v4 `@theme` 매핑 (목표)

`web/src/app/globals.css` 의 `:root` / `.dark` 블록을 아래 목표값으로 재정의한다. 형식은 현재처럼 CSS 변수 + `@theme inline` 별칭 구조를 유지 — `oklch()` 대신 Wanted 원본 hex/rgba 를 그대로 쓴다(Tailwind v4 는 hex 도 동일하게 동작, 임의 변환으로 값을 왜곡하지 않기 위함).

| CSS 변수 | 목표 시맨틱 토큰 | 라이트 | 다크 |
|---|---|---|---|
| `--background` | background-normal-normal | `#FFFFFF` | `#1B1C1E` |
| `--foreground` | label-normal | `#171719` | `#F7F7F8` |
| `--card` | background-elevated-normal | `#FFFFFF` | `#171719` (추정) |
| `--card-foreground` | label-normal | `#171719` | `#F7F7F8` |
| `--popover` / `--popover-foreground` | card 와 동일 | 상동 | 상동 |
| `--primary` | primary-normal | `#0066FF` | `#3385FF` |
| `--primary-foreground` | static-white | `#FFFFFF` | `#FFFFFF` |
| `--secondary` | background-normal-alternative | `#F7F7F8` | `#2E2F33` (추정) |
| `--secondary-foreground` | label-normal | `#171719` | `#F7F7F8` |
| `--muted` | background-normal-alternative | `#F7F7F8` | `#212225` (추정) |
| `--muted-foreground` | label-alternative | `rgba(55,56,60,0.61)` | `rgba(247,247,248,0.61)` (추정) |
| `--accent` | *(Wanted 미정의 — Jet-Rag 자체 판단)* | cyan-50 `#00BDDE` | cyan-60 `#28D0ED` |
| `--destructive` | status-negative | `#FF4242` | `#FF4242` |
| `--border` | line-solid-normal | `#E1E2E4` | `rgba(112,115,124,0.32)` |
| `--input` | line-solid-normal | `#E1E2E4` | `rgba(112,115,124,0.32)` |
| `--ring` | primary-normal | `#0066FF` | `#3385FF` |
| `--success` | status-positive | `#00BF40` | `#00BF40` |
| `--warning` | status-cautionary | `#FF9200` | `#FF9200` |

`--accent` 는 Wanted DS 시맨틱 표에 대응 토큰이 없다 — 현재 코드가 hover/보조강조용 청록 계열(`oklch(0.55 0.18 180)`, hue 180 ≈ cyan)을 쓰고 있어 가장 근접한 atomic 램프인 cyan 으로 대체를 제안한 것으로, **Wanted 실측치가 아니라 Jet-Rag 팀 판단**임을 명확히 구분한다.

### 2.5 현행 → 목표 대응 (diff)

| 현재 (`globals.css`) | 현재 값 | 목표 | 변화 |
|---|---|---|---|
| `--primary` | `oklch(0.45 0.15 250)` | `#0066FF` | oklch 계열 청색 → Wanted blue-50. 색상 자체는 이미 근사 파랑이라 시각 변화는 작음, **정의의 출처가 임의값 → 토큰화**가 핵심 변화 |
| `--background` | `oklch(0.985 0.002 250)` (살짝 청회색 흰색) | `#FFFFFF` | 순백으로 통일 |
| `--muted-foreground` | `oklch(0.5 0.02 250)` (고정 회색, 불투명) | `rgba(55,56,60,0.61)` (반투명) | 배경색이 바뀌어도 자동으로 대비 유지 — 다크 모드 전환 시 이 방식이 유리 |
| `--radius` | `0.5rem` (8px) | 유지 | Wanted Shadow/Radius 세부 수치가 이번 추출 범위에 없어 현행 유지, §5 참조 |
| `--font-sans` fallback | `'Noto Sans KR'` | `'Pretendard Variable', Pretendard, ...` | §3.1 — 폰트 자체가 로딩되지 않고 있어 CSS 변경만으론 반영 안 됨 |

---

## 3. 타이포그래피

### 3.1 폰트 로딩 현황 — 사실 확인

`web/src/app/layout.tsx` 는 현재 **`next/font/google` 의 `Noto_Sans_KR`** 을 로딩하며 `--font-sans` CSS 변수에 바인딩한다(`web/src/app/layout.tsx:2,12-17`). `globals.css` 의 `@theme inline` 은 `--font-sans` 뒤에 `'Noto Sans KR'` 을 폴백으로 나열하지만, 이는 변수가 비어있을 때를 위한 이중 안전망일 뿐 — **Pretendard 는 현재 전혀 로딩되지 않는다.**

Wanted DS 의 지정 폰트는 Pretendard(로고/디스플레이는 Wanted Sans). 이 문서는 Pretendard 적용을 "목표"로 남기고 이번 문서 작성으로 코드를 바꾸지 않는다 — 웹폰트 전환은 다음 중 하나의 방법이 필요하고 각각 트레이드오프가 있어 별도 결정 필요:

| 방법 | 설명 | 주의점 |
|---|---|---|
| `next/font/local` | Pretendard 정적/가변 폰트 파일을 프로젝트에 포함 | 폰트 파일 자체는 npm 의존성이 아니지만, 라이선스·용량(가변폰트 ~2MB) 확인 필요 |
| CDN `@import`(cdn.jsdelivr.net/gh/orioncactus/pretendard) | 코드 변경 최소 | 외부 리소스 의존 — 오프라인/CSP 이슈, 로딩 성능(FOUT) |
| 유지(Noto Sans KR) | 변경 없음 | Wanted DS 와 100% 동일 렌더링은 아님, 다만 한글 가독성 자체는 충분 |

권장: `next/font/local` — 단, 폰트 파일 도입은 "새 의존성"에 준하는 결정이라 실제 적용 전 사용자 승인 필요(§9).

### 3.2 스케일 — Wanted 전체값 vs Jet-Rag 서브셋

Wanted 전체 스케일(px):

| 역할 | px | 역할 | px |
|---|---|---|---|
| Display 1 | 56 | Headline 1 | 18 |
| Display 2 | 40 | Headline 2 | 17 |
| Title 1 | 36 (모바일 32) | Body 1 | 16 |
| Title 2 | 28 | Body 2 | 15 |
| Title 3 | 24 | Label 1 | 14 |
| Heading 1 | 22 | Label 2 | 13 |
| Heading 2 | 20 | Caption 1 | 12 |
| | | Caption 2 | 11 |

Jet-Rag 화면에 실제 필요한 서브셋과 현재 Tailwind 클래스 대응:

| 용도 | 목표 스케일 | 목표 px | 현재 클래스(대략 대응 px) | 비고 |
|---|---|---|---|---|
| Hero 타이틀 | Title 3 → Title 1 (mobile→desktop) | 24 → 36 | `text-2xl sm:text-3xl md:text-4xl lg:text-5xl` (24→30→36→48) | `lg` 48px 는 Wanted 스케일 밖 — Display 2(40) 캡을 권장하되, Hero 는 예외적으로 임팩트가 중요한 화면이라 48 유지도 허용 가능한 판단 |
| 카드 제목(문서명) | Headline 1/2 | 18/17 | `text-base md:text-lg` (16→18) | 거의 일치, `text-base`→`text-[17px]` 미세조정 여지 |
| 본문(quote, snippet) | Body 2 | 15 | `text-[15px]` | 정확히 일치 (`result-card.tsx:108`) — **이미 Wanted 스케일과 정합** |
| 요약(summary) | Body 1 | 16 | `text-sm` (14) | 갭 — Body 1(16) 대신 Label 1(14) 이 쓰이는 중, 요약은 본문급으로 올릴지 판단 필요 |
| 배지/태그 | Caption 1 | 12 | `text-[10px]~text-[11px]` | Wanted 최소 스케일(11px)보다 작은 10px 사용 중 — 가독성 하한선 재검토 권장 |
| 메타 정보(페이지·시간) | Caption 1/2 | 12/11 | `text-[11px]`, `text-xs`(12) | 거의 일치 |

원칙: **새 화면을 만들 때는 이 표의 좌측 "목표 스케일" 명칭으로 사고하고, 우측 px 를 Tailwind 임의값(`text-[Npx]`)으로 적용**한다. `text-sm`/`text-lg` 같은 Tailwind 기본 스케일과 Wanted 스케일이 항상 일치하지 않으므로 임의값 표기가 더 정확하다.

### 3.3 한국어 조판 규칙 (현행 유지)

- `break-keep` — 어절 단위 줄바꿈. 제목·본문 문장형 텍스트 필수(`hero-section.tsx`, `result-card.tsx`)
- `leading-7`(28px) — 인용/본문 블록의 줄간격, 한글 특성상 라틴 문자보다 여유 필요
- `break-words` — 문서 제목처럼 공백 없는 긴 토큰(파일명 등) 대응, `break-keep` 과 병행
- `tabular-nums` — 숫자 정렬(시간, 결과 수) 흔들림 방지

---

## 4. 스페이싱·레이아웃

- **그리드 단위**: 4px. Tailwind 기본 스페이싱(`p-1`=4px ~ `p-24`=96px)을 그대로 사용 — Wanted DS 도 4px 배수 체계라 별도 커스텀 스케일 불필요.
- **브레이크포인트**: Tailwind v4 기본값 유지(커스텀 오버라이드 없음 확인) — `sm` 640 / `md` 768 / `lg` 1024 / `xl` 1280 / `2xl` 1536.
- **모바일 우선 규칙**: 클래스는 기본(모바일) → `sm:`/`md:` 로 확장. 컨테이너는 `px-4 md:px-6`, 섹션 간격은 `py-6 md:py-12`(그리드), `py-10 md:py-24`(히어로) 로 이미 정착.
- **Safe area**: `env(safe-area-inset-top)` (헤더, `header.tsx:22`), `min-h-dvh`(레이아웃 루트) — iOS notch/home-indicator 대응 현행 패턴 유지. 하단 고정 UI(바텀시트, FAB 등)를 새로 만들 경우 `env(safe-area-inset-bottom)` 을 반드시 추가.
- **터치 타깃**: 최소 44px(Toss/HIG 기준). 현재 대부분의 1차 액션은 이를 충족(`h-11`=44px 검색 input, `h-12`=48px 히어로 input) 하나, 아이콘 버튼 기본값(`size-9`=36px, `button.tsx:28`)은 기준 미달 — **마우스 전용(desktop-only) 컨트롤은 예외 허용**(예: 디버그 토글), **모바일에 노출되는 아이콘 버튼은 `icon-lg`(40px) 이상 사용**을 권장.

---

## 5. 라운딩·그림자

### 5.1 라운딩 — 현행 패턴을 표준화

| 반경 | Tailwind | 용도 | 현재 사용처 |
|---|---|---|---|
| 6px | `rounded-md` | 버튼, 인풋(기본), 작은 컨트롤 | `button.tsx`, ablation 토글 |
| 12px | `rounded-xl` | 카드 기본, snippet 서브 박스 | `card.tsx` 기본, `result-card.tsx` snippet |
| 16px | `rounded-2xl` | 강조 표면 — 결과 카드, 히어로 검색 인풋 | `result-card.tsx:23`, `hero-section.tsx:64` |
| full | `rounded-full` | 배지·칩·아바타 | `badge.tsx`, 추천 query 칩 |

규칙: **표면의 "무게감"이 클수록 반경이 커진다** — 카드(주요 콘텐츠) > 인풋(상호작용) > 버튼(액션) 순. 새 컴포넌트도 이 위계를 따른다.

### 5.2 그림자 — Wanted 스케일과 현행의 갭

Wanted DS 그림자 스케일은 이름만 추출됨: `Normal/Xsmall·Small·Medium·Large·Xlarge`, `Spread/Small·Medium`, `Strong`. **정확한 blur/spread/opacity 수치는 이번 추출 범위에 없다** — 수치를 임의로 만들어내지 않고, 아래는 이름 단위의 잠정 대응이다:

| Wanted 스케일(이름만 확정) | 잠정 대응 Tailwind | 현재 사용처 |
|---|---|---|
| Normal/Xsmall | `shadow-xs` | outline 버튼 |
| Normal/Small | `shadow-sm` | 카드(`card.tsx`), sticky 헤더 |
| Normal/Medium~Xlarge, Spread, Strong | 미사용 | 모달/바텀시트 도입 시 필요해질 예정(§6.8) |

**후속 필요**: Figma 파일에서 각 그림자 레이어의 실제 `y-offset/blur/spread/opacity` 를 재추출해야 Medium 이상 스케일을 정의할 수 있다. 그 전까지는 현재 쓰는 `shadow-xs`/`shadow-sm` 두 단계로 충분히 커버되며, 무리해서 늘리지 않는다.

---

## 6. 핵심 컴포넌트 스펙

### 6.1 검색 인풋

| 속성 | 값 |
|---|---|
| 높이 | 히어로: `h-12`(48px) → `sm:h-14`(56px) / 서브헤더: `h-11`(44px) |
| 반경 | 히어로 모바일 `rounded-2xl`(16px) → `sm:rounded-xl`(12px) — 데스크톱에서 살짝 절제 |
| 배경 | background-elevated-normal(`#FFFFFF`) |
| 테두리 | 기본 `line-solid-normal`(`#E1E2E4`), focus 시 `primary-normal`(`#0066FF`) 2px |
| 아이콘 | 좌측 검색 아이콘(`text-muted-foreground`), 우측에 제출 버튼 흡수(별도 버튼 아님, 인풋과 한 몸) |
| placeholder | label-assistive 톤, 실제 검색 예시 문장(구체적 예시가 빈 상태보다 유도력 높음 — 현행 유지) |

### 6.2 결과 카드 (ResultCard)

- 바깥 카드: `rounded-2xl`, `shadow-sm`, border `line-solid-normal`
- 헤더: 문서 제목(Headline) + 문서유형 배지 + 태그 배지(최대 3개, 나머지는 암묵적으로 숨김 — 배지 폭주 방지)
- **quote bar 패턴(현행 유지 필수)**: 인용 snippet 은 `border-l-2 border-primary/30 pl-3` — 좌측 컬러 바로 "이건 원문에서 가져온 인용"임을 형태로 전달. 이 시각 문법을 다른 곳에 재사용해선 안 됨(인용 전용 의미 고정).
- snippet 메타(page·section)는 `uppercase tracking-wide text-[11px]` — Caption 스케일과 일치, 유지.
- footer: "+N개 더 매칭" 링크(matched_chunk_count 초과분) 또는 "매칭 N개", 우측 상대시간 — 매칭 강도(%) 는 `debug` 모드 전용, 일반 사용자에게 숫자 노출 금지(§1 원칙).

### 6.3 AI 답변 카드

현재 `/ask` 라우트로 별도 진입(카드 자체는 결과 리스트에 섞이지 않음) — 이 분리를 유지한다. 카드 내부는:
- 상단: Sparkles 아이콘 + "AI 답변" 레이블(primary 톤)
- 본문: 생성 텍스트, 출처 인용은 결과 카드와 동일한 quote bar 문법 재사용(일관성)
- 하단: 출처 문서로 이동하는 링크 — AI 답변이 "최종 답"이 아니라 "원본으로 가는 다리"임을 항상 표시

### 6.4 칩(모드 토글 / 추천 query)

| 종류 | 반경 | 활성 상태 | 비활성 상태 |
|---|---|---|---|
| 모드 토글(hybrid/dense/sparse) | `rounded-sm`(그룹 컨테이너는 `rounded-md`) | `bg-primary text-primary-foreground` | `text-muted-foreground` |
| 추천 query 칩 | `rounded-full` | — (클릭형, 상태 없음) | `border-border bg-card`, hover 시 `border-primary bg-primary/5 text-primary` |

모드 토글은 세그먼트 컨트롤(단일 그룹 내 상호 배타) 이므로 `rounded-sm` 내부 + `rounded-md` 외부 이중 반경 — 추천 칩은 독립 pill 이므로 `rounded-full`. 이 구분을 유지한다(세그먼트=내부 절제된 반경, 독립 액션=완전한 pill).

### 6.5 헤더

- `sticky top-0`, `bg-card/95 backdrop-blur`, `shadow-sm`, `safe-area-inset-top` 패딩 — 현행 유지
- 로고 + 검색(데스크톱만 인라인) + 업로드 CTA + 계정 메뉴 + 모바일 토글
- 모바일에서는 로고 텍스트 숨김(아이콘만), 보조 액션(설정/로그아웃)도 숨김 — 헤더의 "한 가지 일"은 이동/검색/업로드 세 가지로 제한

### 6.6 하단 영역(Footer)

콘텐츠보다 존재감이 낮아야 한다 — `line-solid-alternative` 급의 옅은 상단 구분선, label-alternative 톤 텍스트. 별도 CTA 배치 금지(이미 헤더/히어로에 있음).

### 6.7 업로드 진입점

히어로의 "파일 업로드" 버튼(outline, 2차 CTA)과 헤더의 업로드 버튼(desktop, primary 축소판) 두 곳 — 이미 위계가 있다: 히어로=탐색 단계의 보조 행동, 헤더=상시 접근 가능한 주 행동. 신규 업로드 진입점을 추가할 때 이 위계(주 행동은 헤더/전용 페이지, 보조는 콘텍스트 내 링크)를 따른다.

### 6.8 바텀시트 / 풀스크린 모달 (신규 정의 — 현재 컴포넌트 없음)

`web/src/components/ui/` 에 `Sheet`/`Dialog` 계열이 아직 없다. Toss 레퍼런스를 따르는 이 프로젝트는 모바일에서 다음 기준으로 도입한다(실제 구현은 별도 작업):

| 상황 | 컴포넌트 |
|---|---|
| 짧은 선택(정렬, 필터 1~2개) | 바텀시트, 높이는 콘텐츠에 맞춤(`auto`), 배경 스크림 `label-normal` 40% |
| 여러 단계 입력(업로드 설정 등) | 풀스크린 모달(모바일), 데스크톱은 중앙 다이얼로그 |
| 확인/취소 단순 결정 | 바텀시트 내 버튼 2개(주 액션 하단 고정, 취소는 텍스트 링크) |

반경은 바텀시트 상단만 `rounded-t-2xl`, 그림자는 §5.2 Spread 계열(수치 미정 — 도입 시 재추출 필요).

### 6.9 빈 상태 / 로딩 / 에러 상태

- **로딩**: `Skeleton` 으로 실제 레이아웃 형태를 미리 그림(현재 `search/loading.tsx` 패턴 — 리스트면 리스트 모양 스켈레톤). 스피너 단독 사용 금지 — 레이아웃 시프트 방지.
- **에러**: 아이콘(원형 배경, `status-negative` 또는 `status-cautionary` 10% 배경 + 해당 컬러 아이콘) + 제목(한 줄) + 설명(한 줄) + 단일 CTA("다시 시도"). 에러 종류가 늘어도 이 4단 구조는 고정(`search/error.tsx` 패턴).
- **빈 상태**: (현재 코드에 전용 컴포넌트 미확인 — 검색 결과 0건 등에서 신규 정의 필요) 아이콘 + "~을 찾지 못했어요" 톤의 짧은 안내 + 대안 행동(다른 검색어 제안 또는 업로드 유도) 1개.

---

## 7. 모션

Wanted DS 추출본에는 duration/easing 수치가 없다 — 이 섹션은 Toss 레퍼런스 원칙에서 도출한 **제안 컨벤션**이며, 향후 Figma 에 모션 토큰이 추가되면 대체한다.

| 토큰 | 값 | 용도 |
|---|---|---|
| `--duration-instant` | 100ms | 호버 컬러 전환, 배지 상태 변화 |
| `--duration-fast` | 150ms | 버튼 press, 토글 전환 |
| `--duration-base` | 220ms | 카드 등장, 바텀시트 슬라이드 |
| `--duration-slow` | 320ms | 풀스크린 모달 전환 |
| `--ease-standard` | `cubic-bezier(0.4, 0, 0.2, 1)` | 대부분의 등장/전환 |
| `--ease-out` | `cubic-bezier(0, 0, 0.2, 1)` | 사라짐(exit) |

절제 기준: **위치 이동은 최소화, 불투명도·색상 전환을 우선**한다(레이아웃 시프트가 모션보다 체감 비용이 큼). 이미 코드의 `useTransition` 기반 interruptible navigation(`search-subheader.tsx`)이 이 원칙과 부합 — React 상태 전환에 별도 CSS 애니메이션을 얹지 않고 `disabled`/`aria-busy` 로 즉각적 피드백만 주는 현재 패턴을 유지한다.

---

## 8. 다크 모드 (적용 시 기준)

**2026-07-19 적용 완료** — layout.tsx THEME_INIT_SCRIPT(FOUC 방지) + ThemeToggle(헤더/모바일 패널) + `.dark` 블록. 적용 당시 기준:

1. §2.3/§2.4 의 다크 값을 `globals.css` `.dark` 블록에 채운다.
2. `(추정)` 표시된 값(elevated 배경, secondary/muted, solid border)은 실제 Wanted 다크 프레임을 Figma 에서 재추출해 검증 — 지금 값은 시작점일 뿐 최종안이 아니다.
3. 토글은 시스템 설정 감지(`prefers-color-scheme`) 우선 + 사용자 수동 오버라이드 저장(로컬스토리지) 조합을 권장 — Toss 앱도 시스템 연동 기본.
4. 다크 모드에서 quote bar(`border-primary/30`)처럼 `/30` 같은 알파 오버레이를 쓰는 부분은 다크 배경에서 대비가 달라지므로 전환 후 반드시 육안 재확인.

---

## 9. 적용 우선순위

이 문서는 기준 정의이며, 아래는 "다음에 무엇을 먼저 할지"에 대한 순서 제안 — 실제 코드 변경은 별도 작업으로 진행한다.

### 즉시 적용 가능 (코드 리스크 낮음, 값 치환 위주)

1. `globals.css` 의 `oklch()` 컬러 변수를 §2.4 표의 hex/rgba 값으로 치환 — 시각적 변화는 미미(이미 유사 색상대)하지만 **임의값에서 토큰 출처가 명확한 값으로 전환**된다는 게 핵심.
2. `--accent` 를 cyan 계열로 명시적 결정(현재도 유사 색이라 변화 작음, 다만 "왜 이 색인지"가 문서화됨).
3. §3.2 타이포 서브셋 표를 참고해 배지류의 `text-[10px]` 를 Caption 하한(11px)에 맞출지 판단.

### 후속 (설계/코드 영향 있음, 별도 결정 필요)

4. ~~**Pretendard 폰트 전환**~~ — ✅ 2026-07-19 적용 (next/font/local 가변 폰트, 사용자 파일 제공·승인).
5. **다크 모드 완성** — ✅ 토글·시스템 감지 적용(2026-07-19). `(추정)` 값의 Figma 재추출 검증만 잔여.
6. **그림자 Medium 이상 스케일 확정** — Figma 재추출 필요(§5.2).
7. **바텀시트/모달 컴포넌트 신규 구현** — §6.8 기준으로 `Sheet` 계열 도입(현재 프로젝트에 Radix Dialog 계열 미설치 — 의존성 추가 여부 판단 필요).
8. **빈 상태(empty state) 컴포넌트 표준화** — §6.9.

이 문서에서 내린 세 가지 핵심 판단:

- **컬러는 Wanted 시맨틱 토큰을 1:1로 채택**하고, Wanted 에 대응 토큰이 없는 항목(`--accent`)만 Jet-Rag 자체 판단으로 채워 명확히 구분했다.
- **다크 모드는 추출 데이터의 공백을 숨기지 않고 `(추정)` 표시로 노출**했다 — 지금 값을 "완성"으로 오인해 그대로 배포하면 재검증 없이 어긋난 다크 UI가 나갈 위험이 있기 때문.
- **현행 Toss 패턴(quote bar, break-keep, rounded-2xl 카드, 매칭 강도 debug 전용)은 전부 유지 대상으로 명시**했다 — 이 문서가 잘 동작하는 것까지 다시 뜯게 만드는 문서가 되지 않도록.
