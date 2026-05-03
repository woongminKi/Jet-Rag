# 2026-05-03 W9 Day 7 — class-based quota 감지 보강 (한계 #50 회수)

> Day 4·6 의 메시지 휴리스틱 fast-fail 위에 class name + status code 직접 검사 추가.
> SDK 응답 형식 변경 시 회귀 보호 — 3 단계 감지 매트릭스.

---

## 0. 한 줄 요약

W9 Day 7 — `is_quota_exhausted` 가 Exception 객체 직접 수용 + 3 단계 감지 (class name → status code → 메시지 fallback). 단위 테스트 **203 → 210** ran (+7 quota 시나리오), 회귀 0. caller (pptx_parser, tag_summarize) 두 곳 변경.

---

## 1. 진입 배경

W9 Day 4·6 의 `is_quota_exhausted` 는 *문자열* 만 수용:
- `"RESOURCE_EXHAUSTED"` / `"429"` / `"QUOTA"` 키워드 검사
- Gemini SDK 메시지 형식 변경 시 false negative 가능 (한계 #50)

핵심 발견: `_gemini_common.with_retry` 가 마지막 실패 시 **원본 exception 그대로 raise** → SDK 의 `ResourceExhausted` 객체가 그대로 caller 에 전달됨. class-based catch 가능.

---

## 2. 비판적 재검토

| 옵션 | 설계 | 결정 |
|---|---|---|
| A | google.api_core / google.genai 직접 import 후 isinstance 검사 | ⚠ SDK 패키지 변경 시 import 회귀 + 의존성 강결합 |
| **B** | type name (`type(exc).__name__`) + status_code attribute 검사 | ✅ 채택 — import 0, robust |
| C | 메시지 휴리스틱만 유지 | ❌ 한계 #50 미회수 |

→ B 채택. import 의존성 0, SDK 변경 시 회귀 risk 0. 메시지는 fallback.

### 2.1 3 단계 감지 매트릭스

```python
def is_quota_exhausted(error_or_msg) -> bool:
    if isinstance(error_or_msg, BaseException):
        # 1) class name 화이트리스트
        if type(error_or_msg).__name__ in {"ResourceExhausted", "TooManyRequests"}:
            return True
        # 2) HTTP-style status code attribute
        for attr in ("status_code", "code"):
            if getattr(error_or_msg, attr, None) == 429:
                return True
        msg = str(error_or_msg)
    else:
        msg = error_or_msg
    # 3) 메시지 fallback
    if not msg: return False
    upper = msg.upper()
    return "RESOURCE_EXHAUSTED" in upper or "429" in msg or "QUOTA" in upper
```

| 단계 | 감지 대상 | 비고 |
|---|---|---|
| 1 | google.api_core.exceptions.ResourceExhausted | gRPC 표준 |
| 1 | TooManyRequests | HTTP 표준 |
| 2 | google.genai.errors.ClientError(code=429) 등 | HTTP wrapper |
| 3 | 메시지 키워드 | Day 4·6 기존 동작 보존 |

### 2.2 caller 변경

```python
# 이전 (Day 4·6)
if is_quota_exhausted(str(exc)):
# 이후 (Day 7)
if is_quota_exhausted(exc):  # ← exc 객체 자체 전달 → 정확 검사
```

`__str__` 만 보고 판단하던 것을 type + attribute 까지 본다. 메시지 변경 영향 X.

---

## 3. 구현

| 파일 | 변경 |
|---|---|
| `app/services/quota.py` | `is_quota_exhausted(error_or_msg)` 시그니처 확장 — Exception/str 모두 수용 |
| `app/adapters/impl/pptx_parser.py` | `is_quota_exhausted(exc)` 직접 전달 |
| `app/ingest/stages/tag_summarize.py` | 동일 |
| `tests/test_quota.py` | **신규 7 시나리오** — class name / status code / 메시지 fallback |

### 3.1 단위 테스트 (7 신규)

| 시나리오 | 검증 |
|---|---|
| `test_resource_exhausted_class_name` | type(exc).__name__ == "ResourceExhausted" → True |
| `test_too_many_requests_class_name` | TooManyRequests → True |
| `test_unknown_class_falls_through_to_message` | CustomError + 메시지 키워드 X → False (3단계 fallthrough 검증) |
| `test_status_code_429_attribute` | exc.status_code == 429 → True |
| `test_code_429_attribute` | exc.code == 429 → True |
| `test_message_string_input` | 5 메시지 케이스 (RESOURCE_EXHAUSTED / 429 / quota / 일반 fail / 빈 문자열) |
| `test_exception_with_message_fallback` | class 미매칭 + attribute 없음 + 메시지 키워드 → True |

---

## 4. 검증

```bash
uv run python -m unittest tests.test_quota
# Ran 7 tests — OK

uv run python -m unittest discover tests
# Ran 210 tests in 4.573s — OK (203 → 210, 회귀 0)
```

PptxParser fast-fail (Day 4 18 시나리오) + tag_summarize fast-fail (Day 6 4 시나리오) 모두 PASS — exc 객체 전달 변경 회귀 0.

---

## 5. 누적 KPI (W9 Day 7 마감)

| KPI | W9 Day 6 | W9 Day 7 |
|---|---|---|
| 단위 테스트 | 203 ran | **210 ran** (+7) |
| 한계 회수 누적 | 10 | **11** (+ #50) |
| **quota 감지 정확도** | 메시지 휴리스틱만 | **class + code + 메시지 (3 단계)** |
| 마지막 commit | 585ec58 | (Day 7 commit 예정) |

---

## 6. 알려진 한계 (Day 7 신규)

| # | 한계 | 회수 시점 |
|---|---|---|
| 56 | google SDK 가 새로운 quota class name 도입 시 화이트리스트 갱신 필요 | SDK upgrade 시 |
| 57 | nested exception (cause) 미감지 — 직접 raise 만 검사 | from … 패턴 도입 시 보강 |

---

## 7. 다음 작업 — W9 Day 8+ 후보

| 우선 | 항목 | 사유 |
|---|---|---|
| 1 | **augment 본 검증** (한계 #48) | quota 회복 후 — 시간 의존 |
| 2 | **search debug mode mobile fallback badge** (한계 #33) | 사용자 가시성 |
| 3 | **mobile 가독성** (한계 #40) | 사용자 피드백 |
| 4 | **CI 첫 실행 결과** (한계 #44) | gh CLI 인증 |
| 5 | **VisionUsageCard 한계 #38** | API quota header |

**추천: search debug mode mobile fallback badge (~15분)** — Day 7 마감 후 작은 sprint.

---

## 8. 한 문장 요약

W9 Day 7 — class-based quota 감지 보강. is_quota_exhausted 가 Exception 객체 수용 + 3 단계 (class name → status code → 메시지 fallback). 단위 테스트 203 → 210 ran 회귀 0. 한계 #50 회수.
