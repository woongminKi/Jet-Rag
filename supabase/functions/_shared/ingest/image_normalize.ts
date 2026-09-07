/**
 * 원본 `image_parser._normalize` 의 축소·재인코딩 경로 포팅.
 *
 * ## 왜 이걸 재현해야 하는가
 * vision 은 150 DPI PNG 를 **그대로** 보내지 않는다. Pillow 로 단변 1024px 까지
 * LANCZOS 축소한 뒤 JPEG q85 로 다시 굽고, 그 바이트를 Gemini 에 넘긴다.
 * Gemini 는 비결정적이라 "이미지가 좀 달라도 캡션이 같은지" 를 **사후에 측정할 수
 * 없다**. 그래서 모델에 들어가는 입력을 원본과 같게 만들어 질문 자체를 없앤다.
 *
 * 다른 후보 둘은 실측으로 탈락했다:
 * - `mupdf.Image#toPixmap(w, h)` — 요청 크기를 무시하고 원본 크기를 돌려준다.
 * - 목표 배율로 직접 렌더 — 크기는 맞지만(높이 ±1) 리샘플 경로가 달라 원본 대비
 *   PSNR 26dB. 선명도는 오히려 낫지만 "다른 그림" 인 건 사실이라 채택하지 않았다.
 *
 * ## Pillow 알고리즘을 그대로 옮겼다
 * `Resample.c` 의 `precompute_coeffs` → `normalize_coeffs_8bpc` → 가로 패스 →
 * 세로 패스. 특히 **중간 결과가 8비트로 잘린다**(가로 패스 출력이 clip8) — float 로
 * 쭉 계산하면 값이 미세하게 달라진다.
 *
 * `PRECISION_BITS = 22`, 누산 초기값 `1 << 21` 은 Pillow 의 반올림 방식이다.
 *
 * ## EXIF
 * 원본은 `ImageOps.exif_transpose` 를 부르지만, 우리 입력은 mupdf 가 방금 만든
 * 픽셀이라 EXIF 자체가 없다 — no-op 이다. 단독 이미지 업로드 경로(사용자 파일)를
 * 나중에 포팅할 때는 이 부분이 필요해진다.
 */

/** 명세 §15.2 DE-06 — 단변 max 1024px. */
export const MAX_SHORT_SIDE = 1024;

const PRECISION_BITS = 22;
const LANCZOS_SUPPORT = 3.0;

function sincFilter(x: number): number {
  if (x === 0.0) return 1.0;
  const t = x * Math.PI;
  return Math.sin(t) / t;
}

function lanczosFilter(x: number): number {
  if (-3.0 <= x && x < 3.0) return sincFilter(x) * sincFilter(x / 3);
  return 0.0;
}

interface Coeffs {
  ksize: number;
  bounds: Int32Array; // [min, count] × outSize
  kk: Int32Array; // 고정소수점 계수, outSize × ksize
}

/** Pillow `precompute_coeffs` + `normalize_coeffs_8bpc`. box 는 항상 전체 이미지다. */
function precomputeCoeffs(inSize: number, outSize: number): Coeffs {
  const scale = inSize / outSize;
  // 축소일 때만 필터를 늘린다. 확대(scale < 1)면 filterscale 은 1 로 고정.
  const filterScale = scale < 1.0 ? 1.0 : scale;
  const support = LANCZOS_SUPPORT * filterScale;
  const ksize = Math.ceil(support) * 2 + 1;

  const bounds = new Int32Array(outSize * 2);
  const kk = new Int32Array(outSize * ksize);
  const k = new Float64Array(ksize);

  for (let xx = 0; xx < outSize; xx++) {
    const center = (xx + 0.5) * scale;
    let ww = 0.0;
    const ss = 1.0 / filterScale;
    // Pillow 의 `(int)` 는 절삭이지만 여기 값은 항상 음이 아니라 trunc = floor 다.
    let xmin = Math.trunc(center - support + 0.5);
    if (xmin < 0) xmin = 0;
    let xmax = Math.trunc(center + support + 0.5);
    if (xmax > inSize) xmax = inSize;
    xmax -= xmin;

    for (let x = 0; x < xmax; x++) {
      const w = lanczosFilter((x + xmin - center + 0.5) * ss);
      k[x] = w;
      ww += w;
    }
    for (let x = 0; x < xmax; x++) {
      if (ww !== 0.0) k[x] /= ww;
    }
    for (let x = xmax; x < ksize; x++) k[x] = 0;

    const base = xx * ksize;
    for (let x = 0; x < ksize; x++) {
      const v = k[x];
      // 음수는 -0.5, 양수는 +0.5 를 더해 절삭 — Pillow 와 같은 0 방향 반올림.
      kk[base + x] = Math.trunc(v < 0 ? -0.5 + v * (1 << PRECISION_BITS)
                                     : 0.5 + v * (1 << PRECISION_BITS));
    }
    bounds[xx * 2] = xmin;
    bounds[xx * 2 + 1] = xmax;
  }
  return { ksize, bounds, kk };
}

function clip8(v: number): number {
  const s = v >> PRECISION_BITS;
  return s < 0 ? 0 : s > 255 ? 255 : s;
}

/** RGB(3채널) 8비트 이미지 축소. Pillow 와 같은 2패스 + 8비트 중간값. */
export function resizeRgbLanczos(
  src: Uint8Array | Uint8ClampedArray,
  srcW: number,
  srcH: number,
  srcStride: number,
  dstW: number,
  dstH: number,
): Uint8Array {
  const C = 3;
  // --- 가로 패스: (srcW, srcH) → (dstW, srcH) ---
  const hc = precomputeCoeffs(srcW, dstW);
  const mid = new Uint8Array(dstW * srcH * C);
  for (let yy = 0; yy < srcH; yy++) {
    const rowIn = yy * srcStride;
    const rowOut = yy * dstW * C;
    for (let xx = 0; xx < dstW; xx++) {
      const xmin = hc.bounds[xx * 2];
      const xmax = hc.bounds[xx * 2 + 1];
      const kbase = xx * hc.ksize;
      let s0 = 1 << (PRECISION_BITS - 1);
      let s1 = s0;
      let s2 = s0;
      for (let x = 0; x < xmax; x++) {
        const w = hc.kk[kbase + x];
        const p = rowIn + (x + xmin) * C;
        s0 += src[p] * w;
        s1 += src[p + 1] * w;
        s2 += src[p + 2] * w;
      }
      const o = rowOut + xx * C;
      mid[o] = clip8(s0);
      mid[o + 1] = clip8(s1);
      mid[o + 2] = clip8(s2);
    }
  }

  // --- 세로 패스: (dstW, srcH) → (dstW, dstH) ---
  const vc = precomputeCoeffs(srcH, dstH);
  const out = new Uint8Array(dstW * dstH * C);
  const midStride = dstW * C;
  for (let yy = 0; yy < dstH; yy++) {
    const ymin = vc.bounds[yy * 2];
    const ymax = vc.bounds[yy * 2 + 1];
    const kbase = yy * vc.ksize;
    const rowOut = yy * midStride;
    for (let xx = 0; xx < dstW; xx++) {
      let s0 = 1 << (PRECISION_BITS - 1);
      let s1 = s0;
      let s2 = s0;
      const col = xx * C;
      for (let y = 0; y < ymax; y++) {
        const w = vc.kk[kbase + y];
        const p = (y + ymin) * midStride + col;
        s0 += mid[p] * w;
        s1 += mid[p + 1] * w;
        s2 += mid[p + 2] * w;
      }
      const o = rowOut + col;
      out[o] = clip8(s0);
      out[o + 1] = clip8(s1);
      out[o + 2] = clip8(s2);
    }
  }
  return out;
}

export interface NormalizeTarget {
  width: number;
  height: number;
  /** 축소가 필요 없으면 false — 원본 픽셀 그대로 JPEG 로 굽는다. */
  resize: boolean;
}

/**
 * 원본 `_normalize` 의 크기 결정. `min_side > 1024` 일 때만 축소한다.
 *
 * Python `int()` 는 절삭이고 `max(1, ...)` 가 붙는다 — 둘 다 그대로 옮겼다.
 */
export function normalizeTarget(width: number, height: number): NormalizeTarget {
  const minSide = Math.min(width, height);
  if (minSide <= MAX_SHORT_SIDE) return { width, height, resize: false };
  const ratio = MAX_SHORT_SIDE / minSide;
  return {
    width: Math.max(1, Math.trunc(width * ratio)),
    height: Math.max(1, Math.trunc(height * ratio)),
    resize: true,
  };
}
