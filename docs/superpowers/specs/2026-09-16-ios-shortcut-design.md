# ③ 아이폰 단축어 자동 수집 — 설계 메모 (2026-09-16)

> **상태**: 조사·설계 초안(조사 에이전트 작성, 컨트롤러 검토). 실기기 실측 6건(§6)이 끝나야 확정.
> **자리**: 자동 수집 로드맵 ① PC 에이전트(완료) → **③ 아이폰** → ② 안드로이드. 서버 계약은 `2026-09-15-pc-agent-auto-ingest-design.md` §10.
> **서버 선행 변경**: 모든 오류 본문에 `code` 필드(401 `auth`, 403 `scope`, 429 `rate_limited`). 단축어는 HTTP 상태코드를 못 읽는다.

## ③ 아이폰 단축어 자동 수집 — 설계 메모 (2026-09-16)

### 0. 전제 (로컬 확인 완료)
`ios-shortcut` 채널은 **이미 서버에 열려 있다** — `/Users/kiwoongmin/Desktop/claude-project/Jet-Rag/supabase/functions/_shared/documents/upload.ts:25`, `/Users/kiwoongmin/Desktop/claude-project/Jet-Rag/api/migrations/031_metering_v2.sql:47`. 계약은 스펙 §10(`docs/superpowers/specs/2026-09-15-pc-agent-auto-ingest-design.md:297`)과 `agent/src/client.ts`를 그대로 따른다. 참고로 iOS **27**이 2026-09-14 정식 배포됐다 ([Yahoo/ZDNET](https://tech.yahoo.com/ai/apple-intelligence/articles/ios-27s-shortcuts-upgrade-makes-203200178.html)).

---

### 1. 트리거 — 스크린샷 트리거가 **실재한다**
Apple 공식 문서가 Event trigger로 `Screenshot`을 명시한다: "Photos: Triggers when a screenshot is saved to the Photos app / Files / Clipboard" ([Apple, Event triggers](https://support.apple.com/guide/shortcuts/event-triggers-apd932ff833f/ios)). 같은 페이지의 Event 트리거 전체: Time of Day, Alarm, Sleep, **Keyboard**, **Screenshot**, **Notification**, Apple Watch Workout, Sound Recognition. Setting 트리거: Wi‑Fi, Bluetooth, Focus, Low Power Mode, Battery Level, **Charger**, NFC, **App(Is Opened / Is Closed)**, Airplane Mode ([Apple, Setting triggers](https://support.apple.com/en-by/guide/shortcuts/apde31e9638b/ios)).

- (a) 새 스크린샷 → **Screenshot 트리거** 있음.
- (b) 새 사진/파일 → iOS엔 폴더 감시 트리거 **없음**(폴더·파일 트리거는 macOS 26 전용, [Apple 125148](https://support.apple.com/en-us/125148)).
- (c) 시각 → Time of Day 있음.
- (d) 앱 열림/닫힘 → App 트리거로 "카카오톡 Is Closed" 가능.
- (e) "파일 수신" 트리거 → **없음**. Notification 트리거(키워드 필터)가 유일한 근사.

**무확인 실행**: Apple이 자동 실행 가능하다고 나열한 목록은 "Time of Day, Alarm, Sleep, Arrive, Leave, CarPlay, Email, Message, Transaction, Wi‑Fi, Bluetooth, Apple Watch Workout, NFC, App, Aeroplane Mode, Do Not Disturb, Low Power Mode, Battery Level, Charger, Sound Recognition"이고 예외는 "Before I Commute" ([Apple](https://support.apple.com/en-au/guide/shortcuts/apd602971e63/ios)). 이 목록에 Screenshot·Keyboard·Notification이 **없다** — 문서가 갱신 안 된 것인지 실제로 확인이 필요한지 **미검증**. 또 iOS 포인트 업데이트가 "Run Immediately"를 "Ask Before Running"으로 되돌린 사례가 보고돼 있다 ([MacRumors](https://forums.macrumors.com/threads/automation-set-to-run-immediately-keeps-asking-for-confirmation-since-18-2.2445766/)).

→ **설계 방어**: 트리거가 스크린샷을 입력으로 넘겨주는지도 **미검증**이므로, 트리거 종류와 무관하게 본문은 `Find Photos`(필터: Is a Screenshot = true, Creation Date is after [저장된 last_run])로 스스로 찾게 한다 ([Apple, Find/Filter 필터 파라미터](https://support.apple.com/guide/shortcuts/add-filter-parameters-apdbdab3433f/ios)). 트리거가 무확인으로 안 돌면 Charger 연결 / Time of Day로 바꿔 끼우기만 하면 된다.

### 2. 액션 능력
- **multipart**: 가능. "To make a multipart HTTP request, choose 'Form' as the request body type and add files as field values" ([Matthew Cassinelli, Get Contents of URL](https://matthewcassinelli.com/actions/get-contents-of-url/)). Headers 자유 추가 가능 → `Authorization`, `User-Agent: JetRag-Shortcut/0.1 (ios)` 로 Cloudflare 1010 회피(스펙 §2).
- **SHA‑256**: `Generate Hash` 액션이 MD5/SHA1/**SHA256**/SHA512 지원, 입력으로 **파일**을 받는다 ([동 출처](https://matthewcassinelli.com/actions/generate-hash/)).
- **상태 저장**: ① iCloud Drive 텍스트 파일(무의존, iCloud 오프로딩 위험) ② Data Jar(키‑값, iCloud 동기화, 3rd‑party) — 둘 다 검증된 관행 ([Hacking Shortcuts](https://nadnosliw.wordpress.com/2023/08/05/how-to-make-persistent-variables-for-ios-and-ipados-shortcuts-hacking-shortcuts-version-2/)). **권장: iCloud Drive `/Shortcuts/jetrag_state.json` 1개.**
- **한계(중요)**: Shortcuts는 **HTTP 상태코드를 읽을 수 없다** — 본문만 돌려준다 ([Apple Dev Forums](https://developer.apple.com/forums/thread/651963)). 그 외 커뮤니티 보고(모두 **미검증**): 요청 ~25초 타임아웃, 5MB 초과 전송 시 "Failed to open URL asynchronously", Base64 액션 기본값이 76자마다 줄바꿈.
- **잠금 중 실행 / 백그라운드 시간**: 공식 수치 없음. 잠금 시 다수 액션이 완주 못 하고 단계 수가 적을수록 안정적이라는 커뮤니티 보고만 있음 — **미검증** ([Automators](https://talk.automators.fm/t/why-do-some-time-triggered-shortcuts-run-on-a-locked-iphone-and-others-fail/18608)).

### 3. 카카오톡 파일
앱이 Files 연동을 하지 않으면 파일은 샌드박스에 남고 Files 앱에서 안 보인다 ([iMazing](https://imazing.com/guides/how-to-access-your-iphone-apps-files-and-data)). 한국 블로그는 "파일 앱의 카카오톡 폴더"를 말하지만 이는 사용자가 **"파일에 저장"을 직접 눌렀을 때** 생기는 경로로 읽힌다 — **미검증, 실기 확인 필요**. 게다가 Shortcuts가 폴더를 처음 읽을 때 "Allow … to access your '<folder>' folder?" 승인 대화가 뜬다 ([Apple Community](https://discussions.apple.com/thread/255526670)).

→ **카톡은 자동 경로를 포기하고 공유 시트로 간다.** 단축어를 Share Sheet 수신으로 켜고(입력 타입: Files, Images), `Repeat with Each [Shortcut Input]` 안에서 파일별 업로드 ([Apple, Repeat](https://support.apple.com/guide/shortcuts/use-repeat-actions-apdc11deb2c1/ios) · [Share Sheet](https://support.apple.com/guide/shortcuts/receive-onscreen-items-apd350ce757a/ios)). 카톡에서 파일 여러 개 선택 → 공유 → "Jet‑Rag에 보내기" 2탭.

### 4. 배포·토큰
iCloud 링크로 공유하며, 내려받는 쪽은 "Apple이 검수하지 않았다"는 경고와 함께 내용을 **검사할 수 있다** ([Apple 보안 가이드](https://support.apple.com/guide/security/secure-features-in-the-shortcuts-app-secec043bdae/web)). **따라서 토큰을 단축어 안에 넣고 링크를 공유하면 토큰이 유출된다.** → 배포 단축어에는 토큰 자리를 비우고, 최초 1회 `Ask for Input` → iCloud Drive `jetrag_state.json`에 저장. 그 파일은 iCloud 동기화·백업에 평문으로 남는다(기기 토큰이라 폐기 가능한 점이 완화책, 스펙 §S3).

### 5. 제안 설계

**(A) "Jet‑Rag 스크린샷 자동 업로드"** — Automation, 트리거 `Screenshot → Photos`(무확인 불가 시 Charger Is Connected 또는 Time of Day 1시간)
1. `Get File` (iCloud/Shortcuts/jetrag_state.json) → `Get Dictionary from Input` → `token`, `last_run`
2. `Find Photos` — Is a Screenshot **is** true AND Creation Date **is after** `last_run`, Sort by Creation Date, Limit 20
3. `Repeat with Each`
   a. `Generate Hash` (SHA256, Repeat Item)
   b. `Get Contents of URL` POST `…/documents/precheck`, Headers: Authorization/User‑Agent, JSON `{"hashes":[hash]}` → `Get Dictionary Value results.<hash>.state`
   c. `If state is not "existing"` → `Get Contents of URL` POST `…/documents`, **Form**: `file`=Repeat Item, `source_channel`=`ios-shortcut`, `title`=파일명
   d. 응답에 `code`가 있으면 `Show Notification` 후 `Stop Shortcut`
4. `Set Dictionary Value last_run = Current Date` → `Save File`(Overwrite)

**(B) "Jet‑Rag에 보내기"** — Share Sheet 수신(Files·Images·Media), Ask 없음. 2~4는 A와 동일, `last_run` 갱신만 생략.

**실패 처리**: 상태코드를 못 읽으므로 **본문의 `code`로만 분기**한다. 402 `storage_limit` → 알림 "용량 초과", 429 → 알림 없이 `Stop`(다음 실행이 이어받음), 401/403 → 알림 "토큰 재발급 필요" + `last_run` 갱신 금지.

**서버 변경 — 최소 1건**: 채널·precheck·multipart는 그대로 쓰면 되므로 `upload-base64`나 `/me/devices/ping`은 **불필요**. 단 401·403·429 응답이 현재 한국어 `detail`만 싣고 5xx는 `text/plain`이라(§10) 단축어가 구분할 수 없다. → **모든 오류 본문에 `code`를 싣는다**(`auth`·`scope`·`rate_limited`·`server`). 업로드 4xx는 이미 싣고 있으니 추가는 3~4줄.

### 6. 사용자 실측 목록
1. Screenshot 트리거에 **"Run Immediately" 토글이 있는지**, 잠금 상태·화면 꺼짐에서 실제로 도는지 (스크린샷 3장 연속 촬영 후 알림 확인)
2. 트리거가 스크린샷을 `Shortcut Input`으로 넘기는지 (`Quick Look`으로 확인)
3. 카톡에서 PDF 받기 → Files 앱에 "카카오톡" 폴더가 **자동으로** 생기는지, 아니면 "파일에 저장"을 눌러야만 생기는지
4. `Get Contents of URL` Form 업로드로 **5MB·20MB PDF**가 실제로 202를 받는지, 25초 타임아웃 여부
5. `Generate Hash(SHA256)` 결과가 `agent/src/hash.ts`의 `sha256Hex`와 동일한지 (같은 파일로 대조)
6. precheck 응답 `results` 파싱이 `Get Dictionary Value`로 되는지(키가 해시 문자열)

**Sources**: [Event triggers](https://support.apple.com/guide/shortcuts/event-triggers-apd932ff833f/ios) · [Setting triggers](https://support.apple.com/en-by/guide/shortcuts/apde31e9638b/ios) · [자동 실행 목록](https://support.apple.com/en-au/guide/shortcuts/apd602971e63/ios) · [iOS/macOS 26 신규](https://support.apple.com/en-us/125148) · [Find/Filter 필터](https://support.apple.com/guide/shortcuts/add-filter-parameters-apdbdab3433f/ios) · [Repeat](https://support.apple.com/guide/shortcuts/use-repeat-actions-apdc11deb2c1/ios) · [Share Sheet 입력](https://support.apple.com/guide/shortcuts/receive-onscreen-items-apd350ce757a/ios) · [Shortcuts 보안](https://support.apple.com/guide/security/secure-features-in-the-shortcuts-app-secec043bdae/web) · [Get Contents of URL](https://matthewcassinelli.com/actions/get-contents-of-url/) · [Generate Hash](https://matthewcassinelli.com/actions/generate-hash/) · [상태코드 불가](https://developer.apple.com/forums/thread/651963) · [잠금 중 실행](https://talk.automators.fm/t/why-do-some-time-triggered-shortcuts-run-on-a-locked-iphone-and-others-fail/18608) · [영구 변수](https://nadnosliw.wordpress.com/2023/08/05/how-to-make-persistent-variables-for-ios-and-ipados-shortcuts-hacking-shortcuts-version-2/) · [앱 샌드박스](https://imazing.com/guides/how-to-access-your-iphone-apps-files-and-data) · [폴더 접근 승인](https://discussions.apple.com/thread/255526670) · [Run Immediately 리셋](https://forums.macrumors.com/threads/automation-set-to-run-immediately-keeps-asking-for-confirmation-since-18-2.2445766/) · [iOS 27 배포](https://tech.yahoo.com/ai/apple-intelligence/articles/ios-27s-shortcuts-upgrade-makes-203200178.html)
