/**
 * 아직 Edge 로 안 옮긴 기능 목록 — 켜지면 **시끄럽게 실패**한다.
 *
 * ## 왜 이게 필요한가
 * 아래 토글은 운영에서 전부 꺼져 있어서(플랜 §3 실측) 이식 대상에서 뺐다. 그런데
 * "지금 꺼져 있으니 안 옮긴다" 를 그대로 두면 조용한 함정이 된다 — 운영자가 나중에
 * `JETRAG_RERANKER_ENABLED=true` 를 켜면 **Railway 에서는 동작하고 Edge 에서는 아무 일도
 * 일어나지 않은 채 다른 순위**가 나온다. 로그도 안 남는다.
 *
 * Phase 1 에서 같은 종류의 위험을 두 번 만났다 — `SUPABASE_` 접두어 secret 이 조용히
 * 건너뛰어진 것, 검색 토글의 기본값이 곧 운영 동작이었던 것. 조용한 차이는 발견이 늦고
 * 발견돼도 원인 추적이 어렵다.
 *
 * 그래서 **켜져 있으면 요청을 처리하지 않고 500 + 어느 토글인지 명시**한다.
 * 나중에 그 기능을 이식하면 이 목록에서 한 줄을 지우는 것으로 활성화된다.
 *
 * ## 여기 없는 것 — meta fast path
 * `meta_filter_fast_path` 는 ENV 토글이 아니라 **항상 켜져 있는 분기**다(질의가 메타 전용
 * 이라고 판정되면 임베딩·RPC 없이 documents 만 보고 답한다). 아직 안 옮겼으므로
 * **전환(Task 2.9) 전에 반드시 이식해야 한다** — 토글이 아니라서 여기서 막을 수 없다.
 */

/** 이식하지 않은 기능과 그 ENV 토글. 값 판정 규칙까지 원본과 맞춘다. */
export interface UnsupportedToggle {
  env: string;
  /** 사람이 읽는 기능 이름. */
  label: string;
  /** 원본이 "켜짐" 으로 보는 값 판정. */
  isOn: (raw: string) => boolean;
}

/** 원본 다수가 쓰는 판정 — `lower() == "true"`. */
const lowerTrue = (raw: string) => raw.toLowerCase() === "true";
/** decomposition·cross-doc scoped 는 더 넓게 받는다 — `strip().lower() in {true,1,yes,on}`. */
const looseTrue = (raw: string) => ["true", "1", "yes", "on"].includes(raw.trim().toLowerCase());

export const UNSUPPORTED_TOGGLES: readonly UnsupportedToggle[] = [
  { env: "JETRAG_RERANKER_ENABLED", label: "BGE reranker 재정렬", isOn: lowerTrue },
  { env: "JETRAG_HYDE_ENABLED", label: "HyDE 가상 문서 임베딩", isOn: lowerTrue },
  { env: "JETRAG_QUERY_EXPANSION", label: "질의 확장", isOn: lowerTrue },
  { env: "JETRAG_DOC_EMBEDDING_RRF", label: "문서 임베딩 RRF 가산", isOn: lowerTrue },
  { env: "JETRAG_VISION_ADJACENT_RETRIEVAL", label: "vision 인접 청크 추가 수집", isOn: lowerTrue },
  { env: "JETRAG_VISION_ADJACENT_BOOST", label: "vision 인접 청크 boost", isOn: lowerTrue },
  { env: "JETRAG_ENTITY_BOOST", label: "엔티티 매칭 boost", isOn: lowerTrue },
  { env: "JETRAG_PAID_DECOMPOSITION_ENABLED", label: "LLM 질의 분해", isOn: looseTrue },
  { env: "JETRAG_CROSS_DOC_SCOPED_SEARCH", label: "cross-doc 스코프 검색", isOn: looseTrue },
];

/** 켜져 있는 미이식 토글의 ENV 이름들. 비어 있으면 진행해도 된다. */
export function findEnabledUnsupported(read: (k: string) => string | undefined): string[] {
  const on: string[] = [];
  for (const t of UNSUPPORTED_TOGGLES) {
    const raw = read(t.env);
    if (raw !== undefined && t.isOn(raw)) on.push(t.env);
  }
  return on;
}

/** 500 응답에 실을 메시지. 어느 토글인지 반드시 이름으로 밝힌다. */
export function unsupportedDetail(envs: readonly string[]): string {
  const labels = envs.map((e) => {
    const t = UNSUPPORTED_TOGGLES.find((x) => x.env === e);
    return t ? `${e} (${t.label})` : e;
  });
  return `이 기능은 Edge 로 아직 이관되지 않았습니다: ${labels.join(", ")}`;
}
