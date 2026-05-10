"""S5-B 후속 (B 단계) — golden v2 minority qtype 표본 자동 확장.

motivation
----------
golden v2 의 minority qtype (synonym_mismatch 4 / cross_doc 5 / vision_diagram 6)
은 통계 신뢰도가 낮음 (직전 핸드오프 §6 한계 #11). RAGAS 회귀 baseline (S5-B) 에서도
qtype 별 1 row 표본 한계 명시. 본 도구는 minority qtype 의 candidate row 를 LLM
(Gemini 2.5 Flash) 자동 생성하여 사용자 review 부담 경감.

설계 원칙
---------
- **자동 append 금지** — 출력은 `evals/results/golden_v2_candidates.csv` (gitignored).
  사용자 수동 review 후 본 골든셋에 add (false positive 라벨링 위험 회피).
- **few-shot prompt** — 기존 golden v2 의 같은 qtype rows (4~6) 를 LLM 에게 패턴으로
  제시 → 새 query 생성. 동일 doc / 동일 스타일 보존.
- **외부 의존성 = LLM 호출만** — Gemini 2.5 Flash text-gen, JSON mode.
- **cost cap** — `--qtypes` × `--count-per-qtype` 로 통제. default 2 qtype × 5 = 10
  call ≈ ~$0.02~0.05.

사용
----
    cd api && DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' \
      uv run python ../evals/generate_golden_rows.py \
        --qtypes synonym_mismatch vision_diagram --count-per-qtype 5

비판적 한계 (정직 인정)
- LLM hallucination 위험 — 생성된 query 가 실제 doc 에 답이 없을 수 있음.
  사용자 review 가 필수 (자동 append 안 함).
- chunk text 미참조 — LLM 은 기존 row 의 expected_answer_summary / must_include 만 활용.
  실 chunk 매칭은 사용자가 수동으로 acceptable_chunks 채워야 함.
- doc_id 는 few-shot 예제의 doc_id 재사용 — 새 doc 자동 발견 X.
- cross_doc qtype: doc_id 빈 U-row 패턴 + multi-doc 합성 query — 첫 ship 에서 제외 권고.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# api/ 를 import path 에 추가 — app.config 통한 .env 자동 로드 위해
_API_PATH = Path(__file__).resolve().parents[0].parent / "api"
if (_API_PATH / "app").exists():
    sys.path.insert(0, str(_API_PATH))

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GOLDEN_V2_CSV = _REPO_ROOT / "evals" / "golden_v2.csv"
_DEFAULT_OUT_CSV = _REPO_ROOT / "evals" / "results" / "golden_v2_candidates.csv"

_GEMINI_MODEL = os.environ.get("GENERATE_GOLDEN_MODEL", "gemini-2.5-flash")
_GENERATION_TEMPERATURE = 0.7  # 다양성 ↑ (회귀 측정용 다양한 query 패턴)

# golden v2 의 14 컬럼 (out CSV 헤더와 동일)
_CSV_COLUMNS = [
    "id",
    "query",
    "query_type",
    "doc_id",
    "expected_doc_title",
    "relevant_chunks",
    "acceptable_chunks",
    "source_chunk_text",
    "expected_answer_summary",
    "must_include",
    "source_hint",
    "negative",
    "doc_type",
    "caption_dependent",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


@dataclass
class GoldenExample:
    """few-shot prompt 입력용."""

    id: str
    query: str
    query_type: str
    doc_id: str
    expected_doc_title: str
    expected_answer_summary: str
    must_include: str
    doc_type: str
    caption_dependent: str  # "true" / "false"


@dataclass
class CandidateRow:
    """LLM 이 생성한 신규 candidate row."""

    id: str  # 자동 부여 (G-Z-XXX 패턴, 사용자 후속 수정 권장)
    query: str
    query_type: str
    doc_id: str
    expected_doc_title: str
    expected_answer_summary: str
    must_include: str
    doc_type: str
    caption_dependent: str

    def to_csv_row(self) -> dict[str, str]:
        return {
            "id": self.id,
            "query": self.query,
            "query_type": self.query_type,
            "doc_id": self.doc_id,
            "expected_doc_title": self.expected_doc_title,
            "relevant_chunks": "",  # 사용자 수동 채움
            "acceptable_chunks": "",  # 사용자 수동 채움
            "source_chunk_text": "",
            "expected_answer_summary": self.expected_answer_summary,
            "must_include": self.must_include,
            "source_hint": "",
            "negative": "false",
            "doc_type": self.doc_type,
            "caption_dependent": self.caption_dependent,
        }


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_examples_by_qtype(csv_path: Path) -> dict[str, list[GoldenExample]]:
    """golden v2 → qtype 별 GoldenExample list."""
    out: dict[str, list[GoldenExample]] = defaultdict(list)
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            qid = (row.get("id") or "").strip()
            qtype = (row.get("query_type") or "").strip()
            if not qid or not qtype:
                continue
            out[qtype].append(
                GoldenExample(
                    id=qid,
                    query=(row.get("query") or "").strip(),
                    query_type=qtype,
                    doc_id=(row.get("doc_id") or "").strip(),
                    expected_doc_title=(row.get("expected_doc_title") or "").strip(),
                    expected_answer_summary=(
                        row.get("expected_answer_summary") or ""
                    ).strip(),
                    must_include=(row.get("must_include") or "").strip(),
                    doc_type=(row.get("doc_type") or "").strip(),
                    caption_dependent=(row.get("caption_dependent") or "false").strip(),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


_QTYPE_GUIDANCE = {
    "synonym_mismatch": (
        "사용자가 chunk 본문의 정확한 단어 대신 **유사어/동의어/일상 표현**으로 묻는 패턴입니다. "
        "예: '관련도' → '매칭 강도', '환자 정보 보호' → '개인정보 안전 조치'. "
        "기존 example 의 doc 내용을 반영하되, 동일 의미의 다른 어휘로 표현하는 query 를 생성하세요."
    ),
    "vision_diagram": (
        "사용자가 문서 안의 **그림/도표/사진/다이어그램**에 대해 묻는 패턴입니다. "
        "구어체 (\"그림 어떻게 생겼더라\", \"~~ 그림 어디\") 또는 명사구 (\"~~ 그림\") 모두 가능. "
        "기존 example 의 doc 내용을 반영하여 vision 관련 query 를 생성하세요."
    ),
    "fuzzy_memory": (
        "사용자가 정확한 표현을 잊고 **추측형/구어체** ('그때 ~ 뭐였더라', '~~ 어떻게 됐었지') 로 "
        "묻는 패턴입니다. 기존 example 의 doc 내용을 반영하여 자연스러운 일상 회상 query 를 생성하세요."
    ),
    "cross_doc": (
        "사용자가 **여러 문서를 가로질러** 비교/대조/합성을 요구하는 패턴입니다. "
        "doc_id 는 빈 문자열 (single-doc scope X). 기존 example 의 doc 조합을 참고하세요."
    ),
}

_SYSTEM_PROMPT = """당신은 한국어 RAG 골든셋 큐레이터입니다.
주어진 query_type 의 패턴에 맞는 신규 query candidate 를 JSON array 로 생성하세요.

각 candidate 는 아래 schema 의 JSON object:
- query (string): 신규 사용자 query (한국어)
- doc_id (string): 기존 example 중 하나의 doc_id (재사용)
- expected_doc_title (string): doc_id 와 정합한 title
- expected_answer_summary (string): 답변 요약 1-2 문장 (한국어)
- must_include (string): 답변에 포함되어야 하는 키워드 (세미콜론 구분, 1~3개)
- doc_type (string): 기존 example 의 doc_type (pdf/hwpx/docx/hwp/pptx)
- caption_dependent (string): "true" 또는 "false" — vision_diagram 은 보통 "true"

응답은 JSON array 만 반환 (markdown fence 금지, 설명 텍스트 금지).
"""


def build_prompt(
    *,
    qtype: str,
    examples: list[GoldenExample],
    count: int,
) -> str:
    """qtype × N candidate 생성용 user prompt."""
    guidance = _QTYPE_GUIDANCE.get(qtype, "")
    examples_json = json.dumps(
        [
            {
                "id": e.id,
                "query": e.query,
                "doc_id": e.doc_id,
                "expected_doc_title": e.expected_doc_title,
                "expected_answer_summary": e.expected_answer_summary,
                "must_include": e.must_include,
                "doc_type": e.doc_type,
                "caption_dependent": e.caption_dependent,
            }
            for e in examples
        ],
        ensure_ascii=False,
        indent=2,
    )
    return f"""query_type: {qtype}

패턴 가이드:
{guidance}

기존 골든셋 example ({len(examples)} 건):
{examples_json}

위 example 의 doc 들과 동일 doc_id 들을 재사용하여, 같은 query_type 패턴으로
**{count} 건**의 신규 candidate 를 생성하세요. 기존 query 와 중복 금지."""


# ---------------------------------------------------------------------------
# LLM call + parse
# ---------------------------------------------------------------------------


def call_gemini_json(*, system: str, user: str, model: str = _GEMINI_MODEL) -> str:
    """Gemini text-gen JSON 응답."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 미설정")
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        system_instruction=system,
        temperature=_GENERATION_TEMPERATURE,
        response_mime_type="application/json",
    )
    response = client.models.generate_content(
        model=model,
        contents=[
            types.Content(role="user", parts=[types.Part.from_text(text=user)])
        ],
        config=config,
    )
    text = response.text
    if text is None or not text.strip():
        raise RuntimeError(f"Gemini 빈 응답: {response}")
    return text


def parse_candidates(
    *, raw_json: str, qtype: str, id_prefix: str, start_idx: int = 1
) -> list[CandidateRow]:
    """LLM JSON → CandidateRow list. id 자동 부여 (G-Z-XXX)."""
    try:
        items = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM JSON parse 실패: {exc}\nraw: {raw_json[:300]}") from exc
    if not isinstance(items, list):
        raise RuntimeError(f"LLM 응답이 array 아님: {type(items)} / {raw_json[:200]}")
    out: list[CandidateRow] = []
    for i, item in enumerate(items, start=start_idx):
        if not isinstance(item, dict):
            continue
        new_id = f"{id_prefix}{i:03d}"
        out.append(
            CandidateRow(
                id=new_id,
                query=str(item.get("query", "")).strip(),
                query_type=qtype,
                doc_id=str(item.get("doc_id", "")).strip(),
                expected_doc_title=str(item.get("expected_doc_title", "")).strip(),
                expected_answer_summary=str(
                    item.get("expected_answer_summary", "")
                ).strip(),
                must_include=str(item.get("must_include", "")).strip(),
                doc_type=str(item.get("doc_type", "")).strip(),
                caption_dependent=str(
                    item.get("caption_dependent", "false")
                ).strip().lower(),
            )
        )
    return out


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------


def write_candidates_csv(*, path: Path, candidates: list[CandidateRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for c in candidates:
            writer.writerow(c.to_csv_row())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="golden v2 minority qtype 자동 확장 (LLM 생성 → 사용자 review)."
    )
    parser.add_argument(
        "--qtypes",
        nargs="+",
        default=["synonym_mismatch", "vision_diagram"],
        help="확장할 qtype list (default: synonym_mismatch + vision_diagram). "
        "cross_doc 은 첫 ship 에서 제외 권고.",
    )
    parser.add_argument(
        "--count-per-qtype",
        type=int,
        default=5,
        help="qtype 당 생성할 candidate 수 (default 5).",
    )
    parser.add_argument(
        "--id-prefix",
        default="G-Z-",
        help="신규 id prefix (default 'G-Z-' — 자동 생성 분리). 사용자 후속 수정 권장.",
    )
    parser.add_argument(
        "--out", type=Path, default=_DEFAULT_OUT_CSV, help="출력 CSV 경로"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="prompt 만 출력하고 종료 (cost 0).",
    )
    args = parser.parse_args(argv)

    # api/app/config.py → .env 자동 로드
    try:
        from app import config as _app_config  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] app.config import 실패 ({exc})", file=sys.stderr)

    examples_by_qtype = load_examples_by_qtype(_GOLDEN_V2_CSV)
    print(f"[load] golden v2 qtype 수: {len(examples_by_qtype)}", file=sys.stderr)

    candidates: list[CandidateRow] = []
    for qtype in args.qtypes:
        examples = examples_by_qtype.get(qtype, [])
        if not examples:
            print(f"  ⚠ qtype={qtype}: 기존 example 0건 → skip", file=sys.stderr)
            continue
        print(
            f"  [{qtype}] examples={len(examples)} → request {args.count_per_qtype} candidates",
            file=sys.stderr,
        )
        prompt = build_prompt(
            qtype=qtype, examples=examples, count=args.count_per_qtype
        )
        if args.dry_run:
            print(f"\n=== prompt for {qtype} ===\n{prompt}\n", file=sys.stderr)
            continue
        t0 = time.monotonic()
        try:
            raw = call_gemini_json(system=_SYSTEM_PROMPT, user=prompt)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ {qtype} LLM 실패: {exc}", file=sys.stderr)
            continue
        elapsed = time.monotonic() - t0
        try:
            new_rows = parse_candidates(
                raw_json=raw,
                qtype=qtype,
                id_prefix=args.id_prefix,
                start_idx=len(candidates) + 1,
            )
        except RuntimeError as exc:
            print(f"  ⚠ {qtype} parse 실패: {exc}", file=sys.stderr)
            continue
        print(
            f"    ✅ {len(new_rows)} candidates 생성 ({elapsed:.1f}s)", file=sys.stderr
        )
        candidates.extend(new_rows)

    if args.dry_run:
        print("[dry-run] 종료 (cost 0)", file=sys.stderr)
        return 0

    if not candidates:
        print("[FAIL] 생성된 candidate 0건", file=sys.stderr)
        return 1

    write_candidates_csv(path=args.out, candidates=candidates)
    print(f"[OK] {len(candidates)} candidates → {args.out}", file=sys.stderr)
    print(
        "  → 사용자 review 후 evals/golden_v2.csv 에 직접 append 권고 "
        "(acceptable_chunks 수동 채움 + 라벨 검증).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
