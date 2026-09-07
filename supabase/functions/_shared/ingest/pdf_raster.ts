/**
 * PDF 페이지 → PNG 래스터화. 원본 `extract.py` 의 `page.get_pixmap(dpi=150)` 대응.
 *
 * vision 대상 페이지를 이미지로 구워 Gemini 에 넘긴다.
 *
 * ## API 는 Phase 0 스파이크가 실측으로 확정했다
 * mupdf.js 의 메서드 이름을 짐작하지 않고 `?kind=pdf-render` 로 실제 객체를 훑어
 * 정했다 — `page.toPixmap(matrix, colorspace, alpha, extras)` + `pix.asPNG()`.
 *
 * ## 배율
 * PDF 기본 해상도가 72dpi 라 `zoom = dpi / 72` 다. 원본이 `dpi=150` 이므로
 * `zoom ≈ 2.083`.
 *
 * ## `alpha=false`, `extras=true`
 * PyMuPDF `get_pixmap()` 기본값이 `alpha=False` 이고 주석(annotation)을 함께 그린다.
 * mupdf.js 의 네 번째 인자가 그 "extras" 라 `true` 를 준다 — 스캔 PDF 에 도장·서명이
 * 주석으로 들어간 경우 그게 빠지면 OCR 결과가 달라진다.
 *
 * ## CPU 가 여기서 가장 많이 든다
 * Phase 0 실측 **페이지당 최악 443ms**(텍스트 추출은 100.8ms). 그래서 vision 이 붙는
 * 페이지는 태스크당 1~2 개로 쪼개야 한다 — 텍스트 전용 10 페이지와 다른 예산이다.
 */

import { normalizeTarget, resizeRgbLanczos } from "./image_normalize.ts";

/** 원본 `_SCAN_RENDER_DPI`. */
export const SCAN_RENDER_DPI = 150;

/** 원본 `_normalize` 의 `img.save(..., quality=85)`. */
const JPEG_QUALITY = 85;

export interface RenderedPage {
  png: Uint8Array;
  width: number;
  height: number;
}

interface MupdfLike {
  Document: { openDocument(b: Uint8Array, mime: string): MupdfDoc };
  Matrix: { scale(x: number, y: number): unknown };
  ColorSpace: { DeviceRGB: unknown };
  Pixmap: new (cs: unknown, bbox: number[], alpha: boolean) => MupdfPixmap;
}
interface MupdfDoc {
  countPages(): number;
  loadPage(n: number): MupdfPage;
  destroy?(): void;
}
interface MupdfPage {
  toPixmap(m: unknown, cs: unknown, alpha: boolean, extras: boolean): MupdfPixmap;
  destroy?(): void;
}
interface MupdfPixmap {
  asPNG(): Uint8Array;
  asJPEG(quality: number, invertCMYK: boolean): Uint8Array;
  getPixels(): Uint8ClampedArray;
  getStride(): number;
  getWidth(): number;
  getHeight(): number;
  destroy?(): void;
}

/**
 * 열린 문서의 페이지 하나를 PNG 로 굽는다.
 *
 * `pageIndex` 는 **0 기반**이다(원본 `doc[i]` 와 같다).
 */
export function renderPageToPng(
  mupdf: unknown,
  doc: MupdfDoc,
  pageIndex: number,
  dpi: number = SCAN_RENDER_DPI,
): RenderedPage {
  const M = mupdf as MupdfLike;
  if (typeof M?.Matrix?.scale !== "function" || M?.ColorSpace?.DeviceRGB === undefined) {
    // 못 하면 못 한다고 말한다 — 추측으로 다른 경로를 시도하지 않는다(Phase 0 규칙).
    throw new Error("mupdf 에 Matrix.scale / ColorSpace.DeviceRGB 가 없다 — 래스터화 불가");
  }
  const zoom = dpi / 72;
  const page = doc.loadPage(pageIndex);
  try {
    const pix = page.toPixmap(M.Matrix.scale(zoom, zoom), M.ColorSpace.DeviceRGB, false, true);
    try {
      return { png: pix.asPNG(), width: pix.getWidth(), height: pix.getHeight() };
    } finally {
      pix.destroy?.();
    }
  } finally {
    page.destroy?.();
  }
}

/**
 * vision 에 보낼 바이트를 만든다 — 원본 `_reroute_pdf_to_image` / `_enrich_pdf_with_vision`
 * 의 `get_pixmap(dpi=150).tobytes("png")` → `ImageParser._normalize` 까지가 한 덩어리다.
 *
 * 원본은 PNG 를 만들어 Pillow 에 넘기지만 Pillow 가 곧바로 디코드하므로, 중간 PNG 는
 * **관찰 가능한 산출물이 아니다**. 여기서는 픽셀을 바로 축소해 PNG 인코딩·디코딩 한
 * 왕복을 건너뛴다(무손실이라 결과 동일).
 *
 * 알파는 없다 — `toPixmap(..., alpha=false)` 라 원본의 `has_meaningful_alpha` 분기는
 * 항상 거짓이고, 따라서 항상 JPEG q85 다.
 *
 * ## 알려진 차이 — 크로마 서브샘플링 (해소 불가, 무해함을 실측)
 * Pillow 는 4:2:0(subsampling=2), mupdf 는 4:4:4(0)로 굽는다. `asJPEG(quality,
 * invertCMYK)` 에 이걸 바꿀 인자가 없다.
 *
 * `verify_vision_input_parity.py` 실측(5페이지):
 * - 크기·mime 은 10/10 일치 → Gemini 타일링·토큰 비용 동일(바이트가 아니라 크기 기준)
 * - py↔ts 픽셀 차이 42.9dB~∞ 인데, **양쪽이 공유하는 JPEG q85 자체 손실이 39.5~41.9dB**
 *   다. 즉 인코더 차이가 압축 손실보다 작다.
 * - 무손실 기준 대비 ts 40.5~45.2dB vs py 39.5~41.9dB — 모든 케이스에서 포팅 쪽이
 *   실제 페이지에 더 가깝다(크로마를 안 버리므로).
 *
 * 맞추려면 JPEG 인코더를 직접 포팅해야 하는데, 결과가 더 나쁜 이미지다. 안 한다.
 */
export function renderPageForVision(
  mupdf: unknown,
  doc: MupdfDoc,
  pageIndex: number,
  dpi: number = SCAN_RENDER_DPI,
): { jpeg: Uint8Array; width: number; height: number; mimeType: string } {
  const M = mupdf as MupdfLike;
  const zoom = dpi / 72;
  const page = doc.loadPage(pageIndex);
  try {
    const pix = page.toPixmap(M.Matrix.scale(zoom, zoom), M.ColorSpace.DeviceRGB, false, true);
    try {
      const w = pix.getWidth();
      const h = pix.getHeight();
      const t = normalizeTarget(w, h);
      if (!t.resize) {
        return { jpeg: pix.asJPEG(JPEG_QUALITY, false), width: w, height: h, mimeType: "image/jpeg" };
      }
      const resized = resizeRgbLanczos(pix.getPixels(), w, h, pix.getStride(), t.width, t.height);
      const outPix = new M.Pixmap(M.ColorSpace.DeviceRGB, [0, 0, t.width, t.height], false);
      try {
        const dstStride = outPix.getStride();
        const dst = outPix.getPixels();
        // stride 는 폭 × 3 과 다를 수 있다 — 행 단위로 옮긴다.
        for (let y = 0; y < t.height; y++) {
          dst.set(resized.subarray(y * t.width * 3, (y + 1) * t.width * 3), y * dstStride);
        }
        return {
          jpeg: outPix.asJPEG(JPEG_QUALITY, false),
          width: t.width,
          height: t.height,
          mimeType: "image/jpeg",
        };
      } finally {
        outPix.destroy?.();
      }
    } finally {
      pix.destroy?.();
    }
  } finally {
    page.destroy?.();
  }
}

/** 페이지 수만 센다. 문서 열기는 0.7~2.0ms 라 이것만 위해 열어도 된다(§23 실측). */
export async function countPdfPages(bytes: Uint8Array): Promise<number> {
  // deno-lint-ignore no-explicit-any
  const mupdf = await import("mupdf") as any;
  const doc = mupdf.Document.openDocument(bytes, "application/pdf") as MupdfDoc;
  try {
    return doc.countPages();
  } finally {
    doc.destroy?.();
  }
}

/**
 * 바이트에서 문서를 열어 페이지 범위를 굽는다.
 *
 * 문서 열기는 0.7~2.0ms 로 사실상 공짜다(§23 실측) — 태스크마다 다시 열어도 된다.
 */
export async function renderPages(
  bytes: Uint8Array,
  pageIndices: number[],
  dpi: number = SCAN_RENDER_DPI,
): Promise<Map<number, RenderedPage>> {
  // 동적 import — vision 이 없는 경로에서는 WASM 을 로드하지 않는다.
  // deno-lint-ignore no-explicit-any
  const mupdf = await import("mupdf") as any;
  const doc = mupdf.Document.openDocument(bytes, "application/pdf") as MupdfDoc;
  try {
    const out = new Map<number, RenderedPage>();
    const total = doc.countPages();
    for (const idx of pageIndices) {
      if (idx < 0 || idx >= total) continue; // 범위 밖은 조용히 건너뛴다
      out.set(idx, renderPageToPng(mupdf, doc, idx, dpi));
    }
    return out;
  } finally {
    doc.destroy?.();
  }
}
