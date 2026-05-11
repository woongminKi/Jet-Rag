"""Phase 3 일회용 — golden_v2.csv 끝에 신규 cross_doc row 5건 추가.

CRLF 줄바꿈, UTF-8 BOM 유지. 컬럼 순서:
id, query, query_type, doc_id, expected_doc_title, relevant_chunks,
acceptable_chunks, source_chunk_text, expected_answer_summary, must_include,
source_hint, negative, doc_type, caption_dependent
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

_CSV = Path(__file__).resolve().parent / "golden_v2.csv"

# 컬럼 순서 그대로 — 14개
_NEW_ROWS = [
    [
        "G-A-124",
        "운영내규와 직제규정에서 개정 절차가 어떻게 다른가요?",
        "cross_doc",
        "",
        "한마음생활체육관_운영_내규(2024.4.30.개정)|직제_규정(2024.4.30.개정)",
        "22,58",
        "",
        "",
        "운영내규는 운영시간 변경 시 사전 고지, 직제규정은 직제 개편 시 이사회 의결 필요",
        "고지;이사회",
        "각 자료 절차 조항",
        "false",
        "hwpx",
        "false",
    ],
    [
        "G-A-125",
        "데이터센터 안내서와 보건의료 빅데이터 자료에서 예산 규모가 어떻게 다른가요?",
        "cross_doc",
        "",
        "(붙임2) 2025년 데이터센터 산업 활성화 지원 사업 통합_안내서|보건의료_빅데이터_플랫폼_시범사업_추진계획(안)",
        "15,9",
        "",
        "",
        "데이터센터는 분야별 과제당 예산 (최대 25억 등), 보건의료는 플랫폼 46억 + 분석네트워크 24억",
        "예산;억",
        "각 자료 예산 섹션",
        "false",
        "pdf",
        "false",
    ],
    [
        "G-A-126",
        "기웅민 이력서와 이한주 포트폴리오의 핵심 역량은 어떻게 다른가요?",
        "cross_doc",
        "",
        "[당근서비스_Product Engineer(ERP)] 기웅민 이력서 (1)|포트폴리오_이한주 - na Lu",
        "7,11",
        "",
        "",
        "기웅민은 프론트/백엔드 개발자 (React, Node.js), 이한주는 PM (기획, 데이터 분석)",
        "기술;역량",
        "각 자료 스킬/소개 섹션",
        "false",
        "pdf",
        "false",
    ],
    [
        "G-A-127",
        "승인글 템플릿1과 템플릿3은 어떤 주제를 다루고 있나요?",
        "cross_doc",
        "",
        "승인글 템플릿1|승인글 템플릿3",
        "0,0",
        "",
        "",
        "템플릿1은 태양계 구조와 특징, 템플릿3은 삼국시대 정치 구조",
        "태양계;삼국시대",
        "각 자료 도입부 카테고리 선언",
        "false",
        "docx",
        "false",
    ],
    [
        "G-A-128",
        "law sample2와 law sample3 두 판결에서 대법원이 내린 결정은 무엇인가요?",
        "cross_doc",
        "",
        "law sample2|law sample3",
        "10,13",
        "",
        "",
        "두 판결 모두 원심판결 일부를 파기하고 환송함",
        "파기;환송",
        "각 판결문 주문 부분",
        "false",
        "hwp",
        "false",
    ],
]


def main() -> None:
    # 기존 파일 — 마지막 newline 보장 후 신규 row append
    raw = _CSV.read_bytes()
    if not raw.endswith(b"\r\n"):
        # 기존 line ending 자동 보정 (마지막 \n 또는 부재)
        if raw.endswith(b"\n"):
            raw = raw[:-1] + b"\r\n"
        else:
            raw = raw + b"\r\n"

    # 신규 row 를 CSV (CRLF) 로 직렬화 — BOM 없이 (이미 파일에 있음)
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    for row in _NEW_ROWS:
        writer.writerow(row)
    appended = buf.getvalue().encode("utf-8")
    _CSV.write_bytes(raw + appended)
    print(f"[OK] {len(_NEW_ROWS)}건 추가됨 → {_CSV}")


if __name__ == "__main__":
    main()
