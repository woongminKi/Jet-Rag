# 2026-07-19 다크모드 + Pretendard 적용

> design.md §9 후속 항목 4·5 구현 — 다크모드 활성화(시스템 감지 + 수동 토글) + Pretendard 가변 폰트 self-host 전환. 사용자가 폰트 파일 제공으로 도입 승인.

## 배경

2026-07-18 design.md에서 보류했던 두 후속 작업을 사용자가 승인·지시. Pretendard 1.3.9는 사용자가 로컬 파일(`~/Downloads/Pretendard-1.3.9`)로 제공. Railway → Supabase 이전은 계산 결과(백엔드 22,113줄 TS 재작성 + HWP 파싱 Deno 생태계 부재 vs 절감 월 $5) 비추천으로 결론, Railway 유지 확정.

## 한 일

### 1. Pretendard 가변 폰트 전환
- `web/src/fonts/PretendardVariable.woff2` (2.0MB, SIL OFL — 라이선스 동봉) 도입. 정적 웨이트 4개(~3MB)보다 가변 1파일이 유리해 가변 선택
- `layout.tsx`: `next/font/google` Noto_Sans_KR → `next/font/local`, `weight: '45 920'`, `--font-sans` 변수명 유지(하위 호환)
- `globals.css` 폰트 fallback 체인 정리

### 2. 다크모드 활성화 (의존성 0)
- `globals.css` `.dark` 블록: 구 oklch 잔재 → design.md §2.4 다크 컬럼 전면 교체 (--background #1B1C1E, --primary #3385FF, --border rgba(112,115,124,0.32) 등). `(추정)` 값은 인라인 주석으로 표시 유지. chart-*/sidebar-*는 라이트와 동일하게 불변
- FOUC 방지: `layout.tsx` `THEME_INIT_SCRIPT` (beforeInteractive) — 첫 페인트 전 `<html>`에 `.dark` 적용. localStorage `theme` 수동 오버라이드 우선, 없으면 `prefers-color-scheme`. `<html suppressHydrationWarning>`
- 토글 UI: 신규 `theme-toggle.tsx` — 헤더 데스크톱(icon-lg 40px) + 모바일 패널(전체폭). React 19 lint(`set-state-in-effect`)는 AGENTS.md 패턴대로 `.then()` 마이크로태스크로 회피
- `viewport.themeColor` media 배열화 (light #FFFFFF / dark #1B1C1E). manifest는 라이트 기본값 유지
- 다크 대비 스팟픽스 3곳: `answer-view`(신뢰도 배지)·`upload-item`(warning 안내)·`router-signals-badge` — 틴트 배경(`bg-success/10` 등) 위 #171719 텍스트가 다크에서 묻히는 조합만 최소 수정. solid 배지는 양 테마 안전해 무수정
- design.md §2.3/§8/§9 상태 서술 현행화 (미적용 → 적용됨)

## 검증

- `pnpm tsc --noEmit` 0 에러 / `pnpm lint` 0 에러 / `pnpm build` 성공(exit 0) — executor 검증 후 본 세션에서 재실행 재확인
- 백엔드 변경 0건 (프론트 전용)
- 신규 npm 의존성 0 (폰트 파일은 승인된 도입, next-themes 미사용 자체 구현)

## 주요 의사결정

| 항목 | 결정 | 근거 |
|---|---|---|
| 폰트 포맷 | 가변 1파일 (2.0MB) | 정적 4웨이트 합계(~3MB)보다 작고 weight 자유 |
| 테마 상태 관리 | localStorage + 인라인 스크립트 (라이브러리 X) | 의존성 추가 금지, next-themes 없이 동일 UX |
| themeColor meta | 시스템 기준 media 배열만 | 수동 오버라이드까지 meta 동기화는 과설계 |
| 다크 status 색 | 라이트와 동일 hex | design.md §2.3 — 고채도라 다크 배경 대비 충분 |
| Railway → Supabase 이전 | 하지 않음 | 절감 연 $60 vs 22k줄 재작성 + HWP 파싱 블로커 |

## 보류/이월

- [ ] 다크 `(추정)` 값(elevated/secondary/muted 등) Figma 다크 프레임 재추출 검증 — design.md §8-2
- [ ] 실기기 다크모드 육안 확인 (iOS/Android, quote bar `border-primary/30` 등 알파 합성부)
- [ ] 폰트 2.0MB 최초 로드 — 필요 시 서브셋 경량화 검토 (현재 cache 후 무부담)

## 다음 작업

1. Vercel 자동 배포 확인 후 모바일 실기기에서 다크 토글 + Pretendard 렌더 확인
2. 이전 이월 항목: Android HWP 공유 실측, `meta_fast_fallback` 헤더 비율 모니터링
