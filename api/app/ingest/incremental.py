"""W25 D14 B sprint — incremental vision reingest 파이프라인.

기존 `run_pipeline()` 의 한계:
- chunks 전부 삭제 → 새로 적재 (full reingest)
- random 503 으로 정답 페이지 누락 시 답변 회귀 위험 (Sprint 3 G-S 데이터센터 PDF p.6 회귀)
- 매 시도마다 비용 + 시간 (Sprint 4 fail 시 chunks 0 영구 위험)

본 모듈의 incremental 흐름:
1. 기존 chunks 의 vision 처리 페이지 set 추출 (section_title `(vision) p.N` 매칭)
2. PDF storage download → PyMuPDF open → total_pages
3. 누락 페이지 list = (1..total) - vision 처리 페이지
4. 누락 페이지만 vision 호출 (extract.py 의 `_enrich_pdf_with_vision()` 의 sweep 패턴 재사용
   하되, **누락 페이지 list 만 처리**)
5. 새 vision sections 을 ChunkRecord 로 변환 (chunk_idx max + 1 부터)
6. load stage 호출 (chunks insert, dense_vec NULL)
7. embed stage 호출 — NULL dense_vec 만 BGE-M3 embed (기존 embed.py 변경 0)

특징:
- 기존 chunks 보존 (실패해도 검색 가능 상태 유지)
- vision 호출 = 누락 페이지만 (비용 / 시간 ↓↓)
- chunk_filter / content_gate / tag_summarize / doc_embed / dedup 안 호출 (이미 적용됨)
- 사용자 PDF 자체가 변경된 case 는 부정확 — full reingest 권고
"""

from __future__ import annotations

import logging
import os
from typing import Any

import fitz

from app.adapters.impl.image_parser import ImageParser
from app.adapters.impl.supabase_storage import SupabaseBlobStorage
from app.adapters.parser import ExtractedSection
from app.adapters.vectorstore import ChunkRecord
from app.config import get_settings
from app.db import get_supabase_client
from app.ingest.jobs import fail_job, finish_job, start_job
from app.ingest.stages.embed import run_embed_stage
from app.ingest.stages.load import run_load_stage
from app.services import budget_guard
from app.services.vision_need_score import score_page as _score_page_for_vision

logger = logging.getLogger(__name__)

_VISION_ENRICH_TITLE_PREFIX = "(vision) p."  # extract.py 의 vision section_title 패턴
_SCAN_RENDER_DPI = 150
# 2026-05-09 S2 D3 P1-1 fix — extract.py 의 `_VISION_ENRICH_MAX_SWEEPS` 와 default 통일.
#   같은 ENV 키를 공유하는데 fallback 이 3 vs 2 로 갈리면 ENV 미설정 시 incremental 흐름이
#   cost/latency 1.5배 (master plan §7.3 "default 3 → 2, sweep × retry 곱셈 제거" 정합 위반).
#   회귀 발생 시 ENV `JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS=3` 으로 즉시 회복.
_MAX_SWEEPS = int(os.environ.get("JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS", "2"))
# S0 D4 — extract.py 와 동일 정책. ENV 공유.
_BUDGET_RECHECK_EVERY_N_PAGES = int(
    os.environ.get("JETRAG_BUDGET_RECHECK_EVERY_N_PAGES", "5")
)

_image_parser = ImageParser()


def _page_needs_vision(
    page: fitz.Page,
    *,
    page_num: int,
    file_name: str,
) -> bool:
    """S2 D1 — incremental 흐름의 needs_vision 판정. extract.py 와 동일 정책.

    graceful: 점수 계산 raise 시 needs_vision=True 보수적 fallback (vision 흐름 보존).
    """
    try:
        page_dict = page.get_text("dict")
        rect = page.rect
        area = float(rect.width) * float(rect.height)
        score = _score_page_for_vision(page_dict, page_num=page_num, page_area_pt2=area)
        return bool(score.needs_vision)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "vision_need_score 계산 실패 (graceful, vision 호출 진행): page=%d file=%s err=%s",
            page_num, file_name, exc,
        )
        return True


def _vision_processed_pages(supabase, doc_id: str) -> set[int]:
    """기존 chunks 중 vision 처리된 페이지 set."""
    resp = (
        supabase.table("chunks")
        .select("page,section_title")
        .eq("doc_id", doc_id)
        .execute()
    )
    pages: set[int] = set()
    for r in resp.data or []:
        title = r.get("section_title") or ""
        if title.startswith(_VISION_ENRICH_TITLE_PREFIX) and r.get("page"):
            pages.add(int(r["page"]))
    return pages


def _max_chunk_idx(supabase, doc_id: str) -> int:
    """기존 chunks 의 max chunk_idx (없으면 -1)."""
    resp = (
        supabase.table("chunks")
        .select("chunk_idx")
        .eq("doc_id", doc_id)
        .order("chunk_idx", desc=True)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return int(rows[0]["chunk_idx"]) if rows else -1


def _vision_pages_with_sweep(
    pdf_data: bytes,
    *,
    pages: list[int],  # 1-indexed
    file_name: str,
    image_parser: ImageParser,
    doc_id: str | None = None,
    sha256: str | None = None,
    page_cap_override: int | None = None,
) -> tuple[list[ExtractedSection], list[str], budget_guard.BudgetStatus | None]:
    """누락 페이지 list 만 vision 호출 + sweep. 성공한 페이지의 sections 반환.

    extract.py 의 `_enrich_pdf_with_vision()` 의 sweep 패턴 재사용.

    Phase 1 S0 D2 — sha256 전달 시 ImageParser 가 vision_page_cache lookup → hit 시
    호출 0. incremental 이 누락 페이지만 처리하므로 보통 cache miss 가 default 지만,
    중복 reingest / 동시성 race 시 hit 가능.

    S2 D2 — 반환 tuple 3번째 = page_cap_exceeded_status (도달 시 BudgetStatus,
    아니면 None). caller 가 flags 마킹에 사용. (시그니처 변경 회귀 — 단위 테스트
    test_incremental_vision.py 도 함께 갱신.)
    """
    sections: list[ExtractedSection] = []
    warnings: list[str] = []
    if not pages:
        return sections, warnings, None

    try:
        doc = fitz.open(stream=pdf_data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"incremental_vision: PDF 열기 실패: {exc}")
        return sections, warnings, None

    settings = get_settings()
    budget_pages_since_check = 0
    budget_exceeded_status: budget_guard.BudgetStatus | None = None

    # S2 D1 — vision_need_score 운영 hook 메트릭 누적 (extract.py 와 동일 정책).
    need_score_enabled = settings.vision_need_score_enabled
    skipped_by_need_score: list[int] = []
    called_count = 0

    # S2 D2 — page cap 운영 hook (extract.py 와 동일 정책). called_count 카운터 공유.
    # incremental 은 "누락 페이지만 sweep" — 본 흐름 안에서 page cap 도달은 이전
    # extract 시점부터 누적된 chunks 상태와 무관 (vision call 페이지 수만 cap).
    # 첫 진입 시 called_count=0 부터 시작 — full extract 와 incremental 누적 합산이
    # 필요한 case 는 cost cap (DB SUM) 이 책임 (page cap 은 단일 sweep run 한도).
    # S2 D3 — page_cap_override 가 주어지면 mode 별 cap (extract.py 와 동일 helper).
    from app.ingest.stages.extract import _resolve_page_cap_for_doc
    page_cap = _resolve_page_cap_for_doc(
        override=page_cap_override, settings=settings,
    )
    page_cap_exceeded_status: budget_guard.BudgetStatus | None = None

    try:
        # 1-indexed → 0-indexed for PyMuPDF
        pending = [p - 1 for p in pages if 0 < p <= len(doc)]
        for sweep_idx in range(1, _MAX_SWEEPS + 1):
            if not pending:
                break
            if budget_exceeded_status is not None:
                break
            if page_cap_exceeded_status is not None:
                break  # S2 D2 — page cap 도달 sweep 추가 진입도 차단
            if sweep_idx > 1:
                logger.info(
                    "incremental_vision sweep %d/%d: 누락 %d 재시도 %s (file=%s)",
                    sweep_idx, _MAX_SWEEPS, len(pending),
                    [p + 1 for p in pending], file_name,
                )
            failed: list[int] = []
            for page_idx in pending:
                # S0 D4 — N 페이지마다 cap 재검사. cap 도달 시 sweep 즉시 break.
                if (
                    doc_id
                    and budget_pages_since_check >= _BUDGET_RECHECK_EVERY_N_PAGES
                ):
                    budget_pages_since_check = 0
                    # S0 D5 — sliding 24h 추가.
                    status = budget_guard.check_combined(
                        doc_id=doc_id,
                        doc_cap_usd=settings.doc_budget_usd,
                        daily_cap_usd=settings.daily_budget_usd,
                        sliding_24h_cap_usd=settings.sliding_24h_budget_usd,
                    )
                    if not status.allowed:
                        budget_exceeded_status = status
                        msg = (
                            f"incremental_vision: budget cap 도달 — "
                            f"{status.reason} (남은 페이지 {len(pending)} skip)"
                        )
                        warnings.append(msg)
                        logger.warning("%s (file=%s)", msg, file_name)
                        break
                budget_pages_since_check += 1

                # S2 D2 — page cap 검사 (cost cap 직후, needs_vision 직전).
                # called_count 가 sweep 간 누적 카운터 — needs_vision skip 페이지는
                # 증가 X (사용자 가치 페이지만 cap 차감).
                page_cap_status = budget_guard.check_doc_page_cap(
                    called_pages=called_count, page_cap=page_cap,
                )
                if not page_cap_status.allowed:
                    page_cap_exceeded_status = page_cap_status
                    msg = (
                        f"incremental_vision: page cap 도달 — "
                        f"{page_cap_status.reason} "
                        f"(남은 페이지 {len(pending) - pending.index(page_idx)} skip)"
                    )
                    warnings.append(msg)
                    logger.warning("%s (file=%s)", msg, file_name)
                    break

                try:
                    page = doc[page_idx]

                    # S2 D1 — needs_vision OR rule 검사. False 면 vision 호출 회피.
                    # incremental 은 "누락 페이지 보강" 흐름 — needs_vision False 페이지는
                    # 이미 vision 가치 0 으로 판정된 거니 chunk insert 도 skip (ChunkRecord 0).
                    if need_score_enabled and not _page_needs_vision(
                        page, page_num=page_idx + 1, file_name=file_name,
                    ):
                        if sweep_idx == 1:
                            skipped_by_need_score.append(page_idx + 1)
                        # sweep retry 대상 X (failed 에 안 넣고 continue).
                        continue

                    pix = page.get_pixmap(dpi=_SCAN_RENDER_DPI)
                    png = pix.tobytes("png")
                    called_count += 1
                    page_result = image_parser.parse(
                        png,
                        file_name=f"{file_name}#page{page_idx + 1}.png",
                        source_type="pdf_vision_enrich",
                        doc_id=doc_id,
                        page=page_idx + 1,
                        sha256=sha256,
                    )
                    for sec in page_result.sections:
                        base_title = (sec.section_title or "").strip()
                        enriched_title = (
                            f"{_VISION_ENRICH_TITLE_PREFIX}{page_idx + 1} {base_title}".strip()
                            if base_title
                            else f"{_VISION_ENRICH_TITLE_PREFIX}{page_idx + 1}"
                        )
                        sections.append(
                            ExtractedSection(
                                text=sec.text,
                                page=page_idx + 1,
                                section_title=enriched_title,
                                bbox=None,
                            )
                        )
                    warnings.extend(page_result.warnings)
                except Exception as exc:  # noqa: BLE001
                    failed.append(page_idx)
                    if sweep_idx == _MAX_SWEEPS:
                        warnings.append(
                            f"incremental_vision: page {page_idx + 1} 실패 "
                            f"(sweep {sweep_idx}/{_MAX_SWEEPS} 최종): {exc}"
                        )
                    logger.warning(
                        "incremental_vision page %d 실패 (sweep %d/%d): %s",
                        page_idx + 1, sweep_idx, _MAX_SWEEPS, exc,
                    )
            pending = failed

        if pending:
            warnings.append(
                f"incremental_vision: {_MAX_SWEEPS} sweep 후에도 누락: "
                f"{[p + 1 for p in pending]}"
            )
    finally:
        doc.close()

    # S2 D1 + D2 — vision_need_score + page cap 메트릭 1줄 log (운영 진단용).
    logger.info(
        "incremental_vision: file=%s called=%d skipped_need_score=%d (pages=%s) "
        "need_score_enabled=%s page_cap=%d page_cap_exceeded=%s",
        file_name,
        called_count,
        len(skipped_by_need_score),
        skipped_by_need_score[:20],
        need_score_enabled,
        page_cap,
        page_cap_exceeded_status is not None,
    )

    # S2 D2 — page cap status 를 caller 가 flags 마킹에 활용 가능하도록 반환.
    # `run_incremental_vision_pipeline` 이 sweep 후 마킹 처리. 시그니처 변경 1회 —
    # 단위 테스트 (test_incremental_vision.py) 도 함께 갱신.
    return sections, warnings, page_cap_exceeded_status


def _sections_to_chunks(
    sections: list[ExtractedSection],
    *,
    doc_id: str,
    start_chunk_idx: int,
) -> list[ChunkRecord]:
    """vision sections → ChunkRecord (dense_vec NULL — embed stage 가 채움)."""
    out: list[ChunkRecord] = []
    for offset, sec in enumerate(sections):
        out.append(
            ChunkRecord(
                doc_id=doc_id,
                chunk_idx=start_chunk_idx + offset,
                text=sec.text,
                page=sec.page,
                section_title=sec.section_title,
                bbox=sec.bbox,
                char_range=(0, len(sec.text)),
                metadata={"vision_incremental": True},
            )
        )
    return out


def run_incremental_vision_pipeline(
    job_id: str,
    doc_id: str,
    *,
    page_cap_override: int | None = None,
) -> dict[str, Any]:
    """incremental reingest entrypoint — 기존 chunks 보존 + 누락 페이지만 vision 처리.

    S2 D3 — `page_cap_override` 가 주어지면 mode 별 cap. None 이면
    settings.vision_page_cap_per_doc 사용 (S2 D2 기존 동작).

    Returns dict: { processed_pages, skipped_pages, chunks_inserted, total_pages, warnings }
    """
    try:
        start_job(job_id, stage="extract")

        client = get_supabase_client()
        doc = (
            client.table("documents")
            .select("doc_type,storage_path,sha256")
            .eq("id", doc_id)
            .limit(1)
            .execute()
        )
        if not doc.data:
            raise RuntimeError(f"documents 레코드 없음: {doc_id}")
        doc_row = doc.data[0]
        if doc_row["doc_type"] != "pdf":
            raise RuntimeError(
                f"incremental vision 은 PDF 만 지원 (doc_type={doc_row['doc_type']!r})"
            )

        file_name = os.path.basename(doc_row["storage_path"])
        # Phase 1 S0 D2 — vision_page_cache lookup 키.
        doc_sha256 = doc_row.get("sha256")
        storage = SupabaseBlobStorage(bucket=get_settings().supabase_storage_bucket)
        pdf_data = storage.get(doc_row["storage_path"])

        # 누락 페이지 detect
        with fitz.open(stream=pdf_data, filetype="pdf") as fdoc:
            total_pages = len(fdoc)
        processed = _vision_processed_pages(client, doc_id)
        all_pages = set(range(1, total_pages + 1))
        missing = sorted(all_pages - processed)

        logger.info(
            "incremental_vision: doc=%s total=%d processed=%d missing=%d %s",
            doc_id, total_pages, len(processed), len(missing), missing[:20],
        )

        if not missing:
            logger.info("incremental_vision: 누락 0 — skip (doc=%s)", doc_id)
            finish_job(job_id)
            return {
                "processed_pages": sorted(processed),
                "skipped_pages": [],
                "newly_processed_pages": [],
                "chunks_inserted": 0,
                "total_pages": total_pages,
                "warnings": [],
            }

        # S0 D4/D5 — 사전 cap 검사 (doc + daily + 24h sliding).
        settings = get_settings()
        pre_status = budget_guard.check_combined(
            doc_id=doc_id,
            doc_cap_usd=settings.doc_budget_usd,
            daily_cap_usd=settings.daily_budget_usd,
            sliding_24h_cap_usd=settings.sliding_24h_budget_usd,
        )
        if not pre_status.allowed:
            logger.warning(
                "incremental_vision skip — budget cap (scope=%s, used=$%.4f, cap=$%.4f) doc_id=%s",
                pre_status.scope, pre_status.used_usd, pre_status.cap_usd, doc_id,
            )
            existing_flags = (
                client.table("documents")
                .select("flags")
                .eq("id", doc_id)
                .limit(1)
                .execute()
                .data[0].get("flags") or {}
            )
            updated_flags = dict(existing_flags)
            updated_flags["vision_budget_exceeded"] = True
            updated_flags["vision_budget"] = {
                "scope": pre_status.scope,
                "used_usd": round(pre_status.used_usd, 6),
                "cap_usd": round(pre_status.cap_usd, 6),
                "reason": pre_status.reason,
            }
            client.table("documents").update({"flags": updated_flags}).eq(
                "id", doc_id
            ).execute()
            finish_job(job_id)
            return {
                "processed_pages": sorted(processed),
                "skipped_pages": missing,
                "newly_processed_pages": [],
                "chunks_inserted": 0,
                "total_pages": total_pages,
                "warnings": [pre_status.reason],
            }

        # 누락 페이지 vision 호출 (sweep)
        # S2 D2 — 반환 tuple 3번째 = page_cap_exceeded_status (도달 시 BudgetStatus).
        # S2 D3 — page_cap_override 전달 (mode 별 cap).
        sections, warnings, page_cap_status = _vision_pages_with_sweep(
            pdf_data,
            pages=missing,
            file_name=file_name,
            image_parser=_image_parser,
            doc_id=doc_id,
            sha256=doc_sha256,
            page_cap_override=page_cap_override,
        )

        # ChunkRecord 변환
        start_idx = _max_chunk_idx(client, doc_id) + 1
        chunks = _sections_to_chunks(
            sections, doc_id=doc_id, start_chunk_idx=start_idx
        )
        loaded = run_load_stage(job_id, chunks=chunks) if chunks else 0
        embedded = run_embed_stage(job_id, doc_id=doc_id) if loaded > 0 else 0

        # S0 D4/D5 — sweep 도중 cap 도달했으면 flags 마킹 (graceful: 이미 처리한 페이지는 보존).
        post_status = budget_guard.check_combined(
            doc_id=doc_id,
            doc_cap_usd=settings.doc_budget_usd,
            daily_cap_usd=settings.daily_budget_usd,
            sliding_24h_cap_usd=settings.sliding_24h_budget_usd,
        )
        if not post_status.allowed:
            existing_flags = (
                client.table("documents")
                .select("flags")
                .eq("id", doc_id)
                .limit(1)
                .execute()
                .data[0].get("flags") or {}
            )
            updated_flags = dict(existing_flags)
            updated_flags["vision_budget_exceeded"] = True
            updated_flags["vision_budget"] = {
                "scope": post_status.scope,
                "used_usd": round(post_status.used_usd, 6),
                "cap_usd": round(post_status.cap_usd, 6),
                "reason": post_status.reason,
            }
            client.table("documents").update({"flags": updated_flags}).eq(
                "id", doc_id
            ).execute()

        # S2 D2 — page cap 도달 시 flags 마킹 (cost cap 와 직교). graceful — DB 실패해도
        # incremental 결과는 정상 반환. extract.py 의 _mark_page_cap_exceeded_flag 와
        # 같은 payload 구조 (UI 가 양쪽 흐름 공통 처리 가능).
        if page_cap_status is not None:
            try:
                existing_flags = (
                    client.table("documents")
                    .select("flags")
                    .eq("id", doc_id)
                    .limit(1)
                    .execute()
                    .data[0].get("flags") or {}
                )
                updated_flags = dict(existing_flags)
                updated_flags["vision_page_cap_exceeded"] = True
                updated_flags["vision_page_cap"] = {
                    "called_pages": int(page_cap_status.used_usd),
                    "page_cap": int(page_cap_status.cap_usd),
                    "reason": page_cap_status.reason,
                }
                client.table("documents").update({"flags": updated_flags}).eq(
                    "id", doc_id
                ).execute()
            except Exception as exc:  # noqa: BLE001 — graceful
                logger.warning(
                    "incremental_vision page_cap flags 마킹 실패 (graceful): %s "
                    "(doc_id=%s)", exc, doc_id,
                )

        # 다시 누락 detect (sweep 후 여전히 실패한 페이지)
        new_processed = _vision_processed_pages(client, doc_id)
        newly = sorted(new_processed - processed)
        still_missing = sorted(set(missing) - new_processed)

        logger.info(
            "incremental_vision done: doc=%s newly=%d still_missing=%d loaded=%d embedded=%d",
            doc_id, len(newly), len(still_missing), loaded, embedded,
        )

        finish_job(job_id)
        return {
            "processed_pages": sorted(new_processed),
            "skipped_pages": still_missing,
            "newly_processed_pages": newly,
            "chunks_inserted": loaded,
            "embedded": embedded,
            "total_pages": total_pages,
            "warnings": warnings,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "incremental_vision pipeline failed: job=%s doc=%s", job_id, doc_id
        )
        try:
            fail_job(job_id, error_msg=str(exc))
        except Exception:
            logger.exception("fail_job 실패")
        raise
