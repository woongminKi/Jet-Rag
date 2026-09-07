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
 * structured text 옵션 — **PyMuPDF `TEXTFLAGS_DICT` 를 그대로 재현한다.**
 *
 * 짐작하지 말고 실제 값을 봤다(2026-09-07):
 * ```
 * fitz.TEXTFLAGS_DICT == 199
 *   = 1 PRESERVE_LIGATURES | 2 PRESERVE_WHITESPACE | 4 PRESERVE_IMAGES
 *   | 64 MEDIABOX_CLIP     | 128 CID_FOR_UNKNOWN_UNICODE
 * ```
 * 처음엔 `preserve-whitespace,preserve-images` 둘만 켰다. **`preserve-ligatures` 가
 * 빠진 게 실제 버그였다** — mupdf 가 `ﬀ`(U+FB00)를 `ff` 로 풀어 버려서 arXiv 문서의
 * 텍스트가 491 자 길어지고, 800 자 분할 경계가 밀려 **청크가 749 → 803 개(+7.2%)** 로
 * 어긋났다. 옵션을 맞추자 blocks 10 / lines 25 / chars 1,682 / 리거처 4 로 완전 일치.
 *
 * `clip`(=`MEDIABOX_CLIP`, 둘 다 64) · `use-cid-for-unknown-unicode` 는 켜도 결과가
 * 안 바뀌었지만(MuPDF 기본이 이미 그렇게 동작하는 것으로 보인다) 계약을 눈에 보이게
 * 두려고 남긴다.
 *
 * 처음엔 PyMuPDF 상수 이름 그대로 `mediabox-clip` 을 썼는데 mupdf 가 실행 중에
 * `The 'mediabox-clip' option has been deprecated. Use 'clip' instead.` 를 찍었다.
 * 하위호환으로 동작은 했지만(양쪽 결과 동일 확인) 경고를 남겨 두면 다음 사람이
 * "무시되는 옵션" 으로 오해한다 — 새 이름으로 바꿨다.
 *
 * `preserve-spans` 는 쓰지 않는다 — 위 §asJSON 참조. span 은 walk 의 font·size run 으로
 * 만든다. (실측: 켜면 line 수가 30 → 515 로 폭증해 PyMuPDF 와 더 멀어진다.)
 */
export const STEXT_OPTS =
  "preserve-ligatures,preserve-whitespace,preserve-images,clip," +
  "use-cid-for-unknown-unicode";

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
 * PyMuPDF `JM_font_name` — **정확히 6 자 + `+`** 인 서브셋 접두사만 뗀다.
 *
 * 이게 span 경계를 정한다. 처음엔 네이티브 `font.pointer` 로 비교했는데, 같은 글꼴의
 * **다른 서브셋 인스턴스**(`BCDLEE+MalgunGothic` / `BCDEEE+MalgunGothic`)가 다른
 * 포인터라 한 줄이 잘게 쪼개졌다. PyMuPDF 는 접두사를 뗀 이름으로 비교해 하나로 묶는다.
 *
 * 실측(sample-report p12): `< 요약 6/8 > ` 가 PyMuPDF 1 span / 포팅 4 span 이었고,
 * `table_like_score` 가 0.143 → 0.657 로 부풀어 **needs_vision 판정이 뒤집혔다.**
 */
export function pyFontName(raw: string): string {
  // `strchr` 로 **첫** `+` 를 찾고 그게 6 번째일 때만 뗀다 — 앞에 다른 `+` 가 있으면 안 뗀다.
  return raw.indexOf("+") === 6 ? raw.slice(7) : raw;
}

/** PyMuPDF `TEXT_FONT_*` 비트. */
const FLAG_SUPERSCRIPT = 1;
const FLAG_ITALIC = 2;
const FLAG_SERIF = 4;
const FLAG_MONOSPACED = 8;
const FLAG_BOLD = 16;

interface MupdfFont {
  pointer?: number;
  getName?(): string;
  isBold?(): boolean;
  isItalic?(): boolean;
  isSerif?(): boolean;
  isMono?(): boolean;
}

/** 폰트 속성은 글자마다 안 바뀐다 — 네이티브 호출을 포인터로 메모한다. */
function fontStyleOf(
  font: MupdfFont | null,
  cache: Map<unknown, { name: string; flags: number }>,
): { name: string; flags: number } {
  const key = font?.pointer ?? font;
  let v = cache.get(key);
  if (v === undefined) {
    v = {
      name: pyFontName(String(font?.getName?.() ?? "")),
      flags: (font?.isItalic?.() ? FLAG_ITALIC : 0) +
        (font?.isSerif?.() ? FLAG_SERIF : 0) +
        (font?.isMono?.() ? FLAG_MONOSPACED : 0) +
        (font?.isBold?.() ? FLAG_BOLD : 0),
    };
    cache.set(key, v);
  }
  return v;
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
  let curStyle = "";
  /** 위첨자 판정 기준선 — 줄의 **첫 글자** origin.y (PyMuPDF `line->first_char`). */
  let lineFirstOriginY: number | null = null;
  /** 가로쓰기 왼→오 줄에서만 위첨자를 본다(PyMuPDF `detect_super_script`). */
  let lineHorizontal = true;
  const fontCache = new Map<unknown, { name: string; flags: number }>();

  const flushSpan = () => {
    if (curSpan && curLine) curLine.spans.push(curSpan);
    curSpan = null;
    curStyle = "";
  };

  st.walk({
    beginTextBlock(bbox: ArrayLike<number>) {
      curBlock = { type: 0, bbox: rect(bbox), lines: [] };
      blocks.push(curBlock);
    },
    endTextBlock() {
      curBlock = null;
    },
    beginLine(bbox: ArrayLike<number>, wmode?: number, direction?: ArrayLike<number>) {
      curLine = { bbox: rect(bbox), spans: [] };
      curBlock?.lines?.push(curLine);
      lineFirstOriginY = null;
      lineHorizontal = (wmode ?? 0) === 0 &&
        (direction === undefined || (direction[0] === 1 && direction[1] === 0));
    },
    endLine() {
      flushSpan();
      curLine = null;
    },
    onChar(
      c: string,
      origin: ArrayLike<number>,
      font: unknown,
      size: number,
      quad: Quad,
      color?: ArrayLike<number>,
    ) {
      if (!curLine) return;
      const bbox = quadToBBox(quad);
      const originY = origin?.[1] ?? 0;
      if (lineFirstOriginY === null) lineFirstOriginY = originY;

      // PyMuPDF 는 (폰트 이름, size, color, flags) 가 하나라도 바뀌면 span 을 가른다
      // (`JM_make_spanlist`). 넷 다 봐야 한다 — 하나만 빠져도 span 경계가 어긋나고,
      // 그건 `table_like_score` 처럼 span 수를 세는 신호를 통째로 왜곡한다.
      const style = fontStyleOf(font as MupdfFont | null, fontCache);
      // 위첨자는 글자마다 다르다 — 폰트 캐시에 넣으면 안 된다.
      const superscript = lineHorizontal && originY < lineFirstOriginY - size * 0.1
        ? FLAG_SUPERSCRIPT
        : 0;
      // MuPDF 의 `fz_stext_char.color` 는 sRGB 정수다. 실수 배열로 비교하면 반올림 차이로
      // 같은 색이 갈릴 수 있다 — PyMuPDF 와 같은 정수로 바꿔서 본다.
      const colorId = color
        ? ((Math.round(color[0] * 255) << 16) | (Math.round(color[1] * 255) << 8) |
          Math.round(color[2] * 255))
        : 0;
      const styleKey = `${style.name}|${size}|${colorId}|${style.flags + superscript}`;

      if (!curSpan || styleKey !== curStyle) {
        flushSpan();
        curSpan = { text: c, size, bbox };
        curStyle = styleKey;
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
