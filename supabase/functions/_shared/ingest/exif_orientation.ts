/**
 * EXIF Orientation 읽기 + 적용 — Pillow `ImageOps.exif_transpose` 의 자리.
 *
 * ## mupdf 는 이걸 안 해준다 (실측)
 * `new mupdf.Image(bytes)` 는 Orientation=6 인 1600×1200 JPEG 을 **1600×1200 그대로**
 * 준다. `Image` 프로토타입에도 EXIF 를 주는 메서드가 없다
 * (`getWidth · getHeight · getNumberOfComponents · getBitsPerComponent ·
 * getXResolution · getYResolution · getImageMask · getColorSpace · getMask · toPixmap`).
 * 그래서 여기서 직접 읽고 직접 돌린다. **이중 적용 위험은 없다.**
 *
 * ## 방향이 크기를 바꾼다 — 그래서 축소보다 먼저다
 * 원본도 `exif_transpose` → 단변 축소 순서다. 1600×1200(방향 6)은 회전하면 1200×1600 이
 * 되고 단변이 1200 이라 축소 대상이 된다. 순서를 바꾸면 **최종 크기가 달라진다**
 * (실측: Pillow 결과 1024×1365).
 *
 * ## 세그먼트를 훑는다 — APP1 이 첫 세그먼트라고 가정하면 틀린다
 * Pillow 가 쓴 파일도 `APP0(JFIF) → APP1(Exif)` 순서였다(실측 덤프).
 */

/** EXIF 표준 Orientation. 1 은 "그대로". 범위 밖이면 무시한다. */
export type Orientation = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8;

const ORIENTATION_TAG = 0x0112;

function u16(b: Uint8Array, o: number, little: boolean): number {
  return little ? b[o] | (b[o + 1] << 8) : (b[o] << 8) | b[o + 1];
}

function u32(b: Uint8Array, o: number, little: boolean): number {
  return little
    ? (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0
    : ((b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]) >>> 0;
}

/**
 * TIFF 헤더(`II`/`MM` + 42)로 시작하는 블록에서 IFD0 의 Orientation 을 찾는다.
 * 못 찾거나 형식이 깨졌으면 `null` — **던지지 않는다**(원본도 실패를 warning 으로 넘긴다).
 */
function readTiffOrientation(tiff: Uint8Array): Orientation | null {
  if (tiff.length < 8) return null;
  const little = tiff[0] === 0x49 && tiff[1] === 0x49;
  const big = tiff[0] === 0x4d && tiff[1] === 0x4d;
  if (!little && !big) return null;
  if (u16(tiff, 2, little) !== 42) return null;

  const ifd0 = u32(tiff, 4, little);
  if (ifd0 + 2 > tiff.length) return null;
  const count = u16(tiff, ifd0, little);
  for (let i = 0; i < count; i++) {
    const e = ifd0 + 2 + i * 12;
    if (e + 12 > tiff.length) return null;
    if (u16(tiff, e, little) !== ORIENTATION_TAG) continue;
    // type 3 = SHORT. 값이 4 바이트 필드 안에 들어가므로 오프셋 추적이 필요 없다.
    const type = u16(tiff, e + 2, little);
    const v = type === 3 ? u16(tiff, e + 8, little) : u32(tiff, e + 8, little);
    return v >= 1 && v <= 8 ? v as Orientation : null;
  }
  return null;
}

/** JPEG APP1(`Exif\0\0`) 세그먼트를 찾는다. */
function readJpegOrientation(b: Uint8Array): Orientation | null {
  if (b.length < 4 || b[0] !== 0xff || b[1] !== 0xd8) return null;
  let i = 2;
  while (i + 4 <= b.length) {
    if (b[i] !== 0xff) return null;
    const marker = b[i + 1];
    // SOI/EOI/RSTn 은 길이 필드가 없다. SOS 이후는 압축 데이터라 더 볼 것이 없다.
    if (marker === 0xd8 || marker === 0xd9) return null;
    if (marker === 0xda) return null;
    const len = u16(b, i + 2, false);
    if (len < 2 || i + 2 + len > b.length) return null;
    if (marker === 0xe1) {
      const seg = b.subarray(i + 4, i + 2 + len);
      // `Exif\0\0` 6 바이트 뒤부터가 TIFF 다. XMP 도 APP1 이라 id 확인이 필수다.
      if (
        seg.length > 6 && seg[0] === 0x45 && seg[1] === 0x78 && seg[2] === 0x69 &&
        seg[3] === 0x66 && seg[4] === 0x00 && seg[5] === 0x00
      ) {
        const r = readTiffOrientation(seg.subarray(6));
        if (r !== null) return r;
      }
    }
    i += 2 + len;
  }
  return null;
}

/** PNG `eXIf` 청크. Pillow 도 이걸 읽는다(8.4+). */
function readPngOrientation(b: Uint8Array): Orientation | null {
  const SIG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  if (b.length < 8) return null;
  for (let i = 0; i < 8; i++) if (b[i] !== SIG[i]) return null;
  let p = 8;
  while (p + 8 <= b.length) {
    const len = u32(b, p, false);
    const type = String.fromCharCode(b[p + 4], b[p + 5], b[p + 6], b[p + 7]);
    if (type === "IDAT" || type === "IEND") return null; // 이 뒤엔 없다고 봐도 된다
    if (type === "eXIf") {
      if (p + 8 + len > b.length) return null;
      return readTiffOrientation(b.subarray(p + 8, p + 8 + len));
    }
    p += 12 + len; // 길이(4) + 타입(4) + 데이터 + CRC(4)
  }
  return null;
}

/** 바이트에서 Orientation 을 읽는다. 없거나 못 읽으면 `1`(그대로). */
export function readOrientation(bytes: Uint8Array): Orientation {
  return readJpegOrientation(bytes) ?? readPngOrientation(bytes) ?? 1;
}

export interface Raster {
  /** 행 사이 빈틈 없이 `width * comps` 로 채운 픽셀. */
  pixels: Uint8Array;
  width: number;
  height: number;
  comps: number;
}

/**
 * Pillow `Image.transpose` 와 같은 8 변환.
 *
 * 매핑은 Pillow 소스의 `exif_transpose` 표를 따른다 —
 * `2:FLIP_LEFT_RIGHT · 3:ROTATE_180 · 4:FLIP_TOP_BOTTOM · 5:TRANSPOSE ·
 * 6:ROTATE_270 · 7:TRANSVERSE · 8:ROTATE_90`.
 * **8 방향 전부 Pillow 와 픽셀 단위로 대조했다**(`verify_image_normalize_parity.py`) —
 * 이 표는 외워서 쓰면 틀리는 종류라 대조가 곧 근거다.
 */
export function applyOrientation(src: Raster, orientation: Orientation): Raster {
  if (orientation === 1) return src;
  const { pixels, width: w, height: h, comps: c } = src;
  const swap = orientation >= 5;
  const dw = swap ? h : w;
  const dh = swap ? w : h;
  const out = new Uint8Array(dw * dh * c);

  for (let y = 0; y < dh; y++) {
    for (let x = 0; x < dw; x++) {
      let sx: number, sy: number;
      switch (orientation) {
        case 2:
          sx = w - 1 - x;
          sy = y;
          break;
        case 3:
          sx = w - 1 - x;
          sy = h - 1 - y;
          break;
        case 4:
          sx = x;
          sy = h - 1 - y;
          break;
        case 5:
          sx = y;
          sy = x;
          break;
        case 6:
          sx = y;
          sy = h - 1 - x;
          break;
        case 7:
          sx = w - 1 - y;
          sy = h - 1 - x;
          break;
        default:
          sx = w - 1 - y;
          sy = x;
          break; // 8
      }
      const si = (sy * w + sx) * c;
      const di = (y * dw + x) * c;
      for (let k = 0; k < c; k++) out[di + k] = pixels[si + k];
    }
  }
  return { pixels: out, width: dw, height: dh, comps: c };
}
