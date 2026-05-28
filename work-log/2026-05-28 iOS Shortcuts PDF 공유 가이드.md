# iOS Shortcuts PDF 공유 가이드 — Jet-Rag

작성일: 2026-05-28
대상: iOS / iPadOS 사용자
소요 시간: 약 3분 (1회 설정)

## 배경 — 왜 단축어 앱이 필요한가

Android Chrome 은 PWA 의 `manifest.json > share_target` 을 지원해 Acrobat / Drive / 갤러리에서 "Jet-Rag 로 공유" 항목이 OS 공유 시트에 자동 등록된다. 반면 iOS Safari 는 ITP(Intelligent Tracking Prevention) 정책 유지 차원에서 2026-05 현재도 `share_target` 을 미지원한다. iOS 사용자가 Adobe Acrobat 에서 PDF 를 Jet-Rag 로 직접 보내려면 Apple 의 **단축어(Shortcuts) 앱** 으로 우회한다.

단축어 앱은 iOS 기본 탑재 (없으면 App Store 에서 무료 설치). HTTP multipart 요청을 액션으로 만들어 공유 시트에 등록할 수 있다.

---

## 단계별 설정

### 1. 단축어 앱 열기 + 새 단축어 생성

1. **단축어** 앱 실행 → 우측 상단 **+** 탭
2. 상단 단축어 이름 영역을 길게 눌러 **이름 변경** → `Jet-Rag 로 보내기`
3. 우측 상단 **(i)** 아이콘 → **세부사항** 탭 → **공유 시트에서 받기** 토글 ON
4. 그 아래 **수신 항목** 에서 모든 항목 OFF → **PDF 만** ON (오타 방지)

### 2. 액션 추가 — URL 의 콘텐츠 가져오기

1. 하단 **액션 추가** → 검색창에 `URL의 콘텐츠 가져오기` 입력 → 액션 탭
2. 추가된 액션에서 **URL** 필드 탭 → 값 입력:
   ```
   https://jetrag-api.woong-s.com/documents
   ```
3. 동일 액션 안 **자세히 보기** 펼치기 → 다음 값 설정:
   - **방법**: `POST`
   - **요청 본문**: `양식(Form)`
   - **양식 필드 추가** → 키 = `file`, 종류 = `파일`, 값 = `단축어 입력` (= 상단 회색 토큰 선택)

> 백엔드 라우터 prefix 가 `/documents` 라 `/api/v1/documents` 가 아닌 위 경로를 사용해야 한다. 잘못 입력하면 404 가 떨어진다.

### 3. (선택) 결과 확인 액션 추가

1. **액션 추가** → `결과 보기` 검색 → 액션 탭
2. 또는 `알림 표시` 액션으로 "Jet-Rag 업로드 완료" 같은 고정 문구도 가능

### 4. 저장

우측 상단 **완료** 탭 → 단축어 목록에 `Jet-Rag 로 보내기` 표시 확인.

---

## 사용 흐름 (실제 업로드)

1. **Adobe Acrobat** (또는 메일 / iCloud / 다운로드) 에서 PDF 열기
2. 화면 우상단 **공유** 버튼 탭 → iOS 공유 시트 표시
3. 시트 하단 **추가 작업** → **단축어** → `Jet-Rag 로 보내기` 탭
4. (최초 1회) "신뢰할 수 없는 단축어" 경고 → **신뢰** 탭
5. 업로드 시작 — 백엔드 응답을 결과 보기 화면 또는 알림으로 확인
6. 업로드 완료되면 `https://jetrag.woong-s.com/docs` 에서 상태 확인 (수신 → 추출 → 청킹 → 임베딩)

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| 응답 status `503` (서비스 사용 불가) | Portfolio Mode 데모 ENV (`JETRAG_DEMO_READONLY=true`) 활성화 | 로그인 모드 복원 + 본인 계정 토큰 첨부 필요 — 후속 결정 사항 §1 참고 |
| 응답 status `401` (인증 실패) | Auth 가 켜진 시점 + 단축어가 cookie/token 미첨부 | 단축어 액션에 `요청 헤더 > Authorization: Bearer <token>` 추가 (token 유출 위험 → §2 참고) |
| 응답 status `400` "PDF 만 허용" | iOS 가 PDF 가 아닌 항목을 잘못 전달 | 단축어 세부사항 > 수신 항목 에서 PDF 외 항목 모두 OFF 인지 확인 |
| 응답 status `413` (Payload Too Large) | 50MB 초과 | PDF 압축 또는 분할 후 재시도 |
| 응답 status `502/504` | Railway 백엔드 cold start 또는 일시 장애 | 1~2분 후 재시도. `https://jetrag-api.woong-s.com/health` 200 확인 후 진행 |

---

## 후속 결정 사항

### §1. Portfolio Mode 와의 충돌

현재 Vercel/Railway production 은 `JETRAG_DEMO_READONLY=true` 로 모든 write endpoint 가 503 을 반환한다 (Portfolio Mode C+, commit `493a57f`). 본 가이드대로 단축어 설정 후 업로드 시도 시 503 으로 차단된다. 두 가지 선택지가 있다:

1. **개인 사용자 모드만 ENV 토글** — `JETRAG_DEMO_READONLY=false` 로 잠시 해제, 그 시간 동안 readonly 게이트가 없어진다는 사이드 이펙트.
2. **per-user 화이트리스트** — 멀티유저 D1 Auth (`work-log/2026-05-19 세션 종합` Q9 참고) 진입 후 본인 user_id 만 write 허용하는 로직 추가. 권장 — 12 doc 데모 보존하면서 본인 업로드 가능.

→ 사용자 결정 대기.

### §2. 단축어에 Bearer 토큰을 박는 보안 리스크

Auth 켠 뒤 단축어에 long-lived JWT 를 하드코딩하면 디바이스 분실 시 토큰 노출. 대안:

1. **단축어 안에 `텍스트` 액션으로 token 별도 보관** + iCloud Sync OFF — 디바이스 잠금 의존.
2. **백엔드에 device-token 발행 endpoint** — 단축어 전용 짧은 만료 토큰. 구현 비용 증가.

→ 멀티유저 D1 Auth 진입 시점에 함께 결정.

### §3. 동영상 / 이미지 추가 지원

현재 manifest 와 본 가이드는 PDF only. JPEG / PNG / HEIC 도 백엔드 어댑터에서 지원하는데 (`/documents` POST 동일 endpoint), 추후 사용자 요청 시:

1. `manifest.json > share_target.params.files[0].accept` 에 `image/jpeg`, `image/png`, `image/heic` 추가
2. `/share/route.ts` 의 `ACCEPTED_MIME` 상수를 배열로 변경
3. 단축어 세부사항 수신 항목에 이미지 ON

→ 베타 추가 피드백 후 결정.

---

## 참고

- Android 사용자는 단축어 불필요 — PWA 설치 후 share_target 자동 등록.
- 본 흐름은 `web/src/app/share/route.ts` 가 단순 forward proxy 역할만 수행 — 단축어가 직접 백엔드를 호출하는 것과 동일.
- 단축어가 백엔드를 직접 호출하므로 `/share` 라우트를 안 거친다 (CORS 만 통과하면 OK, Railway 가 `https://jetrag.woong-s.com` allowlist 에 들어가 있어 issue 없음).
