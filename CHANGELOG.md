# Changelog

Jet-Rag MVP — W1 (2026-04-22) ~ W31 멀티유저 sprint (2026-05-21 D1+D2+E4 ship) 누적 변경 분류.

semver 적용 전 (v0.1 MVP 단계). 본 문서는 W 단위 / sprint 단위 진행 + Conventional Commits prefix 분류.

> **상세 컨텍스트**: 매 W 종합 핸드오프 + sprint work-log (`work-log/`)
> **commit 누적**: **563** (W1~W31 멀티유저 sprint)
> **단위 테스트**: **1336+ PASS** (W31 D1+D2+E4 누적, baseline flaky 7 동일)
> **마이그레이션**: **20개** (001~020 — 017 invite_codes / 018 데이터 이관 / 019 RLS / 020 Storage prefix 추가)
> **production live**: 2026-05-18 (배포 D 안 = Railway $5 + Vercel + DeepInfra) → 2026-05-19 도메인 부착 (`woong-s.com`) → **2026-05-21 D1+D2 멀티유저 활성화 (본인 단독)**

---

## [Unreleased] — v0.1 MVP + W31 멀티유저 sprint

### W31 멀티유저 sprint (2026-05-20 ~ 2026-05-21) — D1+D2 ship + E4 fix

- `feat(auth-d2-followup)` invite redeem 게이트 보강 (E4 fix) (`378b8db`)
  - senior-qa multi-user E2E audit 에서 발견: backend 4 라우터(`/documents`, `/search`, `/answer`, `/stats`) router-level dependency 가 `require_auth` 만 → redeem 안 한 random user 도 통과 → 베타 30 cap 강제 실패 risk
  - `api/app/auth/dependencies.py` 에 `require_authorized_user` 추가 (60 lines): `invite_codes` `used_by=auth.uid()` SELECT 1회/request (캐시 없음, 1 row per user 가벼움). auth_enabled=false 통과(MVP 보존), DB 조회 실패 시 503 graceful (017 미적용 대응)
  - 4 라우터 router-level dep 교체, 단위 테스트 +8 PASS, 전체 회귀 0
  - Railway auto-deploy `a23b629a` SUCCESS, production smoke 401·200 회귀 0, 본인 inbox 12 doc 표시 정상
- `docs(readme)` 다이어그램 3종(인제스트/검색/어댑터) + 데모 GIF 가이드 (`e5640c6`)
  - mermaid 인제스트 9-stage + Vision rerouting / 검색 파이프라인 (intent_router + Hybrid RRF + 가드 + MMR) / 어댑터 5 Protocol + DeepInfra↔HF swap path
  - `docs/demo/README.md` 137줄 — Kap / QuickTime + gifski / ffmpeg / gifsicle 도구 비교, 시나리오 3종, 임베드 형식
- `docs(work-log)` 2026-05-21 D1 Phase 4 + D2 Phase 5 ship 완료 종합 + API 키 회전 TODO (`96ac048`)
  - **D1 Phase 4 데이터 이관** — admin REST API PATCH 3건 (`documents` 12 / `answer_feedback` 1 / `answer_ragas_evals` 4 = **17 row** legacy→owner UPDATE), Railway GraphQL `variableUpsert` `OWNER_USER_ID` 등록 (자동 redeploy `e89f34d0` SUCCESS), smoke /search 401·/health 200·본인 inbox 12 doc
  - **D2 Phase 5** — 019 RLS apply (사용자 SQL Editor) + 020 PART 1 storage_path PATCH 12 row (PostgREST) + `migrate_storage_to_per_user.py` default 모드 12 객체 native move (~6초, errors=0) + 020 PART 2 Storage RLS 4 정책 apply
  - **API 키 회전 TODO** 별도 문서 — 본 conversation 노출 Supabase service_role + Railway account token 회전 절차 6단계 체크리스트 + production 무중단 보장 + redact 안내
- `docs(work-log)` 2026-05-21 D1 ship Phase 1~3 + JWKS ES256 보강 sprint 종합 (`1a46a5f`)
- `feat(auth-d1-jwks)` ES256/RS256 + JWKS 분기 — Supabase 비대칭 signing key 대응 (`2822ca5`)
  - Phase 1a-4 에서 Supabase JWT signing key 가 이미 ECC(P-256) migration 됨을 발견 — HS256 only 백엔드로는 검증 불가
  - `api/app/auth/jwt_verify.py` 에 `_ASYMMETRIC_ALGORITHMS = {ES256, ES384, ES512, RS256, RS384, RS512}` + `_resolve_signing_key(token, settings)` 알고리즘 화이트리스트 분기, `PyJWKClient` `@lru_cache` 싱글톤
  - `api/app/config.py` `supabase_jwks_url` 필드 + `pyproject.toml` `pyjwt[crypto]` extra
  - 단위 테스트 35 → **40 PASS** (+5 ES256 정상 / JWKS URL 미설정 / PyJWKClientError / OSError / 만료)
  - Railway ENV `SUPABASE_JWT_ALGORITHM=ES256` + `SUPABASE_JWKS_URL` 추가로 우회
- `docs(work-log)` D1+D2 ship 단계별 가이드 runbook (Phase 1-5) (`fb1006e`)
- `docs(work-log)` D2 세션 종합 — D1 push 후 D2 sprint 완수 + 9단계 deploy 가이드 (`f0f4fce`)
- `feat(rls-d2)` RLS 정책 7테이블 + Storage per-user prefix + stats 누출 차단 + Realtime JWT (`31f1e9a`)
  - 마이그 019 — 7 테이블 25 RLS 정책 (`documents`/`chunks` EXISTS join/`ingest_jobs`/`ingest_logs` 2-hop/`answer_feedback`/`answer_ragas_evals`/`invite_codes` SELECT only)
  - 마이그 020 — `documents` 버킷 4 Storage RLS 정책 (`foldername[1]='user' AND [2]=auth.uid()::text`) + `storage_path` `user/<uid>/` prefix UPDATE + `migrate_storage_to_per_user.py` script (default move / `--copy-only` / `--cleanup-only`)
  - RPC `get_chunks_stats_for_user(UUID)` SECURITY DEFINER + auth.uid() 가드 + GRANT EXECUTE TO service_role only (senior-qa P1#2 누출 차단)
  - Realtime JWT — frontend `realtimeSetAuth(client)` 호출 (W31 D2 senior-qa P1#3)
- `feat(auth-d1)` 멀티유저 Auth 인프라 (Phase A+B + P1#1 IDOR 차단) (`da5c640`)
  - Supabase Auth client (cookie session) + JWT verify HS256 (당시) + `require_auth` / `require_admin` dependency + 마이그 017 `invite_codes`
  - `/auth/me` / `/auth/redeem-invite` + 4 라우터 router-level `dependencies=[Depends(require_auth)]`
  - P1#1 IDOR 차단 — `documents` single-doc 엔드포인트(`:815, 905, 1266, 1337`) user_id 가드 추가

### Deploy + 도메인 sprint (2026-05-18 ~ 2026-05-19) — production live

- `docs(work-log)` 2026-05-19 도메인 부착 — woong-s.com production ship (`c2c4e26`)
  - Cloudflare Registrar `woong-s.com` 구매 (~$10/년)
  - Vercel custom domain `jetrag.woong-s.com` (CNAME + 자동 SSL)
  - Railway custom domain `jetrag-api.woong-s.com` (CNAME + TXT verify + 자동 SSL)
  - **코드 변경 0** — CORS env (`JETRAG_CORS_ORIGINS`) + Vercel `NEXT_PUBLIC_API_BASE_URL` 만 갱신
  - production smoke PASS — CORS preflight + cross-domain 검색 200 OK
- `docs(work-log)` §17.2 / §17.3 정정 — production 만 쓸 땐 .env 불필요 명시 (`d3ca8e0`)
- `docs(work-log)` 2026-05-19 §17 다른 컴퓨터 진입 가이드 + §15.1 / §15.6 상태 갱신 (`8dda813`)
- `docs(work-log)` 2026-05-19 §16 W-7 canary 완료 + DECISION-13 배포 D 안 production 도달 (`6361894`)
- `chore(vercel)` Jet-Rag 재연결 후 첫 webhook trigger (`117a98d`)
- `chore(vercel)` reconnect 후 webhook 재발화 — 빈 commit trigger (`1a8e062`)
- `docs(work-log)` 2026-05-18 §15 다른 컴퓨터 핸드오프 가이드 + v0.3 (`fb9df98`)
- `feat(deploy+v1.5)` Railway Dockerfile + DeepInfra W-0 결정성 시험 PASS + Vercel CORS env (`e57c3ec`)
  - `api/Dockerfile` + `.dockerignore` (single-stage uv build, image 1.28GB)
  - DeepInfra 결정성 시험 n=100, min cosine **0.999984** ≥ 0.999 PASS → 캐시 entry 공유 안전
  - CORS env 화 (`JETRAG_CORS_ORIGINS` + Vercel preview regex)
  - **DECISION-13** — 배포 D 안 채택 (Railway $5 + Vercel + DeepInfra)

### v1.5 sprint (2026-05-19) — DeepInfra BGE-M3 어댑터 swap

- `feat(v1.5 W-2)` DeepInfra production swap PASS + DECISION-14 + P2 테스트 3건 보강 (`155653b`)
  - Railway production `JETRAG_EMBED_PROVIDER=deepinfra` 등록 + redeploy
  - **P95 1.705s** (게이트 2.5s 32% 여유, 60 warm 호출)
  - **R@10 회귀 0.0000** (115/115 row top-5 ordering 100% 일치, in-process toggle 비교)
  - 단위 테스트 **1219 → 1229** (+10 P2 보강: `_parse_retry_after` / 영구 캐시 hit / batch retry)
  - **DECISION-14** — HF Inference Providers 의존 제거 (cold-start ~29.7s 위험 해소)
- `feat(v1.5 W-1)` DeepInfra BGE-M3 어댑터 swap (ENV 토글, 호출 사이트 8건 무수정) (`4396913`)
  - `api/app/adapters/impl/bgem3_deepinfra_embedding.py` 신규 (OpenAI-compatible API, 326 line)
  - `api/app/adapters/impl/bgem3_hf_embedding.py` factory `get_bgem3_provider()` ENV 분기
  - `JETRAG_EMBED_PROVIDER` 토글: `hf` (default) / `deepinfra`
  - 호출 사이트 8건 (main / answer / search / doc_embed / embed / ragas_eval) **무수정** — Protocol 직교성 실증
  - 단위 테스트 13건 신규 + factory 4 분기 검증 (default→HF, deepinfra, 대소문자, unknown graceful fallback)

### W30 (2026-05-18) — 배포 검토 + 권고 후속

- `docs(work-log)` 2026-05-18 배포 방법 검토 — Railway + HuggingFace
  - 7 옵션 비교 (Railway / HF Spaces / HF Inference Endpoint / Vercel / Fly.io / Render / 자가 호스팅)
  - 권장 = 3-tier (Railway $5 + Vercel $0 + DeepInfra <$1) = **~$5~6/월**
  - 의사결정 7건 + 멀티유저 추가 작업 D1~D7 설계

### W29 (2026-05-15) — audit 권고 후속 + DECISION-12 보너스

- `docs(work-log)` 2026-05-15 추천 4건 + DECISION-12 + senior-qa 검증 + spec v0.5/v1.5 (`ed11039`)
- `fix(pymupdf)` audit 권고 5 — 영어 학술 heading 패턴 + page-header 블랙리스트 (1202 OK) (`062b130`)
  - arXiv-style page header + 페이지 번호 차단 (`_PAGE_HEADER_BLACKLIST`)
  - 영어 numbered (`1. Introduction`) + standalone (`Abstract`/`References`) heading 인식
  - 단위 테스트 1194 → 1202 (+8)
- `docs(work-log)` 2026-05-15 권고 4 cross_doc T1 warm 재진단 — ROI 낮음 확정 (한국어 12/12 발화)
- `docs(work-log)` 2026-05-15 over-chunking 검색 품질 영향 측정 — 회귀 신호 0 (R@10 0.6829)
- `docs(work-log)` 2026-05-15 HF self-host 검토 — v1.5 sprint 설계 (DeepInfra 권장)
- `docs(work-log)` 2026-05-15 DECISION-12 인제스트 KPI 인프라 측정
  - KPI #1 100% (8/8) / #2 100% (11/11) / #3 100% (201/201, $1.72 누적)
  - 측정 인프라 100% 가동, 벤치 30개 충족만 별도 sprint

### W28 (2026-05-15) — 데이터 정리 + audit + robustness

- `docs(work-log)` 2026-05-15 세션 종합 — 데이터 정리 + audit + robustness + 새 도메인 진단 핸드오프 (`a8e3379`)
- `docs(work-log)` 인제스트 robustness fix — NULL byte + batch split (1194 OK) (`1ce5465`)
- `fix(ingest)` generic robustness — NULL byte sanitize + chunks upsert batch split (`b70b672`)
- `docs(work-log)` audit 권고 1 — query_classifier 역의존 해소 (1171 OK) (`ab7b6de`)
- `refactor(query_classifier)` production → evals 역의존 해소 (audit 권고 1) (`3605dbb`)

### W27 (2026-05-14) — M3 자동 측정 마감

- `revert(evals)` KPI #8 자동 채점 작업 되돌림 (사용자 지시) (`22f7412`)
- `docs(work-log)` KPI #8 1차 자동 채점 (20/50) + P4-b 잔여 정리 (132 row) (`8455ba2`)
- `feat(evals)` KPI #8 출처 일치율 1차 자동 채점 표 + P4-b cross_doc 잔여 정리 (`8d7292e`)
- `docs(work-log)` P4 hard-delete 4 doc + golden_v2 47 row 정리 (135 row) (`c231f76`)
- `chore(repo)` 위생 — golden_batch_smoke API_BASE env override + bak.20260513 untrack (`8cd8be7`)
- `fix(evals)` P4 hard-deleted 4 doc 참조 row 47건 제거 (golden_v2 182→135) (`95ed745`)
- `docs(work-log)` 2026-05-14 세션 종합 — M3 자동 측정 마감 + v1.4 핸드오프 (`7140d71`)
- `docs(living)` v0.4 — W-15 2차 갱신 (M1+M2+M3 + 잡일 P1 반영) (`f1fd50c`)
- `docs(prd)` v1.4 — M3 자동 측정 마감, KPI 7/8 측정 완료 + DECISION-12 명시화 (`f61ac84`)
- `feat(evals)` Acceptable judge 2차 + 본 세션 최종 정산 (`0c5d3e0`) — DECISION-11
- `docs(work-log)` RAGAS 본격 측정 — KPI #4·#5·#9 마감 (`e072eeb`)
- `feat(evals)` KPI #10·#11·#6① 측정 — free 3 KPI 마감 + ingest_slo_report 신규 (`150b3a5`)
- `docs(prd)` v1.3 — M1+M2 완료·M3 부분 진입·KPI #7 실측 (DECISION-13 신규) (`6dc361c`)
  - **DECISION-13** — KPI #7 hybrid 우세 정의를 단순 R@10 +5pp 에서 qtype-aware top-1/R@10 로 재정의

### W26 (2026-05-13) — M2 완료 → noise floor 도달

- `docs(work-log)` 2026-05-13 종합 — M1+M2 완료 + M3 진입 핸드오프
- `feat(eval)` M2 W-4 후속 surgical 실험 → noise floor 도달 확인 (`a7c3a92`)
  - top-1 noise floor ±0.012 정량화, M3 직행 권고
- `feat(eval)` M2 W-4 전체 클린 재인제스트 (13 doc, W-2+W-3 동시 ON) (`beb83b4`)
  - **top-1 0.7910 → 0.7966** (게이트 0.80 의 -0.0034pp = noise band 안, **사실상 달성**)
  - **table_lookup top-1 0.5 → 0.92** (대폭 향상)
  - **caption_dependent gap +0.28 → +0.012** (W-3 효과 96% 압축 결정적 실증)
  - cross_doc R@10 +0.032
  - vision_budget 1차 시도 cap 누적 버그 복구 (P1)
- `feat(ingest)` M2 W-3 caption prefix only (DECISION-8) (`6588053`)
  - `[표 p.N: cap]\n\n{base}` chunk prefix prepend (vision-derived chunk 한정, ENV gated)
  - 단위 테스트 1102 → 1124 (+22)
- `feat(search)` W-1(b) 옵션 A search.py-only 후보 doc-scoped 필터 (`190fbe0`)
  - cross_doc R@10 0.4424 → 0.4080 (net-neg) → ENV default OFF 코드 보존
- `feat(ingest)` W-2 S4-D 동의어 후보 사전 인제스트 (`8aea68d`)
  - 정적 사전 36 entry · chunk 끝 `[검색어:...]` 마커 · ENV gated · 마이그 0
- `feat(eval)` W-1(a) paid LLM decomposition `/search` 노출 + paid eval 실측 (`2db7a79`)
  - cross_doc R@10 0.4424 → 0.4216 (net-neg, P2 함정 실증) → ENV default OFF
- `feat(search)` W-1(a) paid LLM query decomposition 구현 (`0ecc6a3`)
  - `multi_query_search.py` · `rrf_merge_pools` · `_decompose_with_timeout` · `SearchMeta`
- `feat(eval)` W-1 게이트 검증 (dry-run $0) (`dce92f1`)
  - 발화 20/182 = 11%, 비용 ~$0.001/eval

### W25 (2026-05-12) — DB 정합성 감사 + M0 완료

- `docs(work-log)` 2026-05-12 검색 정확도 80% 달성 PRD (sign-off → v1.5 갱신)
- `docs(work-log)` 2026-05-12 종합 마감 + 2026-05-13 진입 핸드오프
- `docs(work-log)` 2026-05-12 M0-a 측정 신뢰성 정비 + W-6 가드 (`ddef019`) / W-12 / W-14 / W-15 (`cda61dc`)
- `feat(evals)` M0-b golden 라벨 재검수 — broken row 정정 (G-U-018 재작성 · G-U-027 제거 · G-U-005/007 라벨 보강), 183 → 182 row
- `feat(evals)` M0-c baseline 재측정 + 게이트 재계산 (embed-cache 검증 churn 0) (`76c607a`)
- `feat(migrations)` 016 embed_query_cache — 영구 캐시 (model_id 기반 entry 공유)
- `feat(api)` HF embed query cold-start 완화 + app 로거 부트스트랩
- `feat(eval)` S4-B 본PC 재검증 + entity_boost ablation (OFF 확정)
- `docs(work-log)` DB chunk 정합성 감사 — sample-report dense_vec NULL 발견 + P0/P1 cross_doc

### W24 (2026-05-11) — Phase 9 회복 + S3 D6 RRF-only 확정

- `docs(work-log)` S3 D6 reranker 재측정 → RRF-only 확정 (net-negative on 회복 DB)
- `feat(eval)` Phase 11 acceptable judge 도입 (1차)
- `feat(eval)` Phase 10 multimodal judge (`fba1316`)
- `feat(eval)` Phase 9 reingest 회복 (sample-report 제외 3 doc) (`6a15d3c`)

### W23 (2026-05-04 ~ 2026-05-10) — Phase 1 S0~S2 sprint + LLMAdapter factory

- `feat(adapters)` LLMAdapter factory.py 도입 + vision_page_cache 마이그 015 (`9501b32`)
- `feat(adapters)` factory 모델 매핑 master plan §4 정합 + 단가 테이블 + Vision purpose 활성화 (`59d3982`)
- `refactor(routers)` search/answer LLM 호출을 factory 경유로 전환 (D2-A) (`9313939`)
- `refactor(ingest)` tag_summarize LLM 호출을 factory 경유로 전환 (D2-A) (`7ccf4c5`)
- `refactor(hyde)` LLMProvider 일반화 + cache key 에 model_id 포함 (D2-A) (`bd1c1ca`)
- `refactor(ragas)` `JETRAG_LLM_PROVIDER` 분기 추가 (D2-A) (`eee81b4`)
- `feat(vision)` `VisionCaption.usage` 필드 + Gemini usage_metadata 파싱 (`2f4183e`)
- `feat(metrics)` `vision_usage_log` 컬럼 확장 + record_call usage 인자 추가 (`bd17fb4`)
- `feat(ingest)` S0 D4 vision 비용 cap 가드 + UI 시각 보강 (`f90fdc8`)
- `feat(budget)` S0 D3 vision_usage_log 기반 budget 초기값 계산 (`318bbda`)
- `feat(vision)` Phase1 S0 D2 vision_page_cache lookup/upsert 통합 (`62aafcf`)
- `feat(eta)` E1 1차 ship — sub-stage 분해 + sample<3 None + TTL 90s (`9259241`)
- `feat(admin)` S1 D3/D4 query 로그 + feedback 통합 분석 (`7311ae2` / `9bf0cec`)
- `feat(evals)` S1 D5 golden_v1 baseline 측정 + 모델 회귀 권고 (`74770da`)
- `feat(evals)` S1 D2 golden_v0.7_auto + v1 통합 + fallback chain (`7dbc2b3`)
- `feat(evals)` S1 D1 auto_goldenset.py v2 + 12 컬럼 통합 schema (`896223c`)
- `feat(test)` E2 1~5차 fixture + negative path + assets 자동 진입 (`9eb9587`~`55d3de9`)
- `feat(adapters)` factory pricing/master plan 정합 + Vision purpose 활성화 회귀 보호 12건 (`2ce5679`)
- `fix(adapters)` D2-D 정정 — 2.0 deprecated 모델 회복 + .gitignore 보강 (`310b20a`)
- `feat(adapters)` Gemini retry default 3→1 + ENV JETRAG_GEMINI_RETRY 신규 (`c717ea8`)
- `feat(ingest)` vision_enrich sweep default 3→2 + 회복용 ENV override (`2fa8c3c`)
- `feat(api)` /ask 답변 품질 가시화 — 신뢰도 배지 + 출처 highlight + Ragas 점수 + 사용자 피드백 (W25 D14~)
- `feat(answer)` /answer 엔드포인트 LLM RAG PoC ship + RAGAS Faithfulness/AnswerRelevancy + 캐시 + UI
- `feat(migrations)` 009 realtime_ingest_jobs · 010 stage_progress · 011 answer_feedback · 012 answer_ragas_evals · 014 vision_usage_log_enhanced
- `feat(ingest)` /documents/{id}/incremental_reingest — DB 보존 + 누락 vision 페이지만 처리
- `feat(search)` doc-level embedding RRF 가산 + reranker 진단 (`eaef851`)
- `feat(search)` D1~D5 검색 성능 향상 sprint — reranker fix + 4 후보 측정 (`e417b45`)

---

## W22 (2026-05-03~04) — 운영 진입 마찰 감소
- `docs(readme)` README.md W21 진척 + 기술 스택 + 운영 env 표 갱신
- `docs(env)` .env.example W15~W18 운영 env 4개 추가 (PERSIST_ENABLED/ASYNC, ERROR_MSG_MAX_LEN, QUERY_TEXT_HASH)
- `feat(scripts)` monitor_search_slo by_mode 분리 측정 + 영속화 baseline 갱신
- `test(vision_metrics)` record_call truncate 동적 적용 검증 (+2)

## W21 (2026-05-03) — golden 회귀 자동화 + edge case
- `feat(scripts)` golden_batch_smoke `--mode all` ablation + `--require-top1-min` CI gate
- `test(search)` mode='dense' + embed transient edge case 단위 테스트
- `docs(migrations)` W15+ 일괄 적용 가이드 + 운영 env 표
- `docs(scripts)` api/scripts/README.md 신규 (6개 스크립트 entry-point)

## W20 (2026-05-03) — 진정 ablation 측정 (한계 #74 회수)
- `feat(migrations)` 008 search_dense_only + search_sparse_only RPC (schema 100% 일관)
- `feat(api)` search.py mode 별 RPC 분기 + 응용 필터 skip + graceful fallback
- `feat(web)` MetricsTrendCard fetch error 재시도 버튼 (retryToken 패턴)
- `feat(web)` SearchSloCard by_mode p50 비교 bar 시각화 (CSS bar)

## W19 (2026-05-03) — race 방지 + cap pre-allocate
- `feat(web)` mode 토글 useTransition race 방지 (한계 #79 회수)
- `feat(api)` mode=dense/sparse 시 RPC top_k 2배 (한계 #75 회수)
- `feat(api)` doc_id 필터 시 RPC top_k 4배 (한계 #66 회수)
- `fix(web)` ablation mode 토글 mobile 가독성 폰트 (W14 #78 follow-up)

## W18 (2026-05-03) — interactivity + 안정성
- `feat(web)` MetricsTrendCard mode 토글 (search 카드 ablation 비교)
- `feat(api)` query_text SHA256 hash 화 사전 wiring (한계 #87 회수, DE-21 사전)
- `feat(api)` _persist_executor graceful shutdown atexit hook
- `test(e2e)` ImageParser class-level patch → 인스턴스 patch (한계 #65 회수)

## W17 (2026-05-03) — first-warn + 비동기 persist
- `feat(web)` MetricsTrendCard 'use client' + range 토글 interactivity
- `feat(web)` vision metric 시계열 카드 추가 (RPD 20 cap 가시성)
- `feat(api)` vision_metrics + search_metrics first-warn 패턴 (한계 #85 회수)
- `feat(api)` metrics persist ThreadPoolExecutor 비동기 fire-and-forget (한계 #88 회수, /search latency 보호)

## W16 (2026-05-03) — 추세 시각화 + 한계 회수 묶음
- `feat(migrations)` 007 추세 분석 RPC 2개 (search/vision 시계열 + epoch floor + zero-fill)
- `feat(api)` /stats/trend endpoint + 단위 테스트 (graceful migrations_pending)
- `feat(web)` MetricsTrendCard SVG sparkline (의존성 0)
- `feat(api+web)` 한계 #40·#84·#90 동시 회수 (mobile 가독성 / error_msg env / source_type enum)

## W15 (2026-05-03) — DB 영속화 ship
- `feat(migrations)` 005 vision_usage_log + 006 search_metrics_log SQL
- `feat(api)` vision_metrics + search_metrics DB write-through (graceful + env gate)
- `test(api)` 스캔 PDF max 페이지 cap e2e (한계 #64 회수)

## W14 (2026-05-03) — frontend ablation + monitor CI
- `feat(web)` ablation mode 토글 (hybrid/dense/sparse) frontend
- `feat(api)` search_metrics by_mode 분리 측정 (한계 #77 회수)
- `feat(ci)` monitor-search-slo.yml workflow + JET_RAG_API_BASE env (한계 #44 회수)

## W13 (2026-05-03) — 유저 스토리 8/8 완성
- `feat(api)` 화이트보드 action_items (US-07) — structured.action_items 추출
- `feat(api)` /search?mode= ablation 인프라 (KPI '하이브리드 +5pp 우세' 측정 가능)
- `feat(web)` SearchSloCard 신규

## W12 (2026-05-03) — US-08 frontend + KPI 노출
- `feat(web)` doc 페이지 검색 doc_id 자동 주입 (US-08 frontend, 한계 #67 회수)
- `feat(api)` /stats.ingest_slo_aggregate KPI 노출

## W11 (2026-05-03) — 자율 v2 + DoD 매트릭스
- `feat(api)` last_quota_exhausted_at 추가 (W11 Day 1, 한계 #38 lite 회수)
- `test(api)` extract HWPML/OLE2/스캔 PDF e2e (한계 #58·#59 회수)
- `feat(api)` /search?doc_id=X US-08 backend (한계 #67 backend ship)

## W10 (2026-05-03) — 자율 진행 v1 + e2e 완성
- `feat(api)` extract e2e + stats router e2e (e2e 8/9 → 9/9)
- `feat(web)` debug UI 가독성 + nested metadata pretty (한계 #16·#17 회수)
- `feat(api)` JSONB path 시뮬 (한계 #20 회수)

## W9 (2026-05-03) — quota 보호 강화
- `fix(api)` PPTX cap 정책 버그 fix (한계 #47 회수)
- `feat(api)` Vision fast-fail + tag_summarize fast-fail (한계 #49·#53 회수)
- `feat(api)` class-based quota 감지 (한계 #50 회수)
- `fix(web)` fallback badge mobile (한계 #33 회수)

## W8 (2026-05-03) — PPTX 추가 + Vision OCR
- `feat(api)` DE-68 PPTX parser ship (python-pptx)
- `feat(api)` PPTX Vision OCR rerouting (텍스트 0 슬라이드, 한계 #23 회수)
- `feat(api)` dedup Tier 2/3 e2e + tag_summarize e2e (한계 #26·#32 회수)
- `feat(api)` vision_metrics 모듈 + VisionUsageCard (한계 #29·#37 회수)

## W7 (2026-05-03) — 가시성 + e2e base
- `feat(api+web)` rrf_score + chunk metadata 노출 (W7 Day 1)
- `feat(web)` 홈 ChunksStatsCard (W7 Day 3)
- `feat(web)` 검색 debug mode (W7 Day 4)
- `test(api)` e2e 4 stage mock + content_gate (W7 Day 5·6)

## W6 (2026-05-02) — 청크 마킹 + golden v0.3
- `feat(api)` DE-65 chunks 555→1256 + extreme_short 회수
- `feat(api)` golden 평가셋 v0.3 (top-1 100%)

## W5 (2026-04-30 ~ 2026-05-01) — DOCX
- `feat(api)` DE-67 DocxParser ship + 청킹 4.3·4.6
- `feat(api)` 지원 doc_type 6종 (pdf·hwp·hwpx·docx·image·url)

## W4 (2026-04-29 ~ 2026-05-02) — 검색 품질
- `feat(api)` embed_query LRU cache (maxsize=512) — p95 < 200ms (cache hit)
- `feat(api)` PDF heading 휴리스틱 (99.84% section_title 채움)
- `feat(api)` chunk.py W4-Q-14 (4건 청킹 정책 변경)

## W3 (2026-04-29) — 검색 ship
- `feat(migrations)` 003 hybrid_search.sql (HNSW + simple FTS + RRF k=60)
- `feat(migrations)` 004 PGroonga 한국어 FTS 교체 (Mecab 형태소 분석)
- `feat(api)` /search endpoint + 하이브리드 RRF
- `feat(api)` chunk_filter stage (DE-62)
- `feat(web)` 홈 dashboard + 검색 페이지 ship

## W2 (2026-04-24 ~ 2026-04-28) — 인제스트 멀티포맷
- `feat(api)` 9 stage 인제스트 파이프라인 (extract→chunk→...→dedup)
- `feat(api)` 5종 파서 (pdf / hwpx / image / url / hwp) 디스패처
- `feat(api)` content_gate (PII / 워터마크 / 제3자 대화 감지)
- `feat(migrations)` 002 received_ms (SLO 측정)
- `feat(api)` Vision OCR (Gemini 2.5 Flash 내장)

## W1 (2026-04-22 ~ 2026-04-23) — 스캐폴드
- `feat(api)` FastAPI 스캐폴드 + 9 stage 파이프라인 뼈대
- `feat(web)` Next.js 16 + Tailwind v4 + shadcn/ui (new-york, neutral)
- `feat(migrations)` 001 init.sql (documents·chunks·ingest_jobs·ingest_logs + pgvector + RLS)

---

## DECISION 사인오프 (v0.1 MVP + v1.5)

| # | 결정 | 시점 | 상태 |
|---:|---|---|---|
| **1** | "검색 정확도 80%" = top-1 hit rate ≥ 0.80 | 2026-05-12 | ✅ 사실상 달성 (M2 W-4 top-1 0.7966, noise band 안) |
| **2~11** | PRD 권고안 전부 승인 | 2026-05-12 ~ 14 | ✅ 적용 완료 |
| **12** | 인제스트 KPI 별도 트랙 | 2026-05-14 | ✅ 인프라 측정 완료 (2026-05-15) |
| **13** | KPI #7 hybrid 우세 = qtype-aware 재정의 | 2026-05-14 | ✅ table top-1 +0.34 / cross_doc R@10 +0.045 실증 |
| **13 (배포)** | 배포 D 안 (Railway + Vercel + DeepInfra) | 2026-05-18 | ✅ production live |
| **14** | v1.5 W-2 DeepInfra production swap | 2026-05-19 | ✅ P95 1.705s / R@10 회귀 0 |

---

## 한계 회수 누적 (43건 + α)

### W7~W14 (24건)
#15·#16·#17·#19·#20·#23·#26·#28·#29·#32·#33·#37·#38 lite·#44·#47·#49·#50·#53·#58·#59·#61·#67·#68·#77·#78·#83

### W15 (6건)
#34 vision_usage 휘발 / #61 search_metrics public reset / #62 ditto / #64 스캔 PDF cap e2e / #76 search_slo 휘발 / #81 monitor artifact 30일 한계

### W16 (3건)
#40 mobile 가독성 / #84 error_msg truncate env 변수화 / #90 vision_usage source_type enum 강제

### W17 (2건)
#85 first-warn 패턴 / #88 metrics persist 비동기 (검색 latency 보호)

### W18 (2건)
#65 ImageParser class-level patch → 인스턴스 patch / #87 query_text SHA256 hash 사전 wiring

### W19 (3건)
#66 doc_id 응용 layer 필터 부족 방지 / #75 ablation top_k 2배 / #79 mode 토글 useTransition race 방지

### W20 (1건)
#74 mode 별 RPC 분리 (008 split RPC + search.py 분기) — 진정 ablation 측정

### 명시 회복 (2건)
#67 doc 페이지 검색 input (W12 회수) / #69 헤더 글로벌 검색 (W12 HeaderSearch 이미 존재)

### W26~v1.5 (PRD M0~M3 + v1.5)
- M0-b broken row 정정 (G-U-018 · G-U-027 · G-U-005/007)
- M2 W-3 caption_dependent gap +0.28 → +0.012 (96% 압축)
- M2 W-4 vision_budget cap 누적 버그 (P1) — `count_uncached_pages` 헬퍼로 cache hit 시 사전 check 우회 (`57d87e4`)
- v1.5 W-2 P2 단위 테스트 3건 보강 (`_parse_retry_after` / 영구 캐시 hit / batch retry)
- audit 권고 1 (query_classifier 역의존) / 권고 5 (arXiv heading)

---

## 활성 한계 (W22 마감 시점 + 잔존)

| # | 한계 | 차단 |
|---|---|---|
| 38 본격 | Gemini SDK quota header | SDK 변경 시점 |
| 46 | frontend e2e (playwright) | 의존성 정책 검토 |
| 48 | augment 본 검증 | quota 회복 |
| 56·57 | google SDK 새 quota class / nested cause | SDK upgrade 시 |
| 70~73 | SLO 5 버킷 외 doc_type / sample N<30 / action_items 검증 | 자료 누적 |
| 80·82 | secrets 미설정 / actionlint | 사용자 액션 |
| 86 | graceful (W15 Day 3 사실상 처리) | 명시 회복 |
| 89 | tests/__init__.py env "0" → conftest 통일 | pytest 도입 시 |

---

## 운영 정책 누적 (W3~v1.5)

핵심 카테고리:
1. 자율 진행 v2 (W11 도입, W19 정책 갱신)
2. **어댑터 레이어 분리** (Cloud→Local 전환 경로 + v1.5 W-1 DeepInfra swap 으로 실증)
3. graceful + env gate 패턴 (모든 외부 의존성)
4. 외부 의존성 0 정책 (visualization·검증 인프라)
5. Server initial / Client refetch + race 방지 패턴
6. **결정성 시험** — 외부 provider swap 전 cosine ≥ 0.999 사전 검증 (v1.5 W-0 패턴)
7. **CORS env 화 + custom domain** — Vercel/Railway 양쪽 default + custom 동시 active, 도메인 부착 시 코드 변경 0
