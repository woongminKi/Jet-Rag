"""KPI #11 인제스트 SLO 달성률 집계 (기획서 §10.11 6항목).

PRD `2026-05-12 검색 정확도 80% 달성 PRD.md` §1.5 #11 가 명시한 W-N — 본 스크립트.

SLO (기획서 §10.11)
    1. 수신 응답 < 2초 (인제스트 접수 = 비동기, 측정 X)
    2. 텍스트 PDF 50p < 60초
    3. 이미지 1장 < 15초
    4. 이미지 많은 PDF (20+p) < 3분
    5. HWP 구포맷 50p < 90초
    6. URL < 30초

계산 모델
    - 각 completed job 의 total duration = SUM(ingest_logs.duration_ms WHERE status IN ('succeeded','skipped'))
    - doc_type + page bucket 으로 SLO 매칭:
        pdf  : page ≤ 20 → 60s SLO (텍스트 PDF 가정) / page > 20 → 180s SLO (이미지 많은 PDF)
        hwp/hwpx: 90s SLO
        image: 15s SLO (페이지 1)
        url  : 30s SLO
        그 외 (docx/pptx/etc): 60s 보수적 — 정식 SLO 부재, 진단용
    - 페이지 수 = SELECT MAX(page) FROM chunks WHERE doc_id = X (NULL 시 1 가정)
    - 달성률 = SLO 만족 job / 전체 completed job × 100%

산출
    - stdout: 마크다운 테이블 (doc_type별 + overall)
    - --out path: 마크다운 파일 저장 (선택)
    - --out-json path: raw json 저장 (선택)

stdlib + supabase-py. 외부 의존성 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# SLO 정의 (기획서 §10.11 6항목 + 누락 doc_type 보수 default)
# ---------------------------------------------------------------------------

_SLO_MS_PDF_TEXT = 60_000      # ≤20p
_SLO_MS_PDF_IMAGE = 180_000    # >20p
_SLO_MS_HWP = 90_000           # hwp/hwpx
_SLO_MS_IMAGE = 15_000         # image
_SLO_MS_URL = 30_000           # url
_SLO_MS_OTHER = 60_000         # docx/pptx 등 — 보수, "기타 포맷" 으로 보고

_PDF_TEXT_PAGE_THRESHOLD = 20


def _resolve_slo_ms(doc_type: str, page_count: int) -> tuple[int, str]:
    """doc_type + page_count → (SLO ms, SLO 라벨)."""
    if doc_type == "pdf":
        if page_count <= _PDF_TEXT_PAGE_THRESHOLD:
            return _SLO_MS_PDF_TEXT, "텍스트 PDF (≤20p, 60s)"
        return _SLO_MS_PDF_IMAGE, "이미지 많은 PDF (>20p, 180s)"
    if doc_type in ("hwp", "hwpx"):
        return _SLO_MS_HWP, f"{doc_type.upper()} (90s)"
    if doc_type == "image":
        return _SLO_MS_IMAGE, "이미지 1장 (15s)"
    if doc_type == "url":
        return _SLO_MS_URL, "URL (30s)"
    return _SLO_MS_OTHER, f"기타({doc_type}) 보수 60s"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class JobMetric:
    job_id: str
    doc_id: str
    doc_type: str
    page_count: int
    duration_ms: int
    slo_ms: int
    slo_label: str
    satisfied: bool


@dataclass
class GroupSummary:
    label: str
    n_total: int = 0
    n_satisfied: int = 0
    avg_duration_ms: float = 0.0
    p95_duration_ms: float = 0.0

    @property
    def rate(self) -> float:
        return self.n_satisfied / self.n_total if self.n_total > 0 else 0.0


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

def _fetch_completed_jobs(client) -> list[dict]:
    resp = (
        client.table("ingest_jobs")
        .select("id, doc_id, status, started_at, finished_at")
        .eq("status", "completed")
        .execute()
    )
    return resp.data or []


def _fetch_documents(client, doc_ids: list[str]) -> dict[str, dict]:
    if not doc_ids:
        return {}
    resp = (
        client.table("documents")
        .select("id, doc_type")
        .in_("id", doc_ids)
        .execute()
    )
    return {row["id"]: row for row in (resp.data or [])}


def _fetch_log_durations(client, job_ids: list[str]) -> dict[str, int]:
    """job_id → SUM(duration_ms) of stages with status in ('succeeded','skipped')."""
    if not job_ids:
        return {}
    resp = (
        client.table("ingest_logs")
        .select("job_id, status, duration_ms")
        .in_("job_id", job_ids)
        .in_("status", ["succeeded", "skipped"])
        .execute()
    )
    sums: dict[str, int] = {}
    for row in resp.data or []:
        jid = row.get("job_id")
        dur = row.get("duration_ms")
        if jid is None or not isinstance(dur, int):
            continue
        sums[jid] = sums.get(jid, 0) + dur
    return sums


def _fetch_max_pages(client, doc_ids: list[str]) -> dict[str, int]:
    """doc_id → MAX(page) from chunks (NULL → 1)."""
    if not doc_ids:
        return {}
    resp = (
        client.table("chunks")
        .select("doc_id, page")
        .in_("doc_id", doc_ids)
        .execute()
    )
    max_pages: dict[str, int] = {}
    for row in resp.data or []:
        did = row.get("doc_id")
        page = row.get("page")
        if did is None:
            continue
        if isinstance(page, int) and page > max_pages.get(did, 0):
            max_pages[did] = page
    return max_pages


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _build_metrics(
    jobs: list[dict],
    docs: dict[str, dict],
    durations: dict[str, int],
    max_pages: dict[str, int],
) -> list[JobMetric]:
    metrics: list[JobMetric] = []
    for j in jobs:
        jid = j["id"]
        did = j["doc_id"]
        doc_row = docs.get(did)
        if doc_row is None:
            continue
        doc_type = (doc_row.get("doc_type") or "").lower()
        # page_count: chunks 의 MAX(page) 또는 1 (URL / 이미지 / chunks 가 page NULL).
        page_count = max_pages.get(did, 0) or 1
        duration_ms = durations.get(jid, 0)
        slo_ms, slo_label = _resolve_slo_ms(doc_type, page_count)
        satisfied = duration_ms > 0 and duration_ms <= slo_ms
        metrics.append(
            JobMetric(
                job_id=jid, doc_id=did, doc_type=doc_type,
                page_count=page_count, duration_ms=duration_ms,
                slo_ms=slo_ms, slo_label=slo_label,
                satisfied=satisfied,
            )
        )
    return metrics


def _summarize(label: str, metrics: list[JobMetric]) -> GroupSummary:
    s = GroupSummary(label=label)
    if not metrics:
        return s
    s.n_total = len(metrics)
    s.n_satisfied = sum(1 for m in metrics if m.satisfied)
    durations = sorted(m.duration_ms for m in metrics)
    s.avg_duration_ms = sum(durations) / s.n_total
    # P95
    idx95 = max(0, int(0.95 * (s.n_total - 1)))
    s.p95_duration_ms = float(durations[idx95])
    return s


def _group_by_doc_type(metrics: list[JobMetric]) -> dict[str, list[JobMetric]]:
    out: dict[str, list[JobMetric]] = {}
    for m in metrics:
        out.setdefault(m.doc_type or "(unknown)", []).append(m)
    return out


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def _render_markdown(
    *,
    metrics: list[JobMetric],
    overall: GroupSummary,
    by_doc_type: list[GroupSummary],
    generated_at: str,
) -> str:
    lines: list[str] = []
    lines.append("# KPI #11 인제스트 SLO 달성률 — 보고서")
    lines.append("")
    lines.append(f"- 생성: {generated_at}")
    lines.append(f"- 기준: 기획서 §10.11 6 SLO 항목 + 보수 default (docx/pptx 60s)")
    lines.append(f"- 측정 대상: `ingest_jobs.status='completed'` 의 모든 job")
    lines.append(
        "- duration_ms = `ingest_logs.duration_ms` 합산 "
        "(status IN ('succeeded','skipped'))"
    )
    lines.append(
        f"- 게이트: 달성률 ≥ 90% (기획서 §13.1 #11)"
    )
    lines.append("")
    lines.append("## Overall")
    lines.append("")
    status = "✅ 통과" if overall.rate >= 0.90 else "❌ 미달"
    lines.append(
        f"- **달성률: {overall.n_satisfied}/{overall.n_total} = "
        f"{overall.rate * 100:.1f}% {status}**"
    )
    lines.append(
        f"- avg duration: {overall.avg_duration_ms / 1000:.1f}s / "
        f"P95: {overall.p95_duration_ms / 1000:.1f}s"
    )
    lines.append("")
    lines.append("## by doc_type")
    lines.append("")
    lines.append("| doc_type | n total | n satisfied | rate | avg (s) | P95 (s) |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for s in by_doc_type:
        lines.append(
            f"| {s.label} | {s.n_total} | {s.n_satisfied} | "
            f"{s.rate * 100:.1f}% | {s.avg_duration_ms / 1000:.1f} | "
            f"{s.p95_duration_ms / 1000:.1f} |"
        )
    lines.append("")
    lines.append("## SLO 위반 job (상위 10건)")
    lines.append("")
    violators = sorted(
        (m for m in metrics if not m.satisfied),
        key=lambda m: m.duration_ms / max(m.slo_ms, 1), reverse=True,
    )[:10]
    if violators:
        lines.append("| doc_type | pages | duration (s) | SLO (s) | 초과 비율 |")
        lines.append("|---|---:|---:|---:|---:|")
        for m in violators:
            ratio = m.duration_ms / m.slo_ms if m.slo_ms > 0 else 0.0
            lines.append(
                f"| {m.doc_type} | {m.page_count} | "
                f"{m.duration_ms / 1000:.1f} | {m.slo_ms / 1000:.0f} | "
                f"{ratio:.2f}x |"
            )
    else:
        lines.append("(SLO 위반 0건)")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="KPI #11 인제스트 SLO 달성률 (기획서 §10.11 6항목)"
    )
    p.add_argument("--out", default=None, help="md 결과 출력 경로 (옵션)")
    p.add_argument("--out-json", default=None, help="raw json 출력 경로 (옵션)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # api/ 가 sys.path 에 있어야 app.db import 가능 (run from api/ 또는 repo root).
    here = Path(__file__).resolve()
    api_dir = here.parents[1] / "api"
    if api_dir.exists():
        sys.path.insert(0, str(api_dir))

    try:
        from app.db import get_supabase_client
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] app.db import 실패: {exc}", file=sys.stderr)
        return 1

    client = get_supabase_client()

    jobs = _fetch_completed_jobs(client)
    if not jobs:
        print("[INFO] completed job 0건 — 측정 데이터 없음", file=sys.stderr)
        return 0

    doc_ids = sorted({j["doc_id"] for j in jobs if j.get("doc_id")})
    job_ids = sorted({j["id"] for j in jobs})

    docs = _fetch_documents(client, doc_ids)
    durations = _fetch_log_durations(client, job_ids)
    max_pages = _fetch_max_pages(client, doc_ids)

    metrics = _build_metrics(jobs, docs, durations, max_pages)
    overall = _summarize("overall", metrics)
    groups = _group_by_doc_type(metrics)
    by_doc_type = [_summarize(dt, ms) for dt, ms in sorted(groups.items())]

    generated_at = datetime.now(timezone.utc).isoformat()
    md = _render_markdown(
        metrics=metrics,
        overall=overall,
        by_doc_type=by_doc_type,
        generated_at=generated_at,
    )
    print(md)

    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"[OK] markdown: {args.out}", file=sys.stderr)

    if args.out_json:
        raw = {
            "generated_at": generated_at,
            "overall": {
                "n_total": overall.n_total,
                "n_satisfied": overall.n_satisfied,
                "rate": overall.rate,
                "avg_duration_ms": overall.avg_duration_ms,
                "p95_duration_ms": overall.p95_duration_ms,
            },
            "by_doc_type": [
                {
                    "doc_type": s.label,
                    "n_total": s.n_total,
                    "n_satisfied": s.n_satisfied,
                    "rate": s.rate,
                    "avg_duration_ms": s.avg_duration_ms,
                    "p95_duration_ms": s.p95_duration_ms,
                }
                for s in by_doc_type
            ],
            "metrics": [
                {
                    "job_id": m.job_id, "doc_id": m.doc_id,
                    "doc_type": m.doc_type, "page_count": m.page_count,
                    "duration_ms": m.duration_ms, "slo_ms": m.slo_ms,
                    "slo_label": m.slo_label, "satisfied": m.satisfied,
                }
                for m in metrics
            ],
        }
        Path(args.out_json).write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[OK] json: {args.out_json}", file=sys.stderr)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
