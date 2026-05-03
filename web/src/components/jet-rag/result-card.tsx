import type { SearchHit } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Highlighted } from './highlighted';
import { docTypeLabel } from '@/lib/doc-type-label';
import { formatRelativeTime } from '@/lib/format';

interface ResultCardProps {
  hit: SearchHit;
  /** W7 Day 4 — true 시 chunk 별 raw metadata 패널 펼쳐 디버깅 가시성 ↑. */
  debug?: boolean;
}

export function ResultCard({ hit, debug = false }: ResultCardProps) {
  const moreCount = Math.max(0, hit.matched_chunk_count - hit.matched_chunks.length);
  const relevancePct = Math.round(hit.relevance * 100);

  return (
    <Card>
      <CardHeader className="space-y-3 pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1 space-y-1">
            <h3 className="text-base font-semibold text-foreground">
              {hit.doc_title}
            </h3>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="outline" className="h-5 px-1.5 text-[10px]">
                {docTypeLabel(hit.doc_type)}
              </Badge>
              {hit.tags.slice(0, 3).map((tag) => (
                <Badge
                  key={tag}
                  variant="secondary"
                  className="h-5 px-1.5 text-[10px]"
                >
                  #{tag}
                </Badge>
              ))}
            </div>
          </div>
          <div className="w-32 shrink-0 space-y-1">
            <div className="flex items-center justify-between text-[10px] text-muted-foreground">
              <span>관련도</span>
              <span className="font-medium text-foreground">{relevancePct}%</span>
            </div>
            <Progress value={relevancePct} className="h-1.5" />
          </div>
        </div>
        {hit.summary ? (
          <p className="line-clamp-2 text-sm text-muted-foreground">{hit.summary}</p>
        ) : (
          <p className="text-xs italic text-muted-foreground">요약 미생성</p>
        )}
      </CardHeader>
      <CardContent className="space-y-3 pb-4">
        <ul className="space-y-2">
          {hit.matched_chunks.map((chunk) => {
            const overlapIdx = chunk.metadata?.['overlap_with_prev_chunk_idx'];
            const hasOverlap = typeof overlapIdx === 'number';
            return (
              <li
                key={chunk.chunk_id}
                className="rounded-md border border-border bg-muted/30 p-3 text-sm"
              >
                <div className="mb-1 flex items-center justify-between gap-2 text-[10px] uppercase tracking-wide text-muted-foreground">
                  <div className="flex items-center gap-2 min-w-0">
                    {chunk.page !== null && <span>p.{chunk.page}</span>}
                    {chunk.section_title && (
                      <>
                        <span className="text-border">·</span>
                        <span className="truncate">{chunk.section_title}</span>
                      </>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5 normal-case tracking-normal">
                    {hasOverlap && (
                      <span
                        className="rounded bg-muted px-1 py-0.5 text-[9px] font-medium text-muted-foreground"
                        title={`이전 청크 idx ${overlapIdx} 와 100자 prefix overlap`}
                      >
                        ↻ overlap
                      </span>
                    )}
                    {typeof chunk.rrf_score === 'number' && (
                      <span
                        className="font-mono tabular-nums text-[9px] text-muted-foreground"
                        title="RRF score (검색 ranking 근거)"
                      >
                        rrf {chunk.rrf_score.toFixed(4)}
                      </span>
                    )}
                  </div>
                </div>
                <p className="leading-relaxed text-foreground/90">
                  <Highlighted text={chunk.text} ranges={chunk.highlight} />
                </p>
                {debug && <ChunkDebugPanel chunk={chunk} />}
              </li>
            );
          })}
        </ul>
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {moreCount > 0 ? `+${moreCount}개 더 매칭` : `매칭 ${hit.matched_chunk_count}개`}
          </span>
          <span>{formatRelativeTime(hit.created_at)}</span>
        </div>
      </CardContent>
    </Card>
  );
}

function ChunkDebugPanel({
  chunk,
}: {
  chunk: SearchHit['matched_chunks'][number];
}) {
  const meta = chunk.metadata ?? {};
  const rows: Array<[string, string]> = [
    ['chunk_id', chunk.chunk_id],
    ['chunk_idx', String(chunk.chunk_idx)],
    ['page', chunk.page === null ? 'null' : String(chunk.page)],
    ['section_title', chunk.section_title ?? 'null'],
    [
      'rrf_score',
      typeof chunk.rrf_score === 'number' ? chunk.rrf_score.toFixed(6) : 'null',
    ],
    ['highlight_ranges', JSON.stringify(chunk.highlight)],
    ['text_len', String(chunk.text.length)],
  ];
  const metaKeys = Object.keys(meta);
  return (
    <div className="mt-2 space-y-1 rounded border border-dashed border-border bg-background/60 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-foreground/85">
      {rows.map(([k, v]) => (
        <div key={k} className="flex gap-2">
          <span className="shrink-0 w-32 font-semibold text-foreground/95">
            {k}
          </span>
          <span className="break-all">{v}</span>
        </div>
      ))}
      {metaKeys.length > 0 && (
        <>
          <div className="pt-1.5 font-semibold uppercase tracking-wide text-[10px] text-foreground/70">
            metadata
          </div>
          {metaKeys.map((k) => (
            <div key={k} className="flex gap-2 pl-3">
              <span className="shrink-0 w-30 font-semibold text-foreground/85">
                {k}
              </span>
              <span className="break-all">{JSON.stringify(meta[k])}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
