/**
 * `/answer` 프롬프트 조립 — `_build_messages` 포팅.
 *
 * 문자 하나까지 원본과 같아야 한다. 프롬프트가 달라지면 LLM 출력이 달라지고,
 * 그건 응답 대조로는 잡히지 않는다(같은 질문에도 매번 다른 문장이 나오므로).
 * **그래서 프롬프트 문자열 자체를 대조 대상으로 삼는다.**
 */

import type { ChatMessage } from "../llm/gemini.ts";
import type { EnrichedChunk } from "./chunks.ts";

/** `_CHUNK_TEXT_MAX` — 청크 하나가 프롬프트에 들어갈 최대 글자. */
export const CHUNK_TEXT_MAX = 1200;

/** `_LLM_MODEL_FALLBACK` — 검색 0 건이라 LLM 을 안 만들었을 때 응답에 표시할 모델명. */
export const LLM_MODEL_FALLBACK = "gemini-2.5-flash";

const SYSTEM_PROMPT =
  "당신은 사용자의 개인 지식베이스에서 검색된 자료를 바탕으로 한국어로 답변하는 어시스턴트입니다. " +
  "다음 규칙을 반드시 지키세요:\n" +
  "1. 답변은 반드시 제공된 '검색 결과' 안의 내용만 사용하세요. 외부 지식이나 추측을 절대 추가하지 마세요.\n" +
  "2. 검색 결과에 답변할 내용이 없으면 '제공된 자료에서 해당 정보를 찾지 못했습니다.' 라고만 답하세요.\n" +
  "3. 답변 문장 끝에 출처 번호를 [1], [2] 와 같이 인라인으로 표시하세요.\n" +
  "4. 한국어로 간결하게 답변하세요 (5문장 이내 권장).";

/** 검색 0 건일 때 LLM 을 부르지 않고 그대로 내보내는 문구. */
export const NO_RESULT_ANSWER = "제공된 자료에서 해당 정보를 찾지 못했습니다.";

export function buildMessages(
  query: string,
  chunks: readonly EnrichedChunk[],
): ChatMessage[] {
  const parts: string[] = [`질문: ${query}`, "", "검색 결과:"];
  chunks.forEach((c, idx) => {
    // 원본은 `text[:_CHUNK_TEXT_MAX]` — **코드포인트가 아니라 Python 슬라이스**다.
    // Python str 슬라이스는 코드포인트 단위이므로 JS 에서도 그렇게 잘라야 한다.
    let text = (c.text ?? "").trim();
    const cps = [...text];
    if (cps.length > CHUNK_TEXT_MAX) {
      text = cps.slice(0, CHUNK_TEXT_MAX).join("") + "...";
    }
    const title = c.doc_title || "(제목 없음)";
    // `f" p.{page}" if page else ""` — page 가 0 이면 붙지 않는다(falsy).
    const pageStr = c.page ? ` p.${c.page}` : "";
    parts.push(`[${idx + 1}] ${title}${pageStr}\n${text}`);
  });
  return [
    { role: "system", content: SYSTEM_PROMPT },
    { role: "user", content: parts.join("\n\n") },
  ];
}
