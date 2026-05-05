'use client';

import { useEffect, useState } from 'react';
import { BarChart3, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  ApiError,
  getSearchPrecision,
  submitSearchPrecision,
  type RagasEvalResponse,
  type SearchHit,
} from '@/lib/api';

/** W25 D14 — 검색 결과 페이지에서 자동 측정되는 검색 적합도 (Context Precision) 카드.
 *
 *  - mount 시 GET 캐시 조회 → 캐시 hit 시 즉시 표시
 *  - 캐시 미스 시 자동 POST (LLM judge 1개, ~5초, ~$0.003)
 *  - 점수 % + tone-coded bar
 *  - graceful: skipped 시 안내, 백엔드 미기동 시 미노출
 */

interface SearchPrecisionCardProps {
  query: string;
  docId: string | null;
  hits: SearchHit[];
}

function scoreToTone(score: number | null): string {
  if (score === null) return 'bg-muted text-muted-foreground';
  if (score >= 0.8) return 'bg-success text-success-foreground';
  if (score >= 0.6) return 'bg-warning text-warning-foreground';
  return 'bg-destructive text-destructive-foreground';
}

export function SearchPrecisionCard({ query, docId, hits }: SearchPrecisionCardProps) {
  const [data, setData] = useState<RagasEvalResponse | null>(null);
  const [phase, setPhase] = useState<'cache' | 'measuring' | 'done' | 'error'>('cache');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // React 19 — useEffect 내 동기 setState 금지. 초기 phase='cache' 그대로 시작.
    const contexts = hits
      .flatMap((h) => h.matched_chunks)
      .map((c) => (c.text || '').slice(0, 800))
      .filter((s) => s.trim().length > 0)
      .slice(0, 10);

    // 1) 캐시 우선 조회
    getSearchPrecision(query, docId)
      .then((res) => {
        if (cancelled) return;
        if (res.cached || res.skipped) {
          setData(res);
          setPhase('done');
          return;
        }
        // 2) 캐시 미스 → 자동 측정 (LLM judge 호출)
        if (contexts.length === 0) {
          setData({ ...res, metrics: { ...res.metrics, context_precision: 0 } });
          setPhase('done');
          return;
        }
        setPhase('measuring');
        submitSearchPrecision({ query, doc_id: docId, contexts })
          .then((measured) => {
            if (cancelled) return;
            setData(measured);
            setPhase('done');
          })
          .catch((err) => {
            if (cancelled) return;
            setError(err instanceof ApiError ? err.detail : '측정 실패');
            setPhase('error');
          });
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.detail : '캐시 조회 실패');
        setPhase('error');
      });

    return () => {
      cancelled = true;
    };
  }, [query, docId, hits]);

  // 로딩 / 측정 중
  if (phase === 'cache' || phase === 'measuring') {
    return (
      <div className="mb-4 flex items-center gap-2 rounded-lg border border-border bg-card px-4 py-3 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
        {phase === 'cache' ? 'RAGAS 캐시 확인 중…' : '검색 적합도 측정 중 (LLM judge ~5초)…'}
      </div>
    );
  }

  if (phase === 'error') {
    return (
      <div className="mb-4 rounded-lg border border-border bg-card px-4 py-3 text-xs text-muted-foreground">
        검색 적합도 측정 일시 실패: {error}
      </div>
    );
  }

  if (data?.skipped) {
    return (
      <div className="mb-4 rounded-lg border border-border bg-card px-4 py-3 text-xs text-muted-foreground">
        {data.note ?? 'RAGAS 평가 비활성'}
      </div>
    );
  }

  const score = data?.metrics.context_precision ?? null;
  const pct = score !== null ? Math.round(score * 100) : 0;

  return (
    <div className="mb-4 rounded-lg border border-border bg-card px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-medium text-foreground">
          <BarChart3 className="h-4 w-4 text-primary" />
          검색 적합도 (RAGAS Context Precision)
        </h3>
        <span
          className={cn(
            'rounded px-1.5 py-0.5 text-[11px] font-mono',
            scoreToTone(score),
          )}
        >
          {score !== null ? `${pct}점` : '—'}
        </span>
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground" title="BGE-M3 dense embedding cosine similarity (DCG-weighted)">
        검색된 chunks 가 질문에 얼마나 잘 맞는가 — BGE-M3 임베딩 cosine + ranking 가중 평균
      </p>
      <div className="mt-2 h-1.5 overflow-hidden rounded-sm bg-muted">
        <div
          className={cn('h-full transition-all', score !== null ? 'bg-primary' : 'bg-muted')}
          style={{ width: `${pct}%` }}
        />
      </div>
      {data && (
        <p className="mt-2 text-[11px] text-muted-foreground/70">
          judge: {data.judge_model ?? '?'} · {data.cached ? '캐시' : `측정 ${data.took_ms ?? '?'}ms`}
          {data.created_at && ` · ${new Date(data.created_at).toLocaleString('ko')}`}
        </p>
      )}
    </div>
  );
}
