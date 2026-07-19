# 2026-07-19 크로스 OS 검증 — Android·Windows·macOS·iPhone

> 실기기 없이 헤드리스 크로미움(UA·뷰포트·스케일 에뮬레이션) + 프로덕션 API 실측으로 4개 환경 검증. 전부 PASS — 검증 중 발견한 모바일 우측 21px 잘림 버그 1건 즉시 수정·ship (`0963e93`).

## 배경

사용자가 Android를 연결할 Windows 머신이 없어 실기기 검증 불가 → 에뮬레이션 기반 검증 요청. 이후 맥북 13인치·아이폰13 크기 추가 요청. 대상은 전날 ship한 다크모드 + Pretendard + share_target 확대 + UX-1 fallback.

## 한 일

### 1. 검증 매트릭스 (프로덕션 jetrag.woong-s.com)

| 환경 | 뷰포트 | 콘솔 에러 | 가로 오버플로 | Pretendard | 다크 토글+새로고침 유지 |
|---|---|---|---|---|---|
| Android (Pixel 8 UA) | 412×915 | 0 | 수정 후 0 | loaded | ✅ (모바일 패널 경로) |
| iPhone 13 (iOS Safari UA) | 390×844 @3x | 0 | 0 | loaded | ✅ + 다크 검색 페이지 정상 |
| Windows (Win10 Chrome UA) | 1280×720 | 0 | 0 | loaded | — (엔진 동일, 코드 레벨 확인) |
| macOS (맥북 13", Chrome UA) | 1440×900 @2x | 0 | 0 | loaded | ✅ (헤더 달 아이콘 경로) |

### 2. Android share_target 재현 (curl, 프로덕션 `/share`)
- `.hwp`(MIME octet-stream — Android 브라우저 실전송 형태) → **401** = 라우트 통과, 백엔드 인증 도달 (정상)
- `.png` → **401** (정상) / `.exe` → **400** + "PDF, HWP, HWPX, JPG, PNG, HEIC 파일만 업로드할 수 있습니다." (차단 정상)

### 3. UX-1 fallback 프로덕션 실증 (보너스)
"SK 사업보고서 매출" 쿼리 → 응답 헤더 **`x-search-path: meta_fast_fallback`** 실측 + SK 사업보고서 매출 내용 반환. 2026-07-18 수정(`a5c89f3`) 전에는 0건이던 트랩 쿼리가 실서비스에서 해소됨을 확인.

### 4. 발견·수정한 버그 — 모바일 우측 21px 잘림 (`0963e93`)
- 증상: 모바일에서 홈 페이지 폭 433px(뷰포트+21px) — "최근 추가" 카드의 "11일"·우측 테두리 잘림. `overflow-x: clip` 때문에 스크롤은 안 생겨 그동안 은폐됨
- 원인: `home-grid.tsx` 그리드 컬럼 래퍼에 `min-w-0` 누락 → grid 아이템 기본 `min-width:auto`가 공백 없는 긴 문서 제목("25년케이터링제이(한국은행)…")의 min-content(417px)로 트랙을 밀어냄. 2026-05-27 모바일 스프린트의 min-w-0 패턴이 컬럼 래퍼에만 빠져 있던 것
- 진단: 라이브 스타일 실험(min-width:0 강제 시 433→412 복구)으로 원인 증명 후 2줄 수정
- 검증: 로컬 412=412 → ship → 프로덕션 재측정 390=390(아이폰13)·412(Android) 정합, 제목 truncate(…) 정상

### 5. 기타
- `.gitignore`에 `.gstack/`(gstack 툴링 자동 추가)·`.omc/`(OMC 에이전트 상태 폴더) 등록 — git status 노이즈 제거

## 검증

- 스크린샷 검수 6장: Android 라이트/다크, 수정 후, 아이폰13 다크 검색, 맥북 라이트/다크 — 두 테마 모두 가독성·잘림 0
- 수정분 `pnpm tsc --noEmit` 0 에러 / `pnpm lint` 0 에러
- 배포 반영 폴링 실측 (433 → 390/412 정합 확인)

## 주요 의사결정

| 항목 | 결정 | 근거 |
|---|---|---|
| 검증 방식 | 헤드리스 크로미움 에뮬레이션 + curl 재현 | 실기기·Windows 머신 부재, OS 레벨 외 전 항목 커버 가능 |
| 21px 잘림 | 발견 즉시 수정 (2줄) | trivial 범위 + 모바일 우선 기획 의도 직결 |

## 보류/이월 (실기기 필요 — 에뮬레이션 한계)

- [ ] Android 실기기: PWA 설치 + 공유 시트 "Jet-Rag" 실제 등록 확인 (OS 레벨, 에뮬레이션 불가 — 조건은 전부 충족 상태)
- [ ] iPhone 실기기: WebKit(Safari) 고유 동작 — standalone 상태바, 사파리 하단 툴바 safe-area (헤드리스는 크로미움이라 100% 동일 보장 불가)
- [ ] iOS 단축어 플로우 실측

## 다음 작업

1. 실기기 확보 시 위 이월 3건 확인
2. `meta_fast_fallback` 헤더 비율 모니터링 (fallback 빈도가 높으면 fast-path 판정 재설계 검토)
