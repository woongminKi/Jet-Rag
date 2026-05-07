"""Extract 스테이지 — 포맷별 원본 파일 추출 (기획서 §10.2 [4] · §10.3).

지원 포맷 (W2 + 후속 누적)
- PDF: `PyMuPDFParser` — 블록 단위 섹션·bbox·페이지 (스캔본 감지 시 ImageParser 재라우팅)
- HWPX: `HwpxParser` — section 단위 단락 (Day 3, §3.C)
- 이미지: `ImageParser` (Vision composition) — PNG/JPEG/HEIC (Day 3, §3.D)
- URL: `UrlParser` — trafilatura 본문 추출 (Day 4, §3.E)
- HWP 5.x: `Hwp5Parser` — pyhwp `hwp5txt` CLI + olefile fallback (Day 4 §3.F + DE-52)
- HWPML: `HwpmlParser` — 법제처/한컴 옛 XML 직렬화. doc_type='hwp' 그대로 두고
  raw bytes prefix sniff 로 dispatcher 가 분기 (DE-39 패턴)
- 그 외(docx/pptx/txt/md): **graceful skip** — `flags.extract_skipped=true` 마킹.
  W3 에 DOCX/PPTX 어댑터 도입 시 `/documents/{id}/reingest` 로 재처리.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

import fitz  # PyMuPDF — 스캔 PDF rerouting 시 페이지를 PNG 로 렌더

from app.adapters.factory import get_vision_captioner
from app.adapters.impl.docx_parser import DocxParser
from app.adapters.impl.hwp_parser import Hwp5Parser
from app.adapters.impl.hwpml_parser import HwpmlParser, is_hwpml_bytes
from app.adapters.impl.hwpx_parser import HwpxParser
from app.adapters.impl.image_parser import ImageParser
from app.adapters.impl.pptx_parser import PptxParser
from app.adapters.impl.pymupdf_parser import PyMuPDFParser
from app.adapters.impl.supabase_storage import SupabaseBlobStorage
from app.adapters.impl.url_parser import UrlParser
from app.adapters.parser import DocumentParser, ExtractedSection, ExtractionResult
from app.config import get_settings
from app.db import get_supabase_client
from app.ingest.jobs import (
    clear_stage_progress,
    skip_stage,
    stage,
    update_stage_progress,
)
from app.services import budget_guard, vision_cache
from app.services.vision_need_score import score_page as _score_page_for_vision

logger = logging.getLogger(__name__)

_STAGE = "extract"

# 가벼운 파서들은 module-level 단일 인스턴스 — 외부 호출/네트워크 0 이라 안전.
_pdf_parser = PyMuPDFParser()
_hwpx_parser = HwpxParser()
_url_parser = UrlParser()
_hwp_parser = Hwp5Parser()
_hwpml_parser = HwpmlParser()
_docx_parser = DocxParser()


# Phase 1 S0 D1 보강 (P1-1) — Vision 의존 파서들은 lazy 인스턴스화.
# 이전 구현은 module-level 에서 `get_vision_captioner("image_parse")` 를 즉시
# 호출 → ENV 가 invalid (예: JETRAG_LLM_PROVIDER=invalid) 거나 OpenAI 어댑터
# NotImplementedError 분기에 닿는 경우 module import 자체가 실패해 API 서버
# startup 까지 폭주했다. 첫 사용 시점 (`run_extract_stage` 진입) 까지 지연하면
# 비-vision 코드 경로 (단위 테스트, 헬스체크) 는 영향 0.
@lru_cache(maxsize=1)
def _get_image_parser() -> ImageParser:
    return ImageParser(captioner=get_vision_captioner("image_parse"))


@lru_cache(maxsize=1)
def _get_pptx_parser() -> PptxParser:
    # PPTX Vision OCR rerouting (W8 Day 2) — 텍스트 0 슬라이드의 가장 큰 Picture 를
    # ImageParser 에 위임. max 5 슬라이드 cap (Gemini Flash RPD 20 제약).
    return PptxParser(image_parser=_get_image_parser())


@lru_cache(maxsize=1)
def _get_parsers_by_doc_type() -> dict[str, DocumentParser]:
    """doc_type → DocumentParser 디스패처. lazy — vision-의존 파서 hydrate 포함.

    W5 DE-67 — DOCX 추가. W7 후속 — DE-68 PPTX ship (사용자 자료 업로드 시점).
    """
    return {
        "pdf": _pdf_parser,
        "hwpx": _hwpx_parser,
        "image": _get_image_parser(),
        "url": _url_parser,
        "hwp": _hwp_parser,
        "docx": _docx_parser,
        "pptx": _get_pptx_parser(),
    }

# 스캔 PDF 감지 임계값 — PyMuPDFParser raw_text 가 이 이하면 텍스트 레이어 부재로 간주
# (DE-36, W2 Day 3). 사용자 정의 가능하게 추후 분리 가능
_SCAN_TEXT_THRESHOLD = 50

# 스캔 PDF rerouting 시 처리할 max 페이지 수 — Vision API 비용·시간 cap
# (DE-36, 5페이지 ≈ Vision API 호출 50초). 그 이상은 warning + skip
_MAX_SCAN_PAGES = 5

# 스캔 페이지 → PNG 렌더 DPI (PyMuPDF 기본 72 → 150 으로 OCR 품질 확보)
_SCAN_RENDER_DPI = 150

# W25 D14 — 일반 PDF 의 표/그림/다이어그램 정보 보강용 vision enrich.
# 무료 quota (RPD 20) 한계로 default false. paid tier 전환 시 운영자가 ENV 로 opt-in.
# - true 이면 _is_scan_pdf 와 무관하게 모든 PDF 페이지에 vision 호출 → ocr_text/structured/caption
#   을 추가 sections 로 병합 (PyMuPDF 결과 보존 + 보강).
_PDF_VISION_ENRICH_ENABLED = (
    os.environ.get("JETRAG_PDF_VISION_ENRICH", "false").strip().lower() == "true"
)
# enrich 모드 페이지 cap (paid tier 환경에서도 RPM/latency 보호).
_VISION_ENRICH_MAX_PAGES = int(os.environ.get("JETRAG_PDF_VISION_ENRICH_MAX_PAGES", "50"))
# W25 D14 Sprint 4 — sweep 로직: 503 random 실패 페이지 자동 재시도.
# 1차 pass 후 누락 페이지만 2차 sweep. 한 reingest 안에서 누락 ↓↓.
# 정상 환경에선 1차에서 모두 성공 → sweep 즉시 종료 (latency 영향 0).
# 2026-05-06 D2-C — master plan §7.3 정합: default 3 → 2.
#   sweep × retry 곱셈 제거 (sweep 2 × retry 1 = worst case 페이지당 2 호출).
#   회귀 발생 시 ENV `JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS=3` 으로 즉시 회복.
_VISION_ENRICH_MAX_SWEEPS = int(os.environ.get("JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS", "2"))

# 2026-05-07 S0 D4 — vision 비용 cap 가드 재검사 간격 (페이지 단위).
# 사전 1회 + 매 N 페이지마다 재검사로 한 doc 안에서 누적 cap 도달 차단.
# default 5 — 50p/doc 시 11회 SQL (latency 0.x초). N=1 (페이지마다) 은 50 SQL → SLO 위반 위험.
# ENV 로 운영자가 조정 가능.
_BUDGET_RECHECK_EVERY_N_PAGES = int(
    os.environ.get("JETRAG_BUDGET_RECHECK_EVERY_N_PAGES", "5")
)


def run_extract_stage(job_id: str, doc_id: str) -> ExtractionResult | None:
    """스테이지 실행. 지원 포맷이면 `ExtractionResult`, 그 외는 skip 후 `None`.

    스테이지 로그 갱신·flags 마킹까지 내부에서 처리한다. 호출자(pipeline)는 반환값이 None 이면
    다음 스테이지를 건너뛰고 job 을 completed 로 마감하면 된다.
    """
    client = get_supabase_client()
    doc = _fetch_document(client, doc_id)
    doc_type = doc["doc_type"]

    parser = _get_parsers_by_doc_type().get(doc_type)
    if parser is None:
        _mark_unsupported_format(client, doc_id, doc_type=doc_type, flags=doc.get("flags") or {})
        skip_stage(
            job_id,
            stage=_STAGE,
            reason=f"{doc_type} 포맷은 아직 지원되지 않습니다 (후속 어댑터 도입 예정).",
        )
        return None

    file_name = os.path.basename(doc["storage_path"])
    storage = SupabaseBlobStorage(bucket=get_settings().supabase_storage_bucket)

    with stage(job_id, _STAGE):
        data = storage.get(doc["storage_path"])

        # HWP 변형 분기 — doc_type='hwp' 가 OLE2 (Hwp5Parser) 와 HWPML XML
        # (HwpmlParser) 둘 다 받음. raw bytes prefix 로 결정 (DE-39 패턴).
        if doc_type == "hwp" and is_hwpml_bytes(data[:4096]):
            logger.info(
                "HWPML(XML) 감지 → HwpmlParser 사용 (file=%s, doc_id=%s)",
                file_name,
                doc_id,
            )
            parser = _hwpml_parser

        result = parser.parse(data, file_name=file_name)

        # 스캔 PDF 재라우팅 (§3.A′) — PyMuPDF 가 텍스트 추출에 실패한 케이스
        if doc_type == "pdf" and _is_scan_pdf(result):
            logger.info(
                "스캔 PDF 감지 (raw_text=%d자, threshold=%d) → ImageParser fallback. doc_id=%s",
                len(result.raw_text.strip()),
                _SCAN_TEXT_THRESHOLD,
                doc_id,
            )
            result = _reroute_pdf_to_image(
                data,
                file_name=file_name,
                image_parser=_get_image_parser(),
                doc_id=doc_id,
            )
            _mark_scan_flag(client, doc_id, existing_flags=doc.get("flags") or {})

        # W25 D14 — 일반 PDF (텍스트 PDF) 도 vision enrich 활성 시 표/그림 보강.
        # 스캔 PDF 가 아니라야 의미 (스캔 PDF 는 이미 vision 처리됨).
        elif (
            doc_type == "pdf"
            and _PDF_VISION_ENRICH_ENABLED
            and not (doc.get("flags") or {}).get("scan")
        ):
            # S0 D4 — vision 호출 진입 직전 cap 사전 검사. 통과 시 enrich, 실패 시 skip + flags.
            # S0 D5 — 24h sliding window 추가 (calendar-day daily 자정 우회 방어).
            settings = get_settings()
            pre_status = budget_guard.check_combined(
                doc_id=doc_id,
                doc_cap_usd=settings.doc_budget_usd,
                daily_cap_usd=settings.daily_budget_usd,
                sliding_24h_cap_usd=settings.sliding_24h_budget_usd,
            )
            if not pre_status.allowed:
                logger.warning(
                    "PDF vision enrich skip — budget cap (scope=%s, used=$%.4f, cap=$%.4f) doc_id=%s",
                    pre_status.scope, pre_status.used_usd, pre_status.cap_usd, doc_id,
                )
                _mark_budget_exceeded_flag(
                    client,
                    doc_id,
                    existing_flags=doc.get("flags") or {},
                    status=pre_status,
                )
            else:
                logger.info(
                    "PDF vision enrich 활성 — 모든 페이지 vision 호출 후 sections 병합. doc_id=%s",
                    doc_id,
                )
                result = _enrich_pdf_with_vision(
                    data,
                    base_result=result,
                    file_name=file_name,
                    image_parser=_get_image_parser(),
                    job_id=job_id,
                    doc_id=doc_id,
                    sha256=doc.get("sha256"),
                )

    return result


# ---------------------- internals ----------------------


def _fetch_document(client: Any, doc_id: str) -> dict:
    # Phase 1 S0 D2 — vision_page_cache lookup 키로 sha256 사용 → SELECT 에 포함.
    resp = (
        client.table("documents")
        .select("doc_type, storage_path, flags, sha256")
        .eq("id", doc_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise RuntimeError(f"documents 레코드를 찾을 수 없습니다: {doc_id}")
    return resp.data[0]


def _mark_unsupported_format(
    client: Any, doc_id: str, *, doc_type: str, flags: dict,
) -> None:
    updated = dict(flags)
    updated["extract_skipped"] = True
    updated["extract_skipped_reason"] = (
        f"doc_type={doc_type} 는 아직 지원되지 않는 포맷입니다 (W2 예정)."
    )
    client.table("documents").update({"flags": updated}).eq("id", doc_id).execute()


def _is_scan_pdf(result: ExtractionResult) -> bool:
    """raw_text 가 너무 빈약 → 텍스트 레이어 없는 스캔 PDF 로 판정."""
    return len(result.raw_text.strip()) <= _SCAN_TEXT_THRESHOLD


def _mark_scan_flag(client: Any, doc_id: str, *, existing_flags: dict) -> None:
    """`flags.scan = true` 마킹. doc_type 은 'pdf' 그대로 (DB CHECK 제약 준수)."""
    updated = dict(existing_flags)
    updated["scan"] = True
    client.table("documents").update({"flags": updated}).eq("id", doc_id).execute()


def _mark_budget_exceeded_flag(
    client: Any,
    doc_id: str,
    *,
    existing_flags: dict,
    status: budget_guard.BudgetStatus,
) -> None:
    """S0 D4 — vision 비용 cap 도달 시 flags 마킹.

    UI 가 `vision_budget_exceeded=true` 로 카드에 안내 + 재처리 버튼 표시.
    `vision_budget` 객체에 scope/used/cap 까지 함께 저장 → 사용자가 어느 한도가
    문제인지 즉시 인지 가능 (master plan §11.5 사용자 통제권 정합).

    기존 flags 보존 (`{**existing, ...}`) — scan/has_pii 등 다른 시그널 유지.
    """
    updated = dict(existing_flags)
    updated["vision_budget_exceeded"] = True
    updated["vision_budget"] = {
        "scope": status.scope,
        "used_usd": round(status.used_usd, 6),
        "cap_usd": round(status.cap_usd, 6),
        "reason": status.reason,
    }
    client.table("documents").update({"flags": updated}).eq("id", doc_id).execute()


def _mark_page_cap_exceeded_flag(
    client: Any,
    doc_id: str,
    *,
    existing_flags: dict,
    status: budget_guard.BudgetStatus,
) -> None:
    """S2 D2 — vision 페이지 cap 도달 시 flags 마킹.

    cost cap (S0 D4) 의 `vision_budget_exceeded` 와 직교한 신규 flag —
    같은 doc 안에서 두 cap 이 동시에 도달 가능 (page cap 먼저 도달 후
    나중에 cost cap 도 도달). 분리 저장으로 사용자에게 어느 한도가 문제였는지
    명확히 표시. UI 후속 sprint (S2 D3) 가 활용.

    기존 flags 보존 — scan/has_pii/vision_budget_exceeded 등 다른 시그널 유지.
    """
    updated = dict(existing_flags)
    updated["vision_page_cap_exceeded"] = True
    updated["vision_page_cap"] = {
        "called_pages": int(status.used_usd),  # used_usd 에 called_pages 저장 (page_cap scope)
        "page_cap": int(status.cap_usd),
        "reason": status.reason,
    }
    client.table("documents").update({"flags": updated}).eq("id", doc_id).execute()


def _reroute_pdf_to_image(
    data: bytes,
    *,
    file_name: str,
    image_parser: ImageParser,
    doc_id: str | None = None,
) -> ExtractionResult:
    """스캔 PDF 의 각 페이지를 PNG 로 렌더 → ImageParser.parse() 호출.

    멀티페이지는 페이지별 sections 을 누적. Vision API 비용 cap 으로 max _MAX_SCAN_PAGES.
    페이지 단위 부분 실패 허용 (warnings).

    명세 §3.A′ — doc_type='pdf' 유지 + flags.scan=true (DB CHECK 위반 회피).
    """
    sections: list[ExtractedSection] = []
    raw_parts: list[str] = []
    warnings: list[str] = []

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise RuntimeError(
            f"스캔 PDF rerouting: PDF 열기 실패: {file_name}: {exc}"
        ) from exc

    try:
        total_pages = len(doc)
        process_count = min(total_pages, _MAX_SCAN_PAGES)
        if total_pages > _MAX_SCAN_PAGES:
            msg = (
                f"스캔 PDF {total_pages}페이지 중 첫 {_MAX_SCAN_PAGES}페이지만 처리 "
                "(Vision API 비용 cap)"
            )
            warnings.append(msg)
            logger.warning("%s (file=%s)", msg, file_name)

        for page_num in range(process_count):
            try:
                page = doc[page_num]
                pix = page.get_pixmap(dpi=_SCAN_RENDER_DPI)
                png_bytes = pix.tobytes("png")
                page_result = image_parser.parse(
                    png_bytes,
                    file_name=f"{file_name}#page{page_num + 1}.png",
                    source_type="pdf_scan",  # W16 Day 4 #90 — vision_usage_log 명시
                    doc_id=doc_id,
                    page=page_num + 1,
                )
                for sec in page_result.sections:
                    base_title = sec.section_title or ""
                    sections.append(
                        ExtractedSection(
                            text=sec.text,
                            page=page_num + 1,
                            section_title=(
                                f"p.{page_num + 1} {base_title}".strip()
                                if base_title
                                else f"p.{page_num + 1}"
                            ),
                            bbox=None,
                        )
                    )
                if page_result.raw_text:
                    raw_parts.append(page_result.raw_text)
                warnings.extend(page_result.warnings)
            except Exception as exc:  # noqa: BLE001 — 페이지 단위 부분 실패 허용
                msg = f"page {page_num + 1} 스캔 fallback 실패: {exc}"
                warnings.append(msg)
                logger.warning("%s (file=%s)", msg, file_name)
    finally:
        doc.close()

    return ExtractionResult(
        source_type="pdf",  # 본질은 PDF, flags.scan=true 로 구분
        sections=sections,
        raw_text="\n\n".join(raw_parts),
        warnings=warnings,
    )


def _page_needs_vision(
    page: fitz.Page,
    *,
    page_num: int,
    file_name: str,
) -> bool:
    """S2 D1 — fitz.Page → vision_need_score 의 OR rule 평가.

    내부적으로 `page.get_text("dict")` + `page.rect` 로 점수 계산. 점수 시스템이 raise
    하더라도 needs_vision=True 보수적 fallback 으로 vision 호출 흐름 보존 (회귀 방어).

    page.rect 면적 ≤ 0 (이상치) 시 density 신호 미발화 — vision_need_score 모듈이
    page_area_pt2 > 0 가드로 처리. 안전하게 OR rule 통과 가능.
    """
    try:
        page_dict = page.get_text("dict")
        rect = page.rect
        area = float(rect.width) * float(rect.height)
        score = _score_page_for_vision(page_dict, page_num=page_num, page_area_pt2=area)
        return bool(score.needs_vision)
    except Exception as exc:  # noqa: BLE001 — 점수 깨져도 vision 흐름 보존
        logger.warning(
            "vision_need_score 계산 실패 (graceful, vision 호출 진행): page=%d file=%s err=%s",
            page_num, file_name, exc,
        )
        return True


def _enrich_pdf_with_vision(
    data: bytes,
    *,
    base_result: ExtractionResult,
    file_name: str,
    image_parser: ImageParser,
    job_id: str | None = None,
    doc_id: str | None = None,
    sha256: str | None = None,
    client: Any | None = None,
) -> ExtractionResult:
    """W25 D14 — 일반 PDF 의 표/그림/다이어그램 정보를 vision 으로 보강.

    motivation:
        PyMuPDF parser 가 (1) PDF 안 이미지 블록 (type=1) 을 if 문으로 무시 → 그림 정보 0
        (2) 표는 raw text 로 cell 순서 뒤섞이고 일부 누락. 사용자가 보고한 데이터센터
        안내서 PDF 에서 p.4 표 잘림 + p.6 그림 누락 이슈 (W25 D14 진단) 직접 fix.

    설계:
        - PyMuPDF 결과 (sections, raw_text, warnings) 보존
        - 페이지별 PNG 렌더 (DPI 150) → ImageParser.parse() 호출 (Gemini Vision)
        - 각 페이지의 vision 결과를 **추가 sections** 로 append (PyMuPDF section 과 병합 X)
        - section_title 에 "(vision) p.N" 명시 — 검색 결과 출처 식별 가능
        - cap _VISION_ENRICH_MAX_PAGES (default 50) — 대형 PDF 안전장치

    Phase 1 S0 D2 — `vision_page_cache` lookup:
        - 호출 직전 (sha256, page, prompt_version) 키로 캐시 조회
        - hit → ImageParser.parse() 호출 0, 캐시된 VisionCaption 으로 sections 합성
        - miss → 기존 vision API 호출 + caption.usage.estimated_cost 와 함께 cache upsert
        - sha256 None (legacy / 단위 테스트) 시 캐시 skip → 기존 동작 100% 보존

    한계 (W25 D14 권고 단계 인정):
        - vision 호출 = paid tier quota 사용 (~$0.00075/페이지)
        - 인제스트 latency ↑ (페이지당 1~3초)
        - vision 의 ocr_text 가 PyMuPDF text 와 일부 중복 (chunk_filter dedup 룰이 처리)
    """
    sections: list[ExtractedSection] = list(base_result.sections)
    raw_parts: list[str] = [base_result.raw_text] if base_result.raw_text else []
    warnings: list[str] = list(base_result.warnings)

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 — PyMuPDF 결과는 이미 있으니 graceful
        warnings.append(f"vision_enrich: PDF 열기 실패: {exc}")
        return ExtractionResult(
            source_type=base_result.source_type,
            sections=sections,
            raw_text="\n\n".join(raw_parts),
            warnings=warnings,
        )

    try:
        total_pages = len(doc)
        process_count = min(total_pages, _VISION_ENRICH_MAX_PAGES)
        if total_pages > _VISION_ENRICH_MAX_PAGES:
            msg = (
                f"vision_enrich: {total_pages}페이지 중 첫 {_VISION_ENRICH_MAX_PAGES}페이지만 "
                "처리 (paid tier RPM/latency 보호)"
            )
            warnings.append(msg)
            logger.warning("%s (file=%s)", msg, file_name)

        # W25 D14 — 페이지 단위 진행 표시 (job_id 있을 때만, indicator 실시간 업데이트).
        completed_pages: set[int] = set()
        if job_id:
            update_stage_progress(
                job_id, current=0, total=process_count, unit="pages",
            )

        # S0 D4 — 페이지 루프 내 cap 재검사 준비. cap 도달 시 즉시 break + flags 마킹.
        settings = get_settings()
        budget_pages_since_check = 0
        budget_exceeded_status: budget_guard.BudgetStatus | None = None

        # S2 D1 (2026-05-08) — vision_need_score 운영 hook 메트릭 누적. master plan §6 S2 D1.
        # OR rule needs_vision False 페이지는 vision 호출 회피 → 비용·latency 절감.
        # ENV `JETRAG_VISION_NEED_SCORE_ENABLED=false` 시 모든 페이지 호출 (회복 토글).
        need_score_enabled = settings.vision_need_score_enabled
        skipped_by_need_score: list[int] = []
        called_count = 0

        # S2 D2 (2026-05-08) — page cap 운영 hook. master plan §6 S2 D2.
        # `called_count` 가 sweep 간 누적 in-memory 카운터 — 본 카운터를 page cap
        # 검사에 그대로 사용 (needs_vision skip 페이지는 increment X — 사용자 가치
        # 페이지만 cap 차감, cap 도달 지연 정합). cost cap 과 직교 — 둘 중 먼저
        # 닿는 지점 stop. ENV `JETRAG_VISION_PAGE_CAP_PER_DOC=0` 시 무한 (회복 토글).
        page_cap = settings.vision_page_cap_per_doc
        page_cap_exceeded_status: budget_guard.BudgetStatus | None = None

        # W25 D14 Sprint 4 — sweep 로직: 503 random 실패 페이지 자동 재시도.
        pending_pages: list[int] = list(range(process_count))
        for sweep_idx in range(1, _VISION_ENRICH_MAX_SWEEPS + 1):
            if not pending_pages:
                break
            if budget_exceeded_status is not None:
                break  # 이전 sweep 에서 cap 도달 — sweep 추가 호출도 차단
            if page_cap_exceeded_status is not None:
                break  # S2 D2 — page cap 도달 sweep 추가 진입도 차단
            if sweep_idx > 1:
                logger.info(
                    "vision_enrich sweep %d/%d: 누락 %d 페이지 재시도 %s (file=%s)",
                    sweep_idx, _VISION_ENRICH_MAX_SWEEPS,
                    len(pending_pages),
                    [p + 1 for p in pending_pages], file_name,
                )
            failed_in_sweep: list[int] = []
            for page_num in pending_pages:
                # S0 D4/D5 — N 페이지마다 cap 재검사. cap 도달 시 sweep 즉시 break.
                if (
                    doc_id
                    and budget_pages_since_check >= _BUDGET_RECHECK_EVERY_N_PAGES
                ):
                    budget_pages_since_check = 0
                    status = budget_guard.check_combined(
                        doc_id=doc_id,
                        doc_cap_usd=settings.doc_budget_usd,
                        daily_cap_usd=settings.daily_budget_usd,
                        sliding_24h_cap_usd=settings.sliding_24h_budget_usd,
                    )
                    if not status.allowed:
                        budget_exceeded_status = status
                        msg = (
                            f"vision_enrich: budget cap 도달 — "
                            f"{status.reason} (남은 페이지 {len(pending_pages)} skip)"
                        )
                        warnings.append(msg)
                        logger.warning("%s (file=%s)", msg, file_name)
                        break
                budget_pages_since_check += 1

                # S2 D2 — page cap 검사 (cost cap 직후, needs_vision 직전).
                # in-memory 카운터 비교라 매 페이지 검사해도 latency 0.
                # needs_vision False 페이지는 called_count 증가 X 라 자연스럽게
                # cap 도달이 지연됨 (사용자 가치 페이지만 차감).
                page_cap_status = budget_guard.check_doc_page_cap(
                    called_pages=called_count, page_cap=page_cap,
                )
                if not page_cap_status.allowed:
                    page_cap_exceeded_status = page_cap_status
                    msg = (
                        f"vision_enrich: page cap 도달 — "
                        f"{page_cap_status.reason} "
                        f"(남은 페이지 {len(pending_pages) - pending_pages.index(page_num)} skip)"
                    )
                    warnings.append(msg)
                    logger.warning("%s (file=%s)", msg, file_name)
                    break

                try:
                    page = doc[page_num]

                    # S2 D1 — needs_vision OR rule 검사. False 면 vision 호출 회피.
                    # sweep idx 무관 항상 평가 (논리 일관 — 첫 sweep skip = 모든 sweep skip).
                    # graceful: 점수 계산 자체가 raise 하면 needs_vision=True 보수적 fallback.
                    if need_score_enabled and not _page_needs_vision(
                        page, page_num=page_num + 1, file_name=file_name,
                    ):
                        if sweep_idx == 1:
                            skipped_by_need_score.append(page_num + 1)
                        # skip 페이지는 sweep retry 대상 X (failed_in_sweep 에 안 넣음).
                        # progress 표시는 기존과 동일하게 누적 — 사용자에 "처리 중" 인식 유지.
                        completed_pages.add(page_num)
                        if job_id:
                            update_stage_progress(
                                job_id,
                                current=len(completed_pages),
                                total=process_count,
                                unit="pages",
                            )
                        continue

                    pix = page.get_pixmap(dpi=_SCAN_RENDER_DPI)
                    png_bytes = pix.tobytes("png")
                    called_count += 1
                    page_result = image_parser.parse(
                        png_bytes,
                        file_name=f"{file_name}#page{page_num + 1}.png",
                        source_type="pdf_vision_enrich",
                        doc_id=doc_id,
                        page=page_num + 1,
                        sha256=sha256,
                    )
                    # vision 결과의 sections 를 page 메타 보강해 추가
                    for sec in page_result.sections:
                        base_title = (sec.section_title or "").strip()
                        enriched_title = (
                            f"(vision) p.{page_num + 1} {base_title}".strip()
                            if base_title
                            else f"(vision) p.{page_num + 1}"
                        )
                        sections.append(
                            ExtractedSection(
                                text=sec.text,
                                page=page_num + 1,
                                section_title=enriched_title,
                                bbox=None,
                            )
                        )
                    if page_result.raw_text:
                        raw_parts.append(page_result.raw_text)
                    warnings.extend(page_result.warnings)
                    completed_pages.add(page_num)
                    if job_id:
                        update_stage_progress(
                            job_id,
                            current=len(completed_pages),
                            total=process_count,
                            unit="pages",
                        )
                except Exception as exc:  # noqa: BLE001 — 페이지 단위 부분 실패 허용
                    failed_in_sweep.append(page_num)
                    if sweep_idx == _VISION_ENRICH_MAX_SWEEPS:
                        msg = (
                            f"vision_enrich: page {page_num + 1} 실패 "
                            f"(sweep {sweep_idx}/{_VISION_ENRICH_MAX_SWEEPS} 최종): {exc}"
                        )
                        warnings.append(msg)
                    logger.warning(
                        "vision_enrich page %d 실패 (sweep %d/%d): %s (file=%s)",
                        page_num + 1, sweep_idx, _VISION_ENRICH_MAX_SWEEPS,
                        exc, file_name,
                    )
            pending_pages = failed_in_sweep

        if pending_pages:
            msg = (
                f"vision_enrich: {_VISION_ENRICH_MAX_SWEEPS} sweep 후에도 누락: "
                f"{[p + 1 for p in pending_pages]}"
            )
            warnings.append(msg)
            logger.error("%s (file=%s)", msg, file_name)
    finally:
        doc.close()
        if job_id:
            clear_stage_progress(job_id)

    # S2 D1 + D2 — vision_need_score + page cap 메트릭 1줄 log (사용자 노출 X, 운영 진단용).
    # need_score_enabled=false 시 skipped 0 + called=process_count 로 기록 → 회복 토글 검증.
    # page_cap=0 시 무한 모드 — page_cap_exceeded 발생 X.
    logger.info(
        "vision_enrich: file=%s processed=%d called=%d skipped_need_score=%d "
        "(pages=%s) need_score_enabled=%s page_cap=%d page_cap_exceeded=%s",
        file_name,
        len(completed_pages),
        called_count,
        len(skipped_by_need_score),
        skipped_by_need_score[:20],
        need_score_enabled,
        page_cap,
        page_cap_exceeded_status is not None,
    )

    # S0 D4 — 페이지 루프 중 cap 도달 시 flags 마킹 (graceful: chunks 는 정상 적재).
    if budget_exceeded_status is not None and doc_id:
        try:
            db_client = client or get_supabase_client()
            existing_flags = _fetch_document(db_client, doc_id).get("flags") or {}
            _mark_budget_exceeded_flag(
                db_client,
                doc_id,
                existing_flags=existing_flags,
                status=budget_exceeded_status,
            )
        except Exception as exc:  # noqa: BLE001 — flags 마킹 실패해도 인제스트는 graceful 진행
            logger.warning(
                "vision_budget_exceeded flags 마킹 실패 (graceful): %s (doc_id=%s)",
                exc, doc_id,
            )

    # S2 D2 — 페이지 루프 중 page cap 도달 시 flags 마킹 (graceful: chunks 정상 적재).
    # cost cap 의 vision_budget_exceeded 와 직교 — 같은 doc 안 둘 다 도달 가능
    # (page cap 먼저 도달 후 후속 reingest 시 cost cap 도 도달 case).
    if page_cap_exceeded_status is not None and doc_id:
        try:
            db_client = client or get_supabase_client()
            existing_flags = _fetch_document(db_client, doc_id).get("flags") or {}
            _mark_page_cap_exceeded_flag(
                db_client,
                doc_id,
                existing_flags=existing_flags,
                status=page_cap_exceeded_status,
            )
        except Exception as exc:  # noqa: BLE001 — flags 마킹 실패해도 인제스트는 graceful 진행
            logger.warning(
                "vision_page_cap_exceeded flags 마킹 실패 (graceful): %s (doc_id=%s)",
                exc, doc_id,
            )

    return ExtractionResult(
        source_type=base_result.source_type,  # 'pdf' 보존
        sections=sections,
        raw_text="\n\n".join(raw_parts),
        warnings=warnings,
    )
