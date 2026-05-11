"""Phase 3 일회용 — cross_doc 후보 doc 들의 chunks 를 조회하여 답 chunk_idx 식별.

사용:
    cd api && uv run python ../evals/_phase3_chunks_probe.py

각 후보 doc 의 chunks 를 dump 하여, 신규 cross_doc query 의 답이 들어있는
chunk_idx 를 수동 식별할 수 있도록 한다. 식별된 chunk_idx 는 evals/golden_v2.csv
의 신규 row 의 relevant_chunks 컬럼에 입력.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# api/ 패키지 경로 등록
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "api"))

from app.db.client import get_supabase_client


# 8자리 prefix → 별칭 (logging 용)
_DOC_PREFIXES: dict[str, str] = {
    "88920c9e": "승인글_템플릿1",
    "75c74e4c": "승인글_템플릿3",
    "80b48fa9": "law_sample2",
    "0ed5a024": "law_sample3",
    "a5f3cfae": "직제_규정",
    "2e083cb0": "한마음생활체육관_운영_내규",
    "430e9fbe": "브랜딩_스튜디오앤드오어",
    "92c38315": "sonata_catalog",
    "b218e8a1": "데이터센터_안내서",
    "2303ef36": "보건의료_빅데이터",
    "51dd2cc0": "기웅민_이력서",
    "9878d7bd": "이한주_포트폴리오",
}


def _load_all_docs(client) -> dict[str, dict]:
    """모든 활성 doc → {prefix8: row} 인덱스. UUID LIKE 불가 우회."""
    rows = (
        client.table("documents")
        .select("id, title, doc_type")
        .is_("deleted_at", "null")
        .execute()
        .data
        or []
    )
    return {row["id"][:8]: row for row in rows}


def _dump_chunks(client, prefix: str, alias: str, docs_index: dict[str, dict], out_dir: Path) -> None:
    doc_row = docs_index.get(prefix)
    if doc_row is None:
        print(f"[WARN] {alias} ({prefix}) — doc_id 조회 실패", file=sys.stderr)
        return
    doc_id = doc_row["id"]
    rows = (
        client.table("chunks")
        .select("chunk_idx, page, section_title, text, metadata")
        .eq("doc_id", doc_id)
        .order("chunk_idx")
        .execute()
        .data
        or []
    )
    out_path = out_dir / f"{alias}_{prefix}.tsv"
    with out_path.open("w", encoding="utf-8") as f:
        f.write("chunk_idx\tpage\tsection\ttext_excerpt\n")
        for r in rows:
            idx = r.get("chunk_idx")
            page = r.get("page") or ""
            section = (r.get("section_title") or "").replace("\t", " ").replace("\n", " ")
            text = (r.get("text") or "").replace("\t", " ").replace("\n", " ")
            excerpt = text[:200]
            f.write(f"{idx}\t{page}\t{section}\t{excerpt}\n")
    print(f"[OK] {alias} ({prefix}) doc_id={doc_id} → {out_path} ({len(rows)} chunks)")


def main() -> None:
    out_dir = Path(__file__).resolve().parent / "_phase3_dumps"
    out_dir.mkdir(exist_ok=True)
    client = get_supabase_client()
    docs_index = _load_all_docs(client)
    print(f"[INFO] docs 인덱스 {len(docs_index)}건 로드")
    for prefix, alias in _DOC_PREFIXES.items():
        _dump_chunks(client, prefix, alias, docs_index, out_dir)


if __name__ == "__main__":
    main()
