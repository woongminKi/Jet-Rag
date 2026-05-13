"""M2 W-4 — 13 doc 전체 클린 재인제스트 오케스트레이터.

PRD v1.2 M2 단계의 W-4 작업: chunk augment + 동의어 사전 정합을 위해 13 doc 을
순차 reingest 한다. 그룹 A (작은 비-vision 8개) → 그룹 B (vision cached 3개) →
그룹 C (이한주포폴 + sample-report) 순서로 5분 cooldown + checkpoint mini eval.

설계 원칙
- API 경유 (POST /documents/{id}/reingest) — Supabase 직접 호출 X (이미 운영 중인
  파이프라인을 그대로 사용). 단 사전·사후 audit 는 supabase python client 로
  chunks count / dense_vec NULL 직접 조회 (HTTP 응답에 포함 안 됨).
- 그룹 사이 5분 cooldown — HF 503/Gemini quota 회복 + checkpoint eval 여유.
- 게이트 즉시 미달 시 abort + runlog 저장 (graceful 종료).
- ENV `JETRAG_W4_VISION_MODE` = "cached_only" (default) / "skip".
  * cached_only — `JETRAG_PDF_VISION_ENRICH=true` 가정. cache hit page 만 사실상
    호출 0, miss page 는 fresh Gemini Vision 발생 (현재 vision_cache 코드는
    "cache miss 시 자동 fresh call" — 별도 cache_only 모드가 없음).
    13 doc 사전 audit (work-log 메모) 기준 fresh call ≈ 23 page ≈ ~$0.02.
  * skip — `JETRAG_PDF_VISION_ENRICH=false`. 모든 vision 호출 OFF. vision chunks
    감소로 정확도 영향 가능 — fallback 옵션.
- ENV `JETRAG_W4_DRY_RUN=true` — API 호출 없이 sequencing/timing 만 출력.
- `--group A|B|C|all` (default all) — 부분 재개용.
- `--skip-checkpoint` — checkpoint eval 스킵 (디버깅).

산출
- `evals/results/m2_w4_runlog_<ts>.json` — 시작/종료, 각 doc job_id·duration·chunks delta
- `evals/results/m2_w4_checkpoint_<group>_<ts>.{md,json}` — 그룹별 checkpoint eval
- `evals/results/m2_w4_final_<ts>.{md,json}` — 그룹 C 종료 후 본 측정 full eval

사용
    # 본 PC 에서 API 서버 띄운 상태로
    cd api && .venv/bin/python scripts/m2_w4_full_reingest.py --group all
    # dry-run
    .venv/bin/python scripts/m2_w4_full_reingest.py --group all --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# api/ 를 import path 에 추가 — app.db 직접 사용 (사전 audit).
_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))
_REPO_ROOT = _API_ROOT.parent

logger = logging.getLogger("m2_w4")


# ---------------------------------------------------------------------------
# 13 doc 그룹 정의 (RUNBOOK 그대로)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocRef:
    doc_id: str
    name: str
    expected_chunks: int  # baseline chunks count — sanity check 용


GROUP_A: tuple[DocRef, ...] = (
    DocRef("80b48fa9-fe83-4fef-af31-af116494183d", "law sample2", 30),
    DocRef("0ed5a024-e076-4893-a766-90b5d689f372", "law sample3", 26),
    DocRef("2e083cb0-05c3-4910-8345-2658c46fb7ed", "운영 내규", 56),
    DocRef("75c74e4c-aa09-4ca5-9d36-7ba9af4d5bf5", "승인글 템플릿3", 113),
    DocRef("88920c9e-0961-4811-8f6a-02ca1bf02820", "승인글 템플릿1", 139),
    DocRef("a5f3cfae-5059-41d4-8623-e9b6f23231c2", "직제 규정", 171),
    DocRef("430e9fbe-3ebc-4c8e-a592-a94034b2ac0f", "브랜딩 pptx", 5),
    DocRef("51dd2cc0-4105-43c4-9585-6026a012a9cc", "기웅민 이력서", 103),
)
GROUP_B: tuple[DocRef, ...] = (
    DocRef("2303ef36-7502-407a-8a88-3c0dc63c8464", "보건의료 빅데이터", 175),
    DocRef("92c38315-659e-42f4-9043-8cec1d62823a", "sonata catalog", 129),
    DocRef("b218e8a1-cb35-4540-b969-f0f4fac517fa", "데이터센터 안내서", 443),
)
GROUP_C: tuple[DocRef, ...] = (
    DocRef("9878d7bd-4766-40fa-bebb-7da45f879768", "이한주 포트폴리오", 79),
    DocRef("d1259dfe-c402-4cd0-bb04-3e67d88a2773", "sample-report", 1000),
)

ALL_GROUPS: dict[str, tuple[DocRef, ...]] = {
    "A": GROUP_A,
    "B": GROUP_B,
    "C": GROUP_C,
}


# ---------------------------------------------------------------------------
# 상수 — RUNBOOK §timing / SLO
# ---------------------------------------------------------------------------

_API_BASE_URL = os.environ.get("JETRAG_API_BASE_URL", "http://localhost:8000").rstrip("/")
_HEALTH_WAIT_MAX_SEC = 30  # /health 200 받을 때까지 대기 한도
_HEALTH_POLL_SEC = 2

_POLL_INTERVAL_SEC = 5  # job status polling 간격
_POLL_MAX_SEC = 20 * 60  # 일반 doc 폴링 한도
_POLL_MAX_SEC_SAMPLE_REPORT = 30 * 60  # sample-report 한정
_DOC_GAP_SEC = 2  # doc 사이 쉼

_COOLDOWN_SEC = 5 * 60  # 그룹 사이 cooldown
_HF_WARMUP_QUERY = "테스트"  # /search 더미 호출

# 게이트 — checkpoint / final 즉시 미달 판정.
_GATE_CHUNKS_TOLERANCE_PCT = 5.0  # 전체 chunks 변동 ±5% 이내
_GATE_CROSS_DOC_R10_MAX_DROP = 0.10  # baseline 대비 -10% 이상 하락 시 abort

# 폴링 timeout (httpx) — reingest endpoint 는 즉시 202 라 짧게.
_HTTP_TIMEOUT_SEC = 30.0

# baseline cross_doc R@10 — M0-c (76c607a) 결과. M1 진행 시 갱신.
# 추후 PRD M2 W-3 측정 후 실측치로 갱신 가능.
_BASELINE_CROSS_DOC_R10 = 0.4424

# 환경변수 — 실행 모드 / dry-run.
_VISION_MODE = os.environ.get("JETRAG_W4_VISION_MODE", "cached_only").strip().lower()
_DRY_RUN = os.environ.get("JETRAG_W4_DRY_RUN", "false").strip().lower() == "true"


# ---------------------------------------------------------------------------
# Runlog — 전체 실행 기록
# ---------------------------------------------------------------------------


@dataclass
class DocRunlog:
    doc_id: str
    name: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_sec: float | None = None
    job_id: str | None = None
    final_status: str | None = None
    chunks_before: int | None = None
    chunks_after: int | None = None
    null_dense_vec_after: int | None = None
    error: str | None = None


@dataclass
class GroupRunlog:
    group: str  # A / B / C
    started_at: str | None = None
    finished_at: str | None = None
    docs: list[DocRunlog] = field(default_factory=list)
    checkpoint_overall_r10: float | None = None
    checkpoint_cross_doc_r10: float | None = None
    aborted: bool = False
    abort_reason: str | None = None


@dataclass
class Runlog:
    started_at: str
    finished_at: str | None = None
    vision_mode: str = ""
    dry_run: bool = False
    api_base_url: str = ""
    audit_before: dict[str, int] = field(default_factory=dict)  # doc_id → chunks count
    groups: list[GroupRunlog] = field(default_factory=list)
    final_overall_r10: float | None = None
    final_cross_doc_r10: float | None = None
    aborted: bool = False
    abort_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "vision_mode": self.vision_mode,
            "dry_run": self.dry_run,
            "api_base_url": self.api_base_url,
            "audit_before": self.audit_before,
            "groups": [
                {
                    "group": g.group,
                    "started_at": g.started_at,
                    "finished_at": g.finished_at,
                    "checkpoint_overall_r10": g.checkpoint_overall_r10,
                    "checkpoint_cross_doc_r10": g.checkpoint_cross_doc_r10,
                    "aborted": g.aborted,
                    "abort_reason": g.abort_reason,
                    "docs": [
                        {
                            "doc_id": d.doc_id,
                            "name": d.name,
                            "started_at": d.started_at,
                            "finished_at": d.finished_at,
                            "duration_sec": d.duration_sec,
                            "job_id": d.job_id,
                            "final_status": d.final_status,
                            "chunks_before": d.chunks_before,
                            "chunks_after": d.chunks_after,
                            "null_dense_vec_after": d.null_dense_vec_after,
                            "error": d.error,
                        }
                        for d in g.docs
                    ],
                }
                for g in self.groups
            ],
            "final_overall_r10": self.final_overall_r10,
            "final_cross_doc_r10": self.final_cross_doc_r10,
            "aborted": self.aborted,
            "abort_reason": self.abort_reason,
        }


# 전역 runlog — signal handler 가 ctrl+c 시 즉시 저장하도록 노출.
_runlog: Runlog | None = None
_runlog_path: Path | None = None


def _save_runlog() -> None:
    if _runlog is None or _runlog_path is None:
        return
    _runlog.finished_at = _now_iso()
    _runlog_path.write_text(
        json.dumps(_runlog.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("runlog 저장 → %s", _runlog_path)


def _sigint_handler(signum: int, frame: Any) -> None:  # noqa: ARG001
    logger.warning("SIGINT 수신 — runlog 저장 후 종료")
    if _runlog is not None:
        _runlog.aborted = True
        _runlog.abort_reason = "SIGINT"
    _save_runlog()
    sys.exit(130)


# ---------------------------------------------------------------------------
# Supabase audit — chunks count / NULL dense_vec
# ---------------------------------------------------------------------------


def _audit_chunks(doc_id: str | None = None) -> dict[str, int]:
    """chunks count + NULL dense_vec count. doc_id 미지정 시 전체 카운트.

    반환 keys = `{"chunks": int, "null_dense_vec": int}`.
    """
    from app.db import get_supabase_client

    c = get_supabase_client()
    base = c.table("chunks").select("id", count="exact")
    if doc_id:
        base = base.eq("doc_id", doc_id)
    total = (base.limit(1).execute()).count or 0

    null_base = c.table("chunks").select("id", count="exact").is_("dense_vec", "null")
    if doc_id:
        null_base = null_base.eq("doc_id", doc_id)
    null_n = (null_base.limit(1).execute()).count or 0

    return {"chunks": total, "null_dense_vec": null_n}


def _audit_all_docs(docs: Iterable[DocRef]) -> dict[str, int]:
    """13 doc 별 chunks count map."""
    out: dict[str, int] = {}
    for d in docs:
        result = _audit_chunks(d.doc_id)
        out[d.doc_id] = result["chunks"]
    return out


# ---------------------------------------------------------------------------
# HTTP — API 클라이언트
# ---------------------------------------------------------------------------


def _wait_for_api() -> None:
    """`/health` 200 받을 때까지 대기. 한도 초과 시 abort."""
    if _DRY_RUN:
        logger.info("[dry-run] /health 체크 skip")
        return
    deadline = time.monotonic() + _HEALTH_WAIT_MAX_SEC
    last_err: str | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{_API_BASE_URL}/health", timeout=5.0)
            if r.status_code == 200:
                logger.info("/health OK — API 서버 준비됨 (%s)", _API_BASE_URL)
                return
            last_err = f"status={r.status_code}"
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
        time.sleep(_HEALTH_POLL_SEC)
    raise RuntimeError(
        f"/health 대기 한도 ({_HEALTH_WAIT_MAX_SEC}s) 초과 — API 서버를 켜주세요. "
        f"(last_err={last_err}, url={_API_BASE_URL})"
    )


def _hf_warmup() -> None:
    """HF BGE-M3 cold-start 완화 — /search 더미 1회. 그룹 B/C 시작 전 호출."""
    if _DRY_RUN:
        logger.info("[dry-run] HF warmup skip")
        return
    try:
        r = httpx.get(
            f"{_API_BASE_URL}/search",
            params={"q": _HF_WARMUP_QUERY, "limit": 1},
            timeout=30.0,
        )
        logger.info("HF warmup /search → status=%d", r.status_code)
    except Exception as exc:  # noqa: BLE001 — graceful
        logger.warning("HF warmup 실패 (graceful): %s", exc)


def _post_reingest(doc_id: str) -> dict[str, Any]:
    """POST /documents/{id}/reingest?mode=default → 202 응답 dict."""
    if _DRY_RUN:
        return {"job_id": f"dryrun-{doc_id[:8]}", "chunks_deleted": 0}
    r = httpx.post(
        f"{_API_BASE_URL}/documents/{doc_id}/reingest",
        params={"mode": "default"},
        timeout=_HTTP_TIMEOUT_SEC,
    )
    if r.status_code != 202:
        raise RuntimeError(
            f"reingest 실패 doc={doc_id} status={r.status_code} body={r.text[:200]}"
        )
    return r.json()


def _poll_job(doc_id: str, *, max_sec: int) -> str:
    """`GET /documents/{id}/status` 폴링. 최종 status 반환.

    completed → "completed", failed → "failed", timeout → raise.
    """
    if _DRY_RUN:
        return "completed"
    deadline = time.monotonic() + max_sec
    while time.monotonic() < deadline:
        try:
            r = httpx.get(
                f"{_API_BASE_URL}/documents/{doc_id}/status",
                timeout=_HTTP_TIMEOUT_SEC,
            )
            if r.status_code == 200:
                payload = r.json()
                job = payload.get("job") or {}
                status_val = job.get("status")
                stage = job.get("current_stage")
                if status_val in ("completed", "failed"):
                    return status_val
                logger.info(
                    "doc=%s job=%s status=%s stage=%s",
                    doc_id[:8], (job.get("job_id") or "?")[:8], status_val, stage,
                )
            else:
                logger.warning(
                    "doc=%s status endpoint %d body=%s",
                    doc_id[:8], r.status_code, r.text[:120],
                )
        except Exception as exc:  # noqa: BLE001 — polling graceful
            logger.warning("doc=%s polling 실패 (재시도): %s", doc_id[:8], exc)
        time.sleep(_POLL_INTERVAL_SEC)
    raise RuntimeError(f"polling timeout ({max_sec}s) — doc={doc_id}")


# ---------------------------------------------------------------------------
# Checkpoint eval — run_s4_a_d4_breakdown_eval.py 호출
# ---------------------------------------------------------------------------


def _run_checkpoint_eval(out_label: str) -> tuple[float | None, float | None]:
    """`run_s4_a_d4_breakdown_eval.py` 호출 → overall R@10 + cross_doc R@10 반환.

    out_label = "<group>_<ts>" — md/json 파일명 prefix. eval 실패 시 (None, None).
    """
    if _DRY_RUN:
        logger.info("[dry-run] checkpoint eval (%s) skip", out_label)
        return None, None

    eval_script = _REPO_ROOT / "evals" / "run_s4_a_d4_breakdown_eval.py"
    if not eval_script.exists():
        logger.warning("eval 스크립트 없음 — checkpoint skip: %s", eval_script)
        return None, None

    out_md = _REPO_ROOT / "evals" / "results" / f"m2_w4_checkpoint_{out_label}.md"
    out_json = _REPO_ROOT / "evals" / "results" / f"m2_w4_checkpoint_{out_label}.json"

    cmd = [
        sys.executable,
        str(eval_script),
        "--out-md", str(out_md),
        "--out-json", str(out_json),
    ]
    logger.info("checkpoint eval 실행: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            cwd=_API_ROOT,
            capture_output=True,
            text=True,
            timeout=15 * 60,
            env={**os.environ, "JETRAG_RERANKER_ENABLED": "false", "JETRAG_MMR_DISABLE": "1"},
        )
    except subprocess.TimeoutExpired:
        logger.error("checkpoint eval timeout — skip")
        return None, None
    except Exception as exc:  # noqa: BLE001
        logger.error("checkpoint eval 예외: %s", exc)
        return None, None

    if result.returncode != 0:
        logger.error(
            "checkpoint eval exit=%d stderr=%s",
            result.returncode, result.stderr[-500:],
        )
        return None, None

    # 결과 JSON 에서 overall.r10 / cross_doc.r10 추출.
    try:
        payload = json.loads(out_json.read_text(encoding="utf-8"))
        overall = payload.get("overall") or {}
        cross = (payload.get("by_qtype") or {}).get("cross_doc") or {}
        return (
            float(overall.get("recall_at_k", 0.0)) if overall else None,
            float(cross.get("recall_at_k", 0.0)) if cross else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("eval 결과 파싱 실패: %s", exc)
        return None, None


# ---------------------------------------------------------------------------
# Gate — checkpoint / final 즉시 미달 판정
# ---------------------------------------------------------------------------


def _check_gate(
    *,
    group: str,
    chunks_before_total: int,
    chunks_after_total: int,
    null_dense_vec_after: int,
    overall_r10: float | None,
    cross_doc_r10: float | None,
) -> tuple[bool, str | None]:
    """게이트 검사. (passed, reason) 반환. False 시 abort.

    - chunks_after 가 before 대비 ±5% 초과 변동 → 의심 (재인제스트 미반영 가능성)
    - NULL dense_vec > 0 → 임베딩 누락
    - cross_doc R@10 이 baseline 대비 -10% 이상 하락 → 회귀
    """
    delta_pct = abs(chunks_after_total - chunks_before_total) / max(chunks_before_total, 1) * 100
    if delta_pct > _GATE_CHUNKS_TOLERANCE_PCT:
        return False, (
            f"chunks 변동 {delta_pct:.1f}% > {_GATE_CHUNKS_TOLERANCE_PCT}% "
            f"(before={chunks_before_total} after={chunks_after_total})"
        )
    if null_dense_vec_after > 0:
        return False, f"NULL dense_vec={null_dense_vec_after} (>0)"
    if cross_doc_r10 is not None:
        drop = _BASELINE_CROSS_DOC_R10 - cross_doc_r10
        if drop > _GATE_CROSS_DOC_R10_MAX_DROP:
            return False, (
                f"cross_doc R@10 회귀 {cross_doc_r10:.4f} "
                f"(baseline {_BASELINE_CROSS_DOC_R10:.4f}, drop {drop:.4f})"
            )
    return True, None


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------


def _process_group(
    group_name: str,
    docs: tuple[DocRef, ...],
    *,
    skip_checkpoint: bool,
) -> GroupRunlog:
    """단일 그룹 처리. 실패 시 group.aborted=True 로 반환."""
    glog = GroupRunlog(group=group_name)
    glog.started_at = _now_iso()
    assert _runlog is not None
    _runlog.groups.append(glog)

    # 그룹 B/C 시작 전 HF warmup — extract 단계 search 가 cold-start 페널티 안 받게.
    if group_name in ("B", "C"):
        _hf_warmup()

    for doc in docs:
        dlog = DocRunlog(doc_id=doc.doc_id, name=doc.name)
        glog.docs.append(dlog)
        dlog.chunks_before = _audit_chunks(doc.doc_id)["chunks"]
        dlog.started_at = _now_iso()
        t0 = time.monotonic()
        logger.info(
            "[group %s] doc=%s (%s) chunks_before=%d 시작",
            group_name, doc.doc_id[:8], doc.name, dlog.chunks_before or 0,
        )
        try:
            resp = _post_reingest(doc.doc_id)
            dlog.job_id = str(resp.get("job_id"))
            max_sec = (
                _POLL_MAX_SEC_SAMPLE_REPORT
                if doc.doc_id == "d1259dfe-c402-4cd0-bb04-3e67d88a2773"
                else _POLL_MAX_SEC
            )
            final_status = _poll_job(doc.doc_id, max_sec=max_sec)
            dlog.final_status = final_status
            audit_after = _audit_chunks(doc.doc_id)
            dlog.chunks_after = audit_after["chunks"]
            dlog.null_dense_vec_after = audit_after["null_dense_vec"]
        except Exception as exc:  # noqa: BLE001 — 부분 실패 허용
            dlog.error = f"{type(exc).__name__}: {exc}"
            logger.error("doc=%s 실패: %s", doc.doc_id[:8], exc)
            dlog.finished_at = _now_iso()
            dlog.duration_sec = round(time.monotonic() - t0, 2)
            glog.aborted = True
            glog.abort_reason = f"doc {doc.doc_id} 처리 실패: {exc}"
            glog.finished_at = _now_iso()
            return glog

        dlog.finished_at = _now_iso()
        dlog.duration_sec = round(time.monotonic() - t0, 2)
        logger.info(
            "[group %s] doc=%s done status=%s chunks=%d→%d null_dense=%d (%.1fs)",
            group_name, doc.doc_id[:8], dlog.final_status,
            dlog.chunks_before or 0, dlog.chunks_after or 0,
            dlog.null_dense_vec_after or 0, dlog.duration_sec,
        )

        if dlog.final_status != "completed":
            glog.aborted = True
            glog.abort_reason = f"doc {doc.doc_id} final_status={dlog.final_status}"
            glog.finished_at = _now_iso()
            return glog

        if not _DRY_RUN:
            time.sleep(_DOC_GAP_SEC)

    # 그룹 종료 — checkpoint eval.
    glog.finished_at = _now_iso()
    if not skip_checkpoint:
        ts = _now_compact()
        overall_r10, cross_r10 = _run_checkpoint_eval(out_label=f"{group_name}_{ts}")
        glog.checkpoint_overall_r10 = overall_r10
        glog.checkpoint_cross_doc_r10 = cross_r10

        # 게이트 — 전체 DB chunks count 기준.
        total_before = sum(_runlog.audit_before.values())
        all_docs = list(GROUP_A) + list(GROUP_B) + list(GROUP_C)
        audit_after_all = _audit_all_docs(all_docs)
        total_after = sum(audit_after_all.values())
        # NULL dense_vec 전체 DB 카운트 (특정 doc 한정 X).
        full_audit = _audit_chunks()  # doc_id None
        passed, reason = _check_gate(
            group=group_name,
            chunks_before_total=total_before,
            chunks_after_total=total_after,
            null_dense_vec_after=full_audit["null_dense_vec"],
            overall_r10=overall_r10,
            cross_doc_r10=cross_r10,
        )
        if not passed:
            glog.aborted = True
            glog.abort_reason = f"gate 미달: {reason}"
            logger.error("[group %s] gate 미달 — %s", group_name, reason)
    return glog


def main(argv: list[str] | None = None) -> int:
    global _runlog, _runlog_path, _DRY_RUN  # noqa: PLW0603 — signal handler 가 접근

    parser = argparse.ArgumentParser(
        description="M2 W-4 13 doc 전체 클린 재인제스트 (RUNBOOK 정합)"
    )
    parser.add_argument(
        "--group", default="all", choices=["A", "B", "C", "all"],
        help="처리할 그룹 (default all)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="API 호출 없이 sequencing 만 출력 (JETRAG_W4_DRY_RUN 도 가능)",
    )
    parser.add_argument(
        "--skip-checkpoint", action="store_true",
        help="checkpoint eval 스킵 (디버깅용)",
    )
    args = parser.parse_args(argv)

    # logging 부트스트랩 — 기본 INFO + console.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # CLI > ENV — ENV 가 dry-run 일 때도 동작.
    dry_run = args.dry_run or _DRY_RUN
    _DRY_RUN = dry_run

    # signal handler 설치 — ctrl+c 시 runlog 저장.
    signal.signal(signal.SIGINT, _sigint_handler)

    # runlog 초기화 — 시작 시각으로 파일명 고정.
    ts = _now_compact()
    _runlog_path = _REPO_ROOT / "evals" / "results" / f"m2_w4_runlog_{ts}.json"
    _runlog = Runlog(
        started_at=_now_iso(),
        vision_mode=_VISION_MODE,
        dry_run=dry_run,
        api_base_url=_API_BASE_URL,
    )

    logger.info("=" * 60)
    logger.info("M2 W-4 전체 클린 재인제스트")
    logger.info("vision_mode=%s dry_run=%s api=%s", _VISION_MODE, dry_run, _API_BASE_URL)
    logger.info("실행 ENV 권고:")
    logger.info("  JETRAG_SYNONYM_INJECTION_ENABLED=true")
    logger.info("  JETRAG_CAPTION_PREFIX_ENABLED=true")
    if _VISION_MODE == "cached_only":
        logger.info("  JETRAG_PDF_VISION_ENRICH=true  # cache hit 활용")
    else:
        logger.info("  JETRAG_PDF_VISION_ENRICH=false  # vision OFF (fallback)")
    logger.info("  JETRAG_RERANKER_ENABLED=false")
    logger.info("  JETRAG_MMR_DISABLE=1")
    logger.info("=" * 60)

    # 1. /health 대기.
    try:
        _wait_for_api()
    except Exception as exc:  # noqa: BLE001
        logger.error("API 준비 실패: %s", exc)
        _runlog.aborted = True
        _runlog.abort_reason = str(exc)
        _save_runlog()
        return 2

    # 2. 사전 audit (13 doc 전체).
    all_docs = list(GROUP_A) + list(GROUP_B) + list(GROUP_C)
    logger.info("사전 audit — 13 doc chunks count")
    if not dry_run:
        _runlog.audit_before = _audit_all_docs(all_docs)
        for d in all_docs:
            logger.info(
                "  %s (%s) chunks=%d expected=%d",
                d.doc_id[:8], d.name, _runlog.audit_before.get(d.doc_id, 0), d.expected_chunks,
            )
    else:
        _runlog.audit_before = {d.doc_id: d.expected_chunks for d in all_docs}
        logger.info("  [dry-run] 사전 audit 생략 (expected_chunks 사용)")

    # 3. 그룹 처리.
    groups_to_run = ["A", "B", "C"] if args.group == "all" else [args.group]
    for idx, gname in enumerate(groups_to_run):
        if idx > 0 and not dry_run:
            logger.info("cooldown %ds 대기 (다음 그룹: %s)", _COOLDOWN_SEC, gname)
            time.sleep(_COOLDOWN_SEC)
        elif idx > 0 and dry_run:
            logger.info("[dry-run] cooldown %ds skip (다음 그룹: %s)", _COOLDOWN_SEC, gname)

        docs = ALL_GROUPS[gname]
        logger.info("=" * 60)
        logger.info("그룹 %s — %d doc", gname, len(docs))
        for d in docs:
            logger.info("  - %s (%s) expected_chunks=%d", d.doc_id[:8], d.name, d.expected_chunks)
        logger.info("=" * 60)

        glog = _process_group(gname, docs, skip_checkpoint=args.skip_checkpoint)
        if glog.aborted:
            logger.error("그룹 %s aborted: %s", gname, glog.abort_reason)
            _runlog.aborted = True
            _runlog.abort_reason = f"group {gname}: {glog.abort_reason}"
            _save_runlog()
            return 3

    # 4. 그룹 C 종료 후 본 측정 (all 또는 C 한정 실행 시).
    if "C" in groups_to_run and not dry_run and not args.skip_checkpoint:
        logger.info("본 측정 full eval 실행")
        ts2 = _now_compact()
        out_md = _REPO_ROOT / "evals" / "results" / f"m2_w4_final_{ts2}.md"
        out_json = _REPO_ROOT / "evals" / "results" / f"m2_w4_final_{ts2}.json"
        cmd = [
            sys.executable,
            str(_REPO_ROOT / "evals" / "run_s4_a_d4_breakdown_eval.py"),
            "--out-md", str(out_md),
            "--out-json", str(out_json),
        ]
        try:
            result = subprocess.run(
                cmd, cwd=_API_ROOT, capture_output=True, text=True, timeout=15 * 60,
                env={**os.environ, "JETRAG_RERANKER_ENABLED": "false", "JETRAG_MMR_DISABLE": "1"},
            )
            if result.returncode == 0:
                payload = json.loads(out_json.read_text(encoding="utf-8"))
                overall = payload.get("overall") or {}
                cross = (payload.get("by_qtype") or {}).get("cross_doc") or {}
                _runlog.final_overall_r10 = float(overall.get("recall_at_k", 0.0)) if overall else None
                _runlog.final_cross_doc_r10 = float(cross.get("recall_at_k", 0.0)) if cross else None
                logger.info(
                    "본 측정: overall R@10=%.4f cross_doc R@10=%.4f",
                    _runlog.final_overall_r10 or 0.0, _runlog.final_cross_doc_r10 or 0.0,
                )
            else:
                logger.error("본 측정 eval 실패 exit=%d stderr=%s", result.returncode, result.stderr[-500:])
        except Exception as exc:  # noqa: BLE001
            logger.error("본 측정 eval 예외: %s", exc)

    _save_runlog()
    logger.info("=" * 60)
    logger.info("M2 W-4 완료 — runlog=%s", _runlog_path)
    return 0


# ---------------------------------------------------------------------------
# Util
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


if __name__ == "__main__":
    sys.exit(main())
