# 실기기 검증 가이드 — iOS + Android

작성일: 2026-05-28
대상: Jet-Rag PWA + share_target 기능 (commit `c9f7c5a`, `74378fe`)
프로덕션 URL: https://jetrag.woong-s.com / API: https://jetrag-api.woong-s.com
소요 시간: Android ~15분 / iOS ~20분

---

## 0. 사전 준비 (PC 에서 1회)

실기기 테스트 전에 PC 브라우저로 배포 상태 확인.

### 0.1 Vercel 자동 빌드 통과 확인

1. Vercel dashboard 접속 → Jet-Rag 프로젝트 → Deployments
2. 최신 commit `74378fe` (docs) 또는 `c9f7c5a` (feat) 의 빌드 상태 = **Ready** 확인
3. 빌드 실패 시 로그 확인 → tsc / eslint 에러 가능성 (로컬 PASS 확인했으나 환경 차이 가능)

### 0.2 manifest.json 응답 확인

PC 터미널 또는 브라우저:
```bash
curl -s https://jetrag.woong-s.com/manifest.json | jq .
```

기대 응답:
```json
{
  "name": "Jet-Rag",
  "short_name": "Jet-Rag",
  "share_target": { ... },
  "icons": [...]
}
```

브라우저로 직접 열어도 동일. JSON 그대로 응답돼야 함.

### 0.3 아이콘 응답 확인

```bash
curl -sI https://jetrag.woong-s.com/icon-192.png | head -3
curl -sI https://jetrag.woong-s.com/icon-512.png | head -3
```

`HTTP/2 200` + `content-type: image/png` 확인.

### 0.4 /share GET redirect 확인

```bash
curl -sI https://jetrag.woong-s.com/share | head -5
```

기대: `HTTP/2 303` + `location: /docs` (또는 절대 URL `https://jetrag.woong-s.com/docs`)

### 0.5 /share POST 503 wrap 확인 (선택)

```bash
echo "dummy" > /tmp/test.pdf
curl -i -X POST https://jetrag.woong-s.com/share \
  -F "file=@/tmp/test.pdf;type=application/pdf"
```

기대: `HTTP/2 503` + `{"error":"현재 데모 모드입니다. 본인 로그인 후 다시 시도하세요."}` (Railway `JETRAG_DEMO_READONLY=true` 적용 결과)

`200` 또는 다른 상태가 나오면 Portfolio Mode 게이트 검증 필요.

### 0.6 PC Chrome DevTools — Manifest 검사

1. Chrome 으로 https://jetrag.woong-s.com 접속
2. F12 → Application 탭 → 좌측 Manifest 항목
3. 다음 모두 표시되는지 확인:
   - Name: Jet-Rag
   - Start URL: /
   - Theme color: #0B0B0F (검정)
   - Display: standalone
   - **Share target** 섹션 — action /share, method POST, accept application/pdf
   - Icons — 192 / 512 둘 다 미리보기 보임 (404 아님)
4. 좌측 패널 하단에 빨간 에러 없음

PC Chrome 도 주소창 우측에 **앱 설치** 아이콘 (모니터 + 아래쪽 화살표) 노출 가능. 이건 정상 — PC 에 설치해도 read-only 데모 동작 동일.

---

## 1. Android (AOS) 검증 — Chrome 권장

### 사전
- Android 9+ (Pie+) 권장. 구버전은 share_target spec 일부 미지원.
- **Chrome 90+** 사용 (Samsung Internet, Firefox 도 share_target 지원하지만 Chrome 권장)
- Adobe Acrobat Reader 앱 설치 (Play Store 무료)
- 테스트용 PDF 1개 (작은 사이즈 권장, 5MB 이하)

### 1.1 PWA 설치

1. Android Chrome 으로 https://jetrag.woong-s.com 접속
2. 우상단 점 3개 (⋮) → **앱 설치** (또는 "홈 화면에 추가")
   - 옵션이 안 보이면 manifest 응답 또는 HTTPS 인증서 문제 → §5 트러블슈팅
3. 설치 확인 dialog → **설치** 탭
4. 홈 화면에 Jet-Rag 아이콘 (검정 배경 + 흰 삼각형) 추가 확인
5. 아이콘 탭 → 주소창 없는 standalone 앱처럼 열림 확인 (status bar 위 색상 #0B0B0F 검정)

**검증 포인트**:
- [ ] 아이콘 모양 정상 (깨지지 않음, 흐릿하지 않음)
- [ ] 설치 후 standalone 으로 열림 (주소창 보이지 않음)
- [ ] status bar 색 검정 (#0B0B0F)
- [ ] 홈 화면·검색 결과·앱 서랍 모두 Jet-Rag 검색 가능

### 1.2 OS 공유 시트에 Jet-Rag 출현 확인 — Acrobat

1. Adobe Acrobat Reader 앱 실행 → 테스트 PDF 열기
2. 우상단 또는 하단 **공유** 아이콘 (보통 ↗ 모양) 탭
3. 공유 방식 선택 화면에서 "**파일 보내기**" / "**다른 앱으로 열기**" / "**공유**" 등 (앱 버전 차이) → 일반 안드로이드 공유 시트 진입
4. 공유 시트에 **Jet-Rag** 항목 출현 확인
   - 아이콘 192px 가 표시됨
   - 항목 이름 = "Jet-Rag"

**검증 포인트**:
- [ ] Jet-Rag 항목이 공유 시트에 보임
- [ ] 아이콘 정상 (192px 가 잘 렌더링됨)
- [ ] 항목 이름 정확 (Jet-Rag)
- [ ] PDF 외 형식 (이미지·텍스트) 공유 시도 시에는 **출현 안 함** (manifest accept 가 application/pdf only 이므로 정상)

### 1.3 1탭 공유 → 업로드 시도 → 503 wrap 확인

1. 공유 시트의 **Jet-Rag** 탭
2. Android Chrome 이 백그라운드에서 `POST /share` 호출 → 백엔드 `/documents` forward → Portfolio Mode `JETRAG_DEMO_READONLY=true` 라 503 응답
3. PWA 가 503 응답을 받음. 두 가지 동작 가능:
   - **case A**: 토스트/배너 "현재 데모 모드입니다. 본인 로그인 후 다시 시도하세요." 표시 (NextResponse.json 응답)
   - **case B**: PWA 안에서 raw JSON `{"error":"..."}` 화면 표시 (브라우저 기본 동작)
4. 현재 코드는 jsonError() 응답이라 **case B 가 정상** — 본 sprint 는 UX wrap 까지는 안 만듦 (UX 개선은 후속 sprint)

**검증 포인트**:
- [ ] 공유 후 PWA 가 열림 (또는 백그라운드 동작)
- [ ] 503 응답이 사용자에게 보임 (raw JSON 도 OK)
- [ ] 한국어 "현재 데모 모드입니다..." 문구 확인
- [ ] 500/401/timeout 등 다른 에러 아님 (Portfolio Mode 게이트 정상 작동 증거)

### 1.4 다른 앱에서 공유 시도

Acrobat 외에도 share_target 정상 동작 확인:
- **Google Drive**: PDF 파일 우상단 ⋮ → 공유 → Jet-Rag 선택
- **Files (구글 파일 관리자)**: PDF 길게 누름 → 공유 → Jet-Rag
- **Samsung 내 파일** / **OneDrive** 등도 동일

**검증 포인트**:
- [ ] Drive 에서 출현
- [ ] 파일 관리자에서 출현
- [ ] 모든 출처에서 동일한 503 wrap 응답

### 1.5 라이트한 부정 케이스 (선택)

- **이미지 (JPG/PNG) 공유**: Jet-Rag 출현 안 함 — 정상 (accept=application/pdf only)
- **링크/텍스트 공유**: Jet-Rag 출현 안 함 — 정상
- **PWA 미설치 상태에서 공유 시트**: Jet-Rag 미출현 — 정상 (설치 전엔 share_target 미등록)

### 1.6 PWA 제거 확인

1. 홈 화면 Jet-Rag 아이콘 길게 누름 → **앱 정보** 또는 **제거**
2. 제거 후 Acrobat 공유 시트에서 Jet-Rag 사라짐 확인
3. (재테스트 시) 다시 §1.1 부터 설치

---

## 2. iOS 검증 — Safari + 단축어

iOS Safari 는 PWA share_target 을 지원하지 않으므로, 다음 두 가지를 분리해서 검증:

(A) PWA 설치 (홈화면 추가) → 일반 RAG 사용 검증
(B) 단축어 앱으로 공유 시트 진입점 우회 → PDF 업로드 검증

### 사전
- iOS 16+ 권장 (단축어 앱 안정성 확보)
- **Safari** 사용 (Chrome iOS 도 가능하지만 PWA 설치는 Safari 권장)
- Adobe Acrobat Reader iOS 앱 (App Store 무료)
- 단축어(Shortcuts) 앱 (iOS 기본 탑재 — 없으면 App Store 에서 무료)
- 테스트용 PDF 1개

### 2.1 PWA 설치 (홈 화면에 추가)

1. iOS Safari 로 https://jetrag.woong-s.com 접속
2. 하단 가운데 **공유** 아이콘 (↑ 박스) 탭
3. 스크롤 → "**홈 화면에 추가**" 탭
4. 미리보기 확인 (아이콘 + 이름 "Jet-Rag") → 우상단 **추가** 탭
5. 홈 화면에 Jet-Rag 아이콘 추가 확인

**검증 포인트**:
- [ ] 아이콘 모양 정상 (apple-touch-icon 으로 icon-192.png 사용)
- [ ] 아이콘 탭 시 standalone 모드 (주소창·하단 탭바 없음)
- [ ] status bar 색 검정 + 흰 글자 (appleWebApp.statusBarStyle="black-translucent" + viewport.themeColor="#0B0B0F" 결과)
- [ ] notch / Dynamic Island 영역 침범 없음 (viewportFit=cover + safe-area-inset 결과)

### 2.2 PWA 안에서 일반 동작 확인

1. 홈 화면 Jet-Rag 탭 → 홈 페이지 진입
2. 검색창에 한국어 query 입력 (예: "경제전망") → 검색 결과 표시 확인
3. 결과 카드 탭 → 문서 상세 진입
4. 모바일 UIUX (Toss 풍, 2026-05-27 sprint) 정상 동작 확인:
   - 좌우 스크롤 0
   - 콘텐츠 잘림 0
   - 카드 rounded-2xl + min-w-0 패턴 적용됨

**검증 포인트**:
- [ ] 검색 정상 동작
- [ ] 답변 생성 정상
- [ ] 문서 상세 정상
- [ ] 모바일 UIUX 회귀 없음

### 2.3 단축어 앱 설정 (1회만)

`work-log/2026-05-28 iOS Shortcuts PDF 공유 가이드.md` 참조. 핵심만:

1. **단축어** 앱 실행 → "+" → 단축어 이름 "Jet-Rag 로 보내기"
2. **(i)** → **공유 시트에서 받기** ON → **PDF 만** ON (다른 형식 OFF)
3. 액션 추가 — **URL의 콘텐츠 가져오기**:
   - URL: `https://jetrag-api.woong-s.com/documents`
   - 방법: POST
   - 본문: **양식**
   - 양식 입력값: 키 `file`, 값 **단축어 입력** (변수 선택)
4. 액션 추가 — **결과 보기** (response 확인용)
5. 저장 → 단축어 목록에 "Jet-Rag 로 보내기" 표시 확인

**검증 포인트**:
- [ ] 단축어 저장 성공
- [ ] 공유 시트에서 받기 토글 ON 확인
- [ ] PDF 타입만 활성화 확인

### 2.4 단축어로 공유 → 503 응답 확인

1. Adobe Acrobat Reader iOS 에서 테스트 PDF 열기
2. 우상단 또는 하단 **공유** 아이콘 탭
3. iOS 공유 시트 표시 → 하단 액션 영역에서 **"Jet-Rag 로 보내기"** 찾기
   - 없으면 ⋯ 또는 "편집" → "단축어" 활성화
4. 탭 시 단축어 실행 → POST 호출 → 503 응답
5. **결과 보기** 액션이 503 JSON 응답을 alert 로 표시:
   ```
   {"detail":"503: 데모 모드(read-only) 입니다..."}
   ```
   또는 `share/route.ts` 통하지 않고 백엔드 직접 호출이므로 백엔드 503 응답 원문이 보임 — 정상.

**검증 포인트**:
- [ ] 공유 시트에 "Jet-Rag 로 보내기" 출현
- [ ] 탭 시 단축어 실행됨
- [ ] 503 응답 alert 표시
- [ ] 한국어 detail 메시지 확인 (Portfolio Mode 게이트 정상 작동 증거)

### 2.5 부정 케이스 / 라이트 검증 (선택)

- **JPG 공유 시도**: 단축어 "PDF 만" 설정이라 출현 안 함 — 정상
- **단축어 미설정 상태**: 공유 시트에 항목 없음 — 정상
- **3G/LTE 환경**: 같은 동작 확인 (Wi-Fi 의존 아님)

### 2.6 PWA 제거 확인 (선택)

1. 홈 화면 Jet-Rag 길게 누름 → "북마크 삭제" 또는 "앱 삭제"
2. 단축어는 별도 — 단축어 앱에서 "Jet-Rag 로 보내기" 길게 눌러 삭제

---

## 3. 검증 체크리스트 (요약)

| # | 항목 | Android | iOS |
|---|---|---|---|
| 1 | PWA 설치 | ✅ Chrome → "앱 설치" | ✅ Safari → "홈 화면에 추가" |
| 2 | 아이콘 표시 | 192px maskable | apple-touch-icon |
| 3 | Standalone 모드 | 주소창 없음 | 주소창 없음, status bar 검정 |
| 4 | manifest 응답 | DevTools 확인 (PC) | (브라우저 직접 fetch) |
| 5 | 검색·답변 기본 동작 | OK | OK |
| 6 | 공유 시트 진입점 | **PWA share_target** (자동 등록) | **단축어 앱** (1회 설정) |
| 7 | Acrobat 공유 → Jet-Rag 출현 | ✅ | ✅ (단축어 설정 후) |
| 8 | PDF 공유 시 응답 | 503 한국어 wrap | 503 백엔드 원문 |
| 9 | 비-PDF 형식 거부 | 공유 시트 미출현 | 단축어 미출현 |
| 10 | PC 로직 회귀 | 0 (검색·답변 정상) | 0 (검색·답변 정상) |

모든 항목 PASS 시 본 sprint 검수 종료. 단 1건이라도 미동작 시 §5 트러블슈팅 + 후속 sprint 결정.

---

## 4. 멀티유저 D1 Auth 진입 후 재검증 (후속)

현재 모든 업로드 경로 503 (Portfolio Mode). D1 Auth 진입 시:
- 본인 로그인 → JWT 발급 → `/share` route 가 cookie 에서 토큰 추출 → Authorization 헤더 forward
- 백엔드 `forbid_demo_writes` 가 본인 user_id 화이트리스트로 우회 처리
- 단축어는 JWT 토큰 하드코딩 (Bearer auto-fill) → 디바이스 분실 시 토큰 회전 필요

재검증 시:
- §1.3 / §2.4 에서 503 대신 **201 Created** + 인제스트 시작 확인
- 인제스트 큐 (/docs) 에서 새 PDF 항목 진입 확인
- 30초~몇 분 후 검색 가능 상태 확인

---

## 5. 트러블슈팅

### 5.1 Android Chrome 에 "앱 설치" 옵션이 안 보임

원인 후보:
1. manifest.json 응답 실패 (404 / 5xx) → §0.2 재확인
2. HTTPS 인증서 문제 (자체 서명 등) → Vercel 은 Let's Encrypt 라 정상이어야 함
3. 아이콘 192/512 응답 실패 → §0.3 재확인
4. Chrome PWA install heuristic 미충족 → 화면 살짝 스크롤 후 메뉴 재진입
5. 이미 설치됨 → 메뉴에서 "설치된 앱 열기" 보임

확인 방법: Android Chrome 에서 `chrome://flags` → "Mobile PWA Install Promotion" 검색 (디버그 용도)

### 5.2 Android Acrobat 공유 시트에 Jet-Rag 안 보임

원인 후보:
1. PWA 미설치 — §1.1 재실행
2. PDF 가 아닌 형식 공유 시도 — accept=application/pdf only
3. Chrome 의 OS share registration 지연 — 재부팅 또는 PWA 다시 열기
4. 기기가 share_target spec 미지원 (Android 8 이하 / Chrome 89 이하) — 버전 확인

확인 방법: `chrome://flags/#web-share-target` 가 enabled 인지 확인 (기본 enabled)

### 5.3 iOS 단축어가 공유 시트에 안 보임

원인 후보:
1. 단축어 "공유 시트에서 받기" 토글 OFF — §2.3 (i) 다시 확인
2. "수신 항목" 에서 PDF 미체크 — 동일
3. iOS 공유 시트 하단 액션 영역 스크롤 못 봄 — 좌우/하단 더 살펴보기
4. 단축어 앱 권한 미허용 — 설정 앱 → 단축어 → 권한 확인

### 5.4 503 가 아닌 다른 에러

| 응답 | 원인 | 해결 |
|---|---|---|
| 400 | manifest accept 불일치 / 파일 형식 다름 | PDF 만 시도 |
| 401 | Auth 토큰 누락 (Portfolio Mode 라 정상은 503) | Railway ENV `JETRAG_DEMO_READONLY` 값 재확인 |
| 413 | 50MB 초과 | 더 작은 PDF 로 재시도 |
| 502 | 백엔드 다운 / Railway 재시작 중 | 1분 후 재시도 |
| 5xx (기타) | 백엔드 예외 | Railway logs 확인 |
| timeout | 네트워크 / 백엔드 cold start | 첫 호출은 ~10초 가능, 재시도 |

### 5.5 PC 사이트가 영향 받음 (회귀)

본 sprint 의 절대 안전 조건 4개를 어긴 변경 가능성. 의심 시 (Read 후) git revert.

빠른 체크:
1. PC Chrome 으로 https://jetrag.woong-s.com 접속 → 검색 / 답변 / 문서 상세 정상 동작
2. DevTools Console 에 unhandled 에러 없음
3. Network 탭 — /api/v1/search 정상 200 응답

회귀 발견 시 본 sprint commit `c9f7c5a` revert 후 senior-developer 재투입.

---

## 6. 검증 종료 후 다음 단계

### 6.1 모든 PASS

1. 본 문서에 PASS 도장 (체크박스 채우기)
2. 베타테스터 3명에게 검증 결과 + 사용법 공유:
   - Android: "Chrome 에서 jetrag.woong-s.com → 앱 설치 → Acrobat 공유" (3 step)
   - iOS: "단축어 앱 가이드 따라 1회 설정 → Acrobat 공유 → Jet-Rag 로 보내기" (5 step)
3. 다음 sprint 결정:
   - 다크 모드 (~30분)
   - 멀티유저 D1 Auth (~6-10h, share_target 도 본인 업로드 활성화)
   - v1.6 / PRD M3 잔여 (W-9 답변 UX)

### 6.2 부분 PASS

미동작 항목별 후속 sprint 등록. 보통:
- 아이콘 깨짐 → manifest 또는 favicon 재생성
- 공유 시트 미출현 → manifest share_target 스펙 재확인
- 503 외 응답 → 백엔드 forbid_demo_writes 확인

---

## 7. 핵심 한 줄

> **검증 = 사전 PC 1회 + Android 3 step (설치·공유·503 확인) + iOS 5 step (설치·단축어 설정·공유·503 확인) + PC 회귀 0 확인. 1건이라도 미동작 시 §5 트러블슈팅, 모두 PASS 시 멀티유저 D1 Auth sprint 진입 권장.**
