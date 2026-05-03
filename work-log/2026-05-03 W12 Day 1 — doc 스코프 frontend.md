# 2026-05-03 W12 Day 1 — doc 스코프 frontend (US-08 frontend 회수)

> W11 Day 4 backend (`/search?doc_id=X`) 자산을 frontend 에서 활용 완성.
> 한계 #67 회수.

---

## 0. 한 줄 요약

W12 Day 1 — doc 페이지 HeroSearch 가 `docId` 자동 주입으로 단일 문서 스코프 자연어 QA 라우팅. SearchSubheader 에 "이 문서 내 검색" 라벨 + 전체 검색 링크. 단위 테스트 223 ran (변경 0), tsc·lint 0 error. **US-08 완전 ship** (backend + frontend).

---

## 1. 비판적 재검토

### 1.1 HeroSearch 변경 vs 별도 input

| 옵션 | 설계 | 결정 |
|---|---|---|
| A | HeroSearch placeholder 변경 + doc_id 자동 주입 | ✅ 채택 — 단순, doc 페이지에서 검색은 doc 스코프가 자연 |
| B | HeroSearch 유지 + 새 doc-scope input 추가 | ❌ 화면 복잡도↑ |
| C | HeroSearch 에 토글 (전체 vs 이 문서) | ⚠ UX 복잡 |

→ A 채택. 사용자가 doc 페이지에서 검색 = "이 문서 내" 자연 의도.

### 1.2 doc 스코프 해제 UX

doc 스코프 검색 결과에서 "전체 검색으로 돌아가기" 가 필요. SearchSubheader 에 "이 문서 내 검색" Badge → 클릭 시 `/search?q=X` (doc_id 제외) 로 라우팅.

→ **시각적 명시 + 1-click 해제** — UX 친화적.

---

## 2. 구현

### 2.1 변경 파일 (4)

| 파일 | 변경 |
|---|---|
| `web/src/lib/api/index.ts` | `searchDocuments(q, limit, offset, docId?)` 시그니처 확장 — URLSearchParams 로 변경 |
| `web/src/app/search/page.tsx` | `searchParams.doc_id` 인식 + `searchDocuments(..., docId)` + `SearchSubheader docId` prop |
| `web/src/components/jet-rag/search-subheader.tsx` | `docId` prop + "이 문서 내 검색" Badge (FileText 아이콘) + 전체 검색 Link + handleSubmit / toggleDebug 에 doc_id 보존 |
| `web/src/app/doc/[id]/page.tsx` | `HeroSearch({ docId })` → `/search?q=X&doc_id=Y` 자동 주입 + placeholder "이 문서 내 자연어 검색" |

### 2.2 검색 흐름

```
/doc/[id]                              # 문서 상세
  ↓ HeroSearch (placeholder: "이 문서 내 자연어 검색")
  ↓ submit: q="질문"
/search?q=질문&doc_id=[id]
  ↓ search/page.tsx → searchDocuments(q, limit=10, offset=0, doc_id=id)
  ↓ 백엔드 응용 layer 필터 (W11 Day 4): rpc_rows 중 doc_id 일치만
  ↓ SearchSubheader 에 "📄 이 문서 내 검색" Badge 노출 (mobile + 데스크톱)
  ↓ Badge 클릭 → /search?q=질문 (doc_id 제외) → 전체 검색
```

### 2.3 doc_id 보존 정책

SearchSubheader 의 `handleSubmit` (검색어 변경) + `toggleDebug` (debug 토글) 에서 doc_id 보존:

```tsx
const next = new URLSearchParams();
next.set('q', trimmed);
if (debug) next.set('debug', '1');
if (docId) next.set('doc_id', docId);  // ← 보존
```

→ 사용자가 검색어 바꾸거나 debug 토글해도 doc 스코프 유지. 명시 해제는 Badge 클릭 only.

---

## 3. 검증

```bash
cd web && pnpm exec tsc --noEmit && pnpm lint
# 0 error

cd ../api && uv run python -m unittest discover tests
# Ran 223 tests in 5.156s — OK (회귀 0)
```

backend 변경 0 — frontend 만 W11 Day 4 자산 회수.

---

## 4. 누적 KPI (W12 Day 1 마감)

| KPI | W11 Day 5 | W12 Day 1 |
|---|---|---|
| 단위 테스트 | 223 ran | 223 ran (frontend 변경) |
| 한계 회수 누적 | 19 | **20** (+ #67) |
| **유저 스토리 완료** | 7/8 (US-08 backend) | **7/8 + US-08 frontend 완전** |
| frontend 검색 routing | 전역만 | + doc 스코프 |
| 마지막 commit | 40d4ea3 | (Day 1 commit 예정) |

---

## 5. 알려진 한계 (Day 1 신규)

| # | 한계 | 회수 시점 |
|---|---|---|
| 68 | doc 스코프에서 결과 0 시 "이 문서에 일치 없음 → 전체 검색?" 자동 fallback UX 미도입 | 사용자 피드백 후 |
| 69 | doc 페이지 우측 상단 글로벌 검색 (별도 nav) 미존재 — HeroSearch 가 doc 스코프로 변경되어 전역 검색 진입점 약화 | nav menu 별도 검색 검토 |

---

## 6. 다음 작업 — W12 Day 2 (자동 진입)

| 우선 | 항목 | 사유 |
|---|---|---|
| 1 | **인제스트 SLO 달성률 KPI 집계** | KPI 1개 측정 (Ragas 없이) ~1.5h |
| 2 | **하이브리드 +5pp ablation 자동 비교** | KPI 1개 측정 ~2h |
| 3 | **OpenAI 어댑터 스왑 시연** | DoD ④ ~3h |
| 4 | **US-07 vision structured action items** | US 1건 회수 |
| 5 | **monitor CI yaml + 가이드** | 운영 인프라 |
| 6 | **doc 스코프 fallback UX (한계 #68)** | 사용자 피드백 후 |

**Day 2 자동 진입**: 인제스트 SLO 달성률 KPI 집계 — 메인 스레드 가능 KPI 회수 가성비↑.

---

## 7. 한 문장 요약

W12 Day 1 — doc 페이지 HeroSearch 의 doc_id 자동 주입 + SearchSubheader "이 문서 내 검색" 라벨 ship. US-08 backend (W11 Day 4) + frontend 완전 ship. 단위 테스트 223 ran 회귀 0, tsc·lint 0. 한계 #67 회수.
