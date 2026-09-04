/**
 * OOXML(DOCX/PPTX) 텍스트 추출 — ZIP + XML 만으로 현행 Python 파서의 산출물을 재현한다.
 *
 * ## 왜 라이브러리를 안 쓰나
 * `mammoth`(DOCX)는 deps 6개(jszip/bluebird/underscore…)를 끌고 오고 **style 이름을 그대로
 * 주지 않는다**. 현행 `DocxParser` 는 `paragraph.style.name` 정규식으로 heading 을 판정하므로
 * (실측: `승인글 템플릿1.docx` 에 Title 16 · Heading2 99 · Heading1 1 = 116건) 이름이 없으면
 * section_title sticky propagate 가 통째로 죽는다. PPTX 쪽은 쓸 만한 후보가 아예 없다.
 * OOXML 은 ZIP + XML 이라 직접 읽는 편이 의존성·정확도 모두 낫다.
 *
 * ## 재현 대상 (원본 코드가 진실이다)
 * - `app/adapters/impl/docx_parser.py` — body 직계 `w:p`/`w:tbl` 를 **XML 순서대로**,
 *   style 이름 정규식 → heading, 표는 `cell1 | cell2` + 행 개행
 * - `app/adapters/impl/pptx_parser.py` — slide 단위, title placeholder 우선,
 *   `p:grpSp` 재귀, 표는 DOCX 와 같은 규칙
 *
 * ## 대상 문서 실측 (2026-09-04)
 * 하이퍼링크 0 · `w:tab` 0 · gridSpan/vMerge 0 · `w:sdt` 0 — 병합셀 확장이나
 * 하이퍼링크 run 제외 같은 python-docx 특유의 처리가 필요 없는 문서다.
 * 그런 구조가 들어오면 아래 구현과 갈릴 수 있다(§알려진 한계).
 *
 * ## 알려진 한계
 * - `w:gridSpan`/`w:vMerge`: python-docx `row.cells` 는 병합된 셀을 grid 열 수만큼 **반복**해서
 *   돌려준다. 여기서는 `w:tc` 를 그대로 세므로 병합표에서 열 수가 달라진다.
 * - `w:hyperlink` 안의 run: python-docx `Paragraph.text` 는 직계 `w:r` 만 본다. 여기서도
 *   직계 `w:r` 만 읽어 같은 동작이지만, 그 결과 하이퍼링크 텍스트는 **양쪽 다** 빠진다.
 */

import { unzipSync } from "fflate";

export interface OoxmlSection {
  text: string;
  page: number | null;
  sectionTitle: string | null;
}

export interface OoxmlResult {
  sourceType: "docx" | "pptx";
  sections: OoxmlSection[];
  warnings: string[];
}

/* ------------------------------------------------------------------ XML 스캐너 */

interface Tag {
  name: string;
  attrs: string;
  closing: boolean;
  selfClosing: boolean;
  start: number;
  end: number;
}

// 속성값 안의 `>` 에 걸리지 않도록 따옴표 구간을 통째로 건너뛴다.
const TAG_RE = /<(\/?)([A-Za-z_][\w.:-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/g;

function* scanTags(xml: string): Generator<Tag> {
  TAG_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TAG_RE.exec(xml)) !== null) {
    const attrs = m[3] ?? "";
    yield {
      name: m[2],
      attrs,
      closing: m[1] === "/",
      selfClosing: attrs.trimEnd().endsWith("/"),
      start: m.index,
      end: m.index + m[0].length,
    };
  }
}

interface Element {
  name: string;
  attrs: string;
  inner: string;
}

/** `xml` 의 **직계 자식**만 고른다. 같은 이름이 중첩돼도(중첩 표) 깊이로 구분한다. */
function directChildren(xml: string, wanted: Set<string>): Element[] {
  const out: Element[] = [];
  let depth = 0;
  let openStart = -1;
  let openEnd = -1;
  let openName = "";
  let openAttrs = "";

  for (const t of scanTags(xml)) {
    if (t.closing) {
      depth--;
      if (depth === 0 && openStart >= 0) {
        if (wanted.has(openName)) {
          out.push({ name: openName, attrs: openAttrs, inner: xml.slice(openEnd, t.start) });
        }
        openStart = -1;
      }
      continue;
    }
    if (t.selfClosing) {
      if (depth === 0 && wanted.has(t.name)) out.push({ name: t.name, attrs: t.attrs, inner: "" });
      continue;
    }
    if (depth === 0) {
      openStart = t.start;
      openEnd = t.end;
      openName = t.name;
      openAttrs = t.attrs;
    }
    depth++;
  }
  return out;
}

/** 첫 번째 후손 요소의 속성 문자열. 없으면 null. */
function firstDescendantAttrs(xml: string, name: string): string | null {
  for (const t of scanTags(xml)) {
    if (!t.closing && t.name === name) return t.attrs;
  }
  return null;
}

/**
 * 첫 번째 후손 요소의 내부 XML. 없으면 null.
 *
 * depth 는 **같은 이름의 태그만** 센다. 모든 닫는 태그로 depth 를 줄이면
 * `<w:body>` 를 찾을 때 첫 `</w:p>` 에서 0 이 되어 곧바로 반환한다(= 본문 전체 유실).
 */
function firstDescendantInner(xml: string, name: string): string | null {
  let depth = 0;
  let start = -1;
  for (const t of scanTags(xml)) {
    if (t.name !== name) continue;
    if (t.selfClosing) {
      if (start < 0) return "";
      continue;
    }
    if (t.closing) {
      if (start < 0) continue;
      depth--;
      if (depth === 0) return xml.slice(start, t.start);
      continue;
    }
    if (start < 0) start = t.end;
    depth++;
  }
  return null;
}

function attrValue(attrs: string, name: string): string | null {
  const m = new RegExp(`${name}\\s*=\\s*"([^"]*)"`).exec(attrs);
  return m ? m[1] : null;
}

function decodeXml(s: string): string {
  return s
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(Number(d)))
    .replace(/&#x([0-9a-fA-F]+);/g, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&amp;/g, "&"); // amp 는 마지막 — 먼저 풀면 `&amp;lt;` 가 `<` 로 잘못 된다
  }

/**
 * `<w:t>`/`<a:t>` 텍스트를 문서 순서대로 모은다.
 * `tab`/`br`/`cr` 는 python-docx `Run.text` 와 같은 문자로 바꾼다.
 */
function collectText(xml: string, textTag: string, ns: string): string {
  let out = "";
  let capture = -1;
  for (const t of scanTags(xml)) {
    if (!t.closing && !t.selfClosing && t.name === textTag) {
      capture = t.end;
      continue;
    }
    if (t.closing && t.name === textTag && capture >= 0) {
      out += decodeXml(xml.slice(capture, t.start));
      capture = -1;
      continue;
    }
    if (t.selfClosing || !t.closing) {
      if (t.name === `${ns}:tab`) out += "\t";
      else if (t.name === `${ns}:br` || t.name === `${ns}:cr`) out += "\n";
    }
  }
  return out;
}

/* ------------------------------------------------------------------ 공통 */

function readZip(bytes: Uint8Array): Record<string, Uint8Array> {
  return unzipSync(bytes);
}

function textOf(files: Record<string, Uint8Array>, path: string): string | null {
  const f = files[path];
  return f ? new TextDecoder().decode(f) : null;
}

/** 표 → `cell1 | cell2` 행 + 행 사이 개행. DocxParser `_table_to_text` 와 같은 규칙. */
function tableToText(rows: string[][]): string {
  const out: string[] = [];
  for (const cells of rows) {
    if (cells.some((c) => c !== "")) out.push(cells.join(" | "));
  }
  return out.join("\n");
}

/* ------------------------------------------------------------------ DOCX */

const HEADING_STYLE_RE =
  /^(Heading\s*\d*|Title|Subtitle|법-제목|제목|소제목|머리말|간지\d*|장|절|조|편|관|항목제목|별표(\s*-.*)?|별첨(\s*-.*)?|개요\s*\d*|chapter\s*\d*)$/i;
const HEADING_TEXT_RE = /^(제\s*\d+\s*[조항장절편관]|부칙|별표\s*\d*|별첨\s*\d*)([\s(].*)?$/;
const HEADING_TEXT_MAX_LEN = 80;

function isHeading(text: string, styleName: string | null): boolean {
  if (styleName && HEADING_STYLE_RE.test(styleName.trim())) return true;
  if (text.length <= HEADING_TEXT_MAX_LEN && HEADING_TEXT_RE.test(text)) return true;
  return false;
}

/** styles.xml: styleId → w:name. 정규식 `Heading\s*\d*` 는 대소문자 무시라 `heading 2` 도 잡는다. */
function styleNames(stylesXml: string | null): Map<string, string> {
  const map = new Map<string, string>();
  if (!stylesXml) return map;
  for (const el of directChildren(firstDescendantInner(stylesXml, "w:styles") ?? "", new Set(["w:style"]))) {
    const id = attrValue(el.attrs, "w:styleId");
    const nameAttrs = firstDescendantAttrs(el.inner, "w:name");
    const name = nameAttrs ? attrValue(nameAttrs, "w:val") : null;
    if (id && name) map.set(id, name);
  }
  return map;
}

/** 단락 텍스트 — 직계 `w:r` 만 본다(python-docx `Paragraph.text` 와 동일). */
function paragraphText(pInner: string): string {
  let out = "";
  for (const run of directChildren(pInner, new Set(["w:r"]))) {
    out += collectText(run.inner, "w:t", "w");
  }
  return out;
}

function docxTableRows(tblInner: string): string[][] {
  const rows: string[][] = [];
  for (const tr of directChildren(tblInner, new Set(["w:tr"]))) {
    const cells: string[] = [];
    for (const tc of directChildren(tr.inner, new Set(["w:tc"]))) {
      const paras = directChildren(tc.inner, new Set(["w:p"])).map((p) => paragraphText(p.inner));
      cells.push(paras.join("\n").trim());
    }
    rows.push(cells);
  }
  return rows;
}

export function extractDocx(bytes: Uint8Array): OoxmlResult {
  const files = readZip(bytes);
  const doc = textOf(files, "word/document.xml");
  if (!doc) throw new Error("word/document.xml 없음 — DOCX 가 아니다");
  const styles = styleNames(textOf(files, "word/styles.xml"));

  const body = firstDescendantInner(doc, "w:body") ?? "";
  const sections: OoxmlSection[] = [];
  const warnings: string[] = [];
  let currentTitle: string | null = null;

  for (const el of directChildren(body, new Set(["w:p", "w:tbl"]))) {
    if (el.name === "w:p") {
      const text = paragraphText(el.inner).trim();
      if (!text) continue;
      const pPr = firstDescendantInner(el.inner, "w:pPr");
      const styleAttrs = pPr ? firstDescendantAttrs(pPr, "w:pStyle") : null;
      const styleId = styleAttrs ? attrValue(styleAttrs, "w:val") : null;
      const styleName = styleId ? (styles.get(styleId) ?? styleId) : null;
      if (isHeading(text, styleName)) currentTitle = text;
      sections.push({ text, page: null, sectionTitle: currentTitle });
    } else {
      const tableText = tableToText(docxTableRows(el.inner));
      if (!tableText) continue;
      sections.push({ text: tableText, page: null, sectionTitle: currentTitle });
    }
  }

  return { sourceType: "docx", sections, warnings };
}

/* ------------------------------------------------------------------ PPTX */

/** `p:txBody` → 단락 사이 개행. python-pptx `TextFrame.text` 와 같은 규칙. */
function txBodyText(txBodyInner: string): string {
  const paras = directChildren(txBodyInner, new Set(["a:p"])).map((p) => collectText(p.inner, "a:t", "a"));
  return paras.join("\n");
}

function pptxTableRows(tblInner: string): string[][] {
  const rows: string[][] = [];
  for (const tr of directChildren(tblInner, new Set(["a:tr"]))) {
    const cells: string[] = [];
    for (const tc of directChildren(tr.inner, new Set(["a:tc"]))) {
      const tx = firstDescendantInner(tc.inner, "a:txBody");
      cells.push(tx === null ? "" : txBodyText(tx).trim());
    }
    rows.push(cells);
  }
  return rows;
}

const SHAPE_TAGS = new Set(["p:sp", "p:grpSp", "p:graphicFrame", "p:pic", "p:cxnSp"]);

function walkShapes(treeInner: string, parts: string[]): void {
  for (const shape of directChildren(treeInner, SHAPE_TAGS)) {
    if (shape.name === "p:grpSp") {
      walkShapes(shape.inner, parts); // GroupShape 재귀 — 디자인 PPT 의 흔한 구조
      continue;
    }
    if (shape.name === "p:sp") {
      const tx = firstDescendantInner(shape.inner, "p:txBody");
      if (tx !== null) {
        const text = txBodyText(tx).trim();
        if (text) parts.push(text);
      }
      continue;
    }
    if (shape.name === "p:graphicFrame") {
      const tbl = firstDescendantInner(shape.inner, "a:tbl");
      if (tbl !== null) {
        const t = tableToText(pptxTableRows(tbl));
        if (t) parts.push(t);
      }
    }
  }
}

/** title placeholder(`type="title"|"ctrTitle"`) 우선, 없으면 첫 텍스트의 첫 줄. */
function slideTitle(treeInner: string): string | null {
  for (const shape of directChildren(treeInner, SHAPE_TAGS)) {
    if (shape.name !== "p:sp") continue;
    const ph = firstDescendantAttrs(shape.inner, "p:ph");
    const type = ph ? attrValue(ph, "type") : null;
    if (type === "title" || type === "ctrTitle") {
      const tx = firstDescendantInner(shape.inner, "p:txBody");
      const text = tx === null ? "" : txBodyText(tx).trim();
      if (text) return text.split("\n")[0].trim();
    }
  }
  const parts: string[] = [];
  walkShapes(treeInner, parts);
  return parts.length ? parts[0].split("\n")[0].trim() : null;
}

/** `ppt/slides/slideN.xml` 을 N 오름차순으로. 문자열 정렬하면 slide10 이 slide2 앞에 온다. */
export function slidePaths(files: Record<string, Uint8Array>): string[] {
  return Object.keys(files)
    .filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n))
    .sort((a, b) => Number(a.match(/\d+/)![0]) - Number(b.match(/\d+/)![0]));
}

export function extractPptx(bytes: Uint8Array): OoxmlResult {
  const files = readZip(bytes);
  const sections: OoxmlSection[] = [];
  const warnings: string[] = [];

  slidePaths(files).forEach((path, idx) => {
    const xml = new TextDecoder().decode(files[path]);
    const tree = firstDescendantInner(xml, "p:spTree") ?? "";
    const parts: string[] = [];
    walkShapes(tree, parts);
    if (!parts.length) return; // 텍스트 0 슬라이드는 운영에서 Vision 경로로 간다
    sections.push({
      text: parts.join("\n"),
      page: idx + 1,
      sectionTitle: slideTitle(tree),
    });
  });

  return { sourceType: "pptx", sections, warnings };
}
