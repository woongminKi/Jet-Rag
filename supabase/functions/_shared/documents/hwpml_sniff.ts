/**
 * HWPML(XML) 판별 — `adapters/impl/hwpml_parser.is_hwpml_bytes` 포팅.
 *
 * `.hwp` 는 두 갈래다: OLE2(한컴 5.x)와 HWPML XML(법제처 export 등). 입력 게이트가
 * 둘 다 통과시키고, 실제 분기는 extract 가 다시 한다.
 *
 * 임의 XML 을 `.hwp` 로 위장한 것은 루트 태그가 달라 걸러진다.
 */

const HWPML_ROOT = "HWPML";
const SNIFF_BYTES = 4096;

/** Python `bytes.lstrip()` 의 기본 공백 집합. */
const PY_BYTE_WS = new Set([0x20, 0x09, 0x0A, 0x0D, 0x0B, 0x0C]);
/** UTF-8 BOM 바이트. 원본 `lstrip(b"\xef\xbb\xbf")` 는 **세 바이트를 집합으로** 본다. */
const BOM_BYTES = new Set([0xEF, 0xBB, 0xBF]);

function indexOfBytes(hay: Uint8Array, needle: number[], end: number): boolean {
  const limit = Math.min(hay.length, end) - needle.length;
  outer: for (let i = 0; i <= limit; i++) {
    for (let k = 0; k < needle.length; k++) if (hay[i + k] !== needle[k]) continue outer;
    return true;
  }
  return false;
}

export function isHwpmlBytes(head: Uint8Array): boolean {
  if (head.length === 0) return false;

  // ① BOM 바이트를 앞에서 걷어낸다. 원본은 집합 제거라 순서·개수를 안 따진다.
  let i = 0;
  while (i < head.length && BOM_BYTES.has(head[i])) i++;
  // ② 그다음 공백을 걷어내고 `<?xml` 로 시작해야 한다.
  while (i < head.length && PY_BYTE_WS.has(head[i])) i++;
  const XML_DECL = [0x3C, 0x3F, 0x78, 0x6D, 0x6C]; // "<?xml"
  for (let k = 0; k < XML_DECL.length; k++) {
    if (head[i + k] !== XML_DECL[k]) return false;
  }

  // ③ 앞 4KB 안에 `<HWPML` 이 나와야 한다. **원본은 여기서 head 원본을 본다**
  //    (BOM 을 걷어낸 문자열이 아니라) — 그대로 옮긴다.
  const needle = [0x3C, ...Array.from(HWPML_ROOT, (c) => c.charCodeAt(0))];
  return indexOfBytes(head, needle, SNIFF_BYTES);
}
