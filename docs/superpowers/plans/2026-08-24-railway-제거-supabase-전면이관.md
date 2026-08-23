# Railway 제거 — Supabase 전면 이관 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 또는 superpowers:executing-plans 로 task 단위 실행. 체크박스(`- [ ]`) 로 진행 추적.

**Goal:** Railway Hobby 플랜을 완전히 해지하고, Jet-Rag 백엔드 전 기능을 Supabase(Edge Functions + Queues + pg_cron)에서 동작시킨다.

**Architecture:** FastAPI 단일 컨테이너(상주 프로세스 + 인프로세스 `BackgroundTasks`)를 **Deno Edge Functions 다수 + pgmq 큐 기반 페이지 단위 팬아웃**으로 재구성한다. Edge Functions 의 요청당 CPU 2s 제한 때문에 인제스트 파이프라인은 "한 번에 전부"가 아니라 "작업 단위 1개씩" 처리하는 큐 드리븐 상태 머신이 되어야 한다. 문서 파싱은 Python 네이티브 라이브러리(PyMuPDF/pyhwp/python-pptx) 대신 **Rust/WASM 파서**로 교체한다.

**Tech Stack:** Deno / TypeScript / Supabase Edge Functions / pgmq(Supabase Queues) / pg_cron / pg_net / Supabase Vault / WASM 파서(`@ohah/hwpjs` 또는 `@rhwp/core`, `mupdf-wasm` 또는 `pdf.js`) / Web Crypto API(Fernet 호환)

---

## 0. 확정된 제약 (실측·문서 확인 완료)

| 항목 | 값 | 출처 | 영향 |
|---|---|---|---|
| 런타임 | **Deno/TypeScript 전용** (Python 미지원, 로드맵 없음) | Supabase Docs / Discussion #22944 | Python 20,714 LOC 전량 재작성 |
| CPU time | **2s / 요청 — 전 플랜 동일** (async I/O 는 미포함) | Supabase Limits | 파싱을 페이지 단위로 분할 필수 |
| Memory | **256MB** | Supabase Limits | 대용량 PDF 스트리밍 처리 필요 |
| Wall clock | 초기 응답 **150s**, 백그라운드 **400s** | Supabase Limits | **Pro 확정 → 400s 사용 가능** |
| 번들 크기 | 로컬 20MB / 서버 5MB | Supabase Platform Limits | WASM 파서 번들 크기 제약 |

**CPU 2s 는 유료 전환으로 완화되지 않는다.** 자체 호스팅 외 우회 경로 없음 — 이 플랜 전체가 이 제약 위에 설계된다.

**전 함수 공통 설정:** Edge Functions 는 기본적으로 게이트웨이가 `Authorization` JWT 를 먼저 검증한다. 브라우저 직접 호출은 쿠키만 보내고 헤더가 없으므로 **모든 함수를 `verify_jwt = false` 로 배포하고 인증은 `_shared/current_user.ts` 가 직접 수행**한다. 빠뜨리면 로그인 사용자·익명 방문자 모두 함수 코드에 닿기 전에 401 이 된다.

**비용:** Supabase Pro 를 이미 사용 중이므로 신규 비용 0. Railway Hobby **$5/mo 가 순수 절감**이고, 관리 지점은 4개 벤더 → 3개로 줄어든다.

**부수 이득 (실측):** `app/db/client.py` 의 `_force_postgrest_http1()` 우회 코드는 postgrest-py 가 `http2=True` 를 하드코딩해 생기는 GOAWAY/HPACK 손상 문제 대응이다. Deno 의 `supabase-js` 는 `fetch` 기반이라 **이 클래스의 장애가 구조적으로 사라진다** — 이관과 함께 삭제 가능한 부채.

## 1. 이관 대상 규모 (실측)

| 영역 | LOC | 이식 난이도 | 비고 |
|---|---|---|---|
| 파서 (`adapters/impl/*_parser.py`) | 1,769 | **최상** | 네이티브 의존 → WASM 교체 |
| 인제스트 (`ingest/` + `stages/`) | 3,876 | **최상** | 실행 모델 자체가 바뀜 |
| 검색·답변 (`routers/search,answer`) | 2,983 | 중 | I/O 위주, CPU 는 MMR/RRF 뿐 |
| 기타 라우터 | 1,918 | 하 | DB 질의 위주 |
| services | 6,643 | 중 | `vision_need_score`(616) 는 PDF 의존 |
| adapters (비파서) | 2,530 | 하 | HTTP 클라이언트 |
| auth/db/config/main | 995 | 하 | JWT 검증은 Deno 라이브러리 존재 |
| **소스 합계** | **20,714** | | |
| 테스트 (pytest 127 파일) | **32,176** | — | Deno test 로 재작성 필요 |

**총 재작성 대상 ≈ 52,890 LOC.** 이것이 이 플랜의 실제 비용이다.

## 2. 목표 아키텍처

```
┌─ Cloudflare ─────────┐   Email Routing(MX) + Worker   ← 이관 불가(유지)
└──────────┬───────────┘
           │ webhook
┌─ Supabase ───────────────────────────────────────────┐
│  Edge Functions (Deno)                                │
│   ├ api-search      /search                           │
│   ├ api-answer      /answer                           │
│   ├ api-documents   업로드·CRUD                        │
│   ├ api-account     /me /auth/me /stats /admin        │
│   ├ api-payments    /payments/*                       │
│   ├ email-webhook   /email/ingest                     │
│   ├ ingest-worker   큐 1건 처리 (CPU 2s 이내)          │
│   └ billing-run     정기결제 배치                      │
│  pgmq(Queues) ── pg_cron(드레인/배치) ── pg_net        │
│  Postgres(pgvector) · Storage · Auth                  │
└───────────────────────────────────────────────────────┘
┌─ Vercel ─────────────┐   web/ Next.js                 ← 유지
└──────────────────────┘
```

**관리 지점: 4개 벤더 → 3개** (Railway 제거). Cloudflare 는 이메일 수신 MX 가 필요해 이관 불가.

### 공개 URL — Cloudflare 리버스 프록시 (전 Phase 에 영향하는 결정)

Edge Functions 를 `*.supabase.co` 로 직접 노출하면 **로그인이 붕괴한다.** 프론트는 브라우저에서 `credentials: 'include'` 로 호출하고(`web/src/lib/api/client.ts:55`), 백엔드는 `sb-<ref>-auth-token` 쿠키에서 토큰을 꺼낸다(`app/auth/cookie_token.py`). 이 쿠키는 `web/src/lib/supabase/server.ts:43` 의 `COOKIE_DOMAIN` 으로 `.woong-s.com` 에 스코프돼 있어 **다른 등록 도메인으로는 전송되지 않는다.**

따라서 `jetrag-api.woong-s.com` 을 Cloudflare Worker 리버스 프록시로 유지하고, 경로를 `/functions/v1/api-*` 로 매핑한다.

```
브라우저 ─(.woong-s.com 쿠키 첨부)─▶ jetrag-api.woong-s.com [CF Worker] ─▶ <ref>.supabase.co/functions/v1/api-*
                                                    └─ 미이관 경로는 LEGACY_ORIGIN(기존 백엔드)으로
```

이 결정이 주는 것:
- **프론트 코드 변경 0줄** — `NEXT_PUBLIC_API_BASE_URL` 도 그대로
- **경로 단위 점진 전환** — Phase 마다 라우팅 한 줄씩 옮기고, 전부 끝나면 `LEGACY_ORIGIN` 을 비워 Railway 를 끊는다. Phase 6 의 전환 장치가 Phase 1 에 이미 서 있는 셈
- Supabase Custom Domain 유료 애드온 불필요

### 인제스트 실행 모델 전환

| | 현재 (Railway) | 이후 (Supabase) |
|---|---|---|
| 트리거 | `BackgroundTasks.add_task` (6개소) | `pgmq.send` → `pg_cron` 드레인 |
| 실행 단위 | 문서 1건 = 9-stage 통째 | **작업 1건**(1페이지 추출 / 1청크배치 임베딩) |
| 상태 | 인프로세스 | `ingest_jobs` 테이블 (이미 stage progress 보유 — `010_*.sql`) |
| 고아 job 정리 | `main.py` lifespan sweep | `pg_cron` 주기 sweep |
| BGE-M3 warmup | lifespan fire-and-forget | `pg_cron` 주기 ping |

`ingest_jobs` 의 stage progress 스키마가 이미 존재하는 것이 최대 이점 — 상태 머신 테이블을 새로 설계하지 않아도 된다.

---

## 3. Phase 0 — 타당성 스파이크 (필수 게이트)

**이 Phase 를 통과하지 못하면 나머지 Phase 는 착수하지 않는다.** 5개 스파이크 전부 실제 배포된 Edge Function 위에서 검증한다 — 로컬 `supabase functions serve` 는 CPU 제한이 적용되지 않아 무의미하다.

예상 소요: **3~5일**

### Task 0.1: 스파이크용 Edge Function 골격 + CPU 계측 하네스

**Files:**
- Create: `supabase/functions/spike/index.ts`
- Create: `supabase/functions/spike/deno.json`

- [ ] **Step 1: Supabase CLI 로 함수 스캐폴드 생성**

```bash
cd /Users/kiwoongmin/Desktop/claude-project/Jet-Rag
supabase functions new spike
```

- [ ] **Step 2: CPU 계측 하네스 작성**

`supabase/functions/spike/index.ts`:

```ts
// CPU 시간 계측 하네스 — Edge Function 의 2s CPU 제한 대비 실측값 확보용.
// performance.now() 는 wall clock 이므로, async I/O 를 포함하지 않는 동기 블록만 감싼다.
Deno.serve(async (req: Request) => {
  const url = new URL(req.url);
  const kind = url.searchParams.get("kind") ?? "noop";

  const t0 = performance.now();
  let result: unknown = null;
  let error: string | null = null;

  try {
    switch (kind) {
      case "noop":
        result = { ok: true };
        break;
      default:
        error = `unknown kind: ${kind}`;
    }
  } catch (e) {
    error = e instanceof Error ? `${e.name}: ${e.message}` : String(e);
  }

  const elapsedMs = performance.now() - t0;
  return new Response(
    JSON.stringify({ kind, elapsedMs, error, result }, null, 2),
    { headers: { "content-type": "application/json" }, status: error ? 500 : 200 },
  );
});
```

- [ ] **Step 3: 배포**

```bash
supabase functions deploy spike --no-verify-jwt
```

Expected: `Deployed Function spike`

- [ ] **Step 4: 호출해서 하네스 동작 확인**

```bash
curl -s "https://<PROJECT_REF>.supabase.co/functions/v1/spike?kind=noop" \
  -H "Authorization: Bearer $SUPABASE_ANON_KEY"
```

Expected: `{"kind":"noop","elapsedMs":<1,"error":null,"result":{"ok":true}}`

- [ ] **Step 5: 커밋**

```bash
git add supabase/functions/spike
git commit -m "chore(spike): Edge Function CPU 계측 하네스 추가"
```

---

### Task 0.2: S1 — HWP/HWPX WASM 파서 검증 (최대 리스크)

**판정 기준:** 기존 `pyhwp` 출력과 텍스트가 **95% 이상 일치**하고, 1개 문서 파싱 CPU 가 **2s 미만**.

**Files:**
- Modify: `supabase/functions/spike/index.ts`
- Create: `api/scripts/spike_hwp_baseline.py`

- [ ] **Step 1: Python 기준선(baseline) 텍스트 추출**

`api/scripts/spike_hwp_baseline.py`:

```python
"""S1 스파이크 — 현행 pyhwp/hwpx 파서의 기준 텍스트를 JSON 으로 덤프.
WASM 파서 출력과 대조할 정답지를 만든다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

from app.adapters.impl.hwp_parser import HwpParser
from app.adapters.impl.hwpx_parser import HwpxParser

_SAMPLES = [
    ("law sample2.hwp", HwpParser),
    ("직제_규정(2024.4.30.개정).hwpx", HwpxParser),
    ("한마음생활체육관_운영_내규(2024.4.30.개정).hwpx", HwpxParser),
]


def main() -> None:
    repo_root = _API_ROOT.parent
    out: dict[str, str] = {}
    for name, parser_cls in _SAMPLES:
        path = repo_root / name
        if not path.exists():
            print(f"SKIP (없음): {name}", file=sys.stderr)
            continue
        # DocumentParser Protocol: parse(data: bytes, *, file_name: str) -> ExtractionResult
        # ExtractionResult.raw_text 가 전체 텍스트 (`.text` 아님 — app/adapters/parser.py 확인).
        text = parser_cls().parse(path.read_bytes(), file_name=name).raw_text
        out[name] = text
        print(f"{name}: {len(text)} chars", file=sys.stderr)
    (_API_ROOT / "scripts" / "spike_hwp_baseline.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 기준선 생성 — 실패하면 파서 API 시그니처를 코드에서 확인해 맞춘다**

```bash
cd api && uv run python scripts/spike_hwp_baseline.py
```

Expected: 각 샘플의 char 수가 stderr 로 출력되고 `scripts/spike_hwp_baseline.json` 생성.

- [ ] **Step 3: 후보 WASM 파서 2종을 스파이크 함수에 결선**

`supabase/functions/spike/index.ts` 의 `switch` 에 case 추가:

```ts
      case "hwp": {
        // 후보 A: @ohah/hwpjs (Rust 코어). 후보 B 는 kind=hwp-rhwp 로 별도 측정.
        const { parse } = await import("npm:@ohah/hwpjs");
        const bytes = new Uint8Array(await req.arrayBuffer());
        const t = performance.now();          // I/O 제외한 순수 파싱 구간만 계측
        const doc = parse(bytes);
        const cpuMs = performance.now() - t;
        const text = JSON.stringify(doc);
        result = { cpuMs, textLength: text.length, head: text.slice(0, 500) };
        break;
      }
      case "hwp-rhwp": {
        const rhwp = await import("npm:@rhwp/core");
        const bytes = new Uint8Array(await req.arrayBuffer());
        const t = performance.now();
        const doc = await rhwp.parse(bytes);
        const cpuMs = performance.now() - t;
        const text = JSON.stringify(doc);
        result = { cpuMs, textLength: text.length, head: text.slice(0, 500) };
        break;
      }
```

> 두 패키지의 실제 export 이름은 배포 전 `npm view @ohah/hwpjs` / README 로 확인해 맞춘다. import 자체가 Deno 에서 실패하는 것도 유효한 스파이크 결과다 — 그 경우 에러 전문을 기록하고 다음 후보로 넘어간다.

- [ ] **Step 4: 배포 후 실제 HWP 로 호출**

```bash
supabase functions deploy spike --no-verify-jwt
curl -s -X POST "https://<PROJECT_REF>.supabase.co/functions/v1/spike?kind=hwp" \
  -H "Authorization: Bearer $SUPABASE_ANON_KEY" \
  --data-binary "@law sample2.hwp"
```

Expected(성공): `cpuMs < 2000`, `textLength > 0`
Expected(실패): `error` 에 import 실패 또는 `WORKER_LIMIT` — 이 경우 판정은 **FAIL**

- [ ] **Step 5: 텍스트 일치율 측정**

```bash
cd api && uv run python -c "
import json, difflib, pathlib
base = json.loads(pathlib.Path('scripts/spike_hwp_baseline.json').read_text())['law sample2.hwp']
wasm = pathlib.Path('/tmp/wasm_hwp_out.txt').read_text()
print('일치율:', difflib.SequenceMatcher(None, base, wasm).ratio())
"
```

Expected: `일치율: 0.95` 이상이면 PASS

- [ ] **Step 6: 결과를 판정표에 기록하고 커밋**

```bash
git add api/scripts/spike_hwp_baseline.py supabase/functions/spike
git commit -m "spike(s1): HWP WASM 파서 Deno 실행 가능성 검증"
```

---

### Task 0.3: S2 — PDF 파서 검증 (두 번째 리스크)

**판정 기준:** 페이지 단위 텍스트 + **span 좌표** 추출이 가능하고, 1페이지 CPU 가 **2s 미만**, 100페이지 PDF 를 256MB 안에서 스트리밍 처리 가능.

> span 좌표는 선택 사항이 아니다 — `app/adapters/parser.py` 의 `ExtractedSection` 이 `bbox: tuple[float, float, float, float] | None` 을 1급 필드로 갖고 있고, `vision_need_score.py`(616 LOC)가 이 값으로 vision 호출 여부를 판정해 비용을 깎는다. bbox 를 못 얻으면 전 페이지 vision 호출로 회귀해 **운영 비용이 오른다.**

**Files:**
- Modify: `supabase/functions/spike/index.ts`

- [ ] **Step 1: 현행 파서가 실제로 요구하는 필드 확인**

```bash
cd api && grep -n "get_text\|\"blocks\"\|\"dict\"\|span\|bbox" app/adapters/impl/pymupdf_parser.py app/services/vision_need_score.py | head -30
```

이 출력이 WASM 후보가 반드시 제공해야 하는 API 목록이다. 목록을 이 Task 아래에 적어둔다.

- [ ] **Step 2: mupdf-wasm 후보를 스파이크에 결선**

```ts
      case "pdf": {
        const mupdf = await import("npm:mupdf");
        const bytes = new Uint8Array(await req.arrayBuffer());
        const t = performance.now();
        const doc = mupdf.Document.openDocument(bytes, "application/pdf");
        const pageCount = doc.countPages();
        // 1페이지만 처리 — 페이지 단위 팬아웃이 목표 아키텍처이므로 1p CPU 가 핵심 수치.
        const page = doc.loadPage(0);
        const structured = JSON.parse(page.toStructuredText("preserve-spans").asJSON());
        const cpuMs = performance.now() - t;
        result = {
          cpuMs,
          pageCount,
          hasBbox: JSON.stringify(structured).includes("bbox"),
          head: JSON.stringify(structured).slice(0, 500),
        };
        break;
      }
```

- [ ] **Step 3: 배포 후 대용량 PDF 로 호출**

```bash
supabase functions deploy spike --no-verify-jwt
curl -s -X POST "https://<PROJECT_REF>.supabase.co/functions/v1/spike?kind=pdf" \
  -H "Authorization: Bearer $SUPABASE_ANON_KEY" \
  --data-binary "@sonata-the-edge_catalog.pdf"
```

Expected: `cpuMs < 2000`, `hasBbox: true`

- [ ] **Step 4: 메모리 한계 확인 — 5MB PDF 를 통째로 올릴 때 OOM 나는지**

```bash
curl -s -X POST "https://<PROJECT_REF>.supabase.co/functions/v1/spike?kind=pdf" \
  -H "Authorization: Bearer $SUPABASE_ANON_KEY" \
  --data-binary "@sonata-the-edge_catalog.pdf" -w "\nHTTP %{http_code}\n"
```

Expected(성공): HTTP 200
Expected(실패): HTTP 546 또는 `WORKER_LIMIT` → **Storage 에서 range 요청으로 부분 읽기** 설계가 추가로 필요하다는 뜻. 기록만 하고 FAIL 로 판정하지는 않는다.

- [ ] **Step 5: 커밋**

```bash
git add supabase/functions/spike
git commit -m "spike(s2): PDF WASM 파서 span 좌표·CPU·메모리 검증"
```

---

### Task 0.4: S3 — Fernet 호환 복호화 검증 (데이터 손실 리스크)

`subscriptions.billing_key` 에 이미 저장된 카카오페이 SID 는 Python `cryptography.fernet` 으로 암호화돼 있다(`app/services/billing_crypto.py`). Deno 가 이걸 못 읽으면 **기존 구독자 전원의 정기결제가 끊긴다.**

**판정 기준:** Deno Web Crypto 로 기존 Fernet 토큰 복호화 성공.

**Files:**
- Modify: `supabase/functions/spike/index.ts`

- [ ] **Step 1: 테스트용 Fernet 토큰 생성 (운영 키·운영 SID 사용 금지)**

```bash
cd api && uv run python -c "
from cryptography.fernet import Fernet
key = Fernet.generate_key()
token = Fernet(key).encrypt(b'TEST_SID_1234567890')
print('KEY=', key.decode())
print('TOKEN=', token.decode())
"
```

- [ ] **Step 2: Deno 측 Fernet 복호화 구현**

Fernet 포맷 = `version(0x80) | timestamp(8B) | IV(16B) | ciphertext | HMAC-SHA256(32B)`, 키는 urlsafe-base64 32바이트를 앞 16B(서명키) + 뒤 16B(암호키)로 분할, 암호는 AES-128-CBC + PKCS7.

```ts
      case "fernet": {
        const { key, token } = await req.json();
        const t = performance.now();
        const raw = Uint8Array.from(
          atob(key.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
        const encKey = raw.slice(16, 32);              // 뒤 16B = AES-128 키
        const blob = Uint8Array.from(
          atob(token.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
        const iv = blob.slice(9, 25);                  // 1(version)+8(timestamp) 이후 16B
        const ct = blob.slice(25, blob.length - 32);   // 끝 32B 는 HMAC
        const ck = await crypto.subtle.importKey("raw", encKey, "AES-CBC", false, ["decrypt"]);
        const pt = await crypto.subtle.decrypt({ name: "AES-CBC", iv }, ck, ct);
        const cpuMs = performance.now() - t;
        result = { cpuMs, plaintext: new TextDecoder().decode(pt) };
        break;
      }
```

- [ ] **Step 3: 배포 후 호출**

```bash
supabase functions deploy spike --no-verify-jwt
curl -s -X POST "https://<PROJECT_REF>.supabase.co/functions/v1/spike?kind=fernet" \
  -H "Authorization: Bearer $SUPABASE_ANON_KEY" \
  -H "content-type: application/json" \
  -d '{"key":"<KEY>","token":"<TOKEN>"}'
```

Expected: `{"plaintext":"TEST_SID_1234567890"}`

> HMAC 검증은 스파이크 범위 밖이지만, **본 구현에서는 반드시 추가**한다(검증 없는 복호화는 위조 토큰을 수용함). Phase 4 Task 에 포함.

- [ ] **Step 4: 커밋**

```bash
git add supabase/functions/spike
git commit -m "spike(s3): Deno Web Crypto 로 기존 Fernet SID 복호 가능 확인"
```

---

### Task 0.5: S4 — DOCX/PPTX/이미지 파서 대안 확인

**판정 기준:** 텍스트 추출이 가능하면 PASS(레이아웃 충실도는 하위 우선순위).

- [ ] **Step 1: 후보를 스파이크에 결선** — DOCX 는 `npm:mammoth`, PPTX 는 `npm:pptx-parser` 계열, 이미지는 Gemini Vision 직접 호출(현행 `image_parser.py` 도 결국 Vision 을 태우므로 Pillow 없이도 가능한지 확인)

```ts
      case "docx": {
        const mammoth = await import("npm:mammoth");
        const buf = await req.arrayBuffer();
        const t = performance.now();
        const out = await mammoth.extractRawText({ arrayBuffer: buf });
        result = { cpuMs: performance.now() - t, textLength: out.value.length,
                   head: out.value.slice(0, 300) };
        break;
      }
```

- [ ] **Step 2: 배포 + 실제 파일로 호출**

```bash
supabase functions deploy spike --no-verify-jwt
curl -s -X POST "https://<PROJECT_REF>.supabase.co/functions/v1/spike?kind=docx" \
  -H "Authorization: Bearer $SUPABASE_ANON_KEY" --data-binary "@승인글 템플릿1.docx"
```

Expected: `textLength > 0`

- [ ] **Step 3: 커밋**

```bash
git add supabase/functions/spike
git commit -m "spike(s4): DOCX/PPTX/이미지 파서 Deno 대안 확인"
```

---

### Task 0.6: 스파이크 판정 회의 (사용자 승인 게이트)

- [ ] **Step 1: 판정표 작성**

`docs/superpowers/plans/2026-08-24-스파이크-판정표.md` 에 아래 표를 실측값으로 채운다.

| ID | 항목 | 판정 기준 | 실측 | P/F |
|---|---|---|---|---|
| S1 | HWP/HWPX WASM | 텍스트 95% 일치 & CPU<2s | | |
| S2 | PDF WASM | span 좌표 확보 & 1p CPU<2s | | |
| S3 | Fernet 복호 | 기존 토큰 복호 성공 | | |
| S4 | DOCX/PPTX | 텍스트 추출 성공 | | |
| S5 | 메모리 | 5MB PDF OOM 없음 | | |

- [ ] **Step 2: 중단 기준 적용**

- **S1 또는 S2 가 FAIL → 이 플랜 중단.** HWP 는 Jet-Rag 의 제품 차별점이고, PDF span 은 vision 비용 절감 로직(616 LOC)의 전제다. 둘 중 하나라도 못 넘기면 "기능을 그대로 사용"이라는 목표가 성립하지 않는다. → §6 대안으로 전환.
- **S3 FAIL → 완화 가능.** 이관 직전에 기존 `billing_key` 를 Deno 호환 스킴(AES-GCM)으로 **일괄 재암호화**하는 마이그레이션 Task 를 Phase 4 에 추가한다.
- **S4/S5 FAIL → 완화 가능.** S4 는 해당 포맷만 일시적으로 미지원 처리, S5 는 Storage range 요청 설계 추가.

- [ ] **Step 3: 사용자에게 판정표 제시하고 Phase 1 착수 승인 요청**

---

## 4. Phase 1~6 (Phase 0 통과 시)

각 Phase 는 **착수 시점에 별도 상세 플랜을 작성**한다. 지금 상세 Task 를 쓰면 스파이크 결과에 따라 대부분 폐기되므로 여기서는 범위·완료 기준·의존만 확정한다.

> **전 Phase 공통 완료 조건 — work-log 필수.**
> Phase 종료 시 `work-log/YYYY-MM-DD Phase{n} <제목>.md` 를 작성한다. 같은 날짜·주제 문서가 있으면 새로 만들지 말고 업데이트한다. 반드시 포함할 것:
> ① 무엇을 했는가(파일·커밋 단위) ② **실측 수치**(CPU ms, 테스트 통과 수, 응답 시간 — "잘 됨" 금지) ③ 내린 결정과 근거 ④ 막힌 것·미검증으로 남긴 것 ⑤ 다음 Phase 진입 조건.
> Phase 0 은 판정표(§Task 0.6)가 work-log 를 겸한다.

| Phase | 범위 | LOC | 완료 기준 | 예상 |
|---|---|---|---|---|
| **1. 기반** | `_shared/` 공통 모듈 + CF 프록시 + CI → **상세 플랜 작성 완료: `2026-08-24-phase1-기반-계층.md`** | 995 | 실 토큰으로 `/auth/me` 200 + `access_token` forward 보존 | 1주 |
| **2. 읽기 경로** | `search` / `answer` / `stats` / `me` / `admin` + 의존 services | ~9,000 | 골든셋 회귀 0 (`evals/` 재실행) | 3~4주 |
| **3. 인제스트** | pgmq 큐 + `ingest-worker` + 9-stage 재작성 + WASM 파서 결선 | ~5,600 | 골든 문서 전 포맷 인제스트 성공, 결과가 현행과 동등 | 4~6주 |
| **4. 결제** | `payments` + `billing` + Fernet(또는 재암호화) + pg_cron 배치 | ~900 | 샌드박스 구독→결제→해지 E2E | 1~2주 |
| **5. 이메일** | `email-webhook` + Cloudflare Worker 의 타깃 URL 교체 | ~370 | 실제 메일 1통 → 문서 생성 | 3일 |
| **6. 전환·철거** | 병행 운용 → 트래픽 전환 → Railway 삭제 | — | Railway 프로젝트 삭제, 2주 무사고 | 2주 |

**총 예상: 12~17주** (1인 기준, 테스트 재작성 포함)

### 목표 파일 구조 (Phase 별 산출물)

```
supabase/
├── functions/
│   ├── _shared/                      ← Phase 1
│   │   ├── config.ts                 app/config.py       (239 LOC)
│   │   ├── db.ts                     app/db/client.py    (HTTP/1.1 우회 삭제)
│   │   ├── auth.ts                   app/auth/*          (ES256+JWKS → jose)
│   │   ├── logging.ts                app/logging_setup.py
│   │   ├── errors.ts                 HTTPException → Response 매핑
│   │   └── cors.ts                   main.py 의 CORS 미들웨어
│   ├── api-search/     index.ts      ← Phase 2  routers/search.py (1,983)
│   ├── api-answer/     index.ts      ← Phase 2  routers/answer.py (1,000)
│   ├── api-account/    index.ts      ← Phase 2  me/auth/stats/admin (1,322)
│   ├── api-documents/  index.ts      ← Phase 3  routers/documents.py (1,376)
│   ├── ingest-worker/  index.ts      ← Phase 3  ingest/ 전체 (3,876)
│   ├── api-payments/   index.ts      ← Phase 4  routers/payments.py (135)
│   ├── billing-run/    index.ts      ← Phase 4  services/billing.py (365)
│   └── email-webhook/  index.ts      ← Phase 5  routers/email_ingest.py (136)
├── migrations/
│   ├── 026_pgmq_ingest_queue.sql     ← Phase 3  큐 + 드레인 cron
│   ├── 027_cron_billing.sql          ← Phase 4  정기결제 배치 스케줄
│   └── 028_cron_maintenance.sql      ← Phase 3  고아 job sweep + BGE-M3 warmup ping
└── config.toml

workers/
├── email-ingest/                     기존 (Phase 5 에서 타깃 URL 만 교체)
└── api-proxy/                        ← Phase 1  도메인·경로 보존 + 점진 전환
```

**공유 모듈 원칙:** `_shared/` 는 상태를 갖지 않는다. 현행 `@lru_cache` 싱글톤(`get_settings`, `get_supabase_client`, `get_bgem3_provider`)은 상주 프로세스 전제라 Edge Functions 에서는 **요청마다 새로 생성**되거나 모듈 스코프 상수여야 한다. 캐시가 필요한 것(`embed_query_cache`, `reranker_cache`, `vision_cache`)은 이미 DB 테이블 기반이므로(`015`/`016` 마이그레이션) 그대로 동작한다.

**인제스트 상태 머신 (Phase 3 핵심):**

| 큐 메시지 | 처리 단위 | CPU 예산 | 다음 메시지 |
|---|---|---|---|
| `extract_page` | 페이지 1장 | <2s | 마지막 페이지면 `chunk` |
| `chunk` | 문서 전체 텍스트 | <2s | `chunk_filter` |
| `chunk_filter` + `content_gate` | 청크 배치 50 | <2s | `tag_summarize` |
| `tag_summarize` | 청크 배치 (Gemini I/O) | I/O 위주 | `embed` |
| `embed` | 청크 배치 50 (HF I/O) | I/O 위주 | `doc_embed` |
| `doc_embed` → `dedup` | 문서 1건 | `dedup` 은 SQL 로 이관 | 완료 |

배치 크기 50 은 현행 `chunk_upsert_batch_size` 기본값을 그대로 승계한다(`config.py`). 각 단계는 `ingest_jobs.stage` 를 갱신해 기존 프론트 진행률 UI(`009`/`010` 마이그레이션 + Realtime)를 **변경 없이 재사용**한다.

### Phase 6 전환 절차 (되돌릴 수 있게)

Phase 1 에서 세운 Cloudflare 프록시가 전환 장치다 — **별도 도메인도, 프론트 플래그도 필요 없다.**

1. Phase 2~5 진행 중 각 Phase 완료 시점에 프록시 `ROUTES` 에 해당 경로를 한 줄 추가 → 그 경로만 Supabase 로 전환. 문제가 생기면 **그 한 줄을 지우면 즉시 롤백**된다
2. 전 경로 이관 후 2주 병행 운용 — `search_metrics_log` / `ingest_jobs` 지표를 이관 전 값과 대조
3. 지표 동등 확인 후 프록시의 `LEGACY_ORIGIN` 을 빈 문자열로 변경 → 기존 백엔드 트래픽 0
4. **1주 더 Railway 유지** (지표 이상 시 `LEGACY_ORIGIN` 복구만으로 롤백)
5. Railway 프로젝트 삭제 + Hobby 플랜 해지 → **관리 지점 4 → 3, 월 $5 절감**

---

## 5. 반드시 확인해야 할 결정 사항

- [x] ~~Supabase 플랜~~ — **Pro 확정(2026-08-24).** 백그라운드 400s 사용 가능, 신규 비용 0.
- [ ] **`answer` 스트리밍 상한** — LLM 응답이 초기 응답 150s 를 넘길 수 있는지 현행 로그로 확인. 넘으면 `EdgeRuntime.waitUntil` + 폴링 방식으로 전환(400s 확보).
- [ ] **`ragas_eval.py`(253 LOC)** — Deno 대안 없음. 평가 파이프라인은 로컬 실행으로 남기고 배포 대상에서 제외할지 결정.
- [ ] **pgroonga** — `004_pgroonga_korean_fts.sql` 이 Supabase 관리형에서 실제 활성 상태인지 확인(미지원이면 현행도 fallback 경로로 동작 중일 수 있음).

## 6. 기술적 폴백 — S1/S2 가 물리적으로 불가할 때만

**이관 방침은 확정됐다(2026-08-24 사용자 결정). 이 절은 방침 재고가 아니라, WASM 파서가 기술적으로 존재하지 않을 경우의 폴백이다.** S1/S2 가 FAIL 이면 "기능을 그대로 사용"이 성립하지 않으므로, 그때만 아래를 검토한다.

**폴백 우선순위 1 — 하이브리드:** 파싱만 컨테이너에 남기고 나머지 전부 Supabase 로 이관. 파싱 컨테이너는 큐 소비자 1개로 축소되므로 Railway 는 여전히 해지 가능하고, 컨테이너는 이미 쓰는 Cloudflare 로 옮겨 벤더 수를 3개로 유지한다.

**폴백 우선순위 2 — 전량 컨테이너 이전:**

| 대안 | Railway 제거 | 벤더 수 | 코드 재작성 | 월 비용 | 비고 |
|---|---|---|---|---|---|
| **Cloudflare Containers** | O | **3** (CF 이미 사용 중) | **0** — Dockerfile 그대로 | Workers Paid **$5** + 사용량 | 이메일 Worker 와 같은 벤더로 통합 |
| **Fly.io / Render** | O | 4 | 0 | 유사 | 벤더 수는 그대로 |
| **Google Cloud Run** | O | 4 | 0 | 요청 과금, 유휴 $0 | scale-to-zero |

폴백 채택 시에도 `api/Dockerfile` 을 그대로 쓴다 — `wrangler deploy` 가 로컬 Docker 로 이미지를 빌드한다. 인스턴스는 dev 256MiB / basic 1GiB / standard 4GiB + scale-to-zero 과금.

**폴백은 S1/S2 FAIL 시에만 발동한다.** PASS 면 본 플랜대로 Phase 1 로 직행한다.

---

## 7. 검증 안 된 가정 (실행 전 확인 필요)

1. `@ohah/hwpjs` / `@rhwp/core` 가 **Deno 런타임에서 동작**한다 — Node 전용 API 의존 시 실패. (Task 0.2 에서 확인)
2. `mupdf-wasm` 이 PyMuPDF 와 **동등한 span/bbox** 를 준다 — `vision_need_score` 이식 가능 여부가 여기 달림. (Task 0.3)
3. 번들에 WASM 을 포함해도 **서버측 5MB 제한**을 넘지 않는다. (Task 0.2/0.3 배포 시 확인)
4. 9-stage 를 페이지 단위로 쪼갰을 때 **각 단위가 2s CPU 안에 들어간다** — 청킹·정규화 로직이 예상보다 무거울 수 있음. (Phase 3 착수 시 재측정)
5. 예상 소요 12~17주는 **테스트 32,176 LOC 재작성을 포함한 값**이지만, 실측이 아닌 LOC 기반 추정이다.
