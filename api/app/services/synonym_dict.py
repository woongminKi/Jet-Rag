"""M1 W-2 (S4-D) — 인제스트 단계 동의어 후보 정적 사전.

목적: 사용자가 문서에 없는 어휘(외래어·약어·일상어·이형 표기)로 검색해 sparse path
(PGroonga `&@~`) 가 0 hits 되는 synonym_mismatch 회귀를, 인제스트 시점에 chunk text
끝에 `[검색어: ...]` 마커를 주입해 회복한다. (검색 측 `query_expansion._SYNONYMS` 와
방향이 반대 — 이쪽은 corpus 측 보강, 그쪽은 query 측 확장. 같은 어휘를 의도적으로 공유.)

사전 정책 (senior-planner 명세 §1.2):
- 양방향 lookup — 키 등장 → 값들 후보 / 값 등장 → 키 후보 (`lookup_synonyms` 가 처리).
- 11 docs 도메인 기준 (자동차 / IT·정책 / 보건의료 / 법령 / 운영내규 / 학습·천문·역사).
- 보수적 (false positive 회피) — 키는 반드시 2어절+ 또는 도메인 한정 어휘.
  너무 일반적인 단독 명사("정보"·"관리"·"규정") 는 키 금지.
- `query_expansion._SYNONYMS` 와 의도적으로 겹치는 항목은 동일 어휘 유지 (어휘 일관성).
- eval 효과 측정은 M2 W-4 전체 클린 재인제스트 후 (이번엔 측정 안 함, default OFF).
"""

from __future__ import annotations

# 양방향 동의어 사전.
# 키: 한쪽 표현 / 값: 동의어·일상어 후보 list. lookup 시 양방향 매칭 (lookup 함수가 처리).
#
# ⚠ 키 선정 원칙 — 너무 일반적인 단독 명사 금지. 반드시 2어절 이상 또는 도메인 한정 어휘.
#   (예: "관리" X / "재산물품관리" O, "정보" X / "환자 정보 보호" O)
_DOMAIN_SYNONYMS: dict[str, list[str]] = {
    # === 자동차 (sonata catalog) — query_expansion 과 어휘 일치 ===
    "쏘나타": ["sonata", "Sonata"],
    "전장": ["전체길이", "전체 길이"],
    "전폭": ["전체너비", "차폭"],
    "전고": ["전체높이", "차높이"],
    "윤거": ["트레드", "바퀴 간격"],
    "트림": ["등급", "사양 등급"],
    "공차중량": ["차량 중량", "빈차 무게"],
    # === IT / 정책 (데이터센터 안내서) — query_expansion 과 어휘 일치 ===
    "데이터센터": ["DC", "전산센터", "data center"],
    "전산센터": ["데이터센터", "DC"],
    "인공지능": ["AI", "artificial intelligence"],
    "전자의무기록": ["EHR", "전자 의무 기록"],
    "상면 임대": ["코로케이션", "랙 임대"],
    "무중단 전원": ["UPS", "무정전 전원장치"],
    # === 보건의료 (보건의료 비식별화 가이드) ===
    "비식별화": ["가명처리", "익명처리", "개인정보 비식별"],
    "가명정보": ["가명처리 정보", "가명화 정보"],
    "재식별": ["재식별화", "신원 재확인"],
    "환자 정보 보호": ["환자정보 보호", "개인정보 보호", "진료정보 보호"],
    "동의서": ["사전 동의서", "informed consent"],
    "민감정보": ["민감 개인정보", "특수 개인정보"],
    # === 법령 (하도급법 / 채권 변제 sample) — query_expansion 과 어휘 일치 ===
    "하도급대금": ["공사대금", "하도급 대금", "하청 대금"],
    "직접지급": ["직불", "직접 지급", "발주자 직접지급"],
    "변제충당": ["변제 순서", "변제 충당"],
    "소멸시효": ["시효 소멸", "권리 소멸시효"],
    "지연손해금": ["지연이자", "연체 이자"],
    "원사업자": ["발주자", "원도급자"],
    "수급사업자": ["하도급자", "하청업체"],
    # === 운영내규 / 직제 (학회·기관 내규) — query_expansion 과 어휘 일치 ===
    "재산물품관리": ["자산관리", "물품관리", "재산 물품 관리"],
    "회원카드": ["이용카드", "멤버십카드"],
    "회비": ["연회비", "회원 회비"],
    "직제": ["조직 구조", "조직도"],
    "사무국": ["사무처", "운영 사무국"],
    "정기총회": ["정기 총회", "연례 총회"],
    # === 학습자료 (태양계 / 삼국시대) — query_expansion 과 어휘 일치 ===
    "태양계": ["solar system", "태양 행성계"],
    "삼국시대": ["고구려 백제 신라", "삼국 시대"],
    "왜소행성": ["왜행성", "dwarf planet"],
    "고대 한반도": ["삼국시대 한반도", "고대 한국"],
}


def lookup_synonyms(token: str) -> list[str]:
    """주어진 token 에 대한 동의어 후보 list (양방향).

    - token 이 사전 키면 → 그 값 list
    - token 이 어느 값에 포함돼 있으면 → 해당 키 + 같은 그룹의 다른 값들
    - 미등록 → 빈 list
    - 자기 자신은 항상 제외

    Args:
        token: 단일 표현 (공백 포함 가능 — 사전 키가 어절구 일 수 있음).
    """
    out: list[str] = []
    direct = _DOMAIN_SYNONYMS.get(token)
    if direct:
        out.extend(v for v in direct if v != token)
    for key, vals in _DOMAIN_SYNONYMS.items():
        if token in vals:
            if key != token and key not in out:
                out.append(key)
            for v in vals:
                if v != token and v not in out:
                    out.append(v)
    # 보존 순서 dedupe
    seen: set[str] = set()
    deduped: list[str] = []
    for v in out:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    return deduped


def iter_dict_keys() -> list[str]:
    """사전에 등록된 모든 키 (text 스캔용)."""
    return list(_DOMAIN_SYNONYMS.keys())
