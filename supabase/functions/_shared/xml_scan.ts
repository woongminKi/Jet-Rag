/**
 * 최소 XML 스캐너 — OOXML(DOCX/PPTX) · HWPX · HWPML 추출기가 공유한다.
 *
 * DOM 파서를 쓰지 않는 이유: Edge 런타임에 `DOMParser` 가 없고, npm XML 파서는
 * 문서 순서와 직계/후손 구분을 그대로 돌려주지 않아 원본 파서의 규칙
 * (`직계 w:r 만`, `.//hp:p 전부`, `./TEXT/CHAR 만`)을 재현하기 어렵다.
 * 대상 XML 은 워드/한글이 생성한 것이라 형태가 규칙적이고, 필요한 연산은
 * "직계 자식" · "첫 후손" · "모든 후손" 세 가지뿐이다.
 */

/* ------------------------------------------------------------------ XML 스캐너 */

/**
 * XML 주석을 제거한다. **스캔 전에 반드시 통과시켜야 한다.**
 *
 * 주석 구분자 자체는 태그 정규식에 안 걸리지만, 주석 **안의** `<P>` 같은 태그는 그대로 잡힌다.
 * ElementTree 는 주석을 아예 트리에 넣지 않으므로 그대로 두면 원본 파서보다 단락이 더 나온다.
 * (실측: `law sample2.hwp` 의 주석 처리된 템플릿 자리표시자 `{이유소제목}`·`{이유본문}` 이
 * 본문 단락으로 새어 나와 58개여야 할 섹션이 60개가 됐다.)
 *
 * 위치가 바뀌므로 **제거한 문자열을 그대로 이후 슬라이싱에 써야 한다.**
 */
export function stripComments(xml: string): string {
  return xml.replace(/<!--[\s\S]*?-->/g, "");
}

export interface Tag {
  name: string;
  attrs: string;
  closing: boolean;
  selfClosing: boolean;
  start: number;
  end: number;
}

// 속성값 안의 `>` 에 걸리지 않도록 따옴표 구간을 통째로 건너뛴다.
const TAG_RE = /<(\/?)([A-Za-z_][\w.:-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/g;

export function* scanTags(xml: string): Generator<Tag> {
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

export interface Element {
  name: string;
  attrs: string;
  inner: string;
}

/** `xml` 의 **직계 자식**만 고른다. 같은 이름이 중첩돼도(중첩 표) 깊이로 구분한다. */
export function directChildren(xml: string, wanted: Set<string>): Element[] {
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
export function firstDescendantAttrs(xml: string, name: string): string | null {
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
export function firstDescendantInner(xml: string, name: string): string | null {
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

export function attrValue(attrs: string, name: string): string | null {
  const m = new RegExp(`${name}\\s*=\\s*"([^"]*)"`).exec(attrs);
  return m ? m[1] : null;
}

export function decodeXml(s: string): string {
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
export function collectText(xml: string, textTag: string, ns: string): string {
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


/**
 * 이름이 같은 **모든 후손**을 문서 순서(pre-order)로. 중첩도 전부 낸다.
 *
 * ElementTree 의 `.//tag` / `iter(tag)` 와 같은 결과여야 한다 — HWPX 의 표 안 `hp:p`,
 * HWPML 의 중첩 `P` 가 여기에 걸린다. 닫는 태그에서 내보내면 안쪽이 먼저 나오므로
 * **여는 위치 순서로 정렬**해서 pre-order 를 맞춘다.
 */
export function allDescendants(xml: string, name: string): Element[] {
  const open: { order: number; innerStart: number; attrs: string }[] = [];
  const found: { order: number; el: Element }[] = [];
  let order = 0;

  for (const t of scanTags(xml)) {
    if (t.name !== name) continue;
    if (t.selfClosing) {
      found.push({ order: order++, el: { name, attrs: t.attrs, inner: "" } });
      continue;
    }
    if (!t.closing) {
      open.push({ order: order++, innerStart: t.end, attrs: t.attrs });
      continue;
    }
    const o = open.pop();
    if (!o) continue;
    found.push({ order: o.order, el: { name, attrs: o.attrs, inner: xml.slice(o.innerStart, t.start) } });
  }

  found.sort((a, b) => a.order - b.order);
  return found.map((f) => f.el);
}
