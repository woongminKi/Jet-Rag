/**
 * Python `datetime.fromisoformat()` + `.isoformat()` 호환 파서.
 *
 * ## 왜 JS `Date` 를 안 쓰나
 * `_parse_iso_date` 의 결과는 **오직** PostgREST 필터 문자열로만 쓰인다
 * (`search.py` 1367·1369 — `gte("created_at", from_dt.isoformat())`).
 * 그런데 `new Date()` 는 Python 과 **양방향으로** 어긋난다 (2026-09-04 실측, 17 케이스 중 7 불일치):
 *
 * | 입력 | Python | `new Date()` |
 * |---|---|---|
 * | `2026-02-30` | ValueError → 400 | **3 월 2 일로 롤오버** |
 * | `2026-04-01T24:00:00` | ValueError → 400 | **익일 0 시** |
 * | `+002026-04-01` | ValueError → 400 | 통과 |
 * | `2026-W14-1` | 2026-03-30 | Invalid → 400 |
 * | `20260401` | 2026-04-01 | Invalid → 400 |
 * | `2026-04-01T09` | 09:00 | Invalid → 400 |
 * | `...T00:00:00.123456Z` | 마이크로초 유지 | **밀리초로 절삭** |
 *
 * 아래쪽 3 줄(거부해야 할 걸 받는 쪽)이 특히 위험하다 — 400 대신 **조용히 다른 날짜로
 * 필터**된다. `Date` 를 고쳐 쓰는 게 아니라 파서를 직접 쓰는 이유다.
 *
 * ## 문자열로 들고 있는 이유
 * `Date` 는 밀리초까지만 담고 오프셋을 UTC 로 정규화한다. Python 은 마이크로초를 담고
 * **원래 오프셋을 보존**한다(`+09:00` 이 그대로 남는다). 같은 순간이라 검색 결과는 같지만
 * 요청 URL 이 달라진다. 정규화된 isoformat 문자열로 들고 있으면 둘 다 해결된다.
 *
 * ## 지원 문법 (Python 3.12 실측 기준)
 * - 날짜: `YYYY-MM-DD` / `YYYYMMDD` / `YYYY-Www[-D]` / `YYYYWww[D]`
 *   (확장형과 기본형을 섞을 수 없다 — `2026-0401` 은 오류)
 * - 구분자: **아무 한 글자**나 된다 (`2026-04-01x09:00:00` 통과)
 * - 시각: `HH` / `HH:MM` / `HH:MM:SS` / `HHMM` / `HHMMSS`, 소수점은 `.` 또는 `,`,
 *   자릿수 제한 없고 **6 자리로 절삭**
 * - 오프셋: `±HH` / `±HH:MM` / `±HHMM` / `±HH:MM:SS[.ffffff]`, 절댓값 24 시간 미만
 * - 연도 0000 거부, 초 60(윤초) 거부, 소문자 `z` 거부
 *
 * ## 의도한 차이 — 원본보다 엄격하다
 * CPython 은 `fromisoformat` 을 C 로 구현했고 그게 순수 파이썬 미러와도 어긋난다
 * (`2026-04-01T090Z`·`09:00.5` 를 C 는 받고 미러는 거부한다 — 2026-09-04 실측).
 * C 쪽은 두 자리를 `int()` 로 읽어서 `" 9"`·`"+9"` 는 물론 아랍-인도 숫자까지 통과시킨다.
 * 그걸 버그까지 흉내내는 대신 **문법을 엄격하게** 잡았다. 방향이 중요하다:
 *
 * - 원본이 거부하는 걸 여기서 **받으면 안 된다** — 400 대신 엉뚱한 날짜로 필터된다.
 * - 원본이 받는 걸 여기서 거부하는 건 안전하다 — 사용자가 400 을 받는다.
 *
 * 후자에 해당하는 입력은 `verify_iso_datetime_parity.py` 가 목록으로 출력한다.
 * 실제로 오는 형식(`YYYY-MM-DD`, `...Z`, `...+09:00`)은 같은 스크립트의 필수 통과
 * 목록으로 고정돼 있어 조용히 어긋날 수 없다.
 */

const DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

function isLeap(y: number): boolean {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

function daysInMonth(y: number, m: number): number {
  return m === 2 && isLeap(y) ? 29 : DAYS_IN_MONTH[m - 1];
}

function digits(s: string): boolean {
  // `\d` 는 유니코드 숫자(아라비아-인도 숫자 등)까지 잡는다. Python `int()` 도 그것들을
  // 받지만 `fromisoformat` 은 ASCII 만 받으므로 여기서 막는다.
  return s.length > 0 && /^[0-9]+$/.test(s);
}

function toInt(s: string): number | null {
  return digits(s) ? Number.parseInt(s, 10) : null;
}

function pad(n: number, width: number): string {
  return String(n).padStart(width, "0");
}

/** ISO 주차(`YYYY-Www-D`) → 그레고리력. 해당 해 첫 목요일이 든 주가 1 주차다. */
function fromIsoWeek(year: number, week: number, weekday: number): [number, number, number] | null {
  if (week < 1 || week > 53 || weekday < 1 || weekday > 7) return null;
  // 1 월 4 일은 항상 1 주차에 든다.
  const jan4 = Date.UTC(year, 0, 4);
  // `getUTCDay()` 는 일요일이 0 이라 ISO(월요일 1) 로 옮긴다.
  const jan4Dow = new Date(jan4).getUTCDay() || 7;
  const week1Monday = jan4 - (jan4Dow - 1) * 86400000;
  const target = new Date(week1Monday + ((week - 1) * 7 + (weekday - 1)) * 86400000);
  // 53 주차가 없는 해에 53 을 주면 다음 해로 넘어간다 — Python 도 이걸 거부한다.
  if (getIsoWeekYear(target) !== year) return null;
  return [target.getUTCFullYear(), target.getUTCMonth() + 1, target.getUTCDate()];
}

function getIsoWeekYear(d: Date): number {
  const dow = d.getUTCDay() || 7;
  // 그 주의 목요일이 속한 해가 ISO 주차 연도다.
  const thursday = new Date(d.getTime() + (4 - dow) * 86400000);
  return thursday.getUTCFullYear();
}

interface DatePart {
  y: number;
  m: number;
  d: number;
}

/** 날짜부 파싱. 확장형(`-` 있음)과 기본형을 섞으면 거부한다. */
function parseDate(s: string): DatePart | null {
  // 주차 형식이 먼저다 — `W` 가 있으면 월/일 형식이 아니다.
  const wExt = /^(\d{4})-W(\d{2})(?:-(\d))?$/.exec(s);
  const wBas = /^(\d{4})W(\d{2})(\d)?$/.exec(s);
  const w = wExt ?? wBas;
  if (w) {
    const y = toInt(w[1])!;
    const week = toInt(w[2])!;
    const dow = w[3] === undefined ? 1 : toInt(w[3])!;
    if (y < 1) return null;
    const g = fromIsoWeek(y, week, dow);
    return g ? { y: g[0], m: g[1], d: g[2] } : null;
  }

  let y: number | null, m: number | null, d: number | null;
  if (s.length === 10 && s[4] === "-" && s[7] === "-") {
    y = toInt(s.slice(0, 4));
    m = toInt(s.slice(5, 7));
    d = toInt(s.slice(8, 10));
  } else if (s.length === 8 && !s.includes("-")) {
    y = toInt(s.slice(0, 4));
    m = toInt(s.slice(4, 6));
    d = toInt(s.slice(6, 8));
  } else {
    return null;
  }
  if (y === null || m === null || d === null) return null;
  // Python `MINYEAR` 는 1 이다 — `0000-01-01` 은 오류.
  if (y < 1 || m < 1 || m > 12 || d < 1 || d > daysInMonth(y, m)) return null;
  return { y, m, d };
}

interface TimePart {
  h: number;
  mi: number;
  s: number;
  us: number;
}

/** 시각부(오프셋 제외) 파싱. 소수점은 `.`/`,` 둘 다, 6 자리 초과분은 절삭한다. */
function parseTime(s: string): TimePart | null {
  let us = 0;
  let head = s;
  const fracIdx = s.search(/[.,]/);
  if (fracIdx !== -1) {
    const frac = s.slice(fracIdx + 1);
    if (!digits(frac)) return null;
    head = s.slice(0, fracIdx);
    // 6 자리로 맞춘다 — 넘치면 버리고 모자라면 오른쪽을 0 으로 채운다.
    us = toInt(frac.slice(0, 6).padEnd(6, "0"))!;
  }

  let h: number | null, mi = 0, sec = 0;
  const ext = head.includes(":");
  if (ext) {
    const p = head.split(":");
    if (p.length > 3 || p.some((x) => x.length !== 2)) return null;
    h = toInt(p[0]);
    if (p.length > 1) {
      const v = toInt(p[1]);
      if (v === null) return null;
      mi = v;
    }
    if (p.length > 2) {
      const v = toInt(p[2]);
      if (v === null) return null;
      sec = v;
    }
    // 소수점은 가장 작은 단위에만 붙는다 — `09:00.5` 는 분의 소수라 Python 이 거부한다.
    if (fracIdx !== -1 && p.length !== 3) return null;
  } else {
    if (head.length !== 2 && head.length !== 4 && head.length !== 6) return null;
    h = toInt(head.slice(0, 2));
    if (head.length >= 4) {
      const v = toInt(head.slice(2, 4));
      if (v === null) return null;
      mi = v;
    }
    if (head.length === 6) {
      const v = toInt(head.slice(4, 6));
      if (v === null) return null;
      sec = v;
    }
    if (fracIdx !== -1 && head.length !== 6) return null;
  }
  if (h === null) return null;
  // 24:00 도, 윤초(60)도 Python 은 거부한다.
  if (h > 23 || mi > 59 || sec > 59) return null;
  return { h, mi, s: sec, us };
}

/** 오프셋 문자열 → 부호 있는 마이크로초. */
function parseOffset(sign: number, body: string): number | null {
  const t = parseTime(body);
  if (t === null) return null;
  const total = ((t.h * 60 + t.mi) * 60 + t.s) * 1_000_000 + t.us;
  // Python: `-timedelta(hours=24) < offset < timedelta(hours=24)`
  if (total >= 24 * 3600 * 1_000_000) return null;
  return sign * total;
}

/** Python 의 `timedelta` → `%z` 표기. 초·마이크로초는 0 이 아닐 때만 붙는다. */
function formatOffset(us: number): string {
  const sign = us < 0 ? "-" : "+";
  const a = Math.abs(us);
  const h = Math.floor(a / 3_600_000_000);
  const mi = Math.floor((a % 3_600_000_000) / 60_000_000);
  const s = Math.floor((a % 60_000_000) / 1_000_000);
  const rest = a % 1_000_000;
  let out = `${sign}${pad(h, 2)}:${pad(mi, 2)}`;
  if (s || rest) out += `:${pad(s, 2)}`;
  if (rest) out += `.${pad(rest, 6)}`;
  return out;
}

/**
 * `datetime.fromisoformat(value).isoformat()` 과 같은 결과를 낸다.
 * 파싱 실패는 `null` — 호출부가 400 메시지를 만든다.
 */
function isAsciiDigit(ch: string | undefined): boolean {
  return ch !== undefined && ch >= "0" && ch <= "9";
}

/**
 * 날짜부 길이를 **한 번에** 정한다. 원본은 후보를 되짚지 않는다 — 고른 길이로 시각부가
 * 깨지면 그대로 실패지, 더 짧은 형식으로 다시 시도하지 않는다(`2026042509:00:00` 은
 * 날짜 8 자를 고른 뒤 시각 `9:00:00` 이 깨져서 오류다). 되짚으면 원본보다 관대해진다.
 *
 * 주차 형식은 날짜부에 요일이 붙는지가 **뒤따르는 구분자**로 갈린다. 구분자가 숫자면
 * 요일과 구분이 안 되므로 짧은 쪽을 고른다 (2026-09-04 실측 — `2026W145T…` 는 요일 5,
 * `2026W14509:00:00` 은 요일 없이 구분자 `5`).
 */
function dateLength(s: string): number | null {
  if (s.length < 7) return null;
  if (s[4] === "-") {
    if (s[5] !== "W") return 10; // YYYY-MM-DD
    // YYYY-Www-D 는 구분자(10 번째)가 숫자가 아닐 때만 고른다.
    const withDay = s.length >= 10 && s[8] === "-" && isAsciiDigit(s[9]) &&
      (s.length === 10 || !isAsciiDigit(s[10]));
    return withDay ? 10 : 8; // 아니면 YYYY-Www
  }
  if (s[4] === "W") {
    const withDay = s.length >= 8 && isAsciiDigit(s[7]) &&
      (s.length === 8 || !isAsciiDigit(s[8]));
    return withDay ? 8 : 7; // YYYYWwwD / YYYYWww
  }
  return 8; // YYYYMMDD
}

/**
 * `datetime.fromisoformat(value).isoformat()` 과 같은 결과를 낸다.
 * 파싱 실패는 `null` — 호출부가 400 메시지를 만든다.
 */
export function parseIsoDatetime(value: string): string | null {
  const dlen = dateLength(value);
  if (dlen === null || value.length < dlen) return null;

  const datePart = parseDate(value.slice(0, dlen));
  if (datePart === null) return null;
  const { y, m, d } = datePart;
  const dateIso = `${pad(y, 4)}-${pad(m, 2)}-${pad(d, 2)}`;

  const rest = value.slice(dlen);
  if (rest === "") return `${dateIso}T00:00:00`;

  // 날짜부와 시각부를 가르는 구분자는 아무 한 글자나 된다(원본 실측).
  const timeStr = rest.slice(1);
  if (timeStr === "") return null;

  // 오프셋 부호는 시각부 안에서 찾는다(맨 앞은 부호가 될 수 없다).
  let offUs: number | null = null;
  let timeBody = timeStr;
  const signIdx = Math.max(timeStr.lastIndexOf("+"), timeStr.lastIndexOf("-"));
  if (signIdx > 0) {
    offUs = parseOffset(timeStr[signIdx] === "-" ? -1 : 1, timeStr.slice(signIdx + 1));
    if (offUs === null) return null;
    timeBody = timeStr.slice(0, signIdx);
  }

  const t = parseTime(timeBody);
  if (t === null) return null;

  let iso = `${dateIso}T${pad(t.h, 2)}:${pad(t.mi, 2)}:${pad(t.s, 2)}`;
  if (t.us) iso += `.${pad(t.us, 6)}`;
  if (offUs !== null) iso += formatOffset(offUs);
  return iso;
}

/**
 * `_parse_iso_date` 포팅. 실패는 `undefined`, 빈 값은 `null` 을 돌려주고
 * 호출부가 필드 이름을 넣어 400 메시지를 만든다.
 *
 * 원본은 길이 10 이면 `fromisoformat` 후 tzinfo 를 UTC 로 **덮어쓴다**. 그래서
 * `2026-W14-1`(길이 10) 도 이 가지로 들어와 UTC 0 시가 된다.
 */
export function parseSearchDate(value: string | null): string | null | undefined {
  if (!value) return null;

  if (value.length === 10) {
    const iso = parseIsoDatetime(value);
    if (iso === null) return undefined;
    // 길이 10 은 날짜만 가능하므로(`YYYY-MM-DD` / `YYYY-Www-D`) 시각·오프셋이 붙지 않는다.
    return `${iso}+00:00`;
  }

  // `endswith("Z")` 일 때만 치환하고, 소문자 `z` 는 그대로 둬서 파싱 실패시킨다.
  const normalized = value.endsWith("Z") ? value.replaceAll("Z", "+00:00") : value;
  const iso = parseIsoDatetime(normalized);
  if (iso === null) return undefined;
  // tzinfo 가 없으면 UTC 로 간주한다. 날짜부의 `-` 와 헷갈리지 않게 `T` 뒤만 본다.
  const hasOffset = /[+-]\d{2}:\d{2}(?::\d{2}(?:\.\d{6})?)?$/.test(iso.slice(11));
  return hasOffset ? iso : `${iso}+00:00`;
}
