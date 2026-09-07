/**
 * `services/synonym_dict.py` 포팅 — 인제스트 시점 동의어 후보 정적 사전.
 *
 * 사용자가 문서에 없는 어휘(외래어·약어·일상어·이형 표기)로 검색하면 sparse
 * (PGroonga `&@~`)가 0 hit 이 된다. 그걸 회복하려고 **corpus 쪽**에 후보를 심는다
 * (검색 쪽 `query_expansion` 과 방향이 반대다 — 같은 어휘를 의도적으로 공유한다).
 *
 * ## 순서가 결과를 바꾼다
 * `iterDictKeys()` 는 **삽입 순서**를 돌려주고, `collectSynonymCandidates` 가 그 순서로
 * 후보를 쌓다가 5 개에서 자른다. 즉 사전 순서가 **어떤 후보가 잘리는지**를 정한다.
 * 그래서 `Map` 을 쓴다 — 객체 리터럴은 정수형 키를 앞으로 당기는 규칙이 있어
 * (지금 키는 전부 한글이라 무해하지만) 순서를 언어 규칙에 맡기지 않는다.
 *
 * ## 이 표는 손으로 옮기지 않았다
 * `api/app/services/synonym_dict.py` 의 `_DOMAIN_SYNONYMS` 를 스크립트로 뽑아 넣었다.
 * 36 entry 를 눈으로 옮기면 한 글자가 틀린다.
 */

/** 양방향 동의어 사전. 키: 한쪽 표현 / 값: 동의어·일상어 후보. */
const DOMAIN_SYNONYMS = new Map<string, string[]>([
  ["쏘나타", ["sonata", "Sonata"]],
  ["전장", ["전체길이", "전체 길이"]],
  ["전폭", ["전체너비", "차폭"]],
  ["전고", ["전체높이", "차높이"]],
  ["윤거", ["트레드", "바퀴 간격"]],
  ["트림", ["등급", "사양 등급"]],
  ["공차중량", ["차량 중량", "빈차 무게"]],
  ["데이터센터", ["DC", "전산센터", "data center"]],
  ["전산센터", ["데이터센터", "DC"]],
  ["인공지능", ["AI", "artificial intelligence"]],
  ["전자의무기록", ["EHR", "전자 의무 기록"]],
  ["상면 임대", ["코로케이션", "랙 임대"]],
  ["무중단 전원", ["UPS", "무정전 전원장치"]],
  ["비식별화", ["가명처리", "익명처리", "개인정보 비식별"]],
  ["가명정보", ["가명처리 정보", "가명화 정보"]],
  ["재식별", ["재식별화", "신원 재확인"]],
  ["환자 정보 보호", ["환자정보 보호", "개인정보 보호", "진료정보 보호"]],
  ["동의서", ["사전 동의서", "informed consent"]],
  ["민감정보", ["민감 개인정보", "특수 개인정보"]],
  ["하도급대금", ["공사대금", "하도급 대금", "하청 대금"]],
  ["직접지급", ["직불", "직접 지급", "발주자 직접지급"]],
  ["변제충당", ["변제 순서", "변제 충당"]],
  ["소멸시효", ["시효 소멸", "권리 소멸시효"]],
  ["지연손해금", ["지연이자", "연체 이자"]],
  ["원사업자", ["발주자", "원도급자"]],
  ["수급사업자", ["하도급자", "하청업체"]],
  ["재산물품관리", ["자산관리", "물품관리", "재산 물품 관리"]],
  ["회원카드", ["이용카드", "멤버십카드"]],
  ["회비", ["연회비", "회원 회비"]],
  ["직제", ["조직 구조", "조직도"]],
  ["사무국", ["사무처", "운영 사무국"]],
  ["정기총회", ["정기 총회", "연례 총회"]],
  ["태양계", ["solar system", "태양 행성계"]],
  ["삼국시대", ["고구려 백제 신라", "삼국 시대"]],
  ["왜소행성", ["왜행성", "dwarf planet"]],
  ["고대 한반도", ["삼국시대 한반도", "고대 한국"]],
]);

/**
 * 원본 `lookup_synonyms` — **양방향**이다.
 *
 * - token 이 키면 → 그 값들
 * - token 이 어느 값에 들어 있으면 → 그 키 + 같은 그룹의 다른 값들
 * - 자기 자신은 언제나 뺀다
 */
export function lookupSynonyms(token: string): string[] {
  const out: string[] = [];
  const direct = DOMAIN_SYNONYMS.get(token);
  if (direct && direct.length > 0) {
    for (const v of direct) if (v !== token) out.push(v);
  }
  for (const [key, vals] of DOMAIN_SYNONYMS) {
    if (!vals.includes(token)) continue;
    if (key !== token && !out.includes(key)) out.push(key);
    for (const v of vals) if (v !== token && !out.includes(v)) out.push(v);
  }
  // 원본은 여기서 한 번 더 순서 보존 dedupe 를 한다 — 위 루프가 이미 걸러도 그대로 옮긴다.
  const seen = new Set<string>();
  const deduped: string[] = [];
  for (const v of out) {
    if (seen.has(v)) continue;
    seen.add(v);
    deduped.push(v);
  }
  return deduped;
}

/** 원본 `iter_dict_keys` — 삽입 순서 그대로. */
export function iterDictKeys(): string[] {
  return [...DOMAIN_SYNONYMS.keys()];
}
