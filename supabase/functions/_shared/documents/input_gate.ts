/**
 * 업로드 입력 게이트 — `routers/_input_gate.py` 포팅.
 *
 * 목적은 **"exe 가 .docx 로 위장" 차단**이지 정밀 분류가 아니다(원본 주석).
 *
 * ## `filetype` 전체를 옮기지 않는다 — 옮길 필요가 없다
 * 원본은 `filetype.guess()` 로 MIME 을 얻어 `_EXT_TO_MIMES[ext]` 에 있는지 본다.
 * 즉 판정은 **"허용 목록에 드는가"** 뿐이다. 허용 목록에 없는 MIME 은 어떤 값이든
 * 전부 거절이므로, **허용되는 것만 정확히 인식하고 나머지는 `null` 로 두면 결과가 같다.**
 *
 * 실측으로 확인했다(2026-09-07, filetype 1.2.0):
 * | 입력 | Python `guess` | 판정 |
 * |---|---|---|
 * | `MZ…`(exe) as .pdf | `application/x-msdownload` | 거절 |
 * | Mach-O as .docx | `None` | 거절 |
 * | 3 바이트 as .png | `None` | 거절 |
 *
 * 앞의 둘은 반환값이 다르지만 **둘 다 거절**이다. 그래서 exe·Mach-O 매처를 옮기지
 * 않아도 판정이 갈리지 않는다. 매처는 원본 그대로 옮겼다 — 느슨하게 짜면 위장 파일을
 * 통과시킨다.
 *
 * ## HWP 는 두 갈래다
 * OLE2(5.x) 와 HWPML(XML, 법제처 export). 어느 쪽인지는 extract 가 다시 판단한다.
 */

import { isHwpmlBytes } from "./hwpml_sniff.ts";

/** `documents.py` 의 `_ALLOWED_EXTENSIONS`. */
export const ALLOWED_EXTENSIONS: Record<string, string> = {
  ".pdf": "pdf",
  ".hwp": "hwp",
  ".hwpx": "hwpx",
  ".docx": "docx",
  ".pptx": "pptx",
  ".jpg": "image",
  ".jpeg": "image",
  ".png": "image",
  ".heic": "image",
  ".txt": "txt",
  ".md": "md",
};

/** `filetype` 가 안정적으로 식별하려면 ≥262 B. 원본과 같은 4KB 마진. */
export const HEAD_BYTES = 4096;
export const MAX_SIZE_BYTES = 50 * 1024 * 1024;

/** OLE2 / Compound File Binary — HWP 5.x, 옛 Office 공통. */
const OLE2_PREFIX = [0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1];

/** 확장자 → 허용 MIME 집합. 빈 집합 = 시그니처가 없는 포맷(평문, OLE2 HWP). */
const EXT_TO_MIMES: Record<string, string[]> = {
  ".pdf": ["application/pdf"],
  ".png": ["image/png"],
  ".jpg": ["image/jpeg"],
  ".jpeg": ["image/jpeg"],
  ".heic": ["image/heic", "image/heif"],
  // ZIP 컨테이너 — filetype 이 buf prefix 에 따라 zip 또는 deep MIME 을 준다. 양쪽 허용.
  ".docx": [
    "application/zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ],
  ".hwpx": ["application/zip"],
  ".pptx": [
    "application/zip",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ],
  ".hwp": [],
  ".txt": [],
  ".md": [],
};

function startsWith(buf: Uint8Array, sig: number[]): boolean {
  if (buf.length < sig.length) return false;
  for (let i = 0; i < sig.length; i++) if (buf[i] !== sig[i]) return false;
  return true;
}

/** `filetype.types.isobmff.IsoBmff._is_isobmff` + `_get_ftyp`. */
function ftypInfo(buf: Uint8Array): { major: string; brands: string[] } | null {
  if (buf.length < 16) return null;
  // buf[4:8] === "ftyp"
  if (buf[4] !== 0x66 || buf[5] !== 0x74 || buf[6] !== 0x79 || buf[7] !== 0x70) return null;
  // 원본: `len(buf) < int(hex(buf[0:4]), 16)` 이면 False — 박스 길이가 버퍼보다 크면 탈락.
  const boxLen = (buf[0] << 24 | buf[1] << 16 | buf[2] << 8 | buf[3]) >>> 0;
  if (buf.length < boxLen) return null;
  const dec = new TextDecoder("utf-8", { fatal: false });
  const major = dec.decode(buf.subarray(8, 12));
  const brands: string[] = [];
  for (let i = 16; i < boxLen; i += 4) brands.push(dec.decode(buf.subarray(i, i + 4)));
  return { major, brands };
}

/**
 * `filetype.guess(head).mime` 중 **허용 목록에 드는 것만** 재현한다.
 * 나머지는 `null` — 원본이 어떤 MIME 을 주든 허용 목록에 없으면 거절이라 결과가 같다.
 */
export function guessAllowedMime(buf: Uint8Array): string | null {
  // 매처는 filetype 1.2.0 소스 그대로. 느슨하면 위장 파일이 통과한다.
  if (buf.length > 3 && buf[0] === 0x25 && buf[1] === 0x50 && buf[2] === 0x44 && buf[3] === 0x46) {
    return "application/pdf"; // "%PDF"
  }
  if (buf.length > 3 && buf[0] === 0x89 && buf[1] === 0x50 && buf[2] === 0x4E && buf[3] === 0x47) {
    return "image/png";
  }
  if (buf.length > 2 && buf[0] === 0xFF && buf[1] === 0xD8 && buf[2] === 0xFF) {
    return "image/jpeg";
  }
  if (
    buf.length > 3 && buf[0] === 0x50 && buf[1] === 0x4B &&
    (buf[2] === 0x3 || buf[2] === 0x5 || buf[2] === 0x7) &&
    (buf[3] === 0x4 || buf[3] === 0x6 || buf[3] === 0x8)
  ) {
    return "application/zip";
  }
  // `filetype` 1.2.0 의 Heic 매처 그대로. **`image/heif` 타입은 존재하지 않는다** —
  // `_EXT_TO_MIMES[".heic"]` 에 적혀 있지만 filetype 이 절대 반환하지 않는 도달 불가
  // 값이다. 처음엔 "mif1/msf1 이면 heif" 로 넓혔다가 대조가 잡았다
  // (`mif1` + brand 없음: py 거절 / ts 통과). 넓히면 위장 파일이 통과한다.
  const ftyp = ftypInfo(buf);
  if (ftyp) {
    if (ftyp.major === "heic") return "image/heic";
    if ((ftyp.major === "mif1" || ftyp.major === "msf1") && ftyp.brands.includes("heic")) {
      return "image/heic";
    }
  }
  return null;
}

export class InputGateError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "InputGateError";
  }
}

/**
 * 확장자 ↔ 매직바이트 일치 검증. 어긋나면 400 으로 던진다.
 *
 * 호출자 책임: `ext` 가 `ALLOWED_EXTENSIONS` 를 통과한 상태여야 한다.
 */
export function validateMagic(ext: string, head: Uint8Array): void {
  const expected = EXT_TO_MIMES[ext];
  if (expected === undefined) {
    // 화이트리스트와 매핑이 어긋난 경우 — 코드 버그.
    throw new InputGateError(400, `지원되지 않는 확장자입니다: ${ext}`);
  }

  // HWP — OLE2(5.x) 또는 HWPML(XML) 둘 다 허용. 분기는 extract 가 다시 한다.
  if (ext === ".hwp") {
    if (startsWith(head, OLE2_PREFIX)) return;
    if (isHwpmlBytes(head)) return;
    throw new InputGateError(400, "HWP 5.x(OLE2) 또는 HWPML(XML) 시그니처가 아닙니다.");
  }

  // 평문(.txt/.md) — 시그니처가 없어 검증을 건너뛴다.
  if (expected.length === 0) return;

  const mime = guessAllowedMime(head);
  if (mime === null) {
    // 식별 불가 — Mach-O 같은 것 포함. 보수적으로 거절.
    throw new InputGateError(
      400,
      `파일 형식을 식별할 수 없습니다. 확장자(${ext})와 일치하는 시그니처가 필요합니다.`,
    );
  }
  if (!expected.includes(mime)) {
    throw new InputGateError(400, `확장자(${ext})와 파일 내용(${mime})이 일치하지 않습니다.`);
  }
}
