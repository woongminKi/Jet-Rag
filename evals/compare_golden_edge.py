"""Task 2.9 착수 — 골든셋을 Railway 구현과 Edge 구현에 나란히 돌려 회귀를 판정한다.

## 판정 기준을 응답 동일성으로 잡은 이유
플랜 §9 는 "Recall@10 · MRR · nDCG@10 차이 0" 을 기준으로 뒀다. 그런데 지표는 **여러
응답 차이가 상쇄돼 같은 값이 나올 수 있다** — 문서 A 와 B 의 순위가 맞바뀌어도 둘 다
정답이면 R@10 은 그대로다. 그래서 여기서는 두 층으로 본다:

1. **응답 동일성** — `took_ms` 만 빼고 전 필드 비교. 이게 0 이면 지표는 자동으로 같다.
2. **지표** — 그래도 플랜의 기준대로 양쪽에서 계산해 나란히 찍는다. 응답이 같으면
   같은 값이 나오는 게 당연하지만, 기준선(2026-09-04 실측)과 대조할 수 있어야 한다.

## 두 모드를 다 돈다
기준선(2026-09-04, R@10 0.4722)은 **per-query doc-scope · limit 50 · golden_v1(123행)**
조건에서 나왔다(러너의 `_run_batch` → `_evaluate_one`). 그 조건을 그대로 재현한다.

거기에 **multi-doc** 을 더한다. 운영 트래픽은 `doc_id` 없는 검색이 대부분인데 경로가
다르기 때문이다 — top_k 200 vs 50, 청크 cap 200 vs 3, 표시 순서 score vs chunk_idx.
doc-scope 만 돌면 그 분기가 통째로 안 덮인다.

## 제공자를 운영에 맞춘다
`.env` 에는 `JETRAG_EMBED_PROVIDER` 가 없어서 로컬 기본값은 `hf` 인데, 운영은
`deepinfra` 다(2026-09-05 사용자 확인). 그대로 두면 **운영이 실제로 쓰는 경로를 안 재게**
되므로 여기서 명시적으로 `deepinfra` 로 고정한다. `--provider hf` 로 바꿀 수 있다.

## DeepInfra 비결정성 처리
운영 제공자는 `deepinfra` 다(2026-09-05 사용자 확인). 같은 질의에도 벡터가 미세하게
흔들리므로(Task 2.7 실측), **Python 을 먼저 돌려 `embed_query_cache` 를 채우고** 그 다음
Edge 가 같은 캐시 벡터를 읽게 한다. 캐시가 채워진 상태가 운영의 실제 모습이기도 하다.

사용:
    api/.venv/bin/python evals/compare_golden_edge.py [--limit N] [--goldenset PATH]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEARCH_DIR = ROOT / "supabase" / "functions" / "_shared" / "search"
DENO_CONFIG = ROOT / "supabase" / "functions" / "deno.json"

sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "evals"))

RUNNER_TS = f"""
import {{ createClient }} from "@supabase/supabase-js";
import {{ validateSearchParams }} from "file://{SEARCH_DIR}/params.ts";
import {{ runSearch, SearchHttpError }} from "file://{SEARCH_DIR}/pipeline.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const env: Record<string, string> = input.env;
const client = createClient(env.SUPABASE_URL, env.SUPABASE_SERVICE_ROLE_KEY, {{
  auth: {{ persistSession: false }},
}});

const out: unknown[] = [];
for (const qs of input.cases as Record<string, string>[]) {{
  const v = validateSearchParams(new URLSearchParams(qs));
  if (!v.ok) {{
    out.push({{ error: `검증 실패 ${{v.status}}` }});
    continue;
  }}
  try {{
    const r = await runSearch(v.params, input.user_id, {{
      client,
      read: (k) => env[k],
    }});
    out.push(r.body);
  }} catch (e) {{
    out.push({{
      error: e instanceof SearchHttpError ? `HTTP ${{e.status}}: ${{e.detail}}` : String(e),
    }});
  }}
}}
console.log(JSON.stringify(out));
"""


def run_deno(payload: dict, timeout: int = 3600) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        cf = os.path.join(tmp, "cases.json")
        rf = os.path.join(tmp, "runner.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", str(DENO_CONFIG), "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=timeout,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:3000]}")
    return json.loads(proc.stdout)


def diff(a, b, path="") -> list[str]:
    """파싱된 값 기준 깊은 비교. `1.0` 과 `1` 은 같다고 본다(pydantic vs JSON.stringify)."""
    out: list[str] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: 한쪽에만 있음")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: 길이 py={len(a)} ts={len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                out += diff(x, y, f"{path}[{i}]")
    elif isinstance(a, bool) != isinstance(b, bool):
        out.append(f"{path}: py={a!r} ts={b!r}")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a != b:
            out.append(f"{path}: py={a!r} ts={b!r}")
    elif a != b:
        out.append(f"{path}: py={a!r} ts={b!r}")
    return out


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    # 비교 때문에 지표 테이블을 더럽히지 않는다.
    os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
    os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"

    import eval_retrieval_metrics as E
    from app.services.retrieval_metrics import mrr, ndcg_at_k, recall_at_k

    limit_n = None
    if "--limit" in sys.argv:
        limit_n = int(sys.argv[sys.argv.index("--limit") + 1])
    golden_path = Path(
        sys.argv[sys.argv.index("--goldenset") + 1]
    ) if "--goldenset" in sys.argv else ROOT / "evals" / "golden_v1.csv"
    # 운영과 같은 제공자로 잰다 (.env 에는 없어서 기본이 hf 가 된다).
    provider = sys.argv[sys.argv.index("--provider") + 1] if "--provider" in sys.argv \
        else "deepinfra"
    os.environ["JETRAG_EMBED_PROVIDER"] = provider

    user_id = os.environ.get("OWNER_USER_ID")
    if not user_id:
        from supabase import create_client
        c = create_client(
            os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        )
        rows = (
            c.table("documents").select("user_id").is_("deleted_at", "null")
            .limit(1).execute().data
        )
        if not rows:
            raise SystemExit("문서를 가진 사용자를 못 찾았다.")
        user_id = rows[0]["user_id"]
    E._SEARCH_USER_ID = user_id

    golden = E._load_golden(golden_path)
    if limit_n:
        golden = golden[:limit_n]
    # 기준선과 같은 조건 — per-query doc_id 를 쓴다 (러너의 v0.5+ 분기).
    per_query_doc = bool(golden and golden[0].get("doc_id"))
    print(f"골든셋 {golden_path.name} — {len(golden)}행 / 사용자 {user_id}")
    print(f"doc_id: {'per-query' if per_query_doc else 'sonata 단일'}")
    print(f"임베딩 제공자: {os.environ.get('JETRAG_EMBED_PROVIDER')}")

    env = {k: os.environ[k] for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
    for k in ("HF_API_TOKEN", "DEEPINFRA_API_TOKEN", "JETRAG_EMBED_PROVIDER"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    env["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"

    total_diff = 0
    for mode, limit in (("doc-scope", 50), ("multi-doc", 10)):
        print()
        print(f"=== {mode} (limit={limit}) ===")
        cases = []
        py_resps = []
        doc_ids: list[str | None] = []
        for rec in golden:
            q = unicodedata.normalize("NFC", rec["query"])
            # doc-scope 은 기준선과 같이 per-query doc_id 를 쓴다.
            did = rec.get("doc_id") if mode == "doc-scope" and per_query_doc else None
            doc_ids.append(did)
            qs = {"q": q, "limit": str(limit)}
            if did:
                qs["doc_id"] = did
            cases.append(qs)
            # Python 을 먼저 — 응답도 얻고 embed_query_cache 도 채운다.
            try:
                py_resps.append(E._call_search(q, did, limit=limit))
            except Exception as exc:  # noqa: BLE001
                py_resps.append({"error": str(exc)})

        ts_resps = run_deno({"cases": cases, "user_id": user_id, "env": env})

        mismatched = 0
        for rec, pv, tv in zip(golden, py_resps, ts_resps):
            if "error" in pv or "error" in tv:
                if pv.get("error") != tv.get("error"):
                    mismatched += 1
                    print(f"  [{rec['id']}] 오류 불일치 py={pv.get('error')} ts={tv.get('error')}")
                continue
            d = diff(
                {k: v for k, v in pv.items() if k != "took_ms"},
                {k: v for k, v in tv.items() if k != "took_ms"},
            )
            if d:
                mismatched += 1
                print(f"  [{rec['id']}] MISMATCH ({len(d)}건) {rec['query'][:30]!r}")
                for line in d[:4]:
                    print(f"      {line}")
        total_diff += mismatched
        print(f"  응답 동일성 — {len(golden)}행 중 불일치 **{mismatched}건**")

        # 지표는 플랜 §9 기준대로 양쪽에서 계산해 나란히 찍는다.
        def metrics(resps):
            rows = []
            for rec, did, resp in zip(golden, doc_ids, resps):
                if "error" in resp:
                    continue
                relevant = rec["relevant_chunks"]
                acceptable = rec.get("acceptable_chunks", set())
                if did:
                    pred = E._extract_predicted_chunk_idxs(resp, did)
                else:
                    # multi-doc 은 expected doc 의 노출 청크로 본다 (러너와 같은 정의).
                    pred = []
                    for item in resp.get("items", []):
                        if item.get("doc_id") == rec.get("doc_id"):
                            pred = [mc["chunk_idx"] for mc in item.get("matched_chunks", [])]
                            break
                rows.append((
                    recall_at_k(pred, relevant, k=10, acceptable_chunks=acceptable),
                    mrr(pred, relevant, k=10, acceptable_chunks=acceptable),
                    ndcg_at_k(pred, relevant, k=10, acceptable_chunks=acceptable),
                ))
            if not rows:
                return (0.0, 0.0, 0.0, 0)
            n = len(rows)
            return (
                sum(r[0] for r in rows) / n,
                sum(r[1] for r in rows) / n,
                sum(r[2] for r in rows) / n,
                n,
            )

        pr, pm, pn, pc = metrics(py_resps)
        tr, tm, tn, tc = metrics(ts_resps)
        print(f"  {'':<10}{'Recall@10':>11}{'MRR':>10}{'nDCG@10':>10}{'행':>6}")
        print(f"  {'Railway':<10}{pr:>11.4f}{pm:>10.4f}{pn:>10.4f}{pc:>6}")
        print(f"  {'Edge':<10}{tr:>11.4f}{tm:>10.4f}{tn:>10.4f}{tc:>6}")
        print(f"  {'차이':<10}{tr - pr:>11.4f}{tm - pm:>10.4f}{tn - pn:>10.4f}")
        if (pr, pm, pn, pc) != (tr, tm, tn, tc):
            total_diff += 1
            print("  → 지표가 다르다.")

    print()
    print("FAIL 0" if total_diff == 0 else f"FAIL {total_diff}")
    return 1 if total_diff else 0


if __name__ == "__main__":
    raise SystemExit(main())
