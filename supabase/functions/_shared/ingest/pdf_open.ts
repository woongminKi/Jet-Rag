/**
 * mupdf 로 PDF 를 열어 **페이지 범위 하나**를 추출한다.
 *
 * ## 왜 `pdf_extract.ts` 와 나눴나
 * 저쪽은 dict → 섹션 변환 로직뿐이라 입력만 주면 도는 순수 함수다. 패리티 검사기와
 * 단위 테스트가 **WASM 로드 없이** 돌 수 있어야 해서, mupdf 를 만지는 부분만 여기로 뺐다.
 * (mupdf WASM 은 로드에 40~56ms 걸리고 24MB 를 점유한다 — Phase 0 실측.)
 *
 * ## 태스크마다 문서를 다시 연다
 * Edge 워커는 상태가 없다. 그래도 괜찮은 이유는 여는 비용이 0.7~2.0ms 로 사실상
 * 공짜이기 때문이다(mupdf lazy loading). 1,513 페이지 문서도 2.0ms 다.
 *
 * ## `carryTitle` 을 반드시 넘겨야 한다
 * `current_title` 은 **문서 전체 sticky** 다. 페이지 범위를 따로 처리하면서 직전 범위의
 * 마지막 제목을 안 넘기면 제목이 통째로 어긋난다(음성 대조에서 실자산 145 페이지가
 * 깨졌다). 그래서 이 함수는 `carryTitle` 을 받고 `nextTitle` 을 돌려준다.
 */

import { STEXT_OPTS, toPageDict } from "../pdf_dict.ts";
import { extractDictBlocks, type ExtractedSection } from "./pdf_extract.ts";

export interface PdfRangeResult {
  sections: ExtractedSection[];
  rawParts: string[];
  /** 이 범위를 처리한 뒤의 sticky title. 다음 범위 태스크에 넘긴다. */
  nextTitle: string | null;
  /** 문서 전체 페이지 수. 다음 태스크를 큐에 넣을지 판단하는 데 쓴다. */
  totalPages: number;
  /** 실제로 처리한 페이지 수(문서 끝에서 잘릴 수 있다). */
  processed: number;
}

/** 페이지 범위 하나를 추출한다. `from` 은 0-기반, 산출 `page` 는 1-기반이다(원본 규약). */
export async function extractPdfRange(
  bytes: Uint8Array,
  opts: { from: number; count: number; carryTitle: string | null },
): Promise<PdfRangeResult> {
  // 동적 import — 이 모듈을 부르지 않는 경로에서는 WASM 을 로드하지 않는다.
  // deno-lint-ignore no-explicit-any
  const mupdf = await import("mupdf") as any;

  const doc = mupdf.Document.openDocument(bytes, "application/pdf");
  try {
    const totalPages: number = doc.countPages();
    const start = Math.max(0, opts.from);
    const end = Math.min(totalPages, start + Math.max(0, opts.count));

    const sections: ExtractedSection[] = [];
    const rawParts: string[] = [];
    let title = opts.carryTitle;

    for (let i = start; i < end; i++) {
      const page = doc.loadPage(i);
      try {
        const st = page.toStructuredText(STEXT_OPTS);
        try {
          const r = extractDictBlocks(toPageDict(st, page.getBounds()), {
            pageNum: i + 1, // 원본은 `enumerate(doc, start=1)`
            currentTitle: title,
          });
          title = r.nextTitle;
          sections.push(...r.sections);
          rawParts.push(...r.rawParts);
        } finally {
          st.destroy?.();
        }
      } finally {
        page.destroy?.();
      }
    }

    return {
      sections,
      rawParts,
      nextTitle: title,
      totalPages,
      processed: Math.max(0, end - start),
    };
  } finally {
    doc.destroy?.();
  }
}
