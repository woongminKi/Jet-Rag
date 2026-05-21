# 데모 GIF 녹화 + 변환 가이드

> README 의 `## 데모` 섹션에 임베드되는 화면 녹화 산출물. **사용자(본인)가 녹화 → gif 변환 → commit** 의 1회성 작업. 본 문서는 도구·시나리오·해상도·용량 가이드.

---

## 0. 산출물 규격

| 항목 | 값 |
|---|---|
| 위치 | `docs/demo/*.gif` |
| 형식 | gif (또는 GitHub 가 지원하는 mp4 — README 안 `<video>` HTML 태그) |
| 해상도 | 1280×800 (또는 800×500) — Retina 화면 권장, 모바일에서도 가독 |
| 길이 | 8~15초 (사용자 attention span / GitHub README 자동 재생) |
| 용량 상한 | **10 MB** (GitHub repo size + clone 속도) — gifski `--quality 80` 권장 |
| 프레임 레이트 | 15 fps (검색 plain UI 면 충분, 입력 빠른 경우 25 fps) |

---

## 1. 녹화 도구 선택

### 1-1. macOS Built-in `Cmd+Shift+5` (가장 단순)
- 화면 일부 영역 녹화 → `.mov` 출력 → gifski 변환
- 화살표/클릭 시각화는 사후 처리 필요

### 1-2. **Kap** (권장 — gif 직접 출력)
- <https://getkap.co> (무료 오픈소스)
- 영역 드래그 → 녹화 → 직접 gif 또는 mp4 export
- 마우스 클릭 highlight 자동 옵션
- 별도 변환 단계 0

### 1-3. ffmpeg (CLI · 사후 변환용)
```bash
# .mov → .gif (palette 2-pass 고품질)
brew install ffmpeg
ffmpeg -i input.mov -vf "fps=15,scale=1280:-1:flags=lanczos,palettegen" palette.png
ffmpeg -i input.mov -i palette.png -filter_complex "fps=15,scale=1280:-1:flags=lanczos [x]; [x][1:v] paletteuse" output.gif

# 용량 압축 (gifsicle)
brew install gifsicle
gifsicle -O3 --lossy=80 output.gif -o output-compressed.gif
```

### 1-4. gifski (mov → gif 최고 화질)
```bash
brew install gifski
gifski -o output.gif --fps 15 --quality 80 --width 1280 input.mov
```

---

## 2. 시나리오 (3종 — 1 gif 1 시나리오 권장)

### 2-1. `docs/demo/search.gif` — 검색 (default, 가장 중요)
- 8~12초
- 홈 (`/`) → 검색창에 자연어 질문 (예: `사업보고서에서 매출 추이`) → 결과 카드 3개 + 매칭 강도 100% / 85% / 72% + snippet highlight
- 강조 포인트: hybrid RRF 자연어 → 결과 즉시 (KPI #10 P95 1.7s) + 매칭 강도 시각화

### 2-2. `docs/demo/ingest.gif` — 인제스트 (선택)
- 10~15초
- `/ingest` → PDF drop → 9-stage progress bar (detect / extract / chunk / embed / ...) → 완료 후 검색 가능 상태
- 강조 포인트: 9-stage 가시성 + Vision rerouting + tag 자동 생성

### 2-3. `docs/demo/answer.gif` — RAG 답변 (선택)
- 10~15초
- `/answer?q=...` → 답변 + 신뢰도 배지 + 출처 chunk highlight + Ragas 점수 (Faithfulness 0.91)
- 강조 포인트: 측정 가능한 RAG (Ragas + 출처)

---

## 3. 녹화 전 체크리스트

```
□ 본인 production 로그인 상태 (jetrag.woong-s.com)
□ 인박스에 가시 데이터 (12 doc) 보임
□ 브라우저 zoom 100% (Cmd+0)
□ 다른 탭 / 알림 / 시계 음소거
□ 시스템 다크모드 / 라이트모드 통일 (사이트 테마와 매치)
□ 검색어 미리 클립보드에 복사 (typing 빠르게)
□ 녹화 영역 = 브라우저 컨텐츠 영역만 (탭 바 · OS taskbar 제외)
```

---

## 4. README 임베드 형식

`README.md` 의 `## 데모` 섹션에 다음 형식으로 추가:

```markdown
## 데모

### 검색
![검색 데모](docs/demo/search.gif)

> 자연어 질문 → Hybrid RRF (PGroonga sparse + pgvector dense) → doc 그룹 결과 카드 + 매칭 강도 % + snippet ±240자. KPI #10 production P95 1.705s.

### 인제스트 (선택)
![인제스트 데모](docs/demo/ingest.gif)

### RAG 답변 (선택)
![RAG 답변 데모](docs/demo/answer.gif)
```

GitHub README 는 raw blob 경로로 자동 렌더 — 별도 host 불요.

---

## 5. 녹화 후 commit

```bash
# 권장 commit 분리 (gif 용량 큼)
git add docs/demo/search.gif
git commit -m "docs(demo): 검색 시나리오 데모 GIF (10초, 6.2MB)"

# README §데모 섹션 활성화
git add README.md
git commit -m "docs(readme): 데모 GIF 섹션 활성화"

git push origin main
```

용량이 10 MB 초과 시 GitHub LFS 가 강제 — 가능하면 그 전에 gifsicle/gifski quality 조정으로 압축.

---

## 6. 검증 (commit 전)

- GitHub UI 에서 PR diff preview — gif 자동 재생되는지
- 모바일 (iPhone Safari) 으로 raw 접속 → 재생 + 가독성 확인
- gif 용량 `ls -lh docs/demo/*.gif`

---

## 7. 향후 자동화 후보 (선택)

- `puppeteer` / `playwright` 스크립트로 brand 일관 녹화 자동화 — production endpoint 호출 + 스크롤·검색 시퀀스 hard-code
- CI 에서 visual regression diff (Playwright screenshot 비교) — 다음 sprint
