/**
 * EXIF Orientation — 읽기와 8 변환을 고정한다.
 *
 * 전체 대조는 `api/scripts/verify_image_decode_parity.py` 가 **Pillow 와 픽셀 단위로**
 * 한다(163건). 그건 Pillow·numpy 가 있어야 돌아서, 여기엔 방향표가 뒤집히면 바로
 * 깨지는 최소한만 남긴다. 방향표는 외워서 쓰면 틀리는 종류다.
 */

import { assertEquals } from "@std/assert";

import { applyOrientation, type Orientation, readOrientation } from "./exif_orientation.ts";

/**
 * 2×3 (가로 2 · 세로 3) 1채널 라스터.
 * ```
 * 1 2
 * 3 4
 * 5 6
 * ```
 * 값이 곧 위치라 어떤 변환이 걸렸는지 눈으로 읽힌다.
 */
const SRC = { pixels: new Uint8Array([1, 2, 3, 4, 5, 6]), width: 2, height: 3, comps: 1 };

Deno.test("8 변환 — Pillow `exif_transpose` 표와 같다", () => {
  const cases: [Orientation, number, number, number[]][] = [
    [1, 2, 3, [1, 2, 3, 4, 5, 6]], // 그대로
    [2, 2, 3, [2, 1, 4, 3, 6, 5]], // 좌우 반전
    [3, 2, 3, [6, 5, 4, 3, 2, 1]], // 180°
    [4, 2, 3, [5, 6, 3, 4, 1, 2]], // 상하 반전
    [5, 3, 2, [1, 3, 5, 2, 4, 6]], // TRANSPOSE (주대각 반사)
    [6, 3, 2, [5, 3, 1, 6, 4, 2]], // ROTATE_270 (시계 90°)
    [7, 3, 2, [6, 4, 2, 5, 3, 1]], // TRANSVERSE (반대각 반사)
    [8, 3, 2, [2, 4, 6, 1, 3, 5]], // ROTATE_90 (반시계 90°)
  ];
  for (const [o, w, h, expected] of cases) {
    const r = applyOrientation(SRC, o);
    assertEquals([r.width, r.height], [w, h], `방향 ${o} 크기`);
    assertEquals([...r.pixels], expected, `방향 ${o} 픽셀`);
  }
});

Deno.test("방향 5~8 만 가로세로가 뒤바뀐다", () => {
  for (let o = 1 as number; o <= 8; o++) {
    const r = applyOrientation(SRC, o as Orientation);
    const swapped = r.width === 3 && r.height === 2;
    assertEquals(swapped, o >= 5, `방향 ${o}`);
  }
});

Deno.test("EXIF 가 없거나 못 읽으면 1 이다 — 던지지 않는다", () => {
  assertEquals(readOrientation(new Uint8Array(0)), 1);
  assertEquals(readOrientation(new Uint8Array([0xff, 0xd8])), 1); // SOI 뿐
  assertEquals(readOrientation(new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8])), 1);
  // PNG 시그니처만 있고 청크가 없는 경우
  assertEquals(
    readOrientation(new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])),
    1,
  );
  // 세그먼트 길이가 깨진 JPEG — 무한 루프도 예외도 안 된다
  assertEquals(readOrientation(new Uint8Array([0xff, 0xd8, 0xff, 0xe1, 0x00, 0x00])), 1);
});

Deno.test("JPEG APP1 을 읽는다 — APP0 뒤에 있어도 찾는다", () => {
  // 실제 Pillow 출력이 `APP0(JFIF) → APP1(Exif)` 순서였다(실측 덤프).
  // APP1 을 첫 세그먼트로 가정하면 여기서 깨진다.
  const tiff = [
    0x4d,
    0x4d,
    0x00,
    0x2a, // "MM" + 42 (big endian)
    0x00,
    0x00,
    0x00,
    0x08, // IFD0 offset = 8
    0x00,
    0x01, // entry 1개
    0x01,
    0x12,
    0x00,
    0x03,
    0x00,
    0x00,
    0x00,
    0x01,
    0x00,
    0x06,
    0x00,
    0x00, // Orientation=6
  ];
  const app1 = [0x45, 0x78, 0x69, 0x66, 0x00, 0x00, ...tiff]; // "Exif\0\0" + TIFF
  const bytes = new Uint8Array([
    0xff,
    0xd8,
    0xff,
    0xe0,
    0x00,
    0x04,
    0x00,
    0x00, // APP0 더미
    0xff,
    0xe1,
    ((app1.length + 2) >> 8) & 0xff,
    (app1.length + 2) & 0xff,
    ...app1,
    0xff,
    0xda,
    0x00,
    0x02, // SOS
  ]);
  assertEquals(readOrientation(bytes), 6);
});

Deno.test("리틀엔디언 TIFF 도 읽는다", () => {
  const tiff = [
    0x49,
    0x49,
    0x2a,
    0x00, // "II" + 42 (little endian)
    0x08,
    0x00,
    0x00,
    0x00,
    0x01,
    0x00,
    0x12,
    0x01,
    0x03,
    0x00,
    0x01,
    0x00,
    0x00,
    0x00,
    0x08,
    0x00,
    0x00,
    0x00, // Orientation=8
  ];
  const app1 = [0x45, 0x78, 0x69, 0x66, 0x00, 0x00, ...tiff];
  const bytes = new Uint8Array([
    0xff,
    0xd8,
    0xff,
    0xe1,
    ((app1.length + 2) >> 8) & 0xff,
    (app1.length + 2) & 0xff,
    ...app1,
    0xff,
    0xda,
    0x00,
    0x02,
  ]);
  assertEquals(readOrientation(bytes), 8);
});

Deno.test("범위 밖 방향값은 무시한다", () => {
  const mk = (v: number) => {
    const tiff = [
      0x4d,
      0x4d,
      0x00,
      0x2a,
      0x00,
      0x00,
      0x00,
      0x08,
      0x00,
      0x01,
      0x01,
      0x12,
      0x00,
      0x03,
      0x00,
      0x00,
      0x00,
      0x01,
      (v >> 8) & 0xff,
      v & 0xff,
      0x00,
      0x00,
    ];
    const app1 = [0x45, 0x78, 0x69, 0x66, 0x00, 0x00, ...tiff];
    return new Uint8Array([
      0xff,
      0xd8,
      0xff,
      0xe1,
      ((app1.length + 2) >> 8) & 0xff,
      (app1.length + 2) & 0xff,
      ...app1,
      0xff,
      0xda,
      0x00,
      0x02,
    ]);
  };
  assertEquals(readOrientation(mk(0)), 1);
  assertEquals(readOrientation(mk(9)), 1);
  assertEquals(readOrientation(mk(3)), 3);
});
