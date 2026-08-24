/**
 * HWP 5.x → 평문 추출기 (`@ohah/hwpjs` 의 `toJson` 출력 위에서 동작).
 *
 * 왜 toJson 인가 — Phase 0 / S1 실측 결과:
 *   toHtml     18,530자  표 셀을 통째로 누락 (기준선 대비 유사도 0.9413, 격차 원인 전부 표)
 *   toMarkdown 49자      본문을 거의 못 냄 (`{markdown, images}` 객체이고 markdown 은 헤더뿐)
 *   toJson    238,962자  **표 셀 포함 모든 텍스트가 들어 있다**
 * 따라서 텍스트 추출은 toJson 을 직접 걸어야 한다.
 *
 * 구조 (0.1.0-rc.10 실측):
 *   body_text.sections[].paragraphs[].records[]
 *     - { type: "para_text", text, runs: [{kind:"text",text} | {kind:"control",code,name}] }
 *     - { type: "ctrl_header" | "list_header", children: [...], paragraphs: [...] }
 *   표 셀 텍스트는 ctrl_header 아래에만 있으므로 **재귀**가 필수다.
 *
 * 함정 — ctrl_header 의 `paragraphs` 는 `children` 의 **평탄화 사본**이다.
 * 둘 다 순회하면 문서 전체가 정확히 두 번 나온다(law_sample1: 985자 → 2,141자, 중복 문단 36건).
 * 실측으로 19개 ctrl_header 전부에서 children 텍스트 == paragraphs 텍스트임을 확인했다.
 * 그래서 children 을 우선하고(표 경계가 남아 나중에 마크다운 표로 올릴 수 있다),
 * children 에서 텍스트가 안 나올 때만 paragraphs 로 폴백한다(글상자·머리말 계열).
 *
 * 문서 순서를 보존해야 한다 — 청킹이 문단 순서에 의존하므로 셀을 뒤로 몰아넣으면 안 된다.
 */

/** HWP 제어문자 중 텍스트 흐름에 의미가 있는 것들. 나머지(개체·머리말 등)는 버린다. */
const CONTROL_AS_TEXT: Record<number, string> = {
  9: "\t", // TAB
  10: "\n", // LINE_BREAK (문단 내 줄바꿈)
  13: "\n", // PARA_BREAK
};

interface Run {
  kind?: string;
  text?: string;
  code?: number;
}

/** para_text 1건을 문자열로. `text` 필드가 비어도 runs 에는 남아 있는 경우가 있어 runs 를 우선한다. */
function paraTextToString(rec: Record<string, unknown>): string {
  const runs = rec.runs;
  if (Array.isArray(runs)) {
    let out = "";
    for (const r of runs as Run[]) {
      if (r?.kind === "text" && typeof r.text === "string") {
        out += r.text;
      } else if (r?.kind === "control" && typeof r.code === "number") {
        const mapped = CONTROL_AS_TEXT[r.code];
        // PARA_BREAK 은 문단 조인에서 처리하므로 여기서 중복으로 넣지 않는다.
        if (mapped && r.code !== 13) out += mapped;
      }
    }
    if (out.length > 0) return out;
  }
  return typeof rec.text === "string" ? rec.text : "";
}

/**
 * toJson 결과에서 문단 배열을 문서 순서대로 뽑는다.
 * @param doc `toJson()` 이 돌려준 JSON 을 파싱한 값
 */
export function extractParagraphs(doc: unknown): string[] {
  const walk = (node: unknown, out: string[]): void => {
    if (Array.isArray(node)) {
      for (const v of node) walk(v, out);
      return;
    }
    if (!node || typeof node !== "object") return;
    const o = node as Record<string, unknown>;

    if (o.type === "para_text") {
      const s = paraTextToString(o).trim();
      if (s) out.push(s);
      return; // runs 안으로 더 들어가면 같은 텍스트를 두 번 담는다.
    }

    // children ↔ paragraphs 중복 처리. 둘 다 있으면 children 만 쓴다.
    if (o.children !== undefined && o.paragraphs !== undefined) {
      const fromChildren: string[] = [];
      walk(o.children, fromChildren);
      if (fromChildren.length > 0) {
        out.push(...fromChildren);
      } else {
        walk(o.paragraphs, out);
      }
      // 나머지 키(캡션 등)도 마저 본다.
      for (const [k, v] of Object.entries(o)) {
        if (k !== "children" && k !== "paragraphs") walk(v, out);
      }
      return;
    }

    // 그 외는 전부 순회 — 새 컨트롤 타입이 추가돼도 텍스트를 놓치지 않는다.
    for (const v of Object.values(o)) walk(v, out);
  };

  // 본문만 대상 — doc_info/summary_information 에는 폰트명·작성자 같은 메타가 있어 섞이면 오염된다.
  const body = (doc as Record<string, unknown> | null)?.body_text;
  const out: string[] = [];
  walk(body ?? doc, out);
  return out;
}

/** 문단을 개행으로 이어 붙인 평문. */
export function extractText(doc: unknown): string {
  return extractParagraphs(doc).join("\n");
}
