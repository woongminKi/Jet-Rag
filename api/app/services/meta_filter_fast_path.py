"""S3 D2 — 메타 필터 fast path detector + executor (planner v0.1 Part C).

목적
----
사용자 query 가 **순수 메타 필터** (날짜·태그·doc명 단독) 만 요구할 때
임베딩·reranker·RPC RRF 호출을 0 으로 만들고 documents 테이블 SELECT 한 번으로
결과를 반환. 검색 latency·HF API 호출비용 절감 + 명확한 의도일수록 빠른 응답.

설계 원칙
---------
- 외부 API 호출 0 — 룰 detector 는 정규식 + 키워드 매칭, 실행은 Supabase SELECT 1회.
- 의존성 추가 0 — 표준 라이브러리만 사용.
- 회귀 가드 — doc명 단독 룰은 **명시적 doc-suffix** (`문서`, `보고서`, `자료`,
  `회의록`, `기획서`, `파일`, `리포트`, `요약`) 가 있을 때만 fire. 명사 단독 query
  ("결론", "시트", "소나타 시트 종류") 는 RAG path 로 fallback — 기존 단위 테스트
  회귀 0. doc-suffix 발화 query 는 RAG 로도 매칭되지만 fast path 가 의미적
  매칭보다 정확 (제목 ILIKE) 하고 latency 0.
- 의문/서술 동사구 잔존 시 None — `is_meta_only` 가 fast path 부정.

매칭 우선순위
-------------
``date_range`` (날짜 표현) > ``tags`` (#태그) > ``title_ilike`` (doc-suffix 명사구).
한 query 가 여러 종류 동시 매칭 시 모두 plan 에 포함 (executor 가 AND 결합).

planner 명세 §C — 8 케이스 중 1~4 fast path / 5~6 None:
| # | query | 결과 |
|---|---|---|
| 1 | `#투자` | tag-only fast path |
| 2 | `어제 받은 문서` | date (어제) + suffix(문서) fast path |
| 3 | `2025년 3월 회의록` | date(월) + suffix(회의록) fast path |
| 4 | `프로젝트 X 기획서` | suffix(기획서) fast path |
| 5 | `왜 이 펀드가 손실났나` | None (T3 인과 → RAG) |
| 6 | `#투자 수익률 어떻게 계산` | None (의문 동사구 잔존 → RAG) |

회귀 영향
--------
- 의존성 0, 마이그 0, 외부 API 0.
- 본 모듈 import 는 `/search` `/answer` 가 D2 ship 시점부터.
- `is_meta_only` 가 None 반환 시 호출자는 기존 RAG path 그대로 진행 — 호환성 보존.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

# 모듈 레벨 import — 단위 테스트에서 `patch.object(meta_filter_fast_path, "get_supabase_client", ...)`
# 로 mock 가능. lazy import 시 patch 가 source module 에 묶여 본 모듈에 미적용.
from app.db import get_supabase_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# doc-suffix 화이트리스트 — doc명 단독 룰의 false-positive 방지 가드
# ---------------------------------------------------------------------------
# 본 suffix 가 query 말미 또는 토큰에 등장해야 doc명 단독 fast path 진입.
# planner 명세 §C 케이스 2~4 에서 등장하는 자연어 패턴 + 운영 흔한 어휘.
_DOC_SUFFIXES: tuple[str, ...] = (
    "문서",
    "보고서",
    "자료",
    "회의록",
    "기획서",
    "파일",
    "리포트",
    "요약",
    "공문",
    "계약서",
)

# ---------------------------------------------------------------------------
# 의문/서술 동사구 — 잔존 시 fast path 무효 (RAG 로 fallback)
# ---------------------------------------------------------------------------
# planner 명세 §C 케이스 6 "#투자 수익률 어떻게 계산" → "어떻게" 잔존 → None.
_QUESTION_VERBS: tuple[str, ...] = (
    "어떻게",
    "왜",
    "언제",
    "어디",
    "누가",
    "뭐야",
    "뭐",
    "무엇",
    "얼마",
    "몇",
    "어떤",
    "어느",
    "할까",
    "있나",
    "없나",
    "되나",
    "인가",
)

# ---------------------------------------------------------------------------
# 명령형/요청 stopword (planner 명세 §C 그대로) — 잔존 토큰이 본 set 만 남으면 meta-only
# ---------------------------------------------------------------------------
_STOPWORD_VERBS: tuple[str, ...] = (
    "보여줘",
    "보여",
    "찾아줘",
    "찾아",
    "열어줘",
    "열어",
    "줘",
    "주세요",
    "알려줘",
    "알려",
)
# 단음절 조사 (어절 끝 strip 용)
_PARTICLES: tuple[str, ...] = ("을", "를", "의", "에", "는", "가", "이", "도", "만", "와", "과", "랑")

# ---------------------------------------------------------------------------
# 날짜 정규식 — 절대 날짜 + 월 단위
# ---------------------------------------------------------------------------
_RE_ABS_YMD = re.compile(r"(?P<y>\d{4})[-./](?P<m>\d{1,2})[-./](?P<d>\d{1,2})")
_RE_KO_YMD = re.compile(r"(?P<y>\d{4})\s*년\s*(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일")
_RE_KO_YM = re.compile(r"(?P<y>\d{4})\s*년\s*(?P<m>\d{1,2})\s*월")

# 상대 날짜 키워드 → 일수 offset / 범위 종류
# offset: 오늘 기준 시작일까지 음수 일수 (예: 어제 = -1).
# span_days: 범위 길이 (예: 어제 = 1일, 지난주 = 7일).
_RELATIVE_DATES: dict[str, tuple[int, int]] = {
    # key: (start_offset_days, span_days)
    "오늘": (0, 1),
    "어제": (-1, 1),
    "그저께": (-2, 1),
    "이번주": (-6, 7),  # 단순화: 오늘 포함 직전 7일
    "지난주": (-13, 7),
    "이번달": (-29, 30),
    "지난달": (-59, 30),
}

# ---------------------------------------------------------------------------
# 태그 정규식 — `#한글영문숫자_-` 1글자 이상
# ---------------------------------------------------------------------------
_RE_TAG = re.compile(r"#([A-Za-z0-9가-힣_\-]+)")

# 토큰 분리 — 공백 기준 (한글 자연어 query 단순 케이스만 처리)
_RE_WHITESPACE = re.compile(r"\s+")

# 결과 SELECT cap — fast path 는 빠른 응답이 본질, 페이지네이션은 본 path 미적용.
_FAST_PATH_LIMIT = 20


@dataclass(frozen=True)
class MetaFilterPlan:
    """메타 필터 fast path 실행 계획.

    Attributes
    ----------
    date_range:
        ``(from_dt, to_dt)`` UTC tz-aware. 날짜 매칭 없으면 None.
    tags:
        매칭된 태그 리스트 (소문자 정규화 X — DB tags 가 사용자 표기 그대로 저장됐음).
    title_ilike:
        제목 ILIKE 매칭 키워드 (한 query 에 1개만 — 가장 길고 명확한 명사구).
    matched_kind:
        ``"date" | "tag" | "title" | "date+title" | "tag+title" | "date+tag"`` 등
        디버그·로그·응답 meta 노출용 식별자.
    """

    date_range: tuple[datetime, datetime] | None = None
    tags: tuple[str, ...] = ()
    title_ilike: str | None = None
    matched_kind: str = ""
    # detector 에서 파싱한 잔여 토큰 (디버그). frozen dataclass 라 default factory 사용.
    residual_tokens: tuple[str, ...] = field(default_factory=tuple)


def _normalize(query: str) -> str:
    """NFC + 양 끝/내부 공백 단일화."""
    nfc = unicodedata.normalize("NFC", query.strip())
    return _RE_WHITESPACE.sub(" ", nfc)


def _strip_particle(token: str) -> str:
    """어절 끝 1자 조사 strip — `보고서를 → 보고서`."""
    if len(token) >= 2 and token[-1] in _PARTICLES:
        return token[:-1]
    return token


def _extract_tags(text: str) -> tuple[str, ...]:
    """`#태그` 추출. 중복 제거 + 등장 순서 보존."""
    seen: list[str] = []
    for m in _RE_TAG.finditer(text):
        tag = m.group(1)
        if tag not in seen:
            seen.append(tag)
    return tuple(seen)


def _extract_date_range(
    text: str, *, today: date | None = None
) -> tuple[datetime, datetime] | None:
    """절대 날짜 / 한국어 날짜 / 월 / 상대 날짜 키워드 → ``(from, to)`` UTC.

    Parameters
    ----------
    today:
        상대 날짜 (어제·오늘 등) 기준 일자. 단위 테스트 결정성 위해 주입 가능.
        프로덕션은 ``date.today()``.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    # 1) 절대 YYYY-MM-DD / YYYY.M.D
    m = _RE_ABS_YMD.search(text)
    if m:
        return _ymd_to_range(int(m.group("y")), int(m.group("m")), int(m.group("d")))

    # 2) 한국어 YYYY년 M월 D일
    m = _RE_KO_YMD.search(text)
    if m:
        return _ymd_to_range(int(m.group("y")), int(m.group("m")), int(m.group("d")))

    # 3) 한국어 YYYY년 M월 (월 단위)
    m = _RE_KO_YM.search(text)
    if m:
        return _ym_to_range(int(m.group("y")), int(m.group("m")))

    # 4) 상대 키워드
    for kw, (start_offset, span_days) in _RELATIVE_DATES.items():
        if kw in text:
            start = today + timedelta(days=start_offset)
            end = start + timedelta(days=span_days)
            return (
                datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
                datetime(end.year, end.month, end.day, tzinfo=timezone.utc),
            )

    return None


def _ymd_to_range(y: int, m: int, d: int) -> tuple[datetime, datetime] | None:
    try:
        start = datetime(y, m, d, tzinfo=timezone.utc)
    except ValueError:
        return None
    return (start, start + timedelta(days=1))


def _ym_to_range(y: int, m: int) -> tuple[datetime, datetime] | None:
    try:
        start = datetime(y, m, 1, tzinfo=timezone.utc)
    except ValueError:
        return None
    # 다음 달 1일 — 12월은 다음해 1월
    if m == 12:
        end = datetime(y + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(y, m + 1, 1, tzinfo=timezone.utc)
    return (start, end)


def _strip_date_expressions(text: str) -> str:
    """날짜 표현을 plain text 에서 제거 → 잔여 토큰 분석용."""
    out = _RE_ABS_YMD.sub(" ", text)
    out = _RE_KO_YMD.sub(" ", out)
    out = _RE_KO_YM.sub(" ", out)
    for kw in _RELATIVE_DATES:
        out = out.replace(kw, " ")
    return _RE_WHITESPACE.sub(" ", out).strip()


def _strip_tags(text: str) -> str:
    return _RE_TAG.sub(" ", text).strip()


def _has_question_verb(tokens: tuple[str, ...]) -> bool:
    """의문/서술 동사구 발화 시 fast path 무효."""
    for tok in tokens:
        for verb in _QUESTION_VERBS:
            if verb in tok:
                return True
    return False


def _residual_only_stopwords(tokens: tuple[str, ...]) -> bool:
    """잔여 토큰이 명령형 stopword 만 — 메타 의도 명확."""
    for tok in tokens:
        cleaned = _strip_particle(tok)
        if cleaned not in _STOPWORD_VERBS:
            return False
    return True


def _extract_title_ilike(
    residual_text: str, *, has_date_or_tag: bool
) -> str | None:
    """잔여 텍스트에서 제목 ILIKE 키워드 추출.

    조건
    ----
    - 토큰 수 ≤ 5 (planner 명세 §C)
    - 의문/서술 동사구 0
    - 토큰 중 하나가 doc-suffix (문서/보고서/자료 ...) 또는
      ``has_date_or_tag=True`` (날짜·태그가 이미 있으면 단독 명사도 허용).

    반환은 stopword 와 조사 strip 한 압축 텍스트. 빈 문자열이면 None.
    """
    if not residual_text:
        return None
    raw_tokens = tuple(t for t in residual_text.split() if t)
    if not raw_tokens:
        return None
    if len(raw_tokens) > 5:
        return None
    if _has_question_verb(raw_tokens):
        return None

    # stopword/조사 정리 후 의미 토큰만
    cleaned: list[str] = []
    for tok in raw_tokens:
        c = _strip_particle(tok)
        if not c or c in _STOPWORD_VERBS:
            continue
        cleaned.append(c)
    if not cleaned:
        return None

    # doc-suffix 체크 — 단독 명사 허용 가드
    has_suffix = any(
        any(c.endswith(suf) or suf in c for suf in _DOC_SUFFIXES) for c in cleaned
    )
    if not has_suffix and not has_date_or_tag:
        # 날짜·태그 가드 없이 단독 명사구는 회귀 위험 (RAG path 보호)
        return None

    return " ".join(cleaned)


def is_meta_only(query: str, *, today: date | None = None) -> MetaFilterPlan | None:
    """query 가 순수 메타 필터인지 판정. fast path 진입 시 ``MetaFilterPlan`` 반환.

    Returns
    -------
    ``MetaFilterPlan`` 또는 None. None 일 때 호출자는 기존 RAG path 그대로 실행.

    동작
    ----
    1. NFC 정규화.
    2. 태그 추출.
    3. 날짜 추출.
    4. 잔여 텍스트 (날짜·태그 strip 후) 분석:
       - 의문/서술 동사구 발화 → None
       - doc-suffix 또는 (날짜·태그 가드 + 짧은 명사구) → title_ilike
       - 잔여가 stopword 만 → meta-only 확정
    5. (date | tag | title) 중 1개 이상 매칭 시 plan 반환.
    """
    if not query or not query.strip():
        return None

    text = _normalize(query)
    tags = _extract_tags(text)
    date_range = _extract_date_range(text, today=today)

    residual_text = _strip_date_expressions(_strip_tags(text))
    residual_tokens = tuple(t for t in residual_text.split() if t)

    # 의문/서술 동사구 발화 → fast path 무효
    if _has_question_verb(residual_tokens):
        return None

    has_date_or_tag = date_range is not None or bool(tags)
    title_ilike = _extract_title_ilike(
        residual_text, has_date_or_tag=has_date_or_tag
    )

    # 잔여가 명령형 stopword 외 의미 토큰을 가지고 있으면서 title_ilike 도 못 뽑았다면
    # 메타 의도가 약함 → None.
    if residual_tokens and title_ilike is None:
        if not _residual_only_stopwords(residual_tokens):
            return None

    if date_range is None and not tags and title_ilike is None:
        return None

    kinds: list[str] = []
    if date_range is not None:
        kinds.append("date")
    if tags:
        kinds.append("tag")
    if title_ilike:
        kinds.append("title")
    matched_kind = "+".join(kinds)

    return MetaFilterPlan(
        date_range=date_range,
        tags=tags,
        title_ilike=title_ilike,
        matched_kind=matched_kind,
        residual_tokens=residual_tokens,
    )


def run(plan: MetaFilterPlan, *, user_id: str) -> list[dict]:
    """``MetaFilterPlan`` 을 documents SELECT 로 실행 → row list 반환.

    실행 정책
    --------
    - ``SELECT id, title, doc_type, tags, summary, created_at`` ORDER BY created_at DESC LIMIT 20.
    - 임베딩·reranker·RPC 호출 0.
    - 사용자 격리 — ``user_id`` 필수, ``deleted_at IS NULL``.
    - date_range 는 ``created_at`` 기준 (반-개구간 [from, to)).
    - tags 는 GIN ``contains`` (모두 포함 — AND 일관 — `/search` 와 동일 의미).
    - title_ilike 는 ``title ILIKE %키%`` — 다중 키워드는 공백 1개로 압축됐으므로 단일 패턴.
    """
    client = get_supabase_client()
    q = (
        client.table("documents")
        .select("id, title, doc_type, tags, summary, created_at")
        .eq("user_id", user_id)
        .is_("deleted_at", "null")
    )
    if plan.date_range is not None:
        from_dt, to_dt = plan.date_range
        q = q.gte("created_at", from_dt.isoformat()).lt(
            "created_at", to_dt.isoformat()
        )
    if plan.tags:
        q = q.contains("tags", list(plan.tags))
    if plan.title_ilike:
        q = q.ilike("title", f"%{plan.title_ilike}%")

    q = q.order("created_at", desc=True).limit(_FAST_PATH_LIMIT)
    resp = q.execute()
    return resp.data or []


__all__ = [
    "MetaFilterPlan",
    "is_meta_only",
    "run",
]
