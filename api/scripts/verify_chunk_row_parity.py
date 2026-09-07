"""`_serialize_chunk` 를 Python 원본과 대조.

## 왜 따로 보는가
이 함수가 틀리면 **DB 에 잘못된 행이 들어간다.** 그런데 틀려도 보통 조용하다 —
`bbox` 를 빠뜨리면 그냥 NULL 이 되고, `dense_vec` 키를 `null` 로 명시하면 `embed` 가
채워 둔 벡터를 `load` 재실행이 **지운다**. 검색이 안 되는 이유를 나중에 못 찾는다.

## 케이스가 노리는 것
- 선택 키(`bbox` · `dense_vec` · `char_range` · `id`)가 **있을 때만** 들어가는가
- 필수 키(`sparse_json` · `metadata` · `flags`)가 **빈 값이라도** 들어가는가
- `char_range` 가 `"[start,end)"` 문자열인가 (INT4RANGE, 끝이 열린 구간)
- NUL 제거
- 0 · 빈 문자열 · 빈 dict 같은 falsy 값이 키를 떨어뜨리지 않는가

사용:
    api/.venv/bin/python api/scripts/verify_chunk_row_parity.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

NUL = "\u0000"

# (설명, ChunkRecord kwargs)
CASES = [
    ("최소", {}),
    ("bbox 있음", {"bbox": (1.5, 2.5, 3.5, 4.5)}),
    ("bbox 0 좌표", {"bbox": (0.0, 0.0, 0.0, 0.0)}),
    ("char_range", {"char_range": (0, 123)}),
    ("char_range 0,0", {"char_range": (0, 0)}),
    ("dense_vec", {"dense_vec": [0.1, -0.2, 0.0]}),
    ("dense_vec 빈 list", {"dense_vec": []}),
    ("chunk_id", {"chunk_id": "11111111-2222-3333-4444-555555555555"}),
    ("chunk_id 빈 문자열", {"chunk_id": ""}),
    ("sparse_json", {"sparse_json": {"토큰": 1.5}}),
    ("metadata", {"metadata": {"entities": {"dates": ["2024-01-01"]}}}),
    ("flags", {"flags": {"filtered_reason": "table_noise"}}),
    ("page None", {"page": None}),
    ("page 0", {"page": 0}),
    ("section_title", {"section_title": "제1조 목적"}),
    ("section_title 빈 문자열", {"section_title": ""}),
    ("text 에 NUL", {"text": f"본문{NUL}조각"}),
    ("section_title 에 NUL", {"section_title": f"제목{NUL}"}),
    ("metadata 안 NUL", {"metadata": {"k": f"v{NUL}"}}),
    ("전부", {
        "bbox": (1.0, 2.0, 3.0, 4.0),
        "char_range": (10, 810),
        "dense_vec": [0.5] * 4,
        "sparse_json": {"a": 1.0},
        "metadata": {"m": 1},
        "flags": {"f": True},
        "page": 7,
        "section_title": "제목",
        "chunk_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    }),
]

RUNNER_TS = f"""
import {{ chunkRecordToRow }} from "file://{SHARED}/ingest/chunk_row.ts";
import {{ stripNulls }} from "file://{SHARED}/ingest/strip_nul.ts";

const cases = JSON.parse(await Deno.readTextFile(Deno.args[0]));
// 원본은 `_serialize_chunk` 안에서 NUL 을 지운다. 이식본은 저장 직전 공통 경로에서
// 지우므로, 대조할 때는 같은 지점을 맞춘다.
console.log(JSON.stringify(
  cases.map((c: unknown) => stripNulls(chunkRecordToRow(c as never)).value),
));
"""


def main() -> None:
    from app.adapters.impl.supabase_vectorstore import SupabasePgVectorStore
    from app.adapters.vectorstore import ChunkRecord

    py_rows = []
    ts_input = []
    for _label, kw in CASES:
        base = dict(doc_id="d1", chunk_idx=3, text="본문입니다")
        base.update(kw)
        rec = ChunkRecord(**base)  # type: ignore[arg-type]
        py_rows.append(SupabasePgVectorStore._serialize_chunk(rec))
        # TS 쪽 입력 — dataclass 를 JSON 으로 옮긴다. tuple 은 list 가 된다.
        ts_input.append({
            "doc_id": rec.doc_id, "chunk_idx": rec.chunk_idx, "text": rec.text,
            "page": rec.page, "section_title": rec.section_title,
            "bbox": list(rec.bbox) if rec.bbox is not None else None,
            "char_range": list(rec.char_range) if rec.char_range is not None else None,
            "dense_vec": rec.dense_vec,
            "sparse_json": rec.sparse_json, "metadata": rec.metadata, "flags": rec.flags,
            "chunk_id": rec.chunk_id,
        })

    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(ts_input, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=600,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:3000]}")
    ts_rows = json.loads(proc.stdout)

    fails = 0
    key_counts: dict[str, int] = {}
    for (label, _), want, got in zip(CASES, py_rows, ts_rows):
        for k in want:
            key_counts[k] = key_counts.get(k, 0) + 1
        # Python tuple → JSON list 로 맞춘 뒤 비교
        want_j = json.loads(json.dumps(want, default=list))
        if want_j != got:
            fails += 1
            print(f"  **[{label}] 불일치**")
            for k in sorted(set(want_j) | set(got)):
                if want_j.get(k, "<없음>") != got.get(k, "<없음>"):
                    print(f"      {k:<14} py={json.dumps(want_j.get(k, '<없음>'), ensure_ascii=False)}")
                    print(f"      {'':<14} ts={json.dumps(got.get(k, '<없음>'), ensure_ascii=False)}")
    if fails == 0:
        print(f"  chunkRecordToRow          {len(CASES)}건 OK")
    print("    키 등장 횟수: " + "  ".join(f"{k} {v}" for k, v in sorted(key_counts.items())))

    # 케이스 무효 검사 — 선택 키가 "있는 경우"와 "없는 경우"가 모두 있어야 의미가 있다.
    for k in ("bbox", "dense_vec", "char_range", "id"):
        n = key_counts.get(k, 0)
        if n == 0 or n == len(CASES):
            fails += 1
            print(f"    **케이스 무효** — 선택 키 {k} 가 한쪽으로만 나왔다 ({n}/{len(CASES)})")
    for k in ("sparse_json", "metadata", "flags"):
        if key_counts.get(k, 0) != len(CASES):
            fails += 1
            print(f"    **필수 키 {k} 가 빠진 행이 있다** ({key_counts.get(k, 0)}/{len(CASES)})")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
