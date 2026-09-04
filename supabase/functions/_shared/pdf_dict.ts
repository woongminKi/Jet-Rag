/**
 * mupdf(structured text) → PyMuPDF `page.get_text("dict")` 호환 변환기.
 *
 * ## 왜 변환이 필요한가
 * 현행 Python 코드는 `get_text("dict")` 의 모양에 강하게 결합돼 있다:
 *   - `app/adapters/impl/pymupdf_parser.py` — `blocks[].bbox`, block 내 **max span size** 로 heading 판정
 *   - `app/services/vision_need_score.py` — `blocks[].type`(0/1), `lines[].spans[]` **개수**,
 *     `spans[0].bbox` 의 x 좌표 cluster, image block `bbox` 면적비
 * 이관 시 이 계약을 그대로 재현하면 두 모듈의 로직(616 LOC 포함)을 구조 변경 없이 옮길 수 있다.
 *
 * ## asJSON 경로를 버린 이유 (2026-09-04 Edge 실측)
 * 처음에는 `toStructuredText(...).asJSON()` 을 두 번(옵션 유무) 불러 line/span 두 층을
 * 합치려 했다. 둘 다 막혔다:
 *   1. **bbox 가 정수로 반올림된다** — 기준선 대비 최대 1.93pt 편차. 좌표 cluster 판정에 쓰기엔 거칠다.
 *   2. **`preserve-spans` 가 블록 분할 자체를 바꾼다** — `sample-report.pdf` p0 에서
 *      7블록(text 5) → 6블록(text 4). 두 출력을 인덱스로 짝지을 수 없다.
 *      (인덱스 짝짓기는 처음 확인한 2개 페이지에서 우연히 성립했을 뿐이다.)
 *
 * ## walk() 경로
 * `StructuredText.walk()` 는 char 단위로 `(글자, origin, font, size, quad, color)` 를 준다.
 * PyMuPDF 의 span 정의가 곧 **같은 font·size·color 의 연속 run** 이므로 한 번의 순회로 복원된다.
 * bbox 도 float 원본이 그대로 온다. CPU 도 asJSON 과 같은 수준이다(law sample3 p0: 63.6ms).
 *
 * ## 알려진 잔차 — 합성 공백 (2026-09-04 실측, 영향 0으로 측정됨)
 * PyMuPDF 는 MuPDF 가 **간격 때문에 끼워 넣은 공백**을 독립 span 으로 두지만
 * (`'52,966,362' / ' ' / '20,138,323'` — 표 컬럼 신호), walk 의 6개 인자에는 그 플래그가 없어
 * 여기서는 한 span 으로 합쳐진다. 반대로 줄 끝 실공백은 우리가 더 쪼갠다.
 * "공백이면 무조건 분리" 규칙은 **틀린다** — 7페이지 전부 불일치했다(진짜 공백까지 쪼갠다).
 * 실측 영향: 기준선 7페이지 중 2페이지의 span 수만 다르고(7/9, 105/97),
 * 섹션 텍스트·bbox·needs_vision·triggers 는 **7/7 동일**, composite 최대 편차 0.0006.
 * Phase 1 에서 표 페이지 회귀 테스트로 고정할 것.
 */

export type BBox = [number, number, number, number];

export interface PdfSpan {
  text: string;
  size: number;
  bbox: BBox;
}

export interface PdfLine {
  bbox: BBox;
  spans: PdfSpan[];
}

export interface PdfBlock {
  /** PyMuPDF 규약: 0 = text, 1 = image */
  type: 0 | 1;
  bbox: BBox;
  lines?: PdfLine[];
}

export interface PdfPageDict {
  width: number;
  height: number;
  blocks: PdfBlock[];
}

/**
 * structured text 옵션.
 * `preserve-spans` 는 쓰지 않는다 — 위 §asJSON 참조. span 은 walk 의 font·size run 으로 만든다.
 */
export const STEXT_OPTS = "preserve-whitespace,preserve-images";

/** mupdf 의 Rect/Quad 는 배열로 온다. */
type Quad = ArrayLike<number>;

function rect(a: ArrayLike<number>): BBox {
  return [a[0], a[1], a[2], a[3]];
}

/** quad 8 좌표(ul, ur, ll, lr)의 축정렬 외접 사각형. */
function quadToBBox(q: Quad): BBox {
  const xs = [q[0], q[2], q[4], q[6]];
  const ys = [q[1], q[3], q[5], q[7]];
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

function union(a: BBox, b: BBox): BBox {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
}

interface StructuredTextLike {
  walk(walker: Record<string, unknown>): void;
}

/**
 * @param st    `page.toStructuredText(STEXT_OPTS)` 결과
 * @param bounds `page.getBounds()` → `[x0, y0, x1, y1]`
 */
export function toPageDict(st: StructuredTextLike, bounds: number[]): PdfPageDict {
  const blocks: PdfBlock[] = [];

  let curBlock: PdfBlock | null = null;
  let curLine: PdfLine | null = null;
  let curSpan: PdfSpan | null = null;
  let curFont: unknown = null;
  let curSize = -1;
  let curColor = "";

  const flushSpan = () => {
    if (curSpan && curLine) curLine.spans.push(curSpan);
    curSpan = null;
    curFont = null;
    curSize = -1;
    curColor = "";
  };

  st.walk({
    beginTextBlock(bbox: ArrayLike<number>) {
      curBlock = { type: 0, bbox: rect(bbox), lines: [] };
      blocks.push(curBlock);
    },
    endTextBlock() {
      curBlock = null;
    },
    beginLine(bbox: ArrayLike<number>) {
      curLine = { bbox: rect(bbox), spans: [] };
      curBlock?.lines?.push(curLine);
    },
    endLine() {
      flushSpan();
      curLine = null;
    },
    onChar(
      c: string,
      _origin: ArrayLike<number>,
      font: unknown,
      size: number,
      quad: Quad,
      color?: ArrayLike<number>,
    ) {
      if (!curLine) return;
      const bbox = quadToBBox(quad);
      // **객체 동일성으로 비교하면 안 된다** — mupdf.js 는 글자마다 새 JS 래퍼를 만들어 넘긴다.
      // 그대로 `!==` 로 재면 모든 글자가 span 경계가 되어 span 수 = 글자 수가 된다(실측 1,543).
      // 네이티브 폰트 핸들인 `pointer` 값이 실제 동일성이다.
      const fontId = (font as { pointer?: number } | null)?.pointer ?? font;
      // PyMuPDF 는 색이 바뀌어도 span 을 가른다. font·size 만 보면 span 수가 어긋난다
      // (실측: sample-report p0 7→9, 삼성 p100 105→97).
      const colorId = color ? `${color[0]},${color[1]},${color[2]}` : "";
      if (!curSpan || fontId !== curFont || size !== curSize || colorId !== curColor) {
        flushSpan();
        curSpan = { text: c, size, bbox };
        curFont = fontId;
        curSize = size;
        curColor = colorId;
      } else {
        curSpan.text += c;
        curSpan.bbox = union(curSpan.bbox, bbox);
      }
    },
    onImageBlock(bbox: ArrayLike<number>) {
      // 이미지 블록도 **문서 순서 그대로** 넣어야 한다. 뒤로 몰면 block 인덱스가 기준선과 어긋난다.
      blocks.push({ type: 1, bbox: rect(bbox) });
    },
  });

  return {
    width: (bounds[2] ?? 0) - (bounds[0] ?? 0),
    height: (bounds[3] ?? 0) - (bounds[1] ?? 0),
    blocks,
  };
}

/** `vision_need_score.page_area_pt2` 와 같은 값. */
export function pageArea(dict: PdfPageDict): number {
  return dict.width * dict.height;
}
