# 2026-07-18 기획 gap 분석 + design.md(Wanted DS) + P1 개선 3건

> 기획 의도("공공기관 PDF/HWP/이미지를 올려두고 어렴풋한 기억으로 검색, 모바일 우선") 대비 6축 gap 분석 → design.md 신설 → P1 개선 2건 + P2 1건 구현·QA PASS — 시작 HEAD `15b6ee2` (pull 후), **본 세션 commit 0 (전부 uncommitted, 사용자 검수 대기)**

## 배경

사용자가 기획 의도 대비 구현 정합성 점검 + `Wanted Design System (Community).fig`를 base로 한 design.md 작성 + 개선 작업을 요청. 세션 도중 `git pull --rebase`로 수익화 W1~W6 스프린트(쓰기 복원·rate limit·quota·email ingest·카카오페이)가 반영된 `15b6ee2`로 갱신하고 그 기준으로 재분석.

## 한 일

### 1. 기획 의도 대비 6축 gap 분석 (QA 에이전트, HEAD 15b6ee2 기준)

| 축 | 판정 |
|---|---|
| 포맷 커버리지 (PDF/HWP/이미지) | 잘 구현됨 — 단 PWA share_target이 PDF-only (→ #3에서 해소) |
| "어렴풋한 기억" 검색 품질 | 잘 구현됨 (하이브리드+동의어+caption+vision enrich ON) |
| 모바일 우선 | 부분 — 다크모드 미적용, share_target PDF-only |
| 핵심 루프(업로드→검색) | **W1 스프린트로 이미 해소** (구 P0 폐기 — 익명=데모 read-only + 로그인=본인 격리 쓰기) |
| 답변/출처 UX | 잘 구현됨 (result-card 문서명·페이지·섹션·quote bar) |
| fast-path 0건 트랩 (UX-1) | **P1 버그 확인** (→ #2에서 해소) |

프로덕션 auth 게이트 smoke 실측: 익명 POST /documents → **401**, 익명 GET /search → **200** (`JETRAG_AUTH_ENABLED=true` 정상 작동 확인). 로컬 `.env`의 죽은 `JETRAG_DEMO_READONLY=true` 제거(코드에서 읽는 곳 0, gitignored 파일).

### 2. UX-1 fast-path 0건 → RAG fallback (`api/app/routers/search.py`)

- 증상: "SK 사업보고서 매출"처럼 문서유형어(doc-suffix)+내용어 혼합 query가 `title ILIKE` 0건이면 빈 결과 그대로 반환 — "어렴풋한 기억 검색" 의도에 정면 배치 (2026-05-19 UX-1 트래킹 이슈).
- 수정: `_run_meta_fast_path`가 0건이면 `None` 반환 → 호출자가 fast path를 버리고 일반 RAG 하이브리드로 계속 진행. non-zero 동작·성능 불변(0건일 때만 발동). 관측성: `X-Search-Path: meta_fast_fallback` 신설(기존 `meta_fast`/`rag` 의미 불변, 외부 소비자 없음 grep 확인).
- 신규 테스트 `api/tests/test_search_meta_fast_fallback.py` (257줄): FB1 fallback 경로 + FB2 non-zero 회귀 없음(임베딩/RPC 호출 0 검증).

### 3. PWA share_target 포맷 확대 (`web/public/manifest.json` + `web/src/app/share/route.ts`)

- PDF-only → **PDF/HWP/HWPX/JPG/PNG/HEIC** (백엔드 `_ALLOWED_EXTENSIONS`의 모바일 공유 서브셋).
- HWP류는 브라우저 MIME이 비표준(octet-stream 등)이라 **확장자 우선 검증** + MIME 보조. forward 파일명 확장자 보정(`_MIME_TO_EXT`, heif→heic 정규화 — 백엔드에 `.heif` 없음). 매직바이트 검증은 백엔드 `_input_gate.py` 그대로 수행(위장 차단 유지).

### 4. design.md 신설 + 즉시 적용분 (globals.css 컬러 토큰)

- **`.fig` 파일에서 토큰 직접 추출** (zip → zstd → kiwi 바이너리 문자열 분석, 의존성 추가 0): 아토믹 팔레트 13종(primary Blue 50 `#0066FF`, coolNeutral 21단계 등), 시맨틱 매핑 48개(+라이트/다크 opacity), 타이포 스케일(Display 56/40 ~ Caption 11).
- 리포 루트 `design.md` (368줄, 디자이너 에이전트): 원칙(Toss 5원칙 + 기억보조/원본주역)·컬러·타이포·스페이싱·라운딩·컴포넌트 스펙·모션·다크모드·적용 우선순위. 현행 Toss 패턴(quote bar, break-keep, rounded-2xl)은 유지 대상 명시. 다크 값은 `(추정)` 표시로 공백을 숨기지 않음.
- 즉시 적용분: `globals.css` :root 라이트 토큰 oklch 임의값 → Wanted DS hex/rgba (`--primary #0066FF`, `--border #E1E2E4` 등). accent/success/warning-foreground는 WCAG 대비 실측으로 `#171719` 채택(diff 주석에 근거). `.dark`·chart-*·sidebar-* 불변. `viewport.themeColor` + manifest `theme_color`/`background_color` `#0B0B0F`(다크값) → `#FFFFFF` (라이트-only 렌더 정합).

## 검증

- 백엔드: `test_search_*` 14파일 + `tests/services/test_meta_filter_fast_path.py` = **102 passed** (`uv run --with pytest`, pyproject 무수정)
- 프론트: `pnpm tsc --noEmit` 0 에러 / `pnpm lint` 0 에러 / `pnpm build` 성공(exit 0)
- QA 에이전트 최종 검증: **PASS, P0/P1 블로커 0** — X-Search-Path 헤더 덮임 없음, /search GET이라 이중 과금·로깅 없음, fallback 추가 비용 documents SELECT 1회뿐, share pickFile 선필터 없음 확인, WCAG 수치 전부 재확인
- golden eval 재측정: **안 함** — fallback은 0건일 때만 발동해 기존 non-zero 결과 불변이므로 유료 재측정 생략 판단

## 주요 의사결정

| 항목 | 결정 | 근거 |
|---|---|---|
| fast-path 0건 처리 | RAG fallback (필터 유지 재검색 아님) | 최소침습 — non-zero 경로 불변, 0건보다 의미검색이 항상 나음 |
| share_target 검증 기준 | 확장자 우선, MIME 보조 | HWP MIME 비표준(octet-stream) 현실 대응 |
| 다크모드 | 이번 미적용 (design.md §9 후속) | 추출 데이터에 다크 시맨틱 표 부재 — 추정값 배포 위험 |
| Pretendard 폰트 | 미적용 (문서에 3안 비교만) | 폰트 파일 도입 = 의존성 준함, 사용자 승인 필요 |
| 카메라 capture 속성 | 미적용 | capture는 갤러리 선택지를 제거하는 역효과 — 현 picker가 이미 카메라 포함 |
| .env 죽은 설정 | `JETRAG_DEMO_READONLY` 제거 | W1에서 읽는 코드 삭제됨, 혼동 유발만 |

## 보류/이월

- [ ] **다크모드 완성** — design.md §2.3 `(추정)` 값의 Figma 다크 프레임 재검증 + 토글 UI (P2/M)
- [ ] **Pretendard 전환** — next/font/local 권장, 폰트 파일 도입 승인 필요 (design.md §3.1)
- [ ] QA P2 노트 3건: ① solid `bg-success` 배지(upload-item "완료" 등) 텍스트 흰→진회색 시각 변경 — 스크린샷 확인 권장 ② fallback 시 fast-path의 date/tag 필터 손실은 의도된 동작임을 문서화(본 문서로 갈음) ③ share 업로드 `source_channel`이 "api"로 기록(기존 이슈, "os-share" 세팅 후보)
- [ ] 웹 업로드에 plan 문서수 quota(402) 미적용 (email 경로만 게이팅) — 수익화 enforcement 갭 트래킹
- [ ] 바텀시트/모달 컴포넌트 (design.md §6.8) — Radix 계열 의존성 판단 필요

## 다음 작업

1. 사용자 검수 → 커밋 여부 결정 (본 세션 변경 = 수정 5 + 신규 2: `search.py` / `manifest.json` / `share/route.ts` / `globals.css` / `layout.tsx` + `design.md` / `test_search_meta_fast_fallback.py`)
2. 배포 후 Android 실기기에서 한글뷰어/갤러리 공유 시트에 Jet-Rag 출현 + HWP/이미지 업로드 실측
3. `meta_fast_fallback` 헤더 비율 모니터링 — fallback이 잦으면 fast-path 판정 자체(title_ilike에 내용어 포함) 재설계 검토
