# Jet-Rag 수익화 Sprint 디자인 (2026-07-05)

## 배경

- production live (jetrag.woong-s.com), 현재 **read-only 데모 모드** — 쓰기 전부 503 (`forbid_demo_writes`)
- 멀티유저 인프라(D1 Auth + D2 RLS + E4)는 ship 완료, 주석/토글로 비활성 상태
- 베타 피드백(갤럭시 유저): **파일 업로드 자체가 귀찮다** — 자동 수집 + 검색만으로 깔끔한 결과를 원함
- 결제/과금 코드 전무, Gemini Free tier(RPD 20) 의존, 노출 API 키 회전 TODO 미완

## 목표·성공 기준

- **북극성**: sprint 종료 시 카카오페이 정기결제 **유료 유저 1명 이상** (첫 정산 확인)
- **모델**: B2C 월 구독 — Free(문서 10개 · 답변 일 5회) / **Pro 월 6,900원**(문서 200개 · 답변 일 50회 · 이메일 인제스트). 숫자는 가안 — 오픈 이슈 #1 에서 확정
- **기간**: 6~8주 / 운영비 상한 **월 $50**
- **전제**: 사업자등록 진행 중 (사용자 확인, 2026-07-05)

## 타임라인 (A안 — 결제 경로 우선 + 심사 리드타임 선행)

| 주차 | 트랙 A (제품) | 트랙 B (서류·운영) |
|---|---|---|
| **W1** | 노출 API 키 전량 회전 → 멀티유저 쓰기 모드 복원(auth ON · RLS 복귀 · 프론트 주석 해제) → production smoke | 사업자등록 완료 확인 → **카카오페이 온라인 가맹 + 정기결제(빌링) 심사 신청** 즉시 |
| **W2** | Gemini 유료 키(pay-as-you-go) 전환 + per-user rate limit → 베타 유저 재초대(업로드 개방) | 이메일 수신 도메인 준비 (`in.woong-s.com` MX) |
| **W3–4** | plans/subscriptions 테이블 + 사용량 미터링(Free/Pro enforcement) → **이메일 인제스트** | 가맹 심사 대응 |
| **W5–6** | **카카오페이 정기결제 연동**(빌링키 → 월 자동결제 → grace period) + 구독 관리 UI | 이용약관 · 개인정보처리방침 |
| **W7–8** | 품질: 환각률 9.2%→≤5% + 인제스트 SLO 재측정 → **베타 30명 유료 전환 캠페인** | 첫 결제 검증 + 정산 확인 |

**크리티컬 패스**: 카카오페이 심사(W1 신청 → 통상 2~4주) → W5 연동. 지연/반려 시 **수동 결제 fallback**(계좌이체 확인 후 admin 이 `subscriptions` 수동 upsert)으로 북극성 목표는 지킨다.

## 기술 설계

### 1. 플랜·사용량 미터링 (W3–4)

**DB (마이그 021~022)**
- `plans` — code(free/pro), 한도 정의(문서 수 · 일일 답변 · vision 페이지)
- `subscriptions` — user_id, plan_code, status(active/past_due/canceled), current_period_end, billing_key(암호화)
- `usage_counters` — user_id, metric(answers/docs/vision_pages), period_yyyymm, count. UPSERT 증가, RLS 본인 SELECT only. 기존 `vision_usage_log`(마이그 005) 패턴 재사용

**Enforcement**
- FastAPI dependency `check_quota(metric)` — 기존 `require_authorized_user`(E4) 뒤에 체인. 한도 초과 시 **402** + 한국어 업그레이드 안내
- 적용 지점: `POST /documents`(문서 수), `POST /answer`(일일 답변), Vision stage(기존 vision_budget cap 과 통합)

### 2. 이메일 인제스트 (W3–4) — 베타 피드백 반영

- **수신 경로**: Cloudflare Email Routing → Worker → 백엔드 webhook ($0, 도메인 기보유·MX 설정만). 대안: Resend/SendGrid inbound parse
- **주소 체계**: `u-{8자리 랜덤 토큰}@in.woong-s.com` — `email_ingest_addresses` 테이블(마이그 023)로 user_id 매핑, 토큰 재발급 가능(스팸 대응)
- **흐름**: webhook → 발신자 검증(가입 이메일 화이트리스트) → 첨부 추출(PDF/HWP/HWPX/DOCX/이미지) → **기존 `POST /documents` 9-stage 파이프라인 그대로 재사용** (magic bytes · SHA-256 dedup · 50MB cap 기존 게이트 통과)
- **플랜 게이트**: Pro 전용. Free 유저 발신 시 안내 메일 회신
- **후속 sprint 이관**: Google Drive 폴더 동기화, Android 네이티브 폴더 감시(카카오톡 파일) — 이번 범위 아님

### 3. 카카오페이 정기결제 (W5–6)

- **구조**: 프론트 결제창 → 빌링키(SID) 발급 → `payments` 라우터가 SID 암호화 저장 → 월 배치(`api/scripts/billing_charge.py`, cron)가 `current_period_end` 도래 건 자동결제
- **상태 머신**: `active → (결제 실패) past_due (7일 grace) → canceled` — canceled 는 Free 강등, 데이터 보존·읽기 가능
- **어댑터 패턴 유지**: `PaymentProvider` Protocol + `KakaoPayImpl` (`api/app/adapters/`) — 기존 5 Protocol 설계와 동일, 토스/Stripe swap 경로 확보
- **테스트**: KakaoPay sandbox(CID `TCSUBSCRIP`) e2e + 기존 unittest mock 패턴으로 단위 테스트 추가

### 4. 품질 게이트 (W7–8)

- 환각률: answer 프롬프트 강화("출처 없으면 모른다고 답변") + Faithfulness 저점 답변에 기존 신뢰도 배지 강조. `golden_v2` 182 row 로 회귀 측정
- 인제스트 SLO: Gemini 유료 키 + DeepInfra 조합으로 재측정 (48.3% 는 HF cold-start 원인 — 90% 기대)

## 리스크·대응

| 리스크 | 확률 | 대응 |
|---|---|---|
| 카카오페이 정기결제 심사 지연/반려 | 중 | W1 신청으로 버퍼. 반려 시 수동 결제 fallback → 차기 sprint 토스페이먼츠 전환 (Protocol swap) |
| 공개 가입 상태에서 악성/과다 업로드 | 중 | W2 per-user rate limit + Free 한도 자체 방어. 50MB cap · magic bytes 게이트 기존 존재 |
| Gemini 유료 전환 후 비용 폭주 | 저 | usage_counters 계량 + Google Cloud budget alert ($50) + Vision cap 유지 |
| 베타 30명 중 전환 0명 | 중 | 이메일 인제스트 Pro 2주 무료 체험 → 락인 후 전환. 실패 시 사유 인터뷰가 차기 입력 |
| Supabase Free tier 한계(500MB/1GB) | 저 | 유료 유저 발생 시점에 Pro($25/월) 승급 — 예산 내 |

## 측정

- **북극성**: 유료 구독 1명+ (카카오페이 첫 정산)
- 보조 KPI: 쓰기 복원 후 WAU / 이메일 인제스트 사용 유저 수 / Free→Pro 전환율 / 환각률 ≤5% / 인제스트 SLO ≥90%
- 기존 `search_metrics_log` · `monitor-search-slo` cron 활용, admin 대시보드에 구독·사용량 카드 추가

## 오픈 이슈

1. **가격 확정** — 6,900원 가안. W5 전 베타 유저 2~3명 지불의사 인터뷰로 확정
2. **비로그인 데모 유지** — 쓰기 복원 후에도 12-doc read-only 데모를 온보딩 퍼널로 유지 (추천: 유지)
3. **이메일 스팸 정책** — 발신자 화이트리스트로 시작, 필요 시 확장

## 결정 이력

- 2026-07-05: 성공 기준 = 유료 유저 1명+ / B2C 월 구독 / 자동 수집은 이메일 인제스트만 / 결제 = 카카오페이 / 6~8주 · 월 $50 (사용자 확정)
- 접근: A안(결제 경로 우선 + 심사 리드타임 선행) 채택 — B안(품질 우선) · C안(베타 성장 우선)은 심사 지연 리스크로 기각
