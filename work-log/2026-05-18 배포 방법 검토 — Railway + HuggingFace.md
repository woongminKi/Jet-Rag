# 2026-05-18 배포 방법 검토 — Railway + HuggingFace

> 작성: senior-planner / HEAD `ed11039` / 트리거: 사용자 명시 "배포 작업 0건 — Railway + HuggingFace 배포 방법 파악" / 제약: 분석·계획만, Dockerfile/코드/실 deploy 0건 / v1.5 sprint (DeepInfra BGE-M3 어댑터 swap) 와 병행
>
> **2026-05-18 갱신 v0.2 — D 안 확정 + 멀티유저 운영 반영**:
> - 사용자 결정: **D 안 (Railway $5 + Vercel + DeepInfra) 채택**
> - 사용자 명시: 배포 목적 = **이직 포트폴리오 + 실 유저 대상 서비스 운영** (페르소나 확장)
> - 추가 의사결정 4건 (§13) — 유료/공개 범위/공개 시점/Vercel Pro 전환
> - 멀티유저 추가 작업 D1~D7 (§14) — Auth / RLS / per-user rate limit / 법적 / 결제 모델 / Vercel 라이선스 / Supabase Pro 전환 시점
>
> **2026-05-18 갱신 v0.3 — 본 세션 실 진행 결과 반영 (commit `e57c3ec`)**:
> - ✅ **v1.5 W-0 결정성 시험 PASS** — n=100, min cosine **0.999984** ≥ 0.999 → DeepInfra swap 안전, dense_vec 재인제스트 불필요
> - ✅ **DeepInfra·Railway 가입 + billing 완료** (사용자)
> - ✅ **`api/Dockerfile` + `.dockerignore` 작성** — single-stage uv build, `/health` 200, image 1.28GB, 로컬 검증 완료
> - ✅ **CORS env 화** — `JETRAG_CORS_ORIGINS` + Vercel preview regex (`https://*.vercel.app`)
> - ✅ **commit `e57c3ec` push 완료** — Railway 자동 rebuild trigger
> - ⏳ **Vercel build 실패** — Root Directory 가 repo 루트 → 712MB exceeded → 사용자 dashboard 에서 `web` 으로 변경 후 redeploy 필요
> - ⏳ Railway ENV vars 미등록 — runtime 시 Supabase 등 연결 실패 가능
> - **§15 다른 컴퓨터 / 새 세션 핸드오프 가이드** 신규 — clone → .env → 검증 절차

---

## 0. 핵심 요약 (~300 word)

현 Jet-Rag 코드베이스는 배포 인프라 파일 0건 — Dockerfile / docker-compose / Procfile / railway.json / fly.toml / render.yaml 전부 없음. backend (`api/`) = FastAPI + uv + Python 3.12, frontend (`web/`) = Next.js 16 + React 19, DB = Supabase SaaS (외부 의존). 사용자 요청은 **Railway 와 HuggingFace 두 배포 옵션 파악**.

7 옵션 (Railway / HF Spaces / HF Inference Endpoint / Vercel / Fly.io / Render / 자가 호스팅) 을 페르소나 A 제약 (Windows + iPhone + Android · 운영 부담 0 · 월 비용 < $10 우선 · Cloud-first MVP §9.4) 기준으로 평가했다.

**권장 = 3-tier 분리 배포**:
1. **backend (FastAPI uv)** → **Railway Hobby ($5/월)** — Singapore region (Supabase ap-northeast-2 Seoul 와 ~50~80ms latency, US 대비 우위), Dockerfile 빌드 (uv + Python 3.12 native), private networking 불요, env secret 안전 관리, log/metric 내장, sleep 없음 (always-on)
2. **frontend (Next.js 16)** → **Vercel Hobby (무료)** — Next.js native 호스트, global CDN, SSR/RSC 최적화, web/.env 만 분리, 단 비상업 비영리 라이선스 (페르소나 A 정합)
3. **embedding (BGE-M3)** → **DeepInfra BGE-M3 ($0.01/M token, <$1/월)** — v1.5 sprint 의 권장안 그대로. backend 가 stateless 라 어디 deploy 든 영향 0

**총 월 비용: ~$5~$6** (Railway $5 + DeepInfra <$1 + Vercel·Supabase·HF Storage·Gemini 무료/현 수준 유지).

**HuggingFace Spaces 비권장** (사용자가 명시 검토 요청했으나) — (a) Spaces 의 `/tmp` 만 쓰기 가능 (마이그·임시파일 운영 부담), (b) Free CPU Basic 48시간 idle 시 sleep + cold-start 첫 호출 수십 초, (c) 단일 컨테이너에 backend 만 (web 도 같이 묶으면 격리·재배포 불편), (d) **Supabase region(Seoul) 과 HF Spaces region(US) latency 격차 200ms+ 가 KPI #10 P95 < 3s 에 위험**. 다만 v1.5 fallback 시나리오 (HF Dedicated CPU Endpoint $24/월) 는 별개 — 그건 Inference **Endpoint** 지 **Spaces** 가 아님 (§5).

**v1.5 와의 연계**: backend 가 stateless (Supabase 만 영구) → 어댑터 swap 은 ENV 토글 1줄 = 배포 인프라 결정과 100% 직교. Railway 든 HF Spaces 든 영향 0. 본 배포 sprint 진입 권장 시점 = **v1.5 W-1 완료 후** (DeepInfra swap 안정화 후 deploy, latency 게이트 통과 확인 후).

**의사결정 7건** (§8): 도메인 보유 / 비용 상한 / Vercel 비상업 제약 수용 / GitHub Actions 도입 / api+web 단일 도메인 / 추후 self-host 가능성 / v1.5 와의 sprint 순서.

총 배포 sprint 작업량: **18~26h** (W-1~W-7 + canary).

---

## 1. 컴포넌트별 배포 옵션 매트릭스

### 1.1 backend (`api/` FastAPI + uv + Python 3.12)

| 옵션 | 빌드 | 무료 한도 | 유료 시작가 | sleep / cold | SSL/도메인 | env 관리 | log | Supabase Seoul 정합 | 종합 |
|---|---|---|---:|---|---|---|---|---|---|
| **Railway Hobby** ★ | Dockerfile / RAILPACK (Nixpacks deprecated). uv native | $5 credit 일회성 trial | **$5/월** + 사용량 | 없음 (always-on) | 자동 SSL · 커스텀 도메인 OK | dashboard env vars | 내장 stream | Singapore region 우선 (Seoul 직선거리 ~3,000km, latency 50~80ms) | ★★★ |
| HF Spaces Docker SDK | Dockerfile (port 7860) | Free CPU Basic: 2 vCPU·16GB RAM·50GB ephemeral disk | $0 (지속) / $9/월 (small CPU upgrade) | **48h idle 시 sleep** + cold | Spaces 도메인 (`huggingface.co/spaces/...`) — 커스텀 도메인 X (Pro Org 만) | Settings → Secrets | 내장 build log + container log | **US region 만 (latency 격차 200ms+)** | ★ |
| HF Inference **Endpoint** | (모델 전용 — FastAPI 호스트 X) | 없음 | $0.033/h (CPU) ≈ $24/월 | scale-to-zero 시 cold | 자동 | 자동 | 자동 | 모델 endpoint 만 — backend 와 분리 | (별 카테고리, §3.2) |
| Fly.io | Dockerfile + `fly.toml` | trial $5 credit (7일 또는 2 VM hr) | shared-cpu-1x 256MB ≈ $1.94/월 | scale-to-zero 가능 (cold delay) | 자동 SSL | secrets CLI | flyctl logs | ap-northeast-1 Tokyo region OK | ★★ |
| Render | Dockerfile / native | Free Web Service (90일 무료 후 sleep) | Starter $7/월 | Free 시 15분 idle sleep | 자동 SSL | env vars | 내장 | Singapore region OK | ★★ |
| AWS Lambda + API Gateway | container image | 1M req/월 free | $0~ | cold start 수 백ms~수초 | 자동 | Param Store | CloudWatch | Seoul region 직접 OK | ★ (운영 복잡도) |

**권장**: **Railway Hobby**. 근거 — Dockerfile 한 번 작성 = 운영 부담 0, sleep 없음 (페르소나 A 의 모바일 검색 시 항상 응답), Singapore region 이 Supabase Seoul 과 같은 ap 권역, $5/월 안정적 예산.

### 1.2 frontend (`web/` Next.js 16 + React 19)

| 옵션 | 무료 한도 | 유료 시작가 | Next.js 16 native | SSR / RSC | 커스텀 도메인 | Supabase 직접 호출 | 종합 |
|---|---|---:|---|---|---|---|---|
| **Vercel Hobby** ★ | 100GB transfer · 1M function inv · 4h Active CPU · 1M edge req | $20/월 (Pro) | **Native** (Vercel = Next.js 제작사) | 완전 지원 | 무료 도메인 + 커스텀 OK | OK (NEXT_PUBLIC_SUPABASE_URL/ANON_KEY 클라이언트 직접) | ★★★ |
| Railway (web 도) | 위와 동일 | $5/월 | Dockerfile 또는 RAILPACK | OK | OK | OK | ★★ (Vercel 대비 RSC 캐싱 약함) |
| HF Spaces Docker | 위와 동일 | $9/월 | Dockerfile (port 7860) | 가능하나 비표준 | Spaces 도메인 (커스텀 X) | OK | ★ |
| Cloudflare Pages | unlimited bandwidth (소형) | $0 | Pages Workers (Edge 함수 제약) | 부분 (Edge runtime 만) | 무료 도메인 + 커스텀 | OK | ★★ (Next.js 16 호환성 검증 필요) |
| Netlify | 100GB transfer · 300 build min | $19/월 | Adapter 필요 | OK | 무료 + 커스텀 | OK | ★★ |

**권장**: **Vercel Hobby**. 근거 — Next.js native = build 최적화 (turbo) · RSC streaming · Image Optimization 자동, 무료, 페르소나 A 비상업 사용 = 라이선스 정합 (§8 Q3 확인 필요).

### 1.3 embedding (BGE-M3 모델)

v1.5 sprint `2026-05-15 HF self-host 검토` §1 의 7 옵션 그대로. 권장 = **DeepInfra BGE-M3 pay-per-token ($0.01/M token, <$1/월)**. 본 배포 검토와 직교 — DeepInfra 채택 시 backend 어디 deploy 든 영향 0 (API URL 만 호출).

---

## 2. v1.5 sprint 와의 연계

### 2.1 DeepInfra 채택 시 (권장 path)

- backend 는 **완전 stateless** (Supabase 만 영구 storage). 어떤 deploy target 도 OK
- embedding API URL = DeepInfra (`https://api.deepinfra.com/...`) — backend container 가 outbound HTTPS 만 되면 호출 가능
- **Railway / HF Spaces / Fly / Vercel functions 모두 호환** — 배포 결정과 100% 직교
- v1.5 W-1 (어댑터 swap) → W-2 (latency 측정) → W-3 (R@10 회귀) → **그 후 deploy sprint 진입** 권장

### 2.2 HF Dedicated CPU Endpoint 채택 시 (v1.5 fallback, $24/월)

- embedding endpoint 만 HF 안에 격리 (Inference **Endpoint** — Spaces 아님)
- backend 는 **여전히 stateless** — Railway / Fly 어디든 OK
- HF Endpoint 와 backend 를 같은 region 에 묶을 시너지: HF Endpoint 는 AWS region 선택 가능 (us-east-1, eu-west-1, ap-southeast-1 등). backend 도 같은 region 에 deploy 시 latency ↓
- 단 HF Endpoint 의 ap region 선택 가능 여부 확인 필요 (§8 Q4)

### 2.3 HF Spaces 에 BGE-M3 self-host (별개 옵션)

- **HF Spaces 와 HF Inference Endpoint 는 다른 제품**:
  - **Spaces** = 데모/앱 호스팅 (Gradio/Streamlit/Docker SDK), 사용자 UI 노출
  - **Inference Endpoint** = 모델 API 전용 서빙 (Auto-scaling, scale-to-zero, security)
- BGE-M3 를 Spaces 에 올리는 것은 비표준 (Spaces 는 UI 데모 의도). 가능은 하나 — Docker SDK + TEI (Text Embedding Inference) 컨테이너 + free CPU Basic
- Free CPU Basic 의 sleep (48h idle) + cold-start (수십 초) = **KPI #10 P95 < 3s 위반**
- 결론: v1.5 권장안 (DeepInfra) 또는 fallback (HF Inference Endpoint) 둘 다 채택 안 되면 검토 가치. 본 sprint 에서는 **비권장**

---

## 3. 권장 배포 조합 + 근거

### 3.1 최종 권장 조합

| 컴포넌트 | 위치 | 월 비용 | 근거 |
|---|---|---:|---|
| backend (`api/`) | **Railway Hobby — Singapore region** | $5 + 사용량 (예상 < $1) | Dockerfile uv 빌드 / sleep 0 / Supabase Seoul ap 권역 / private networking 가능 / log/secret 내장 |
| frontend (`web/`) | **Vercel Hobby** | $0 | Next.js 16 native / RSC streaming / 무료 / global CDN |
| embedding | **DeepInfra BGE-M3 API** | <$1 | v1.5 §2 권장 / always-warm / R@10 회귀 0 |
| DB / Storage | **Supabase (현 외부 SaaS)** | $0 (free tier) | 이미 사용 중 / ap-northeast-2 Seoul region |
| vision LLM | **Gemini 2.0 Flash** | 변동 (vision cap $0.50/일) | 현 사용 중 |

**총 월 비용 예상**: **$5~$6** + 사용량 (vision Gemini · DeepInfra 토큰).

### 3.2 근거

**근거 1 — 페르소나 A 비용 정합 (§11.5)**:
- 무료 티어 우선 + < $10/월 안 (메모리 `project_persona_kpi_decisions.md` 비용 제약)
- HF Dedicated CPU $24/월 은 v1.5 fallback 명시 결정 필요 (§8 Q2)

**근거 2 — 운영 부담 0**:
- 3개 SaaS 모두 dashboard 기반 (CLI 학습 곡선 낮음)
- 장애 대응 자동 (재시작·SSL 갱신·CDN)
- 페르소나 A 1인 = monitoring stack 구축 시간 0

**근거 3 — Cloud-first MVP 어댑터 내러티브 (§9.4)**:
- backend stateless = Cloud→Local v2 전환 시 deploy target 만 교체
- DeepInfra → Ollama local 어댑터 swap 도 같은 ENV 토글 패턴
- Railway 배포 = 외부 의존성 1건 추가지만 어댑터 layer 가 차단

**근거 4 — Supabase region 정합**:
- Supabase ap-northeast-2 Seoul 기존 사용 추정 (MCP `supabase-jetrag` 연결됨)
- Railway Singapore (Southeast Asia Metal) 가 같은 ap 권역 → US 대비 latency 50~150ms 절감 → KPI #10 P95 < 3s 안전 margin ↑
- Vercel 의 CDN edge 는 Seoul edge 포함 → frontend 응답 무관

**근거 5 — IPv6 이슈 회피**:
- Railway 는 outbound IPv6 미지원 → Supabase 직접 연결 시 **Session pooler** (`pooler.supabase.com`, IPv4) 사용해야 함 (§9 위험 §9.4)
- backend 의 supabase-py 가 Session pooler URL 로 연결되도록 ENV `SUPABASE_URL` 검증 필요

### 3.3 비권장 조합 — HF Spaces 에 backend + web 묶음

사용자 요청에 따라 검토하나 **비권장**:
- backend = HF Spaces Docker SDK (port 7860, Dockerfile)
- web = 별도 Vercel
- DB = Supabase 그대로
- embedding = DeepInfra

**문제**:
1. HF Spaces Free CPU Basic 48h idle sleep → cold start = KPI #10 P95 < 3s 위반
2. HF Spaces `/tmp` 만 쓰기 (영구 storage 별도 $5+/월)
3. HF Spaces 커스텀 도메인 무료 X (Pro Org $9/월부터)
4. HF Spaces region 은 US (Supabase Seoul latency 격차 200ms+)
5. HF Spaces 는 ML 데모/앱 호스팅 의도 — 운영 backend 의 표준 path 아님

**대안 검토 — HF Inference Endpoint (Spaces 와 별개)** = §5 에서 상세, v1.5 fallback 으로만 유의미.

---

## 4. HuggingFace Spaces 검토 (사용자 명시 요청)

### 4.1 Spaces SDK 선택지

| SDK | 용도 | 적합도 |
|---|---|---|
| Gradio | ML 데모 UI | Jet-Rag 미적합 (UI 가 Next.js 16) |
| Streamlit | Python 데이터 앱 | 동일 미적합 |
| Static | 정적 HTML | Next.js build 결과만 올리면 가능하나 SSR/RSC X |
| **Docker** | 커스텀 컨테이너 | **유일한 후보** — FastAPI + Next.js 모두 가능 |

### 4.2 Docker SDK 사용법

**구조**:
```
api/
├── Dockerfile          # python:3.12-slim + uv + uvicorn
├── README.md           # YAML frontmatter: sdk: docker, app_port: 7860
└── (소스)
```

**Dockerfile 핵심**:
```dockerfile
FROM python:3.12-slim
RUN pip install uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen
COPY . .
EXPOSE 7860
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
```

**README.md frontmatter**:
```yaml
---
title: Jet-Rag API
sdk: docker
app_port: 7860
---
```

### 4.3 제약

| 제약 | 영향 |
|---|---|
| Free CPU Basic: 2 vCPU · 16GB RAM · 50GB ephemeral | RAM 충분, 디스크는 인제스트 시 임시파일 `/tmp` 만 가능 |
| `/data` 만 영구 (옵션 + $5/월부터) | Jet-Rag 는 Supabase 가 영구 storage 라 무영향 |
| 48h idle 시 sleep | **치명적** — 첫 호출 cold start 수십 초 |
| 커스텀 도메인 무료 X | `huggingface.co/spaces/{user}/{name}` 만 |
| Outbound HTTPS OK | DeepInfra/Gemini/Supabase 호출 가능 |
| log = build/container 내장 | OK |
| Secret 관리 = Settings → Secrets | OK |
| Region = US (변경 불가, free tier) | Supabase Seoul 과 latency 격차 ↑ |

### 4.4 Next.js 16 을 Spaces 에 올리는 경우

가능은 하나 비표준:
```dockerfile
FROM node:20-alpine
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN corepack enable pnpm && pnpm install --frozen-lockfile
COPY . .
RUN pnpm build
EXPOSE 7860
CMD ["pnpm", "start", "--", "-p", "7860"]
```

**문제**:
- Next.js 16 RSC streaming 이 HF Spaces 의 cold-start + 48h sleep 와 충돌
- Vercel 의 Image Optimization · ISR · Edge Cache 모두 없음
- web 을 Spaces 에 올리는 가치 < Vercel 무료

---

## 5. Railway 검토 (사용자 명시 요청)

### 5.1 backend (FastAPI uv) Railway 배포 path

**옵션 A — Dockerfile (권장)**:
```dockerfile
FROM python:3.12-slim
RUN pip install uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```
- Railway 가 `$PORT` 환경변수 자동 주입 → bind
- uv lock 캐싱 → 빌드 시간 2~3분
- Python 3.12 명시 보장 (Nixpacks/RAILPACK 의 자동 추론 불확실성 회피)

**옵션 B — RAILPACK (Nixpacks 후속 빌더)**:
- `requirements.txt` 또는 `pyproject.toml` 감지 → 자동 빌드
- uv 지원 여부 확인 필요 (Railway 의 FastAPI starter 가 uv 사용 사례 존재)
- 빌드 결과 예측 어려움 → **A 안 Dockerfile 권장**

**railway.json (선택)**:
```json
{
  "build": { "builder": "DOCKERFILE", "dockerfilePath": "api/Dockerfile" },
  "deploy": {
    "startCommand": "uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT",
    "healthcheckPath": "/health",
    "restartPolicyType": "ON_FAILURE"
  }
}
```

### 5.2 frontend (Next.js 16) Railway 배포 path

가능. RAILPACK 이 Next.js 자동 감지 + `pnpm build` + `pnpm start`. 단:
- Vercel 의 Image Optimization · Edge cache · Analytics 없음
- 같은 $5/월 두 번 = $10/월 (backend $5 + web $5)
- **web 은 Vercel 권장**, Railway 는 backend 만

### 5.3 무료 한도 / 가격

| 단계 | 비용 | 한도 |
|---|---:|---|
| Free Trial | $0 (일회성 $5 credit, 30일 만료) | 1GB RAM · 공유 vCPU · 5 service/project |
| **Hobby** ★ | **$5/월 + 사용량** | $5 credit 포함, 그 이상 사용량 과금. always-on |
| Pro | $20/월 + 사용량 | 팀·우선순위 지원 |

페르소나 A 트래픽 (일 ~30 search · 인제스트 적음) → 사용량 < $5 credit 안 → **실 비용 $5/월 고정**.

### 5.4 region 선택

Railway 4 region:
- US West Metal (California)
- US East Metal (Virginia)
- EU West Metal (Amsterdam)
- **Southeast Asia Metal (Singapore)** ← 권장 (Supabase Seoul 같은 ap 권역)

Seoul/Tokyo region 미제공 — Singapore 가 ap 최선택.

### 5.5 Supabase 연결

**중요 제약**: Railway outbound IPv6 미지원 → Supabase direct connection (`db.{ref}.supabase.co:5432`, IPv6 우선) 실패 가능.

**해결**: Supabase **Session Pooler** URL 사용:
```
postgresql://postgres.{ref}:{password}@aws-0-{region}.pooler.supabase.com:5432/postgres
```

단 Jet-Rag 는 supabase-py REST client (HTTPS) 만 사용 (`SUPABASE_URL=https://{ref}.supabase.co`) → IPv6 이슈 무관. **무영향**.

### 5.6 env / secret / log

- Settings → Variables → 평문 입력 (HF_API_TOKEN, GEMINI_API_KEY, SUPABASE_SERVICE_ROLE_KEY, DEFAULT_USER_ID, JETRAG_* 토글)
- service_role_key 가 backend 전용 = 노출 위험 0 (env vars 는 build/runtime 만, 클라이언트 번들 X)
- Log = dashboard 실시간 stream + CLI `railway logs`

### 5.7 GitHub Actions 자동 배포

Railway = GitHub repo 연결 → push 시 자동 build/deploy. CI/CD 별도 구축 불요.
- main branch push → production deploy
- PR → preview environment (Pro plan 부터)

---

## 6. 배포 sprint scope (W-1 ~ W-7 + canary)

### 6.1 작업 항목

| ID | 작업 | 작업량 | DoD |
|---:|---|---:|---|
| **W-0** | **사전 의사결정 7건 (§8) 사용자 응답** | 0.5h | Q1~Q7 답변 받음, 비용/도메인/라이선스 확정 |
| **W-1** | `api/Dockerfile` 작성 + 로컬 빌드 검증 | 2~3h | `docker build` 성공, `docker run` 으로 `/health` 200, 빌드 시간 < 5분 |
| **W-2** | `api/railway.json` 또는 dashboard 설정 + Railway service 생성 (Singapore) | 1h | Railway project 생성, GitHub repo 연결, env 등록 (10건), build 성공 |
| **W-3** | Railway env vars 등록 + 초기 deploy + `/health` smoke | 1h | 첫 deploy 성공, `/health` 200, `/docs` 표시, BGE-M3 warmup log 확인 |
| **W-4** | Vercel project 생성 + Next.js 16 build + env 등록 | 1h | Vercel deploy 성공, `NEXT_PUBLIC_API_BASE_URL` → Railway domain, smoke pass |
| **W-5** | **CORS / 도메인 / SSL 정합** — `api/app/main.py` allow_origins 에 Vercel 도메인 추가 | 1h | Vercel → Railway API 호출 200, CORS preflight OK |
| **W-6** | (선택) 커스텀 도메인 — Vercel custom domain + Railway custom domain | 1~2h | DNS A/CNAME 설정, SSL 자동 갱신 확인 |
| **W-7** | **canary 검증** — Golden Path 3종 (Windows / Android / iPhone) 시연 | 2~3h | US-01~04, 06~09 통과, KPI #10 P95 < 3s 측정 |
| **W-8** | (선택) GitHub Actions CI — lint/test on PR | 2h | PR 시 `uv run pytest` + `pnpm lint` 자동 |
| **W-9** | 운영 모니터링 — Railway/Vercel dashboard 즐겨찾기 + Supabase usage alert | 1h | 비용 알림 ($5/월 초과 시 메일), 에러 alert |
| **W-10** | work-log + 기획서 §17 (버전 이력) 갱신 + DECISION-13 등록 (배포 결정) | 2h | 본 work-log refresh + 기획서 §15 §17 갱신 |

**총 작업량: 14~18h (필수)** + 4~8h (선택 W-6·W-8) = **18~26h**

### 6.2 DoD (sprint 완료 조건)

1. backend Railway Singapore deploy = `/health` 200, `/docs` 표시
2. frontend Vercel deploy = `https://{vercel}.app/` 표시, search 1건 성공
3. KPI #10 P95 < 3s 측정 (v1.5 완료 전제) — production deploy 환경에서
4. KPI #4 인덱싱 성공률 ≥ 90% (1 doc 인제스트 sample)
5. CORS / SSL / env 정합 (browser console 에러 0)
6. 운영 비용 알림 setup (Railway $5 cap 알림, Supabase usage alert)
7. work-log + 기획서 갱신, 단위 테스트 회귀 0 (현 1202 OK)

### 6.3 deploy 순서

```
W-0 (의사결정) → v1.5 W-1~W-3 (DeepInfra swap + 회귀 측정)
              → W-1~W-5 (Railway + Vercel + CORS)
              → W-7 canary (Golden Path)
              → W-6 도메인 (선택)
              → W-10 문서화
```

---

## 7. 위험 / 사이드이펙트

### 7.1 Vercel Hobby 비상업 라이선스 (Q3)

Vercel Hobby 는 **비상업·비영리 프로젝트 한정**. Jet-Rag 가 발표/포트폴리오 용도 = 비상업 정합. 단 추후 수익 발생 시 Pro $20/월 전환 의무. 페르소나 A 의 "개인 기억 보조" 포지셔닝 (§11.5) 과 정합.

### 7.2 Railway $5 credit 초과 위험

페르소나 A 트래픽 < $5 credit 안 예상이나 인제스트 폭주 시 RAM/CPU 사용량 ↑ 가능. mitigation:
- Railway dashboard usage alert (예: 80% 도달 시 메일)
- 비용 초과 시 backend 정지 (Pro plan 전환 없으면)
- vision cost cap ($0.50/일) 이미 적용 → 인제스트 폭주 차단

### 7.3 IPv6 미지원 (Railway → Supabase)

Jet-Rag 는 HTTPS REST client (supabase-py) 만 사용 → 무영향. 단 추후 직접 Postgres 연결 (raw psycopg2) 도입 시 Session Pooler URL 강제 필요.

### 7.4 region latency

Railway Singapore ↔ Supabase Seoul: 측정 필요 (예상 50~150ms). 만약 200ms+ 시 검색 latency budget 압박. mitigation:
- Singapore 선택 ✅ (US 대비 우위)
- 추후 Railway Seoul/Tokyo region 출시 시 즉시 이전

### 7.5 Cold start 위험

| 옵션 | cold start | Jet-Rag 영향 |
|---|---|---|
| Railway Hobby | **0** (always-on) | 없음 |
| Vercel Hobby | Serverless functions cold ~수백ms (RSC 첫 호출) | 미미 |
| HF Spaces Free | 48h idle 시 cold 수십 초 | **치명적** — 비채택 |
| Fly.io scale-to-zero | cold 수 초 | 미미 |

**Railway 권장 이유 #1** — sleep 없음.

### 7.6 secret 노출 위험

- `SUPABASE_SERVICE_ROLE_KEY` 는 backend 전용 (Railway env), 클라이언트 번들 X
- `NEXT_PUBLIC_SUPABASE_ANON_KEY` 는 클라이언트 노출 OK (RLS 가 보호)
- `HF_API_TOKEN`, `GEMINI_API_KEY` 는 backend 만
- Railway / Vercel 모두 env vars dashboard 평문 = secret manager 없으나 페르소나 A 1인 = 위험 허용

### 7.7 외부 API rate limit

- HF Inference Providers (현 BGE-M3) → v1.5 DeepInfra swap 후 무관
- Gemini 2.0 Flash → 무료 RPD 1500/일 (페르소나 충분)
- Supabase free tier → 500MB DB · 1GB storage · 50MB egress/월

### 7.8 v1.5 와의 배포 순서

**권장**: v1.5 W-1~W-3 (DeepInfra swap + 회귀 측정) **완료 후** deploy sprint 진입. 이유:
- DeepInfra latency 가 production 환경 KPI #10 P95 측정 결과를 결정
- HF Inference Providers cold start 29.7s 가 그대로 deploy 되면 첫 인상 망침
- Order = v1.5 ship → deploy → canary

---

## 8. 의사결정 포인트 (사용자 결정 필요)

### Q1. 도메인 보유 여부

- 보유 → Railway · Vercel custom domain 등록 (W-6)
- 미보유 → 무료 도메인 사용 (`{project}.up.railway.app`, `{project}.vercel.app`)

권장 안: **무료 도메인부터** (MVP). 발표 직전 custom 추가 가능.

### Q2. 월 운영 비용 상한

- $5/월 (Railway Hobby + Vercel·DeepInfra·Supabase free) — **권장 baseline**
- $10/월 (Railway 사용량 + DeepInfra 토큰 여유)
- $30/월 (HF Dedicated CPU $24 fallback 시)

권장 안: **$10/월 cap** (Railway $5 + DeepInfra $1 + 예비 $4).

### Q3. Vercel Hobby 비상업 라이선스 수용 여부

- 수용 → Vercel Hobby 무료
- 거부 (상업 가능성) → Vercel Pro $20/월 또는 Railway 에 web 도 deploy ($5/월 추가)

권장 안: **수용** (페르소나 A = 개인 기억 보조, 비상업 정합).

### Q4. GitHub Actions / CI 도입 여부

- 도입 → W-8 (2h), PR 시 자동 lint/test
- 미도입 → 수동 검증, 단순 maintain

권장 안: **미도입 (MVP)** — 본 ship 후 W-9 에서 결정. 페르소나 A 1인 PR 자체가 드묾.

### Q5. api+web 단일 도메인 vs 분리

- 분리 (권장) → `api.jet-rag.example.com` + `jet-rag.example.com`
- 단일 → Vercel rewrites 로 `/api/*` → Railway 프록시 (latency +1 hop)

권장 안: **분리** (단순, CORS allow_origins 만 관리).

### Q6. 추후 self-host BGE-M3 가능성

- 가능 → 어댑터 layer 유지 (DeepInfra → Ollama·TEI 로컬 전환 path 보존)
- 무관 → DeepInfra 만 사용

권장 안: **가능 유지** (어댑터 §9.4 내러티브 = 발표 카드).

### Q7. v1.5 와의 sprint 순서

- 순차 (권장): v1.5 W-1~W-3 ship → deploy sprint 진입
- 병행: v1.5 와 deploy 동시 진행 (risk: deploy 후 swap 시 latency 변동)

권장 안: **순차**. v1.5 안정화 후 deploy.

---

## 9. 인용 / 참조

### 9.1 외부 출처

- Railway Pricing — Hobby $5/월 (railway.com/pricing)
- Railway Deployment Regions — US West/East, EU West, Southeast Asia (Singapore) (docs.railway.com/reference/deployment-regions)
- Railway Free Trial — $5 credit 30일 (docs.railway.com/pricing/free-trial)
- Railway FastAPI Guide (docs.railway.com/guides/fastapi)
- Vercel Hobby Plan (vercel.com/docs/plans/hobby)
- Vercel Pricing (vercel.com/pricing)
- HuggingFace Docker Spaces (huggingface.co/docs/hub/en/spaces-sdks-docker)
- HuggingFace Spaces Overview — Free CPU Basic 2 vCPU · 16GB RAM · 50GB ephemeral · 48h idle sleep (huggingface.co/docs/hub/en/spaces-overview)
- HuggingFace Inference Endpoints (Spaces 와 다른 제품) (huggingface.co/docs/inference-endpoints/index)
- HuggingFace Pricing (huggingface.co/pricing)
- Supabase Regions — ap-northeast-2 Seoul 포함 (supabase.com/docs/guides/platform/regions)
- Fly.io Pricing 2026 — no permanent free tier, shared-cpu-1x ~$1.94/월 (fly.io/pricing/)
- Railway nixpacks deprecated → RAILPACK 전환 (blog.railway.com)

### 9.2 본 프로젝트 참조

- 기획서 §9.2 스택 / §9.4 어댑터 / §9.6 iOS 저장 / §9.8 4종 환경 / §9.9 무료 티어 / §11.5 포지셔닝 / §13.1 KPI #10
- `work-log/2026-05-15 HF self-host 검토 — v1.5 sprint 설계.md` (v1.5 권장 = DeepInfra)
- `work-log/2026-05-12 검색 정확도 80% 달성 PRD.md` — KPI #10 deferred
- 메모리 `project_persona_kpi_decisions.md` — 비용 / 자원 / 페르소나 결정
- 메모리 `project_jet_rag.md` — 현 상태 HEAD `ed11039`
- `api/pyproject.toml` — uv + Python 3.12 + FastAPI / supabase / google-genai / pyhwp
- `web/package.json` — Next.js 16 + React 19 + @supabase/supabase-js
- `api/app/main.py` — CORS allow_origins `http://localhost:3001` → deploy 시 Vercel 도메인 추가 필요
- `api/app/config.py` — env settings (SUPABASE_URL/KEY, HF_API_TOKEN, GEMINI_API_KEY, JETRAG_*)
- `.env.example` / `web/.env.example` — env 템플릿

---

## 10. 남은 이슈 (처리 시점별)

| # | 이슈 | 심각도 | 처리 시점 | 예상 작업량 |
|---|---|---|---|---|
| 1 | Railway Seoul/Tokyo region 미제공 — Singapore 가 ap 최선택 | 낮 | Railway 가 region 추가 시 마이그 (보류) | 1~2h migration |
| 2 | Vercel Hobby 비상업 라이선스 — 수익 발생 시 Pro 전환 | 낮 | 발생 시 | 즉시 |
| 3 | Supabase free tier 500MB DB / 1GB storage 한도 — 현 chunks 2,469 ≪ 한도, 그러나 인제스트 증가 시 monitor | 낮 | Supabase dashboard usage 60% 도달 시 | 5분 alert setup |
| 4 | HF Spaces 비채택 — 그러나 사용자 명시 검토 요청이라 §4 보존. 추후 self-host BGE-M3 trial 시 재검토 | 낮 | 보류 | 별도 sprint |
| 5 | v1.5 sprint 와의 dependency — v1.5 미완료 시 deploy 가 KPI #10 측정 불가 | 중 | v1.5 W-1~W-3 ship 후 | (의존) |
| 6 | `api/app/main.py` CORS `allow_origins=["http://localhost:3001"]` 하드코딩 — env 기반 화이트리스트로 리팩토링 필요 | 중 | W-5 (CORS / 도메인) | 30분 |
| 7 | Railway outbound IPv6 미지원 — supabase-py REST 만 사용해 무영향이나, 추후 raw Postgres 도입 시 Session Pooler 강제 필요 | 낮 | 추후 raw 도입 시 | 10분 ENV 교체 |

---

## 11. 다음 스코프 — Sprint v1.5 + Deploy

1. **사용자 응답 — §8 Q1~Q7 의사결정 7건** → ✅ **D 안 채택 (Railway $5 + Vercel + DeepInfra)** 2026-05-18 사용자 확정
2. v1.5 sprint 진입 (DeepInfra swap, W-1~W-3) — `work-log/2026-05-15 HF self-host 검토` §3 위임 — W-0 코드 작성 완료 (DeepInfra token 등록 대기)
3. v1.5 ship 후 → 본 배포 sprint W-1 진입 (Dockerfile 작성, senior-developer 위임)
4. **§14 멀티유저 D1~D7 동시 진입 검토** — Auth (Supabase) + RLS audit + per-user rate limit (Deploy W-3~W-5 와 통합)
5. canary 검증 (W-7) — Golden Path 3종 + KPI #10 측정 + 멀티유저 시연 (지인 5명 sample)
6. work-log + 기획서 §17 갱신, DECISION-13 (배포 결정) 등록

---

## 12. 회고 한 줄

배포 작업 0건이 의외였으나, backend stateless + 어댑터 layer 의 직교성 덕에 deploy target 결정이 v1.5 와 100% 분리 가능했다. Railway Hobby Singapore + Vercel Hobby + DeepInfra 조합이 페르소나 A 비용 ($5~$6/월) · 운영 부담 0 · KPI #10 안전 margin 모두 만족 — 사용자 요청한 HF Spaces 는 cold start 48h idle 로 비권장이지만 §4 에 분석 보존했다.

**2026-05-18 갱신**: 사용자 배포 목적 = 이직 포트폴리오 + 실 유저 서비스 운영 2축 → D 안 단계별 ramp-up ($5 → $25 → $80+) 시나리오로 확장. 페르소나 1인 → 1인 + 외부 유저 N명 확장 (§13/§14).

---

## 13. 멀티유저 단계별 비용 ramp-up (2026-05-18 신규)

### 13.1 단계별 비용 곡선

| 단계 | 유저 수 | 월 비용 | 비고 / 트리거 |
|---|---:|---:|---|
| **0. 포트폴리오 + 본인만** | 1 | **$5~$6** | D 안 그대로, **즉시 진입 가능** |
| **1. 클로즈드 베타** (지인/리뷰어) | 5~20 | **$5~$10** | Supabase free tier 안. DeepInfra 사용량 +$2~$5 |
| **2. 오픈 베타** (공개 가입) | 50~200 | **$25~$50** | **Supabase Pro $25/월 강제** (DB/storage 한도) + DeepInfra +$5~$15 + Gemini cap 관리 |
| **3. 본격 서비스** | 500+ | **$80~$200** | Railway Pro $20 + Supabase Pro + Gemini 유료 등급 + 도메인·모니터링 + Vercel Pro $20 |

**시작점**: 0~1단계 ($5~$10/월) — 본 sprint 의 Deploy W-1~W-7 ship 으로 도달.
**상향 트리거**: Supabase dashboard usage 60% 도달 또는 유저 30명 돌파 → 2단계 진입 검토.

### 13.2 추가 의사결정 4건 (사용자 명시 답변 대기)

#### Q8. 유료 vs 무료 서비스 모델

- A. **완전 무료** (포트폴리오용, 광고·결제 0)
- B. **무료 + 사용량 cap** (한 유저당 일일 인제스트 N건 / 검색 M회 후 lockout)
- C. **유료 구독제** (Stripe / 토스 결제 도입 — 별도 sprint)

권장: **B (무료 + cap)** — 포트폴리오 의도 정합 + 비용 폭주 방지. 본격 서비스 시 C 전환.

#### Q9. 공개 범위

- A. **누구나 가입** (이메일 + OAuth)
- B. **초대 코드 기반** (관리 가능 범위 내 베타)
- C. **본인만** (단일 유저 — 현 `DEFAULT_USER_ID` 그대로)

권장: **B → A** 단계적 진입. 베타 30명까지는 초대 코드, 안정화 후 공개.

#### Q10. 공개 시점

- A. v1.5 + Deploy ship **즉시 공개** (포트폴리오 평가 시기 맞추기)
- B. 1차 베타 (5~10명) → 안정화 → 공개
- C. **포트폴리오 평가 완료 후** 공개

권장: **B**. 안정성 검증 1주 + KPI #10 production 측정 후 공개.

#### Q11. Vercel Hobby 라이선스

- A. **Hobby 유지** — 광고·결제·수익 0 면 hobby 약관 정합
- B. **Pro $20/월 전환** — 안전 우선, 추후 분쟁 회피
- C. **Railway 에 web 도 deploy** ($5 추가) — Vercel 회피

권장: **A (Hobby 유지) — 단 수익 발생 시점에 즉시 Pro 전환**. Q8 가 B (무료) 면 Hobby 안전.

---

## 14. 멀티유저 추가 작업 D1~D7 (2026-05-18 신규)

### 14.1 작업 항목

| # | 작업 | 무엇 | 작업량 | DoD |
|---|---|---|---:|---|
| **D1** | **Auth 시스템** | 현 `DEFAULT_USER_ID` ENV = 단일 유저 가정 → Supabase Auth (Email/Google/GitHub OAuth) 도입 | **6~10h** | web 가입/로그인 페이지, backend `Authorization: Bearer <jwt>` 검증, user_id session 추출, 기획서 §15 갱신 |
| **D2** | **유저별 RLS audit** | `documents/chunks/vision_chunks/ingest_jobs/storage` 테이블 RLS 정책 audit. user_id 격리 검증 | **3~5h** | 5 테이블 RLS policy 확인·정정, 단위 테스트 RLS smoke (다른 user_id row 접근 불가), 마이그 017 (필요 시) |
| **D3** | **Per-user rate limit / 비용 cap** | 현 `JETRAG_DAILY_BUDGET_USD` 글로벌 cap → per-user cap 분리. 신규 `user_daily_usage` 테이블 + middleware | **4~6h** | user 별 인제스트/vision/search count + 비용 누적, cap 도달 시 429 응답, dashboard 노출 |
| **D4** | **법적 항목** | 개인정보처리방침 / 이용약관 / 가입 시 동의 체크박스. 한국 PIPA 필수, EU GDPR 옵션 | **2~4h** | `/privacy` `/terms` 페이지 작성, 가입 시 약관 동의 checkbox, DB 동의 시각 기록 |
| **D5** | **결제 모델 (Q8 답변에 따라)** | A (무료) = 작업 0 / B (cap) = D3 에 흡수 / C (구독) = Stripe/토스 도입 별도 sprint | **0h / 0h / +20~40h** | Q8 답변 후 결정 |
| **D6** | **Vercel 라이선스 (Q11 답변에 따라)** | A 유지 = 작업 0 / B Pro 전환 = $20/월 자동 / C Railway 에 web = 추가 deploy | **0h / 즉시 / +2h** | Q11 답변 후 결정 |
| **D7** | **Supabase Pro 전환 시점** | dashboard usage 60% 도달 시 알람 setup, 도달 시 Pro $25/월 결제 | **알람 5분 / 전환 1h** | Supabase usage alert email setup, 전환 시 마이그 영향 0 (동일 region) |

**총 작업량 (Q8=B / Q11=A 기준)**: **15~25h** (Deploy W-1~W-7 의 18~26h 와 통합 진행 가능 — D1 은 W-4 Vercel 진입 전 필수)

### 14.2 작업 순서 (Deploy sprint 와 통합)

```
1. v1.5 W-0 결정성 시험 (DeepInfra token 등록 후)
2. v1.5 W-1~W-3 ship — DeepInfra swap
3. D2 RLS audit (먼저 — 마이그 필요 시 deploy 전)
4. Deploy W-1~W-3 — Dockerfile + Railway service + env
5. D1 Auth 도입 (backend JWT 검증 + web 가입 페이지)
6. D3 per-user rate limit
7. Deploy W-4~W-5 — Vercel + CORS
8. D4 법적 페이지
9. Deploy W-7 canary — 지인 5명 sample 시연
10. D7 Supabase usage alert
11. (Q10 = B) 안정화 1주 → 오픈 베타
```

### 14.3 이직 포트폴리오 어필 포인트

기술 stack 가산점 (주관):
1. **Railway** — 신생, MZ 스타트업 — ★★
2. **Vercel** — 프론트엔드 표준 — ★★★
3. **Supabase + RLS** — 어필 가능 — ★★★ (멀티유저 격리 운영 경험 = +)
4. **DeepInfra (OpenAI-compatible)** — 어댑터 layer 보여줄 기회 — ★★
5. **BGE-M3 한국어 RAG + Gemini Vision + 하이브리드 검색** — 깊이 자체 가치 — ★★★★

README/디자인 docs 에 노출할 항목:
- 아키텍처 도면 (3-tier + 어댑터 layer)
- KPI 8개 측정 결과 + golden_v2 R@10 / top-1
- v1.5 어댑터 swap 사례 (HF → DeepInfra, R@10 회귀 0)
- 멀티유저 격리 + per-user cost cap 설계
- 비용 곡선 ($5 → $25 → $80) 단계별 운영 의사결정

### 14.4 페르소나 변경 영향

기존 메모리 `project_persona_kpi_decisions.md` v1 = 1인 사용자.
2026-05-18 v2 = **1인 (페르소나 A) + 외부 유저 N명 (페르소나 B)** 동시 운영.

페르소나 B 신규 가정:
- 한국어 RAG 가 필요한 개인 (학생/리서처/지식 워커)
- 무료 베타 진입 → 사용량 cap 도달 시 lockout 수용
- 모바일 우선 (Vercel CDN edge 활용)
- 본인 데이터 노출 우려 = RLS 격리 + 약관 동의

기획서 §11.5 포지셔닝은 **"기억 보조"** 유지하되 페르소나 B 도 동일 가치 약속 (chunk-first UX, 자동 paid 답변 0, 매칭 강도 production hide).

---

## 15. 다른 컴퓨터 / 새 세션 핸드오프 가이드 (2026-05-18 신규)

> 본 섹션은 **다른 컴퓨터** 또는 **새 Claude Code 세션** 에서 본 배포 작업을 이어받기 위한 완전 가이드. 본 세션 (2026-05-18) 의 진행 상황 + 미완료 항목 + 작업 절차를 모두 포함.

### 15.1 현재 진행 상태 한눈에 (2026-05-19 갱신)

| 항목 | 상태 | commit / dashboard |
|---|---|---|
| **HEAD** | `6361894` (origin 동기, push 완료) | `git log --oneline -1` |
| **단위 테스트** | 1206 OK (failures=3 기존 flaky, 회귀 0) | `cd api && uv run python -m unittest discover` |
| **v1.5 W-0 결정성 시험** | ✅ PASS (n=100, min cosine 0.999984) | `evals/run_v1_5_w0_determinism.py` |
| **DeepInfra account** | ✅ 가입 + billing + token | https://deepinfra.com/dash/api_keys |
| **Railway account** | ✅ 가입 + billing + repo 연결 + `/api` Root Directory | https://railway.com → feisty-hope project |
| **Railway Dockerfile push** | ✅ commit `e57c3ec` push 완료, production live | https://jet-rag-production.up.railway.app/ |
| **Railway ENV vars** | ✅ 등록 완료 (ENV 8건 + `JETRAG_CORS_ORIGINS=https://jetrag.vercel.app`) | Service → Variables |
| **Vercel account** | ✅ 가입 + `Jet-Rag` repo 연결 (대문자·하이픈 OK, 잘못된 `jetrag` repo 연결 해제 완료) | https://vercel.com/woongmins-projects/jetrag |
| **Vercel Framework Preset** | ✅ "Next.js" (초기 "Other" 였음 — §16.2.3) | Settings → Build and Deployment |
| **Vercel Root Directory** | ✅ `web` | Settings → Build and Deployment |
| **Vercel ENV `NEXT_PUBLIC_API_BASE_URL`** | ✅ `https://jet-rag-production.up.railway.app` (scheme 누락 fix — §16.2.4) | Settings → Environment Variables |
| **Vercel Deployment Protection** | ✅ Disabled (Q9 답변 후 재검토) | Settings → Deployment Protection |
| **Vercel production** | ✅ 200 OK, 홈 정상 렌더 | https://jetrag.vercel.app/ |
| **CORS env 화** | ✅ `JETRAG_CORS_ORIGINS` + Vercel regex | `api/app/main.py` |
| **W-7 canary** | ✅ 완료 (DECISION-13 등록) — §16 | 본 work-log |
| **v1.5 W-1 어댑터 swap** | ⏳ 미진입 (W-0 PASS 후 다음 단계) | senior-developer 위임 대기 |
| **멀티유저 D1~D7** | ⏳ 미진입 | Q9 답변 후 |

### 15.2 다른 컴퓨터에서 진입 절차 (clone → 검증)

**전제**: GitHub repo 접근 권한 + 카드 등록된 외부 서비스 account 4건 (DeepInfra / Railway / Vercel / Supabase) + .env 의 production 값.

#### Step 1. 코드 clone + 의존성 설치

```bash
# Python 3.12 / uv / Node 20+ / pnpm 사전 설치 가정
git clone https://github.com/woongminKi/Jet-Rag.git
cd Jet-Rag

# backend
cd api
uv sync --frozen
cd ..

# frontend
cd web
pnpm install
cd ..
```

#### Step 2. `.env` 복원 (값은 본인 비밀 저장소에서)

```bash
cp .env.example .env
# .env 편집 — 다음 값 채우기:
#   SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_STORAGE_BUCKET
#   GEMINI_API_KEY, HF_API_TOKEN, DEEPINFRA_API_TOKEN
#   DEFAULT_USER_ID
#   (선택) JETRAG_* 토글
```

`web/.env` 도 동일 (Next.js 용 NEXT_PUBLIC_* 키들).

#### Step 3. W-0 결정성 시험 재실행 (검증)

```bash
cd api
uv run python ../evals/run_v1_5_w0_determinism.py --sample 5    # smoke (~30s, <$0.0001)
uv run python ../evals/run_v1_5_w0_determinism.py --sample 100  # 본 (~5분, <$0.001)
```

기대 결과: min cosine ≥ 0.999, "PASS" 메시지.

#### Step 4. Docker build 로컬 검증

```bash
cd api
docker build -t jetrag-api .              # 빌드 (~1분, image ~1.28GB)
docker run --rm -p 8000:8000 -e PORT=8000 jetrag-api &
sleep 5
curl http://localhost:8000/health         # 기대: {"status":"ok"}
```

#### Step 5. 단위 테스트 회귀

```bash
cd api
uv run python -m unittest discover 2>&1 | tail -5
# 기대: Ran 1206 tests, failures=3 (기존 flaky, 본 변경 무관)
```

### 15.3 외부 서비스 dashboard 작업 (다른 컴퓨터에서도 동일)

#### 15.3.1 DeepInfra (BGE-M3 embedding API)

- URL: https://deepinfra.com/dash/api_keys
- 작업: API key 발급 → `.env` 의 `DEEPINFRA_API_TOKEN` 에 등록
- 검증: W-0 시험 PASS 면 정상
- 비용: <$1/월 (페르소나 트래픽)

#### 15.3.2 Railway (backend FastAPI 호스팅)

- URL: https://railway.com → project `feisty-hope` → service `Jet-Rag`
- 가입: GitHub OAuth → Hobby $5/월 billing 등록
- **Settings → Source**:
  - Source Repo: `woongminKi/Jet-Rag`
  - **Root Directory: `/api`** ← 중요
  - Branch: `main`
  - Auto deploy: Enabled
- **Settings → Build**:
  - Builder: Dockerfile auto-detected (`api/Dockerfile`)
  - Watch Paths (선택): `api/**`
- **Variables** (Raw Editor 에 일괄 입력):
  ```
  SUPABASE_URL=
  SUPABASE_KEY=
  SUPABASE_SERVICE_ROLE_KEY=
  SUPABASE_STORAGE_BUCKET=
  GEMINI_API_KEY=
  HF_API_TOKEN=
  DEEPINFRA_API_TOKEN=
  DEFAULT_USER_ID=
  JETRAG_CORS_ORIGINS=<Vercel URL 확정 후 등록>
  ```
- region: **Southeast Asia (Singapore)** — Supabase Seoul 와 ap 권역
- Deploy trigger: `main` push 시 자동, 또는 Deployments 페이지 Deploy 버튼 수동
- public URL: 자동 발급 `*.up.railway.app`

#### 15.3.3 Vercel (frontend Next.js 호스팅)

- URL: https://vercel.com/woongmins-projects/jetrag
- 가입: GitHub OAuth → Hobby (무료, 비상업 약관)
- **Settings → Build and Deployment**:
  - **Root Directory: `web`** ← 중요 (General 탭이 아니라 Build and Deployment 탭!)
  - Framework Preset: `Next.js` (auto)
  - Build Command: 기본값 (`next build`)
  - Install Command: 기본값 (`pnpm install`)
  - Output Directory: 기본값 (`.next`)
- **Settings → Environment Variables**:
  ```
  NEXT_PUBLIC_SUPABASE_URL=<.env 의 값>
  NEXT_PUBLIC_SUPABASE_ANON_KEY=<.env 의 값>
  NEXT_PUBLIC_API_BASE_URL=<Railway public URL 확정 후 등록>
  ```
- **Deployments → Redeploy** (Use existing Build Cache **해제** 권장)
- public URL: 자동 발급 `*.vercel.app`

#### 15.3.4 Supabase (DB + Storage + Auth)

- URL: https://supabase.com/dashboard
- region: `ap-northeast-2` Seoul (변경 X)
- 작업 없음 (기존 그대로 사용)
- 멀티유저 D1 진입 시: Authentication → Providers (Email / Google / GitHub OAuth) 활성화

### 15.4 트러블슈팅 (본 세션에서 만난 실 사례)

#### 15.4.1 Vercel "Total bundle size 712 MB exceeds 500 MB"

**원인**: Root Directory 가 repo 루트 → `api/` Python 의존성 + `web/` node_modules + `.venv` 모두 번들링.
**해결**:
1. Vercel Settings → **Build and Deployment** (General 탭 아님!)
2. Root Directory 입력란에 `web` 입력 → Save
3. Deployments → 실패 deployment ⋯ → Redeploy (cache 해제)

#### 15.4.2 Railway "Build failed in 00:09"

**원인**: Dockerfile 없음 + monorepo (api/ + web/) → RAILPACK 자동 감지 실패.
**해결** (본 세션 commit `e57c3ec` 이후 자동 해결):
1. `api/Dockerfile` 존재 확인
2. Railway Settings → Source → Root Directory = `/api`
3. 새 commit push 시 자동 rebuild

#### 15.4.3 W-0 시험에서 cwd 에러 `cd: no such file or directory: api`

**원인**: 직전 명령에서 cwd 가 이미 `api/` 안으로 이동됨.
**해결**: 절대경로 사용 또는 `pwd` 로 현재 위치 확인 후 진입.

#### 15.4.4 Railway outbound IPv6 미지원

**원인**: Railway 가 IPv6 outbound 미지원, Supabase 가 IPv6 우선 응답.
**영향**: Jet-Rag 는 supabase-py REST (HTTPS) 만 사용 → **무영향**.
**대비**: 추후 raw Postgres 직접 연결 도입 시 Session Pooler URL (`pooler.supabase.com`) 강제.

#### 15.4.5 Docker image 1.28GB 큼

**원인**: `ragas`, `datasets`, `supabase`, `langchain-google-genai` 등 무거운 의존성을 모두 `uv sync` 로 설치.
**영향**: cold-start 약간 느릴 수 있으나 Railway always-on 이라 무관.
**후속 개선** (별도 sprint): multi-stage build + `--no-dev` group 분리로 ~500MB 까지 축소.

### 15.5 본 세션에서 변경된 파일 (commit `e57c3ec`)

| 파일 | 변경 | 목적 |
|---|---|---|
| `api/Dockerfile` | 신규 (32행) | Railway build |
| `api/.dockerignore` | 신규 (48행) | image 크기 절감 |
| `api/app/main.py` | +14 / -1 | CORS env (`JETRAG_CORS_ORIGINS`) + Vercel regex |
| `api/app/config.py` | +6 | `deepinfra_api_token` Settings 필드 |
| `api/tests/test_config.py` | 신규 (81행) | Settings smoke 4건 (1202 → 1206 OK) |
| `evals/run_v1_5_w0_determinism.py` | 신규 (332행) | HF vs DeepInfra cosine 결정성 시험 |
| `.env.example` | +6 | `DEEPINFRA_API_TOKEN=` 템플릿 |
| `work-log/2026-05-18 배포 방법 검토 — Railway + HuggingFace.md` | 신규 (640+행) | 본 문서 |

### 15.6 다음 단계 (순서, 2026-05-19 갱신)

W-1~W-7 단계 모두 완료 (§16 참조). 다른 컴퓨터에서 다음 sprint 진입 순서:

```
1. (검증) git pull → HEAD = 6361894 확인
2. (검증) https://jetrag.vercel.app/ 200 OK 확인
3. (검증) https://jet-rag-production.up.railway.app/health 200 OK 확인
4. (다음 sprint) v1.5 W-1 — DeepInfra 어댑터 swap (senior-developer 위임, 2~3h)
   - work-log: `2026-05-15 HF self-host 검토 — v1.5 sprint 설계.md` §3
   - 산출: api/app/adapters/embedding/deepinfra.py + 단위 테스트 + R@10 회귀 0 검증
5. (다음 sprint) 멀티유저 D1·D2·D3 — Auth + RLS audit + per-user rate limit
   - 의존: Q9 답변 (공개 범위)
   - work-log: 본 문서 §14
6. (Q1 답변 후) 무료 도메인 부착 — Cloudflare/Freenom → Vercel custom domain
```

### 15.7 미응답 의사결정 (사용자 명시 답변 대기)

| Q | 항목 | 권장값 | 시점 |
|---|---|---|---|
| Q1 | 도메인 보유 | 무료 도메인부터 | 즉시 가능 |
| Q8 | 결제 모델 | 무료 + per-user cap | Phase 5 진입 전 |
| Q9 | 공개 범위 + Auth provider | 초대 코드 + Email/Google OAuth | Phase 3 진입 전 |
| Q10 | 공개 시점 | 1차 베타 5~10명 → 1주 안정화 → 공개 | Phase 5 진입 전 |
| Q11 | Vercel 라이선스 | Hobby 유지 (수익 0 시) | Q8 답변에 의존 |

### 15.8 관련 메모리 / work-log 참조 (다른 컴퓨터에서 컨텍스트 부팅용)

다른 Claude Code 세션 시작 시 자동 로드되는 메모리:
- `~/.claude/projects/-Users-kiwoongmin-Desktop-documents-test-repo-image2-piLab-project-Jet-Rag/memory/MEMORY.md` — 인덱스
- `project_jet_rag.md` — 현 상태 종합 (2026-05-18 진전 반영)
- `project_persona_kpi_decisions.md` v2 — 페르소나 A + B 확장, D 안 확정
- `project_remaining_sprints.md` v0.2 — v1.5 / Deploy / 멀티유저 D1~D7 진입 순서

본 work-log 외 참조 work-log:
- `2026-05-15 HF self-host 검토 — v1.5 sprint 설계.md` — v1.5 권장안 (DeepInfra)
- `2026-05-15 세션 종합 — 추천 4건 진행 + DECISION-12 보너스.md` — 직전 핸드오프
- `2026-05-12 검색 정확도 80% 달성 PRD.md` — KPI #10 deferred (latency)

### 15.9 보안 / secret 관리 주의

- `.env` 는 절대 commit X (`.gitignore` 에 포함 확인)
- Railway/Vercel/Supabase dashboard 의 ENV 값은 절대 채팅 X (값 보유 여부만 보고)
- `SUPABASE_SERVICE_ROLE_KEY` 는 backend 전용 (web 클라이언트 노출 X)
- `NEXT_PUBLIC_SUPABASE_ANON_KEY` 는 클라이언트 노출 OK (RLS 가 보호)
- 다른 컴퓨터로 `.env` 옮길 때: 1Password / Bitwarden 같은 secret 매니저 사용 권장. 평문 파일 전송 X.

### 15.10 회고

배포 작업이 0건이었던 이유는 **MVP 6주 동안 검색 정확도 80% + 인제스트 안정화에 집중**했기 때문. v1.5 sprint 와 동시에 Deploy 가 가능해진 시점에 D 안 (Railway + Vercel + DeepInfra) 으로 단순 deploy 가능했다. 본 세션의 핵심 학습:

1. **monorepo 가 양쪽 PaaS 의 함정** — Vercel·Railway 둘 다 Root Directory 명시 안 하면 자동 감지가 잘못된 디렉토리 (또는 둘 다) 를 build → 실패. 두 서비스 모두 dashboard 에서 명시.
2. **W-0 결정성 시험이 ROI 매우 높음** — 1시간 작업으로 chunks 37k 재인제스트 ($0.185 + 수 시간) 회피 결정 + 옵션 A fallback ($24/월) 회피 결정 둘 다 확정.
3. **결제는 사용자만, 코드는 Claude** — 명확한 권한 분리가 작업 속도 ↑. 사용자가 결제까지 미리 마치면 코드 commit·push → 즉시 자동 rebuild.

---

## 16. W-7 canary 완료 — DECISION-13 등록 (2026-05-19 신규)

### 16.1 진행 결과

**production URL 양쪽 200 OK 도달**:

- Frontend: https://jetrag.vercel.app/ → 홈 화면 정상 렌더 (검색 input, 최근 추가 5건, 인기 태그 8개, 내 문서 현황)
- Backend: https://jet-rag-production.up.railway.app/ → `/health` `/docs` 200, CORS preflight `access-control-allow-origin: https://jetrag.vercel.app` 반영, Singapore edge `asia-southeast1-eqsg3a` 확정
- 통신: 브라우저에서 stats / documents / popular_tags API 호출 정상 — 4일 전 인제스트한 5건 (SK·삼성전자·arXiv·게이지/중력 이중성 등) 그대로 표시

### 16.2 실제로 만난 트러블슈팅 (15.4 추가분)

#### 16.2.1 Vercel 이 잘못된 commit `ab26db5` (Initial commit) 만 배포

- **원인 1**: "Skip deployments when there are no changes to root directory" Enabled → web/ 외 변경은 무시
- **원인 2**: Vercel 이 `woongminKi/jetrag` (별도 private repo, 다른 컴퓨터에서 대문자·하이픈 안 된다 잘못 듣고 새로 만든 것) 에 연결되어 있었음 — 본 작업 repo `woongminKi/Jet-Rag` 와 별개
- **해결**:
  1. Skip 설정 Disabled
  2. `jetrag` repo 연결 해제
  3. Vercel GitHub App 의 repository access 를 `Jet-Rag` 에 부여 (initially "All repositories" 권한 부족)
  4. `Jet-Rag` 재연결 + 빈 commit push 로 webhook 강제 trigger (`1a8e062`, `117a98d`)

#### 16.2.2 새 deployment 가 모두 HTTP 401

- **원인**: Vercel Deployment Protection "Standard Protection" Enabled — `vercel.app` preview/production 모두 Vercel SSO 강제
- **해결**: Settings → Deployment Protection → "Require Log In" Off (포트폴리오라 public 노출 OK, Q9 답변 후 재검토)

#### 16.2.3 모든 route 가 404 (정적 asset 만 200)

- **증거**: `/next.svg` = 200 (web/public/ 정적), `/_next/static/chunks/main.js` = 404 (Next.js 빌드 산출물)
- **원인**: Framework Preset = **"Other"** → Vercel 이 `.next/` build 산출물 무시, `web/public/` 만 static serve
- **해결**: Framework Preset = "Next.js" 변경 + Save + 수동 Redeploy (Use existing Build Cache 체크 해제)

#### 16.2.4 frontend 가 backend URL 을 `/jet-rag-production.up.railway.app/...` 로 path 화

- **증거**: 브라우저 console `GET https://jetrag.vercel.app/jet-rag-production.up.railway.app/documents/active` → 404
- **원인**: Vercel `NEXT_PUBLIC_API_BASE_URL` 값에 `https://` scheme 빠짐 → `fetch(`${BASE_URL}${path}`)` 가 상대경로 해석
- **해결**: ENV 값 정정 `https://jet-rag-production.up.railway.app` + Redeploy (NEXT_PUBLIC_* 은 빌드 시 inlining 이라 ENV 저장만으론 안 됨)

#### 16.2.5 ENV 변경 후 자동 redeploy 안 됨

- **원인**: Vercel 의 settings 변경은 webhook trigger 안 함 (git push 만 trigger)
- **해결**: Deployments 페이지에서 수동 Redeploy 호출

### 16.3 DECISION-13 — 배포 완료 확정

> **배포 D 안 (Railway $5 backend + Vercel Hobby frontend + DeepInfra embedding) production 도달 — 2026-05-19**

- **선정 근거**: §1~§6 비교 분석 + 사용자 결정 D 안 (2026-05-18) → §15.6 9 step W-1~W-7 ship → §16 canary 200 OK 확정
- **현 상태**:
  - 비용: Railway $5/월 + Vercel $0 + DeepInfra ~$0 (token 미사용 — v1.5 W-1 진입 시 측정 시작)
  - SLO: KPI #10 (P95 ≤ 2.5s) production 측정 대기 (Q1 도메인 + 실 유저 트래픽 필요)
  - Multi-user: 현 `DEFAULT_USER_ID` 단일 유저 가정 — 멀티유저 D1~D3 (Q9 답변 후) 다음 단계
- **사이드 이펙트**:
  - Vercel 이 production scheme 강제 (`https://`) — backend URL 등록 시 scheme 누락 금지 (실 사례 16.2.4)
  - Railway Singapore edge → Supabase Seoul cross-region latency 0 측정 (~30ms 1-way) 가정, 실측 v1.5 ship 후 KPI #10 측정 시 검증
  - 두 repo 가 GitHub 에 남아있음 (`woongminKi/jetrag` 미사용, `woongminKi/Jet-Rag` master) — 정리 권장 (Q1 답변 후 도메인 부착 시 같이)

### 16.4 다음 단계 (W-7 ship 이후)

1. **v1.5 W-1 진입** — DeepInfra 어댑터 swap (senior-developer 위임, 2~3h) → R@10 회귀 0 검증 → ship
2. **멀티유저 D1·D2·D3** — Q9 답변 후 진입 (Auth Supabase + RLS audit + per-user cap)
3. **Q1 도메인 부착** — 무료 도메인 (Cloudflare / Freenom) 으로 시작 → Vercel custom domain
4. **KPI #10 production 측정** — Q10 답변 후 1차 베타 5~10명 진입 시 측정 시작

### 16.5 commit / 산출물

| commit | 내용 |
|---|---|
| `e57c3ec` | Dockerfile + DeepInfra W-0 + Vercel CORS env (전 세션) |
| `1a8e062` | reconnect 후 webhook 빈 commit trigger 1 |
| `117a98d` | reconnect 후 webhook 빈 commit trigger 2 |
| `6361894` | §16 추가 + DECISION-13 등록 |

---

## 17. 다른 컴퓨터로 작업 이어가기 — 빠른 진입 (2026-05-19 신규)

본 sprint 종료. 다른 컴퓨터에서 다음 세션 진입 시 5분 안 도달 가능한 단일 가이드.

### 17.1 사전 준비 (다른 컴퓨터에 1회만)

```bash
# 1. 도구 설치 (없으면)
#    Python 3.12 (pyenv 권장)
#    uv (https://docs.astral.sh/uv/)
#    Node 20+
#    pnpm

# 2. git clone
git clone https://github.com/woongminKi/Jet-Rag.git
cd Jet-Rag

# 3. backend 의존성
cd api && uv sync --frozen && cd ..

# 4. frontend 의존성
cd web && pnpm install && cd ..
```

### 17.2 secret 복원 — 로컬 작업할 때만 필요

**중요**: Railway / Vercel / Supabase dashboard 에 ENV 가 이미 모두 등록되어 있다. **production 만 쓸 거면 `.env` 복원 불필요**. 다른 컴퓨터에서 production live URL 그대로 접속해서 쓰면 됨.

#### 17.2.1 `.env` 복원이 필요한 경우 vs 불필요한 경우

| 작업 | `.env` 필요? | 이유 |
|---|---|---|
| 브라우저에서 production 사용 (https://jetrag.vercel.app/) | ❌ | dashboard ENV 만으로 충분 |
| Claude Code 가 코드 편집 + git push (push 후 자동 rebuild) | ❌ | git 만 있으면 됨 |
| Claude Code 가 production smoke (`curl` 외부 URL) | ❌ | 외부 URL 호출 |
| `cd api && uvicorn` 로컬 backend dev 실행 | ✅ | `SUPABASE_*` / `GEMINI_API_KEY` 등 필요 |
| `cd web && pnpm dev` 로컬 frontend dev 실행 | ✅ | `NEXT_PUBLIC_SUPABASE_*` 필요 |
| `unittest discover` 단위 테스트 (DB 의존) | ⚠️ 일부 | Supabase 연결 테스트는 `.env` 필요 |
| W-0 결정성 시험 재실행 | ✅ | `DEEPINFRA_API_TOKEN` 필요 |
| v1.5 W-1 어댑터 swap (senior-developer 위임) | ✅ | DeepInfra 어댑터 로컬 테스트 시 |

→ **v1.5 W-1 / 멀티유저 작업하려면 `.env` 복원 필요. production 만 쓸 거면 skip OK.**

#### 17.2.2 secret 복원 절차 (필요한 경우만)

본 컴퓨터에 있는 다음 2개 파일을 비밀 저장소 (1Password Secure Note 등) 에 미리 옮겨두고, 다른 컴퓨터에서 그대로 복원:

```bash
# 본 컴퓨터에서 secret 백업 (안전 채널만 — 평문 채팅 X)
cp /Users/kiwoongmin/Desktop/piLab/Jet-Rag/.env       <secret-store>/jetrag-api.env
cp /Users/kiwoongmin/Desktop/piLab/Jet-Rag/web/.env   <secret-store>/jetrag-web.env

# 다른 컴퓨터에서 복원
cp <secret-store>/jetrag-api.env   <repo>/.env
cp <secret-store>/jetrag-web.env   <repo>/web/.env
```

`.env` 안 값 (2026-05-21 D1+D2+E4 ship 반영):
- `SUPABASE_URL` / `SUPABASE_KEY` / `SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_STORAGE_BUCKET`
- `GEMINI_API_KEY` / `HF_API_TOKEN` / `DEEPINFRA_API_TOKEN`
- `DEFAULT_USER_ID`
- **D1 멀티유저** (필수 — 2026-05-21 ship 후):
  - `JETRAG_AUTH_ENABLED=true` (production), `false` (local dev MVP 보존)
  - `SUPABASE_JWT_SECRET` — Supabase dashboard > Settings > API > JWT Settings
  - `SUPABASE_JWT_ALGORITHM=ES256` (Supabase 신규 프로젝트 ECC default — memory `supabase_jwt_ecc_default.md`)
  - `SUPABASE_JWKS_URL=https://<ref>.supabase.co/auth/v1/.well-known/jwks.json` (비대칭 알고리즘 사용 시 필수)
  - `OWNER_USER_ID` — 본인 Supabase user UUID (admin 게이트, memory `jetrag_owner_identity.md`)
- (선택) `JETRAG_DAILY_BUDGET_USD` / `JETRAG_CORS_ORIGINS` / `PORT`

`web/.env` 안 값:
- `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` (로컬 dev 시) **또는** `https://jetrag-api.woong-s.com` (로컬 web → production backend 시)
- `NEXT_PUBLIC_SITE_URL=https://jetrag.woong-s.com` (production OAuth callback 용)
- `NEXT_PUBLIC_COOKIE_DOMAIN=.woong-s.com` (D1 ship — 앞 점 필수, subdomain 공유)
- `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY`

### 17.3 검증 (다른 컴퓨터에서)

#### 17.3.1 production 만 쓸 거면 (1분, `.env` 불필요)

```bash
# 1. HEAD 확인
git log --oneline -1
# 기대 (2026-05-21 D1+D2+E4 ship 후): 841ea8a docs(readme+changelog): W31 멀티유저 sprint ...
# 또는 더 신규 commit (다음 sprint 진입 후)

# 2. production smoke (custom domain 우선)
curl -s -o /dev/null -w "frontend: %{http_code}\n" https://jetrag.woong-s.com/
curl -s -o /dev/null -w "/search no-token: %{http_code}\n" "https://jetrag-api.woong-s.com/search?q=test"
curl -s -o /dev/null -w "/health: %{http_code}\n" "https://jetrag-api.woong-s.com/health"
# 기대: frontend 307 (login redirect, auth 활성) / /search 401 (인증 필요) / /health 200
```

#### 17.3.2 로컬 dev 도 할 거면 (5분, `.env` 필요)

```bash
# 1. HEAD + 단위 테스트
git log --oneline -1
cd api && uv run python -m unittest discover 2>&1 | tail -5
# 기대 (2026-05-21 D1+D2+E4 누적): Ran 1336+ tests, failures/errors 7 (baseline flaky 동일)
cd ..

# 2. backend smoke (로컬 — local dev 는 JETRAG_AUTH_ENABLED=false MVP 보존 권장)
cd api && uv run uvicorn app.main:app --port 8000 &
sleep 5
curl http://localhost:8000/health
# 기대: {"status":"ok",...}
kill %1; cd ..

# 3. production smoke (외부 — custom domain 우선)
curl -s -o /dev/null -w "frontend: %{http_code}\n" https://jetrag.woong-s.com/
curl -s -o /dev/null -w "/search no-token: %{http_code}\n" "https://jetrag-api.woong-s.com/search?q=test"
curl -s -o /dev/null -w "/health: %{http_code}\n" "https://jetrag-api.woong-s.com/health"
# 기대 (D1 ship 후): frontend 307 / /search 401 (auth 활성) / /health 200
```

### 17.4 Claude Code 세션 진입

```bash
# 다른 컴퓨터에서 Claude Code 실행
cd Jet-Rag
claude

# 또는 권한 자동 진입
claude --dangerously-skip-permissions
```

세션 시작 시 자동 로드:
- `CLAUDE.md` (없음 — 부모 `/Users/<user>/Desktop/piLab/CLAUDE.md` 참조 권장)
- `~/.claude/projects/-Users-<user>-Desktop-piLab-Jet-Rag/memory/MEMORY.md`

**경로가 다르면 memory 가 자동 로드 안 됨**. 같은 경로 (`~/Desktop/piLab/Jet-Rag/`) 유지 권장. 다른 경로 쓰려면 `~/.claude/projects/` 안 본 디렉토리 명으로 변환된 폴더에 memory 옮겨두기:

```bash
# 본 컴퓨터의 memory 백업
cp -r ~/.claude/projects/-Users-kiwoongmin-Desktop-piLab-Jet-Rag/memory/ <secret-store>/

# 다른 컴퓨터의 user 명에 맞게 경로 변환 (e.g. /Users/홍길동/Desktop/piLab/Jet-Rag → -Users-홍길동-Desktop-piLab-Jet-Rag)
mkdir -p ~/.claude/projects/-Users-<other-user>-Desktop-piLab-Jet-Rag/
cp -r <secret-store>/memory/ ~/.claude/projects/-Users-<other-user>-Desktop-piLab-Jet-Rag/
```

### 17.5 첫 명령 (Claude 에게 진입 요청)

다른 컴퓨터에서 Claude Code 진입 후 첫 메시지:

```
work-log/2026-05-18 배포 방법 검토 — Railway + HuggingFace.md 의 §15 + §16 + §17 읽고 현재 상태 파악해. 다음 후보는 A (v1.5 W-1 DeepInfra swap) / B (멀티유저 D1~D3, Q9 답변 후) / C (Q1 도메인) / D (repo 정리) 중 어디로 갈까?
```

Claude 가 §15.1 표 + §16.4 다음 단계 + §15.7 Q1·Q8~Q11 미응답 의사결정 참조하여 권고안 제시.

### 17.6 다른 컴퓨터에서 NOT 해야 할 작업 (보안)

- ❌ `.env` 파일 평문 채팅·이메일·Slack 전송
- ❌ `SUPABASE_SERVICE_ROLE_KEY` / `GEMINI_API_KEY` / `DEEPINFRA_API_TOKEN` 값 채팅에 노출
- ❌ Railway/Vercel/Supabase dashboard 의 ENV 값 값 자체 출력
- ❌ git push --force 또는 `branch -D main`
- ❌ 두 repo (`jetrag` / `Jet-Rag`) 헷갈리지 말 것 — `Jet-Rag` (대문자·하이픈 포함) 가 master

### 17.7 본 sprint 종합 — 핸드오프 한 줄

> **2026-05-18 D 안 결정 → 05-19 W-7 canary 200 OK 완료. 다음 = v1.5 W-1 DeepInfra swap (senior-developer 위임).** HEAD `6361894`, https://jetrag.vercel.app/ + https://jet-rag-production.up.railway.app/ 양쪽 production live, 단위 테스트 1206 OK 회귀 0, KPI #10 production 측정 대기 (Q1·Q10 답변 후).

---

### 17.8 W31 멀티유저 ship 후 추가 안내 (2026-05-21 갱신)

D1 + D2 + E4 fix 까지 ship 완료. 다른 컴퓨터 진입 시 추가 인지 사항:

**Production endpoint (W31 후 우선 사용):**
- frontend: <https://jetrag.woong-s.com> (auth 활성 → 무토큰 접속 시 `/login` 리다이렉트)
- backend: <https://jetrag-api.woong-s.com> (custom domain — `/search` 무토큰 401, `/health` 공개 200)

**본인 자격 증명 (memory `jetrag_owner_identity.md` 참조):**
- email: `dndals1991@gmail.com`
- password: `JetRag2026Owner!` (admin reset 값, **추후 본인 변경 권장**)
- owner UUID: `2af8fca5-03ab-421b-94b8-53d4fe9d8046`

**ENV 추가 갱신 (Railway / Vercel — 2026-05-21 등록 완료, 다른 dev 환경 동기화 시 참고):**
- Railway: `JETRAG_AUTH_ENABLED=true`, `SUPABASE_JWT_SECRET`, `SUPABASE_JWT_ALGORITHM=ES256`, `SUPABASE_JWKS_URL=https://mpmtydudhojpukuuadrd.supabase.co/auth/v1/.well-known/jwks.json`, `OWNER_USER_ID=2af8fca5-...`
- Vercel: `NEXT_PUBLIC_SITE_URL=https://jetrag.woong-s.com`, `NEXT_PUBLIC_COOKIE_DOMAIN=.woong-s.com`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`

**마이그레이션 17-20 (다른 Supabase 프로젝트로 복제 시):**
- 017 invite_codes — `JETRAG-OWNER-001` 같은 seed 1건 INSERT 후 본인 가입
- 018 데이터 이관 — 기존 single-user MVP 의 `default_user_id` row 를 본인 UUID 로 UPDATE (1회성)
- 019 RLS — 7 테이블 25 정책 + RPC `get_chunks_stats_for_user`
- 020 Storage prefix — `user/<uid>/` migration script (`api/scripts/migrate_storage_to_per_user.py`)
- 적용 가이드: `work-log/2026-05-20 D1+D2 ship 단계별 가이드 (Phase 1-5).md`

**다른 컴퓨터 첫 명령 (Claude 에게):**
```
work-log/2026-05-21 세션 종합 — D1 Phase 4 + D2 Phase 5 ship 완료.md 읽고 현재 상태 파악. 다음 후보는 A (API 키 회전 TODO — 가장 우선) / B (runtime 격리 실증 — anon key + 두 user) / C (베타 30명 공개 진입) / D (데모 GIF 녹화) 중 어디로 갈까?
```

**경고 — TODO 우선 (다른 컴퓨터 첫 작업):**
- `work-log/2026-05-21 TODO — Phase 4 노출 API 키 회전 필수.md` §4 체크리스트 6단계. Supabase service_role + Railway account token 회전 안 하면 보안 risk 누적.
