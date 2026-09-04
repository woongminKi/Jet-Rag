/**
 * HWPX(ZIP+XML) · HWPML(XML) 텍스트 추출 — 현행 Python 파서의 산출물을 재현한다.
 *
 * S1 에서 확정된 것: `@rhwp/core` 는 **HWP 5.x(OLE2) 전용**이다. 기준선 6샘플 중
 * HWPX 2건 + HWPML 1건은 그 경로로 처리할 수 없다. 둘 다 결국 XML 이라 직접 읽는다.
 *
 * ## 재현 대상 (원본 코드가 진실이다)
 *
 * `app/adapters/impl/hwpx_parser.py` (+ `hwpx` 라이브러리의 `TextExtractor`)
 *   - 섹션 = `Contents/section*.xml`, 이름 오름차순
 *   - 단락 = 섹션 안의 **모든 후손 `hp:p`** (`include_nested=True`) — 표 안 단락도 포함된다
 *     (`hp:tbl > hp:tr > hp:tc > hp:subList > hp:p`)
 *   - 단락 텍스트 = **직계 `hp:run`** 의 자식 중 `hp:t`(텍스트) / `lineBreak`(→`\n`) / `tab`(→`\t`).
 *     `ctrl`·`footNote`·그림 등 객체는 기본값(`skip`)으로 버린다
 *   - heading = `Contents/header.xml` 의 `hh:style` 에서 `styleIDRef` → `name` 을 찾아 정규식,
 *     아니면 텍스트 패턴. sticky propagate
 *
 * `app/adapters/impl/hwpml_parser.py`
 *   - 루트가 `HWPML` 이어야 한다
 *   - `BODY > SECTION` 마다 `fallback_title = "section {Id}"`
 *   - 단락 = SECTION 안의 **모든 후손 `P`** 중 **자기 안에 `P` 가 없는 것만**
 *     (중첩 P 가 있으면 컨테이너로 보고 skip — 자식이 자기 텍스트를 따로 낸다)
 *   - 단락 텍스트 = **직계 `TEXT` 의 직계 `CHAR`** 만 (`./TEXT/CHAR`).
 *     `PARAMETERSET`·`SECDEF` 안의 스타일 메타데이터 CHAR 를 피하려는 제한이다
 *   - heading 은 **텍스트 패턴만** — HWPML 은 style 매핑을 쓰지 않는다
 *   - `section_title = current_title || fallback_title`
 *
 * ## 대상 문서 실측 (2026-09-04)
 * `직제_규정.hwpx`: 섹션 1개, `hp:tbl` 6개, 스타일 26개(`조`/`장`/`절`/`원`/`1.` …).
 * `law sample2.hwp`: 이름만 `.hwp` 이고 실제로는 **UTF-8 BOM XML(HWPML)** — 확장자를 믿으면 안 된다.
 */

import { unzipSync } from "fflate";

import {
  allDescendants,
  attrValue,
  decodeXml,
  directChildren,
  scanTags,
  stripComments,
} from "./xml_scan.ts";

export interface HwpSection {
  text: string;
  page: number | null;
  sectionTitle: string | null;
}

export interface HwpResult {
  sourceType: "hwpx" | "hwpml";
  sections: HwpSection[];
  warnings: string[];
}

/** HWPX/HWPML 공용 — DOCX 와 같은 정규식이지만 출처가 달라 별도로 둔다. */
const HEADING_STYLE_RE =
  /^(Heading\s*\d*|Title|Subtitle|법-제목|제목|소제목|머리말|간지\d*|장|절|조|편|관|항목제목|별표(\s*-.*)?|별첨(\s*-.*)?|개요\s*\d*|chapter\s*\d*)$/i;
const HEADING_TEXT_RE = /^(제\s*\d+\s*[조항장절편관]|부칙|별표\s*\d*|별첨\s*\d*)([\s(].*)?$/;
const HEADING_TEXT_MAX_LEN = 80;

function isHeading(text: string, styleName: string | null): boolean {
  if (styleName && HEADING_STYLE_RE.test(styleName.trim())) return true;
  if (text.length <= HEADING_TEXT_MAX_LEN && HEADING_TEXT_RE.test(text)) return true;
  return false;
}

/* ------------------------------------------------------------------ HWPX */

/** `Contents/header.xml` 의 `hh:style` → `id` : `name`. */
function hwpxStyleNames(headerXml: string | null): Map<string, string> {
  const map = new Map<string, string>();
  if (!headerXml) return map;
  for (const t of scanTags(headerXml)) {
    if (t.closing || t.name !== "hh:style") continue;
    const id = attrValue(t.attrs, "id");
    const name = attrValue(t.attrs, "name");
    if (id !== null && name !== null) map.set(id, name);
  }
  return map;
}

/**
 * 단락 텍스트 — **직계 `hp:run`** 의 **직계 자식**만 본다 (Python: `for child in run`).
 *
 * 후손을 전부 훑으면 안 된다. run 안에 `hp:tbl` 이 들어갈 수 있고, 그 표의 중첩 `hp:p` 가
 * 이미 별도 단락으로 나오므로 **같은 텍스트가 두 번** 잡힌다.
 *
 * `hp:t` 안에는 `markpenBegin` 같은 인라인 마커가 들어갈 수 있다. Python 은 element.text 와
 * 자식들의 tail 을 이어 붙이므로(마커 자체는 무시) 여기서는 **태그만 제거**해 같은 결과를 만든다.
 */
function hwpxParagraphText(pInner: string): string {
  let out = "";
  for (const run of directChildren(pInner, new Set(["hp:run"]))) {
    for (const child of directChildren(run.inner, new Set(["hp:t", "hp:lineBreak", "hp:tab"]))) {
      if (child.name === "hp:lineBreak") out += "\n";
      else if (child.name === "hp:tab") out += "\t";
      else out += decodeXml(child.inner.replace(/<[^>]*>/g, ""));
    }
  }
  return out;
}

export function extractHwpx(bytes: Uint8Array): HwpResult {
  const files = unzipSync(bytes);
  const dec = new TextDecoder();

  const sectionNames = Object.keys(files)
    .filter((n) => /^Contents\/section\d+\.xml$/.test(n))
    .sort((a, b) => Number(a.match(/\d+/)![0]) - Number(b.match(/\d+/)![0]));

  const warnings: string[] = [];
  if (!sectionNames.length) throw new Error("Contents/section*.xml 없음 — HWPX 가 아니다");

  const header = files["Contents/header.xml"]
    ? stripComments(dec.decode(files["Contents/header.xml"]))
    : null;
  if (!header) warnings.push("Contents/header.xml 없음 → 텍스트 패턴 fallback 으로 진행");
  const styles = hwpxStyleNames(header);

  const sections: HwpSection[] = [];
  let currentTitle: string | null = null;

  for (const name of sectionNames) {
    const xml = stripComments(dec.decode(files[name]));
    // `.//hp:p` 와 같은 의미 — 표 안 중첩 단락까지 문서 순서대로.
    for (const p of allDescendants(xml, "hp:p")) {
      const text = hwpxParagraphText(p.inner).trim();
      if (!text) continue;
      const styleId = attrValue(p.attrs, "styleIDRef");
      const styleName = styleId !== null ? (styles.get(styleId) ?? null) : null;
      if (isHeading(text, styleName)) currentTitle = text;
      sections.push({ text, page: null, sectionTitle: currentTitle });
    }
  }

  return { sourceType: "hwpx", sections, warnings };
}

/* ------------------------------------------------------------------ HWPML */

/** `<P>` 의 직계 `TEXT` 의 직계 `CHAR` 텍스트만. ElementTree `./TEXT/CHAR` 와 같다. */
function hwpmlParagraphText(pInner: string): string {
  let out = "";
  for (const textEl of directChildren(pInner, new Set(["TEXT"]))) {
    for (const charEl of directChildren(textEl.inner, new Set(["CHAR"]))) {
      // CHAR 는 `element.text` 만 쓴다 — 자식 요소의 tail 은 원본도 읽지 않는다.
      out += decodeXml(charEl.inner.replace(/<[^>]*>[\s\S]*$/, ""));
    }
  }
  return out;
}

export function extractHwpml(bytes: Uint8Array): HwpResult {
  // UTF-8 BOM 이 붙어 온다. `TextDecoder("utf-8")` 은 BOM 을 자동으로 떼지 않으므로 명시한다.
  const xml = stripComments(new TextDecoder("utf-8", { ignoreBOM: false }).decode(bytes));

  let root: string | null = null;
  for (const t of scanTags(xml)) {
    if (t.closing || t.name.startsWith("?")) continue;
    root = t.name;
    break;
  }
  if (root !== "HWPML") throw new Error(`HWPML 루트 태그가 아니다 (root=${root})`);

  const sections: HwpSection[] = [];
  const warnings: string[] = [];

  for (const body of allDescendants(xml, "BODY")) {
    for (const section of allDescendants(body.inner, "SECTION")) {
      const id = attrValue(section.attrs, "Id");
      const fallbackTitle = id !== null ? `section ${id}` : null;
      let currentTitle: string | null = null;

      for (const p of allDescendants(section.inner, "P")) {
        // 중첩 P 를 품은 단락은 컨테이너다 — 자식이 자기 텍스트를 따로 낸다.
        if (/<P[\s>]/.test(p.inner)) continue;
        const text = hwpmlParagraphText(p.inner).trim();
        if (!text) continue;
        if (text.length <= HEADING_TEXT_MAX_LEN && HEADING_TEXT_RE.test(text)) currentTitle = text;
        sections.push({ text, page: null, sectionTitle: currentTitle ?? fallbackTitle });
      }
    }
  }

  if (!sections.length) warnings.push("HWPML 본문에서 텍스트 단락을 찾지 못했습니다.");
  return { sourceType: "hwpml", sections, warnings };
}

/** 매직 바이트로 갈라 준다 — 확장자는 믿을 수 없다(`law sample2.hwp` 가 실제로는 HWPML). */
export function extractHwpXml(bytes: Uint8Array): HwpResult {
  const isZip = bytes[0] === 0x50 && bytes[1] === 0x4b;
  return isZip ? extractHwpx(bytes) : extractHwpml(bytes);
}
