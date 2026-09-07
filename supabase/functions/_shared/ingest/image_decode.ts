/**
 * 원본 `image_parser._normalize` 포팅 — 사용자가 올린 이미지 파일을 Gemini 입력으로 굽는다.
 *
 * EXIF 회전 → 단변 1024px 축소 → 포맷 통일(알파가 의미 있으면 PNG, 아니면 JPEG q85).
 * 디코드에 실패하면 **raw bytes 를 그대로 돌려주고 warning 만 남긴다** — 원본과 같다.
 * 이 경로는 예외를 던지지 않는다.
 *
 * ## HEIC/HEIF 는 여기 오지 않는다
 * 원본은 HEIC 를 **디코드하지 않고** raw bytes 를 Gemini 에 그대로 넘긴다
 * (`image_parser.py:101`, 주석: "pillow-heif 등 추가 의존성 회피"). 호출부가 갈라 준다.
 *
 * ## mupdf 실측으로 정한 것들
 * | 확인한 것 | 결과 |
 * |---|---|
 * | png·jpeg·gray 디코드 | 된다 (`new mupdf.Image(bytes).toPixmap()`) |
 * | `getNumberOfComponents()` | 알파를 **포함**한다 (RGBA → 4, stride/width 로 확인) |
 * | EXIF 적용 | **안 한다** — 1600×1200(방향 6)을 그대로 준다. 그래서 직접 돌린다 |
 * | 알파 | **premultiplied** 다 (alpha=0 픽셀의 RGB 가 0,0,0) |
 * | `convertToColorSpace` 로 알파 떼기 | **못 한다** — "cannot drop alpha when converting pixmap" |
 * | `asJPEG` 에 알파 있는 픽스맵 | **던진다** — "pixmap may not have alpha to save as JPEG" |
 *
 * 그래서 알파를 뗄 때는 `alpha=false` 픽스맵을 새로 만들어 색 채널만 옮긴다.
 */

import {
  applyOrientation,
  type Orientation,
  type Raster,
  readOrientation,
} from "./exif_orientation.ts";
import { normalizeTarget, resizeLanczosN } from "./image_normalize.ts";

const JPEG_QUALITY = 85;

export interface NormalizedImage {
  bytes: Uint8Array;
  mimeType: string;
  warnings: string[];
}

// deno-lint-ignore no-explicit-any
type Any = any;

/**
 * stride 를 걷어내고 `width * comps` 로 촘촘히 채운다.
 *
 * `getPixels()` 는 **`Uint8ClampedArray`** 를 준다(`Uint8Array` 가 아니다). 그대로
 * 들고 다니면 `Deno.writeFile` 같은 곳에서 타입으로 걸린다 — 여기서 한 번에 맞춘다.
 */
function toRaster(pix: Any): Raster {
  const w: number = pix.getWidth();
  const h: number = pix.getHeight();
  const comps: number = pix.getNumberOfComponents();
  const stride: number = pix.getStride();
  const src: Uint8Array | Uint8ClampedArray = pix.getPixels();
  const out = new Uint8Array(w * h * comps);
  const rowBytes = w * comps;
  for (let y = 0; y < h; y++) {
    out.set(src.subarray(y * stride, y * stride + rowBytes), y * rowBytes);
  }
  return { pixels: out, width: w, height: h, comps };
}

/**
 * 원본 `_has_transparency` — 알파 채널에 **255 아닌 값이 하나라도** 있으면 true.
 * `getextrema()[0] < 255` 와 같은 판정이다.
 */
function hasMeaningfulAlpha(r: Raster): boolean {
  const { pixels, width, height, comps } = r;
  const ai = comps - 1;
  for (let i = 0, n = width * height; i < n; i++) {
    if (pixels[i * comps + ai] !== 255) return true;
  }
  return false;
}

/** 색 채널만 뽑아 RGB 로 편다 (그레이는 복제, 알파는 버린다). */
function toRgb(r: Raster, hasAlpha: boolean): Uint8Array {
  const colorComps = r.comps - (hasAlpha ? 1 : 0);
  if (colorComps === 3 && !hasAlpha) return r.pixels;
  const n = r.width * r.height;
  const out = new Uint8Array(n * 3);
  for (let i = 0; i < n; i++) {
    const s = i * r.comps;
    if (colorComps === 1) {
      const v = r.pixels[s];
      out[i * 3] = v;
      out[i * 3 + 1] = v;
      out[i * 3 + 2] = v;
    } else {
      out[i * 3] = r.pixels[s];
      out[i * 3 + 1] = r.pixels[s + 1];
      out[i * 3 + 2] = r.pixels[s + 2];
    }
  }
  return out;
}

/** 촘촘한 픽셀을 mupdf 픽스맵에 실어 인코딩한다. stride 가 폭×채널과 다를 수 있다. */
function encode(
  M: Any,
  pixels: Uint8Array,
  width: number,
  height: number,
  comps: number,
  alpha: boolean,
  format: "png" | "jpeg",
): Uint8Array {
  const colorComps = comps - (alpha ? 1 : 0);
  const cs = colorComps === 1 ? M.ColorSpace.DeviceGray : M.ColorSpace.DeviceRGB;
  const pix = new M.Pixmap(cs, [0, 0, width, height], alpha);
  try {
    const dst: Uint8Array = pix.getPixels();
    const dstStride: number = pix.getStride();
    const rowBytes = width * comps;
    for (let y = 0; y < height; y++) {
      dst.set(pixels.subarray(y * rowBytes, (y + 1) * rowBytes), y * dstStride);
    }
    return format === "png" ? pix.asPNG() : pix.asJPEG(JPEG_QUALITY, false);
  } finally {
    pix.destroy?.();
  }
}

/** 인코딩 **직전** 상태. 대조는 여기서 한다 — 인코더 차이를 방정식에서 뺀다. */
export interface PreEncode {
  /** 디코드 실패로 raw bytes 를 그대로 쓰는 경우. 이때 `raster` 는 없다. */
  passthrough: boolean;
  raster?: Raster;
  /**
   * **축소 직전** 상태(디코드 + EXIF 회전까지). 대조 스크립트가 "축소가 디코더 차이를
   * 증폭했는가" 를 재는 데 쓴다 — 이 값이 없으면 임계값을 손으로 정할 수밖에 없다.
   */
  beforeResize?: Raster;
  /** 알파를 살려 PNG 로 굽는가. */
  meaningfulAlpha: boolean;
  mimeType: string;
  warnings: string[];
}

/**
 * 디코드 → EXIF 회전 → 축소까지. **인코딩 전에 멈춘다.**
 *
 * 대조 스크립트가 이 결과를 Pillow 의 인코딩 전 픽셀과 **바이트 단위로** 비교한다.
 * 최종 바이트로 비교하면 두 JPEG 인코더의 차이가 섞여 내 포팅이 맞는지 알 수 없다
 * (실제로 그렇게 재다가 판정을 못 하게 됐다).
 *
 * 순서가 곧 결과다: **회전 → 축소**. 방향 6 인 1600×1200 은 회전하면 1200×1600 이
 * 되어 단변이 1200 → 축소 대상이 된다. 순서를 바꾸면 최종 크기가 달라진다.
 */
export async function normalizeToRaster(
  data: Uint8Array,
  mime: string,
): Promise<PreEncode> {
  const warnings: string[] = [];
  const M = await import("mupdf") as Any;

  let raster: Raster;
  try {
    const img = new M.Image(data);
    const pix = img.toPixmap();
    try {
      raster = toRaster(pix);
    } finally {
      pix.destroy?.();
    }
  } catch (e) {
    // 원본: 디코드 실패면 raw bytes 그대로 쓰고 warning 만 남긴다.
    warnings.push(`이미지 디코드 실패, raw bytes 그대로 사용: ${e}`);
    console.warn(`이미지 디코드 실패: ${e}`);
    return { passthrough: true, meaningfulAlpha: false, mimeType: mime, warnings };
  }

  try {
    const o: Orientation = readOrientation(data);
    raster = applyOrientation(raster, o);
  } catch (e) {
    // 원본도 transpose 실패를 삼키고 계속 간다.
    warnings.push(`EXIF transpose 실패 (계속 진행): ${e}`);
    console.warn(`EXIF transpose 실패: ${e}`);
  }

  // 알파 판정은 **축소 전** 픽셀로 한다 — 원본도 resize 뒤 `_has_transparency` 를
  // 부르지만, 축소는 알파를 새로 만들지 않으므로(전부 255 면 255 로 남는다) 같다.
  const alpha = raster.comps === 2 || raster.comps === 4;
  const meaningfulAlpha = alpha && hasMeaningfulAlpha(raster);

  const beforeResize: Raster = {
    pixels: raster.pixels,
    width: raster.width,
    height: raster.height,
    comps: raster.comps,
  };

  const t = normalizeTarget(raster.width, raster.height);
  if (t.resize) {
    raster = {
      pixels: resizeLanczosN(
        raster.pixels,
        raster.width,
        raster.height,
        raster.width * raster.comps,
        t.width,
        t.height,
        raster.comps,
      ),
      width: t.width,
      height: t.height,
      comps: raster.comps,
    };
  }

  // 알파가 의미 없으면 여기서 RGB 로 편다 — Pillow `convert("RGB")` 자리다.
  if (!meaningfulAlpha && raster.comps !== 3) {
    raster = {
      pixels: toRgb(raster, alpha),
      width: raster.width,
      height: raster.height,
      comps: 3,
    };
  }

  return {
    passthrough: false,
    raster,
    beforeResize,
    meaningfulAlpha,
    mimeType: meaningfulAlpha ? "image/png" : "image/jpeg",
    warnings,
  };
}

/** 원본 `_normalize(data, mime)` — `(bytes, mime, warnings)`. */
export async function normalizeImage(
  data: Uint8Array,
  mime: string,
): Promise<NormalizedImage> {
  const pre = await normalizeToRaster(data, mime);
  if (pre.passthrough || !pre.raster) {
    return { bytes: data, mimeType: pre.mimeType, warnings: pre.warnings };
  }
  const M = await import("mupdf") as Any;
  const r = pre.raster;
  return {
    bytes: encode(
      M,
      r.pixels,
      r.width,
      r.height,
      r.comps,
      pre.meaningfulAlpha,
      pre.meaningfulAlpha ? "png" : "jpeg",
    ),
    mimeType: pre.mimeType,
    warnings: pre.warnings,
  };
}
