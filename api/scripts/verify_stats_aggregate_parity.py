"""`/stats` 순수 집계 대조 — **합성 문서**로 실데이터가 못 태우는 분기를 덮는다.

## 왜 따로 필요한가
`verify_stats_parity.py` 는 실제 DB 로 전체 응답을 대조한다. 그런데 운영 데이터가
한쪽으로 쏠려 있어서 집계의 상당 부분이 **한 번도 실행되지 않는다** (2026-09-06 실측,
문서 13 건):

```
failed=0  extract_skipped=0  scan=0  이번달(KST) 생성=0
doc_type = pdf 9 · hwpx 2 · pptx 1 · hwp 1   ← image·url 문서가 없다
```

실제로 음성 대조를 걸어 보니 "실패 문서를 안 뺌", "SLO 버킷에서 실패 문서를 뺌",
"월 경계를 UTC 로" 세 가지가 **한 건도 안 잡혔다.** 데이터가 없어서 못 잡는 걸
"통과" 로 읽으면 안 된다.

## 어떻게 재나
- `_compute_slo_buckets` · `_compute_slo_aggregate` · `_bucket_stats` ·
  `_parse_created_at_kst` 는 모듈 수준 함수라 **그대로 import** 해서 부른다.
- documents 집계는 핸들러 안 인라인이라 본문을 복사하되, `stats.py` 에서 소스를 떠와
  고정본과 대조한다(원본이 고쳐지면 채점기가 먼저 죽는다).

시각은 양쪽에 같은 값을 주입한다 — 안 그러면 자정 경계에서 가끔 실패한다.

사용:
    api/.venv/bin/python api/scripts/verify_stats_aggregate_parity.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
STATS_DIR = os.path.join(ROOT, "supabase", "functions", "_shared", "stats")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

PINNED_DOCS_AGG = '''
    now_kst = datetime.now(KST)
    month_start = now_kst.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_ago = now_kst - timedelta(days=7)
    by_doc_type: dict[str, int] = {}
    by_source_channel: dict[str, int] = {}
    total_size = 0
    extract_skipped = 0
    added_this_month = 0
    added_last_7d = 0
    for d in docs:
        by_doc_type[d["doc_type"]] = by_doc_type.get(d["doc_type"], 0) + 1
        by_source_channel[d["source_channel"]] = (
            by_source_channel.get(d["source_channel"], 0) + 1
        )
        total_size += d["size_bytes"] or 0
        if (d.get("flags") or {}).get("extract_skipped"):
            extract_skipped += 1
        created_at_kst = _parse_created_at_kst(d.get("created_at"))
        if created_at_kst is not None:
            if created_at_kst >= month_start:
                added_this_month += 1
            if created_at_kst >= week_ago:
                added_last_7d += 1
    tag_counter = Counter(tag for d in docs for tag in (d.get("tags") or []))
    popular_tags = [
        TagCount(tag=t, count=c) for t, c in tag_counter.most_common(10)
    ]
'''

def _ms(iso: str) -> int:
    """UTC ISO → epoch ms. **손으로 계산하지 않는다.**

    처음엔 상수를 직접 적었다가 셋 다 엉뚱한 날짜를 가리켰고, 그 바람에 "월 경계를
    UTC 로" 음성 대조가 계속 0 건이었다 — 케이스가 경계를 안 태우는 걸 통과로 읽었다.
    """
    from datetime import datetime

    return int(datetime.fromisoformat(iso).timestamp() * 1000)


# 기준 시각 — 합성 케이스의 날짜를 여기에 맞춰 고정한다.
NOW_MS = _ms("2026-09-06T02:00:00+00:00")  # KST 2026-09-06 11:00


def d(**kw) -> dict:
    """문서 한 건. 원본이 `d["doc_type"]` 로 직접 읽는 키는 기본값을 채운다."""
    row = {
        "doc_type": "pdf",
        "source_channel": "upload",
        "size_bytes": 1000,
        "flags": {},
        "tags": [],
        "created_at": "2026-05-05T00:00:00+00:00",
        "received_ms": None,
    }
    row.update(kw)
    return row


MB = 1024 * 1024

# (이름, 문서 목록, 기준 시각 ms) — 기준 시각을 케이스마다 줄 수 있다.
#
# **월 경계는 `now` 를 골라야 태워진다.** KST 와 UTC 가 같은 달인 시각만 쓰면
# "월 경계를 UTC 로" 같은 결함이 안 잡힌다 — 실제로 처음에 0 건이었다.
# `2026-08-31T15:30:00Z` 는 KST 로 9/1 00:30 이라 두 기준이 **다른 달**이 된다.
NOW_KST_SEP_UTC_AUG = _ms("2026-08-31T15:30:00+00:00")  # KST 2026-09-01 00:30
NOW_KST_AUG_UTC_AUG = _ms("2026-08-31T02:00:00+00:00")  # KST 2026-08-31 11:00

DOC_CASES: list[tuple[str, list[dict]]] = [
    ("빈 목록", []),
    ("실패 문서는 집계에서 빠지고 failed_count 로만", [
        d(), d(flags={"failed": True}), d(flags={"failed": True, "scan": True}),
    ]),
    ("extract_skipped 카운트", [d(flags={"extract_skipped": True}), d()]),
    ("doc_type·source_channel 분포", [
        d(doc_type="pdf", source_channel="upload"),
        d(doc_type="image", source_channel="email"),
        d(doc_type="url", source_channel="url"),
        d(doc_type="hwp", source_channel="upload"),
    ]),
    ("size 합 (None 섞임)", [d(size_bytes=10), d(size_bytes=None), d(size_bytes=5)]),
    # --- 날짜 경계 (KST 기준 2026-09-06 11:00) ---
    ("이번 달 경계 — KST 9/1 00:00 직전·직후", [
        d(created_at="2026-08-31T14:59:59+00:00"),  # KST 8/31 23:59:59 → 미포함
        d(created_at="2026-08-31T15:00:00+00:00"),  # KST 9/1 00:00:00 → 포함
    ]),
    ("최근 7일 경계", [
        d(created_at="2026-08-30T01:59:59+00:00"),  # 7일 + 1초 전 → 미포함
        d(created_at="2026-08-30T02:00:01+00:00"),  # 7일 이내 → 포함
    ]),
    ("created_at 결측·형식오류", [
        d(created_at=None), d(created_at=""), d(created_at="not-a-date"),
        d(created_at="2026-09-06T00:00:00Z"),  # `Z` 표기
    ]),
    # --- 태그 ---
    ("인기 태그 상위 10 + 동수 순서", [
        d(tags=["a", "b"]), d(tags=["a", "c"]), d(tags=["b"]),
        d(tags=[f"t{i}" for i in range(12)]),
    ]),
    ("태그 없음·None", [d(tags=None), d(tags=[])]),
]

# (이름, 문서 목록, 기준 시각) — 시각 자체가 결과를 가르는 케이스.
DOC_TIME_CASES: list[tuple[str, list[dict], int]] = [
    ("월 경계 — KST 는 9월, UTC 는 8월인 시각", [
        d(created_at="2026-08-31T15:00:00+00:00"),  # KST 9/1 00:00 → 이번 달
        d(created_at="2026-08-31T14:59:59+00:00"),  # KST 8/31 23:59:59 → 지난 달
        d(created_at="2026-08-20T00:00:00+00:00"),  # 확실히 지난 달
    ], NOW_KST_SEP_UTC_AUG),
    ("월 경계 — 양쪽 다 8월", [
        d(created_at="2026-08-01T00:00:00+00:00"),
        d(created_at="2026-07-31T14:00:00+00:00"),
    ], NOW_KST_AUG_UTC_AUG),
    ("7일 경계 — 다른 기준 시각", [
        d(created_at="2026-08-24T02:00:01+00:00"),
        d(created_at="2026-08-24T01:59:59+00:00"),
    ], NOW_KST_AUG_UTC_AUG),
]

# (이름, 문서 목록) — SLO 버킷용. received_ms 가 핵심이다.
SLO_CASES: list[tuple[str, list[dict]]] = [
    ("빈 목록", []),
    ("received_ms 없으면 제외", [d(received_ms=None), d(received_ms=100)]),
    ("pdf_scan 은 크기와 무관", [
        d(doc_type="pdf", flags={"scan": True}, size_bytes=10, received_ms=100),
        d(doc_type="pdf", flags={"scan": True}, size_bytes=30 * MB, received_ms=200),
    ]),
    ("pdf_50p 은 25MB 경계", [
        d(doc_type="pdf", size_bytes=25 * MB - 1, received_ms=100),  # 미포함
        d(doc_type="pdf", size_bytes=25 * MB, received_ms=200),      # 포함
    ]),
    ("소형 비스캔 pdf 는 어느 버킷에도 안 들어간다", [
        d(doc_type="pdf", size_bytes=1000, received_ms=100),
    ]),
    ("docx·pptx·txt·md 는 대상 아님", [
        d(doc_type="docx", received_ms=100), d(doc_type="pptx", received_ms=100),
        d(doc_type="txt", received_ms=100), d(doc_type="md", received_ms=100),
    ]),
    ("image·url·hwp·hwpx", [
        d(doc_type="image", received_ms=100), d(doc_type="url", received_ms=200),
        d(doc_type="hwp", received_ms=300), d(doc_type="hwpx", received_ms=400),
    ]),
    ("SLO 통과 경계 2000ms", [
        d(doc_type="image", received_ms=1999), d(doc_type="image", received_ms=2000),
    ]),
    ("실패 문서도 SLO 표본이다", [
        d(doc_type="image", received_ms=100, flags={"failed": True}),
    ]),
    ("여러 버킷 혼합 + 가중 평균", [
        d(doc_type="image", received_ms=100), d(doc_type="image", received_ms=5000),
        d(doc_type="url", received_ms=100),
        d(doc_type="pdf", flags={"scan": True}, received_ms=9000),
    ]),
]

# p95 인덱스·pass_rate 를 직접 흔든다.
BUCKET_SAMPLE_CASES: list[list[int]] = [
    [], [100], [100, 200], [100, 200, 300],
    list(range(1, 21)), list(range(1, 101)),
    [2000] * 5, [1999] * 5, [1999, 2000, 2001],
    [3, 1, 2],  # 정렬이 필요한 경우
    list(range(1, 8)),
]


RUNNER_TS = f"""
import {{
  bucketStats, computeDocumentsStats, computeSloAggregate, computeSloBuckets,
}} from "file://{STATS_DIR}/aggregate.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const now = input.now_ms as number;

// deno-lint-ignore no-explicit-any
const docs = input.doc_cases.map((rows: any[]) => {{
  const r = computeDocumentsStats(rows, now);
  return {{ stats: r.stats, popular_tags: r.popularTags }};
}});

// 기준 시각이 케이스마다 다른 것들.
// deno-lint-ignore no-explicit-any
const docsAtTime = input.doc_time_cases.map((c: any) => {{
  const r = computeDocumentsStats(c.rows, c.now_ms);
  return {{ stats: r.stats, popular_tags: r.popularTags }};
}});

// deno-lint-ignore no-explicit-any
const slo = input.slo_cases.map((rows: any[]) => {{
  const buckets = computeSloBuckets(rows);
  return {{ buckets, aggregate: computeSloAggregate(buckets) }};
}});

const buckets = input.bucket_samples.map((s: number[]) => bucketStats(s));

console.log(JSON.stringify({{ docs, docsAtTime, slo, buckets }}));
"""


def run_deno(payload: dict) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "cases.json"), os.path.join(tmp, "runner.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=300,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:2500]}")
    return json.loads(proc.stdout)


def _norm(text: str) -> list[str]:
    out = []
    for line in text.strip("\n").split("\n"):
        s = re.sub(r"\s+#.*$", "", line).strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def assert_pinned_unchanged() -> None:
    src = open(os.path.join(ROOT, "api", "app", "routers", "stats.py"), encoding="utf-8").read()
    m = re.search(
        r"^    now_kst = datetime\.now\(KST\).*?^    \]$",
        src, re.S | re.M,
    )
    if m is None:
        raise SystemExit("documents 집계 블록을 stats.py 에서 못 찾았다.")
    if _norm(m.group(0)) != _norm(PINNED_DOCS_AGG):
        print("원본 documents 집계가 고정본과 다르다. 실제 소스:")
        print(m.group(0))
        raise SystemExit("채점기의 복사본을 원본에 맞춰 갱신할 것.")


def py_documents(rows: list[dict], now_ms: int) -> dict:
    """`stats.py` 의 documents 집계 복사본 (위에서 고정본과 대조했다)."""
    from collections import Counter
    from datetime import datetime, timedelta, timezone

    from app.routers.stats import KST, _parse_created_at_kst

    failed_docs = [x for x in rows if (x.get("flags") or {}).get("failed")]
    docs = [x for x in rows if not (x.get("flags") or {}).get("failed")]

    now_kst = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).astimezone(KST)
    month_start = now_kst.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_ago = now_kst - timedelta(days=7)

    by_doc_type: dict[str, int] = {}
    by_source_channel: dict[str, int] = {}
    total_size = 0
    extract_skipped = 0
    added_this_month = 0
    added_last_7d = 0
    for x in docs:
        by_doc_type[x["doc_type"]] = by_doc_type.get(x["doc_type"], 0) + 1
        by_source_channel[x["source_channel"]] = (
            by_source_channel.get(x["source_channel"], 0) + 1
        )
        total_size += x["size_bytes"] or 0
        if (x.get("flags") or {}).get("extract_skipped"):
            extract_skipped += 1
        created_at_kst = _parse_created_at_kst(x.get("created_at"))
        if created_at_kst is not None:
            if created_at_kst >= month_start:
                added_this_month += 1
            if created_at_kst >= week_ago:
                added_last_7d += 1

    tag_counter = Counter(t for x in docs for t in (x.get("tags") or []))
    popular = [{"tag": t, "count": c} for t, c in tag_counter.most_common(10)]
    return {
        "stats": {
            "total": len(docs),
            "by_doc_type": by_doc_type,
            "by_source_channel": by_source_channel,
            "extract_skipped": extract_skipped,
            "total_size_bytes": total_size,
            "added_this_month": added_this_month,
            "added_last_7d": added_last_7d,
            "failed_count": len(failed_docs),
        },
        "popular_tags": popular,
    }


def _assert_boundary_times() -> None:
    """기준 시각이 의도한 관계인지 확인한다 — 아니면 경계 케이스가 아무것도 못 태운다."""
    from datetime import datetime, timedelta, timezone

    kst = timezone(timedelta(hours=9))
    u = datetime.fromtimestamp(NOW_KST_SEP_UTC_AUG / 1000, tz=timezone.utc)
    k = u.astimezone(kst)
    if not (u.month == 8 and k.month == 9):
        raise SystemExit(
            f"NOW_KST_SEP_UTC_AUG 가 의도와 다르다 — UTC={u.isoformat()} KST={k.isoformat()}. "
            "KST 는 9월, UTC 는 8월이어야 월 경계 결함이 잡힌다."
        )


def main() -> None:
    assert_pinned_unchanged()
    _assert_boundary_times()
    from app.routers.stats import (
        _bucket_stats,
        _compute_slo_aggregate,
        _compute_slo_buckets,
    )

    ts = run_deno({
        "now_ms": NOW_MS,
        "doc_cases": [rows for _, rows in DOC_CASES],
        "doc_time_cases": [
            {"rows": rows, "now_ms": now} for _, rows, now in DOC_TIME_CASES
        ],
        "slo_cases": [rows for _, rows in SLO_CASES],
        "bucket_samples": BUCKET_SAMPLE_CASES,
    })

    fails = 0

    print("=== documents 집계 ===")
    for (name, rows), tv in zip(DOC_CASES, ts["docs"]):
        pv = py_documents(rows, NOW_MS)
        if pv != tv:
            fails += 1
            print(f"  MISMATCH {name}")
            print(f"      py={pv}")
            print(f"      ts={tv}")
    print(f"  {len(DOC_CASES)}건 대조")

    print()
    print("=== documents 집계 — 기준 시각이 다른 경계 ===")
    for (name, rows, now), tv in zip(DOC_TIME_CASES, ts["docsAtTime"]):
        pv = py_documents(rows, now)
        if pv != tv:
            fails += 1
            print(f"  MISMATCH {name}")
            print(f"      py={pv['stats']}")
            print(f"      ts={tv['stats']}")
    print(f"  {len(DOC_TIME_CASES)}건 대조")

    print()
    print("=== SLO 버킷 + 가중 평균 ===")
    for (name, rows), tv in zip(SLO_CASES, ts["slo"]):
        buckets = _compute_slo_buckets(rows)
        want = {
            "buckets": {k: v.model_dump() for k, v in buckets.items()},
            "aggregate": _compute_slo_aggregate(buckets).model_dump(),
        }
        if want != tv:
            fails += 1
            print(f"  MISMATCH {name}")
            print(f"      py={want}")
            print(f"      ts={tv}")
    print(f"  {len(SLO_CASES)}건 대조")

    print()
    print("=== bucket_stats (p95·pass_rate) ===")
    for samples, tv in zip(BUCKET_SAMPLE_CASES, ts["buckets"]):
        want = _bucket_stats(samples).model_dump()
        if want != tv:
            fails += 1
            print(f"  MISMATCH n={len(samples)}: py={want} ts={tv}")
    print(f"  {len(BUCKET_SAMPLE_CASES)}건 대조")

    total = (len(DOC_CASES) + len(DOC_TIME_CASES) + len(SLO_CASES)
             + len(BUCKET_SAMPLE_CASES))
    print()
    print(f"케이스 {total}건 대조")
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
