"""W25 D14 — RAGAS 자동 RAG 평가 (사용자 수작업 0).

motivation
----------
mini-Ragas Phase 1 (W25 D7 ship) 은 골든셋 수작업 + rule-based 측정.
사용자 자료 누적 시 수작업 부담 폭주 → "어떤 파일이 들어와도 자동 측정" 인프라 필요.

본 스크립트는 RAGAS 표준 흐름:
1. **TestsetGenerator** — chunks → LLM 이 question + ground_truth 자동 생성
2. **/search /answer 자동 호출** — 시스템 응답 수집
3. **RAGAS evaluate** — 7 메트릭 LLM-judge 자동 측정
4. **markdown 리포트** — work-log 자동 갱신

사용
----
    cd api && uv run python ../evals/run_ragas_auto.py \
        --doc_id b218e8a1-cb35-4540-b969-f0f4fac517fa \
        --testset_size 10 \
        --output ../work-log/2026-05-05\\ ragas-auto-result.md

전제
- `uvicorn` 8000 가동 + paid tier Gemini key
- 의존성: ragas + langchain-google-genai + datasets

비판적 한계 (정직 인정)
- TestsetGenerator 한국어 미검증 (영어 위주 검증된 도구) — PoC 1회 결과로 신뢰도 판정
- LLM-judge 정확도 ~80~90% (절대 정답 X, 상대 비교는 OK)
- 자동 QA 가 사용자 실제 query distribution 과 다를 수 있음
- 비용 ~$0.1~0.3/eval (paid tier 안 미미)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# 의존성 import — 실패 시 명확한 가이드
try:
    from datasets import Dataset
    from langchain_core.documents import Document
    from langchain_google_genai import (
        ChatGoogleGenerativeAI,
        GoogleGenerativeAIEmbeddings,
    )
    from ragas import EvaluationDataset, evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.testset import TestsetGenerator
    from ragas.metrics import (
        Faithfulness,
        ResponseRelevancy,
        LLMContextRecall,
        LLMContextPrecisionWithReference,
        AnswerCorrectness,
    )
except ImportError as exc:
    print(
        "[FAIL] RAGAS 자동 평가 의존성 누락: "
        "`uv add ragas langchain-google-genai datasets` 후 재시도.",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
from app.db import get_supabase_client  # noqa: E402

_SEARCH_BASE = "http://localhost:8000"
_GEMINI_MODEL = "gemini-2.5-flash"
# W25 D14 fix v2 — Gemini API embedding 모델 이름 변천:
#   embedding-001 (v1beta deprecated, 404)
#   text-embedding-004 (v1beta 도 404)
#   gemini-embedding-001 — 현재 GA (2026 기준)
# ENV 로 override 가능 (Google 측 모델 변경 시 빠른 대응).
_EMBEDDING_MODEL = os.environ.get("RAGAS_EMBEDDING_MODEL", "models/gemini-embedding-001")
# TestsetGenerator 가 input documents 전부에 EmbeddingExtractor (NER overlap) 적용
# → chunks 수백 개 입력 시 embedding 호출 폭주. 샘플링으로 입력 documents 줄임.
# 의미 QA 생성에는 50 documents 면 충분 (대부분 RAG 자료 기준).
_TESTSET_INPUT_DOCS_MAX = int(os.environ.get("RAGAS_AUTO_TESTSET_DOCS_MAX", "50"))


def _load_chunks_as_documents(doc_id: str) -> list[Document]:
    """Supabase chunks → langchain Document. TestsetGenerator 입력."""
    client = get_supabase_client()
    resp = (
        client.table("chunks")
        .select("chunk_idx,page,section_title,text")
        .eq("doc_id", doc_id)
        .order("chunk_idx")
        .execute()
    )
    docs: list[Document] = []
    for r in resp.data or []:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        docs.append(
            Document(
                page_content=text,
                metadata={
                    "chunk_idx": r["chunk_idx"],
                    "page": r.get("page"),
                    "section_title": r.get("section_title"),
                },
            )
        )
    return docs


def _call_search(query: str, doc_id: str, top_k: int = 10) -> list[str]:
    """/search 호출 → top_k chunks 본문 list."""
    qs = urllib.parse.urlencode(
        {"q": query, "limit": str(top_k), "doc_id": doc_id}
    )
    url = f"{_SEARCH_BASE}/search?{qs}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        d = json.load(resp)
    contexts: list[str] = []
    for item in d.get("items") or []:
        for ch in item.get("matched_chunks") or []:
            text = (ch.get("text") or "").strip()
            if text:
                contexts.append(text)
    return contexts[:top_k]


def _call_answer(query: str, doc_id: str, top_k: int = 8) -> str:
    """/answer 호출 → 답변 텍스트."""
    qs = urllib.parse.urlencode(
        {"q": query, "top_k": str(top_k), "doc_id": doc_id}
    )
    url = f"{_SEARCH_BASE}/answer?{qs}"
    with urllib.request.urlopen(url, timeout=120) as resp:
        d = json.load(resp)
    return (d.get("answer") or "").strip()


def _build_eval_dataset(
    testset_df: Any, doc_id: str
) -> Dataset:
    """testset (LLM 자동 QA) → 각 query 마다 /search + /answer 호출 → eval Dataset."""
    rows: list[dict[str, Any]] = []
    for i, row in enumerate(testset_df.itertuples(), start=1):
        question = getattr(row, "user_input", None) or getattr(row, "question", "")
        # ragas 0.4.x: testset rows have 'reference' (ground truth answer)
        reference = getattr(row, "reference", None) or getattr(
            row, "ground_truth", ""
        )
        print(f"  [{i}/{len(testset_df)}] q={question[:60]!r}", file=sys.stderr)
        try:
            contexts = _call_search(question, doc_id, top_k=10)
            answer = _call_answer(question, doc_id, top_k=8)
        except Exception as exc:  # noqa: BLE001
            print(f"    /search or /answer 실패: {exc}", file=sys.stderr)
            contexts, answer = [], ""
        rows.append(
            {
                "user_input": question,
                "retrieved_contexts": contexts,
                "response": answer,
                "reference": reference,
            }
        )
    return Dataset.from_list(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RAGAS 자동 RAG 평가 (TestsetGenerator + 7 메트릭 LLM-judge)"
    )
    parser.add_argument("--doc_id", required=True, help="평가할 문서 doc_id")
    parser.add_argument(
        "--testset_size", type=int, default=10, help="자동 생성할 QA 수"
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=None, help="markdown 리포트 경로"
    )
    args = parser.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        print("[FAIL] GEMINI_API_KEY 환경변수 미설정", file=sys.stderr)
        return 1

    # langchain Gemini wrapper (paid tier)
    chat_llm = ChatGoogleGenerativeAI(
        model=_GEMINI_MODEL,
        temperature=0.2,
        google_api_key=os.environ["GEMINI_API_KEY"],
    )
    embeddings = GoogleGenerativeAIEmbeddings(
        model=_EMBEDDING_MODEL,
        google_api_key=os.environ["GEMINI_API_KEY"],
    )
    ragas_llm = LangchainLLMWrapper(chat_llm)
    ragas_embeddings = LangchainEmbeddingsWrapper(embeddings)

    # 1) chunks 로드 + 샘플링 (TestsetGenerator EmbeddingExtractor 폭주 방지)
    print(f"[1/4] chunks 로드 (doc={args.doc_id})", file=sys.stderr)
    documents = _load_chunks_as_documents(args.doc_id)
    if not documents:
        print(f"[FAIL] doc_id={args.doc_id} 의 chunks 없음", file=sys.stderr)
        return 1
    n_total = len(documents)
    if n_total > _TESTSET_INPUT_DOCS_MAX:
        # 균등 샘플링 — 문서 전반부터 후반까지 골고루
        step = n_total // _TESTSET_INPUT_DOCS_MAX
        documents = documents[::step][:_TESTSET_INPUT_DOCS_MAX]
        print(
            f"  {n_total} chunks → 균등 샘플링 {len(documents)} (step={step}, "
            f"cap RAGAS_AUTO_TESTSET_DOCS_MAX={_TESTSET_INPUT_DOCS_MAX})",
            file=sys.stderr,
        )
    else:
        print(f"  {n_total} chunks → langchain Documents (전체 사용)", file=sys.stderr)

    # 2) TestsetGenerator — 자동 QA 생성
    print(
        f"[2/4] TestsetGenerator → {args.testset_size} QA 자동 생성 (LLM Gemini Flash)",
        file=sys.stderr,
    )
    t0 = time.monotonic()
    generator = TestsetGenerator(
        llm=ragas_llm,
        embedding_model=ragas_embeddings,
    )
    testset = generator.generate_with_langchain_docs(
        documents, testset_size=args.testset_size
    )
    testset_df = testset.to_pandas()
    print(
        f"  testset 생성 완료 ({time.monotonic() - t0:.1f}s, "
        f"행 {len(testset_df)}, 열 {list(testset_df.columns)})",
        file=sys.stderr,
    )

    # 3) 각 QA 에 /search + /answer 호출
    print("[3/4] /search + /answer 호출 (각 QA 마다)", file=sys.stderr)
    eval_dataset = _build_eval_dataset(testset_df, doc_id=args.doc_id)

    # 4) RAGAS evaluate — 7 메트릭 (collection 경로)
    print("[4/4] RAGAS evaluate — LLM-judge 메트릭 측정", file=sys.stderr)
    metrics = [
        LLMContextRecall(),
        LLMContextPrecisionWithReference(),
        Faithfulness(),
        ResponseRelevancy(),
        AnswerCorrectness(),
    ]
    t1 = time.monotonic()
    result = evaluate(
        dataset=EvaluationDataset.from_hf_dataset(eval_dataset),
        metrics=metrics,
        llm=ragas_llm,
        embeddings=ragas_embeddings,
    )
    print(f"  evaluate 완료 ({time.monotonic() - t1:.1f}s)", file=sys.stderr)

    # 결과 markdown
    md = _format_markdown(result, args, len(documents))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md, encoding="utf-8")
        print(f"[OK] 결과 저장 → {args.output}", file=sys.stderr)
    else:
        print(md)
    return 0


def _format_markdown(result: Any, args: argparse.Namespace, n_chunks: int) -> str:
    """RAGAS EvaluationResult → markdown."""
    df = result.to_pandas()
    lines: list[str] = []
    lines.append(
        f"# RAGAS 자동 평가 — doc {args.doc_id[:8]}... (testset {args.testset_size})"
    )
    lines.append("")
    lines.append(f"- 측정 일시: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- chunks: {n_chunks}")
    lines.append(f"- LLM-judge: {_GEMINI_MODEL}")
    lines.append(f"- embeddings: {_EMBEDDING_MODEL}")
    lines.append(f"- testset: TestsetGenerator 자동 생성 (수작업 0)")
    lines.append("")
    lines.append("## 종합 (평균)")
    lines.append("")
    metric_cols = [
        c for c in df.columns
        if c not in ("user_input", "retrieved_contexts", "response", "reference")
    ]
    for col in metric_cols:
        try:
            mean_val = df[col].mean()
            lines.append(f"- **{col}**: {mean_val:.3f}")
        except Exception:  # noqa: BLE001
            pass
    lines.append("")
    lines.append("## QA 별 상세")
    lines.append("")
    lines.append(
        "| # | question | "
        + " | ".join(metric_cols)
        + " |"
    )
    lines.append(
        "|---|---|"
        + "|".join(["---:"] * len(metric_cols))
        + "|"
    )
    for i, row in df.iterrows():
        q = str(row.get("user_input", ""))[:60]
        cells = [f"{i + 1}", f"`{q}`"]
        for col in metric_cols:
            val = row.get(col)
            cells.append(f"{val:.2f}" if isinstance(val, float) else str(val))
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
