/**
 * `billing.py` 의 `_add_one_month` · `_parse_ts` 자리 — **벽시계** 날짜 연산.
 *
 * ## epoch 로 바꿔서 더하면 안 된다
 * 원본은 `dt.replace(year=…, month=…, day=…)` 다. 시·분·초·마이크로초·오프셋을 그대로
 * 두고 **날짜 부분만** 바꾼다. epoch 밀리초로 옮겨 계산하면 오프셋이 UTC 로 접히면서
 * 문자열이 달라진다 — `current_period_end` 는 다시 DB 에 저장되고 다음 배치의 멱등
 * 키(`period_key`)로도 쓰이므로, 표현이 바뀌면 **같은 주기를 두 번 청구**할 수 있다.
 *
 * ## 말일 clamp
 * `min(dt.day, monthrange(year, month)[1])` — 1/31 → 2/28(윤년이면 2/29).
 * 이게 없으면 `replace(month=2, day=31)` 이 예외다.
 *
 * ## Python `isoformat()` 규칙
 * 마이크로초가 0 이면 소수부를 **통째로 생략**하고, 아니면 항상 6 자리다.
 * (`_shared/pytime.ts` 와 같은 규칙이지만 여기는 오프셋이 UTC 가 아닐 수도 있다.)
 */

export interface PyDateParts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
  /** 마이크로초 (0~999999). */
  micro: number;
  /** 오프셋 분. `null` 이면 naive — Python 도 그대로 둔다. */
  offsetMinutes: number | null;
}

const ISO_RE =
  /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?(Z|[+-]\d{2}:?\d{2})?$/;

/**
 * 원본 `_parse_ts` — 빈 값이면 `null`.
 *
 * 원본은 `value.replace("Z", "+00:00")` 뒤 `fromisoformat` 을 부른다. 파싱 실패는
 * 원본에서 `ValueError` 로 터지는 자리라, 여기서도 `null` 이 아니라 예외로 둔다.
 */
export function parseIso(value: string | null | undefined): PyDateParts | null {
  if (!value) return null;
  const m = ISO_RE.exec(value.trim());
  if (!m) throw new Error(`ISO 8601 로 읽을 수 없다: ${value}`);
  // Python `fromisoformat` 은 **불가능한 날짜를 거부한다**(2026-02-29 → ValueError).
  // 정규식만으로 통과시키면 조용히 틀린 값을 계산하게 된다 — 대조에서 걸렸다.
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo)) {
    throw new Error(`ISO 8601 로 읽을 수 없다: ${value}`);
  }
  const tz = m[8];
  let offsetMinutes: number | null = null;
  if (tz === "Z") {
    offsetMinutes = 0;
  } else if (tz) {
    const sign = tz[0] === "-" ? -1 : 1;
    const body = tz.slice(1).replace(":", "");
    offsetMinutes = sign * (Number(body.slice(0, 2)) * 60 + Number(body.slice(2, 4)));
  }
  return {
    year: y,
    month: mo,
    day: d,
    hour: Number(m[4]),
    minute: Number(m[5]),
    second: m[6] ? Number(m[6]) : 0,
    // Python 은 소수부를 **오른쪽으로 채워** 마이크로초로 읽는다: `.789` → 789000.
    micro: m[7] ? Number(m[7].padEnd(6, "0")) : 0,
    offsetMinutes,
  };
}

/** `calendar.monthrange(y, m)[1]`. */
export function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** 원본 `_add_one_month` — 월 1 회 결제 주기. 말일은 clamp 한다. */
export function addOneMonth(dt: PyDateParts): PyDateParts {
  const year = dt.year + Math.floor(dt.month / 12);
  const month = (dt.month % 12) + 1;
  const day = Math.min(dt.day, daysInMonth(year, month));
  return { ...dt, year, month, day };
}

function pad(n: number, w = 2): string {
  return String(n).padStart(w, "0");
}

/** Python `datetime.isoformat()`. */
export function formatIso(dt: PyDateParts): string {
  let s = `${pad(dt.year, 4)}-${pad(dt.month)}-${pad(dt.day)}` +
    `T${pad(dt.hour)}:${pad(dt.minute)}:${pad(dt.second)}`;
  if (dt.micro !== 0) s += `.${pad(dt.micro, 6)}`;
  if (dt.offsetMinutes !== null) {
    const sign = dt.offsetMinutes < 0 ? "-" : "+";
    const abs = Math.abs(dt.offsetMinutes);
    s += `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
  }
  return s;
}

/** `datetime.now(timezone.utc)` 를 성분으로. JS 는 밀리초까지라 뒤 3 자리는 0 이다. */
export function utcParts(ms: number): PyDateParts {
  const d = new Date(ms);
  return {
    year: d.getUTCFullYear(),
    month: d.getUTCMonth() + 1,
    day: d.getUTCDate(),
    hour: d.getUTCHours(),
    minute: d.getUTCMinutes(),
    second: d.getUTCSeconds(),
    micro: d.getUTCMilliseconds() * 1000,
    offsetMinutes: 0,
  };
}

/** `at.strftime('%Y%m%d')` — `partner_order_id` 에 들어간다. */
export function ymd(dt: PyDateParts): string {
  return `${pad(dt.year, 4)}${pad(dt.month)}${pad(dt.day)}`;
}

/** `at - timedelta(days=n)` — grace 임계 계산용. 오프셋을 유지한다. */
export function minusDays(dt: PyDateParts, days: number): PyDateParts {
  const base = Date.UTC(
    dt.year,
    dt.month - 1,
    dt.day,
    dt.hour,
    dt.minute,
    dt.second,
    Math.floor(dt.micro / 1000),
  );
  const d = new Date(base - days * 86_400_000);
  return {
    year: d.getUTCFullYear(),
    month: d.getUTCMonth() + 1,
    day: d.getUTCDate(),
    hour: d.getUTCHours(),
    minute: d.getUTCMinutes(),
    second: d.getUTCSeconds(),
    micro: dt.micro, // 밀리초 미만은 JS 가 못 들고 있으므로 원본 값을 유지한다
    offsetMinutes: dt.offsetMinutes,
  };
}
