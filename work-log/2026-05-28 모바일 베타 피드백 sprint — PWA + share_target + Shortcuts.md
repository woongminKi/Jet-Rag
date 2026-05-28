# 2026-05-28 모바일 베타 피드백 sprint — PWA + Android share_target + iOS Shortcuts

> 본 세션 시작 HEAD `eca12bf` (Portfolio Mode C+ + Mobile UIUX Toss) → 마감 HEAD **(uncommitted, 본 세션 commit 0)**
> 변경 누적 **6 files** (신설 5 + 수정 1), layout.tsx +22/-1, 나머지는 신규.
> 단위 테스트: 백엔드 0 수정 — baseline 1300 PASS 유지 가정 (venv pytest 미설치라 본 머신 실행 불가, 다른 머신/CI 재확인 권장).
> production 상태: **배포 전, 사용자 검수 대기**.

---

## 0. 핵심 요약

베타 사용자가 준 3건의 의견 ("스크린샷 자동 수집·폰에 심기·Adobe PDF 캡처 백그라운드 수집") 을 기술적으로 가능한 형태로 변환해 적용한 sprint. OS 보안 모델상 "캡처 자동 감지 → PDF 자동 수집" 은 불가능함을 사용자에게 솔직히 전달한 후, 동등 UX 를 제공하는 3가지 진입점으로 분해:

1. **PWA 설치** — manifest.json + 192/512 아이콘 + layout metadata 확장. 모바일 사파리/크롬에서 "홈 화면에 추가" → 진짜 앱처럼 동작.
2. **Android share_target** — manifest 의 `share_target` 선언 + `/share` POST route 신설. Acrobat 공유 시트에서 "Jet-Rag" 한 번 탭으로 PDF 자동 업로드.
3. **iOS Shortcuts 가이드** — iOS Safari 가 share_target 미지원이라 단축어 앱으로 1회 설정 우회.

**핵심 제약**: 사용자가 명시 요청한 **"PC 로직 영향 0"** 보장을 위해 작업 전 절대 안전 조건 4개를 메모리에 저장 (`feedback_mobile_pwa_safety.md`), senior-developer prompt 에 명시. 모든 조건 준수 확인.

---

## 1. 본 세션 시작·마감 시점

| 항목 | 시작 (`eca12bf`) | 마감 (uncommitted) | Δ |
|---|---|---|---|
| commit 누적 | 565+ | 565+ | 0 (commit 보류, 사용자 검수 대기) |
| 신설 파일 | — | 5 | +5 |
| 수정 파일 | — | 1 (layout.tsx) | +1 |
| 페이지 컴포넌트 수정 | — | **0건** | (안전 조건 ③ 준수) |
| 백엔드 수정 | — | **0건** | (안전 조건 ④ 준수) |
| Service Worker | 없음 | 없음 | (안전 조건 ① 준수) |

---

## 2. 변경 사항

### 2.1 신설 파일 (5)

| 파일 | 용도 | 크기 |
|---|---|---|
| `web/public/manifest.json` | PWA manifest (share_target 포함) | 792B |
| `web/public/icon-192.png` | PWA 아이콘 192x192 | 6.5KB |
| `web/public/icon-512.png` | PWA 아이콘 512x512 (maskable safe area) | 23KB |
| `web/src/app/share/route.ts` | Android share_target POST handler (117 lines) | — |
| `work-log/2026-05-28 iOS Shortcuts PDF 공유 가이드.md` | iOS 사용자 1회 설정 가이드 | — |

**아이콘 생성**: `web/src/app/favicon.ico` (검정 원 + 흰 삼각형 = "Jet" 모티프) 기반, macOS `sips` 로 PNG 변환 + maskable safe area padding. 외부 의존성 0.

### 2.2 수정 파일 (1)

`web/src/app/layout.tsx` (+22/-1):
- `import type { Metadata, Viewport }` 추가
- `metadata` 객체에 `manifest: '/manifest.json'`, `icons`, `appleWebApp` 추가
- 신규 `export const viewport: Viewport` — themeColor / viewportFit / width / initialScale (Next.js 16 의 `viewport` 별도 export 패턴 준수)
- **RootLayout 본문 0줄 변경** — 다른 페이지 영향 0 보장

### 2.3 manifest.json 핵심 블록

```json
"share_target": {
  "action": "/share",
  "method": "POST",
  "enctype": "multipart/form-data",
  "params": { "files": [ { "name": "file", "accept": ["application/pdf"] } ] }
}
```

이 한 선언이 Android OS 에게 "Jet-Rag 는 PDF 받을 수 있다" 고 알려줘, 공유 시트에 자동 등록됨.

### 2.4 share/route.ts 동작 흐름

- `GET`: `/docs` 로 303 redirect (PC URL 직타이핑 / 검색엔진 크롤러 무해)
- `POST`:
  1. `formData()` parse — 실패 400
  2. `file` / `files` 키 양쪽 시도 (브라우저별 spec 차이)
  3. `application/pdf` 검증 → 400
  4. 50MB 초과 → 413
  5. 새 FormData 에 `file` 키로 forward → `${API_BASE}/documents` POST
  6. 백엔드 503 (Portfolio Mode) → 한국어 wrap 후 503 전달
  7. 성공 → `/docs` 303 redirect
  8. 그 외 → status + detail 보존

**보안 노트**: 현재 Portfolio Mode 라 auth 헤더 미주입. 로그인 모드 복원 시 `cookies()` 로 sb-access-token 추출 + Authorization 헤더 추가 (line 70~73 주석에 위치 명시).

---

## 3. 의사결정 기록

### DECISION-A (베타 피드백의 기술적 가능 여부)
- 베타 사용자 의도: "캡처만 해도 자동 수집"
- **OS 차원 불가능** — iOS/Android 모두 third-party 앱 간 파일 접근 차단 (privacy boundary)
- 대안: **공유 시트 1탭** (OS 가 정식으로 PDF 원본을 다른 앱에 넘기는 통로)
- 사용자에게 솔직히 전달 + 베타 사용자 확인 권장

### DECISION-B (절대 안전 조건 4개 = PC 영향 0 보장)
- 사용자 명시 요청: "PC 로직에 전혀 이상 없다는거지?"
- 4 조건 메모리 저장 (`feedback_mobile_pwa_safety.md`) — 모든 PWA/모바일 sprint 의 강제 가드
  1. Service Worker 만들지 않기
  2. 새 라우트는 POST only
  3. 기존 페이지 컴포넌트 수정 0건
  4. 백엔드 0 변경

### DECISION-C (PWA 도입 깊이)
- 옵션 A — manifest + share_target route 만 (SW 0)
- 옵션 B — manifest + SW (오프라인 캐시 + 푸시 알림)
- **옵션 A 채택** — 사용자 요구 (PC 영향 0) 우선. SW 는 추후 sprint.

### DECISION-D (Portfolio Mode 충돌 처리)
- 현재 `JETRAG_DEMO_READONLY=true` → 백엔드 /documents POST 503
- 옵션 A — share_target 도 503 그대로 전달 (한국어 wrap, **채택**)
- 옵션 B — share_target 만 본인 user_id 화이트리스트로 우회 (멀티유저 D1 Auth 의존, 후속)
- 옵션 C — DEMO_READONLY OFF (Portfolio Mode 전면 해제, 비용 risk)
- 본 sprint 는 옵션 A. 후속 멀티유저 D1 Auth 진입 시 옵션 B 자동 가능.

### DECISION-E (Next.js 16 viewport 별도 export)
- Next.js 15+ deprecation — `metadata.themeColor` / `metadata.viewport` 가 deprecated, `export const viewport: Viewport` 권장
- `web/AGENTS.md` 의 "This is NOT the Next.js you know" 정책 준수

### DECISION-F (iOS 우회 방식)
- 옵션 A — Capacitor / native wrapper (Apple Developer 계정 $99/년)
- 옵션 B — iOS Shortcuts 가이드 (사용자 1회 설정 ~3분, **채택**)
- 옵션 C — react-native 재작성 (전면 재구축)
- 옵션 B 가 비용 0 + 즉시 가능

### DECISION-G (commit 보류)
- senior-developer 작업 완료 후 사용자 검수 / 실기기 테스트 대기
- Vercel 배포 → Android Chrome 에서 PWA 설치 + Acrobat 공유 시트 출현 확인 후 commit 권장
- 본 work-log 도 검수 후 같이 commit

---

## 4. 검증 결과

| 검증 | 결과 | 비고 |
|---|---|---|
| `npx tsc --noEmit` (web) | **PASS** (exit 0) | Viewport 타입 정상 |
| `npx eslint .` (web) | **PASS** (0 error / 0 warning) | — |
| `python -m pytest` (api) | **실행 불가** | 본 머신 venv 에 pytest 미설치, pyproject.toml 에 test extras 없음. 백엔드 0 수정이라 회귀 위험 자체 0. CI/다른 머신에서 baseline 1300 PASS 재확인 권장. |
| `grep serviceWorker.register\|sw.js` | **PASS** (0 매치) | 안전 조건 ① |
| `share/route.ts` GET redirect | **PASS** | line 36~38 NextResponse.redirect 303 |
| `git diff --stat HEAD` | **PASS** | 신설 5 + 수정 1, 허용 목록 정확히 일치 |
| 페이지 컴포넌트 0 수정 | **PASS** | `/`, `/search`, `/ask`, `/doc/[id]`, `/docs`, `/ingest`, `/admin` 전부 untouched |

---

## 5. 미해결 / 후속 결정 사항

### 5.1 production 검증 (배포 후 사용자 실측 필수)

| 항목 | 방법 |
|---|---|
| Vercel 자동 빌드 통과 | dashboard 확인 |
| jetrag.woong-s.com `/manifest.json` 응답 | curl 또는 브라우저 확인 |
| Android Chrome PWA 설치 | 모바일에서 "홈 화면에 추가" |
| Acrobat 공유 시트에 "Jet-Rag" 출현 | Android 실기기 |
| iOS 단축어 설정 후 동작 | iOS 실기기 |
| `/share` POST 503 응답 (Portfolio Mode) | curl 또는 단축어 실측 |

### 5.2 Portfolio Mode 와 share_target 동시 활성화

현재 데모 보존 + 본인 업로드 허용은 멀티유저 D1 Auth 후 가능 (Q9 의사결정 트리 연계).
- 즉시 해결책: Railway ENV `JETRAG_DEMO_READONLY=false` (전면 노출 risk)
- 권장 해결책: 멀티유저 D1 Auth + per-user write 게이트 (별도 sprint)
- 현재 상태: share_target 동작은 503 wrap. iOS 단축어도 동일.

### 5.3 백엔드 pytest 환경

본 머신 `.venv` 에 pytest 미설치. `pyproject.toml` 에 test extras 자체 없음. 의존성 추가 정책상 임의 설치 보류.
- 다른 머신 또는 CI 에서 baseline 1300 PASS 확인 필요
- 백엔드 코드 0 수정이라 위험 자체는 0

### 5.4 후속 기능 후보

- **이미지/HEIC 확장** — 현재 PDF only, manifest accept 배열 + ACCEPTED_MIME 상수만 변경하면 확장 가능
- **Service Worker** — 오프라인 캐시 / 푸시 알림 필요 시 별도 sprint
- **iOS 단축어 JWT 보안** — 디바이스 분실 시 토큰 노출 risk, 멀티유저 진입 후 재검토
- **Android 실기기 테스트 자동화** — Playwright mobile / BrowserStack 도입 검토

---

## 6. 적용 시 사용자가 해야 할 것

### 6.1 즉시 (코드 검수 + 배포)

1. `git status` 로 6개 변경 확인
2. work-log 본 문서 + iOS Shortcuts 가이드 검수
3. (선택) Vercel 미리보기 빌드로 테스트 → main commit & push → 자동 배포
4. jetrag.woong-s.com 에서 manifest.json 응답 확인

### 6.2 검수 후 (실기기)

1. **Android Chrome**: jetrag.woong-s.com → "앱 설치" 또는 "홈 화면에 추가" → Acrobat 에서 PDF → 공유 → "Jet-Rag" 출현 확인 → 탭 시 503 wrap 메시지 확인 (Portfolio Mode 라 정상)
2. **iOS Safari**: jetrag.woong-s.com → "홈 화면에 추가" → 단축어 가이드 따라 1회 설정 → Acrobat 에서 공유 → "Jet-Rag 로 보내기" → 503 응답 확인

### 6.3 Portfolio Mode 와 본인 업로드 동시 활성화 결정 (후속 sprint)

옵션 A (즉시·비추천): Railway ENV `JETRAG_DEMO_READONLY=false` → 전면 노출
옵션 B (권장·후속): 멀티유저 D1 Auth + per-user write 게이트

---

## 7. 다음 세션 진입 우선순위

| 권고도 | 작업 | 비고 |
|---|---|---|
| 🔴 최우선 | **production 실기기 검증** | Android 공유 시트 + iOS 단축어 동작 확인 |
| 🔴 검수 | **본 sprint commit & push** | 사용자 검수 통과 후 |
| 🟡 보통 | **Portfolio Mode + share_target 정책 결정** | 멀티유저 D1 Auth sprint 진입 여부 |
| 🟢 새 sprint | **이미지/HEIC 확장** | 베타 추가 피드백 시 |
| 🟢 큰 그림 | **v1.6 / PRD M3 잔여** | W-9 답변 UX / KPI 측정 |

---

## 8. 핵심 한 줄

> **베타 피드백 3건 (스크린샷·폰에 심기·Adobe 공유) 을 OS 보안 한계 안에서 가능한 형태 (PWA install + Android share_target + iOS Shortcuts) 로 분해 적용. 절대 안전 조건 4개 준수 → PC 로직 영향 0 보장. 6 파일 (신설 5 + 수정 1) 변경, 백엔드 0 수정. 실기기 검증 + commit 대기.**
