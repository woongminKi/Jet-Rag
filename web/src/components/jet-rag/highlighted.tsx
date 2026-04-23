import { Fragment } from 'react';

interface HighlightedProps {
  text: string;
  ranges: Array<[number, number]>;
}

export function Highlighted({ text, ranges }: HighlightedProps) {
  if (!ranges.length) return <>{text}</>;

  const sorted = [...ranges]
    .filter(([s, e]) => Number.isFinite(s) && Number.isFinite(e) && e > s)
    .sort((a, b) => a[0] - b[0]);

  const merged: Array<[number, number]> = [];
  for (const [s, e] of sorted) {
    const last = merged[merged.length - 1];
    if (last && s <= last[1]) {
      last[1] = Math.max(last[1], e);
    } else {
      merged.push([Math.max(0, s), Math.min(text.length, e)]);
    }
  }

  const parts: React.ReactNode[] = [];
  let cursor = 0;
  merged.forEach(([s, e], i) => {
    if (s > cursor) parts.push(<Fragment key={`p${i}`}>{text.slice(cursor, s)}</Fragment>);
    parts.push(
      <mark
        key={`m${i}`}
        className="rounded-sm bg-accent/30 px-0.5 text-foreground"
      >
        {text.slice(s, e)}
      </mark>,
    );
    cursor = e;
  });
  if (cursor < text.length) parts.push(<Fragment key="tail">{text.slice(cursor)}</Fragment>);

  return <>{parts}</>;
}
