'use client';

import { useEffect, useMemo, useState } from 'react';
import { BarChart3, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  ApiError,
  getSearchPrecision,
  submitSearchPrecision,
  type RagasEvalResponse,
  type SearchHit,
} from '@/lib/api';

/** S0 D4 — 검색 결과 페이지의 검색 적합도 (Context Precision) 카드.
 *
 *  - mount 시 GET 캐시 조회 (무료, 즉시 표시)
 *  - 캐시 hit / skipped → 그대로 표시
 *  - 캐시 미스 → "측정" 버튼 노출 (자동 POST 금지)
 *  - 사용자가 버튼 클릭 시에만 LLM judge POST (~5초, ~$0.003)
 *  - 점수 % + tone-coded bar
 *  - graceful: skipped 시 안내, 백엔드 미기동/error 시 안내 문구
 *
 *  master plan §3 원칙 5 — 비용 발생 행위는 사용자 액션 뒤에.
 *  페르소나 A — 모르는 사이 LLM 호출 0.
 */

const _MAX_CONTEXTS = 10;
const _CONTEXT_CHAR_LIMIT = 800;

type Phase = 'cache' | 'idle' | 'measuring' | 'done' | 'error';

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
  const [phase, setPhase] = useState<Phase>('cache');
  const [error, setError] = useState<string | null>(null);

  // contexts 는 GET 결과와 무관하게 hits 로부터 계산. 클릭 시 POST 페이로드.
  const contexts = useMemo(
    () =>
      hits
        .flatMap((h) => h.matched_chunks)
        .map((c) => (c.text || '').slice(0, _CONTEXT_CHAR_LIMIT))
        .filter((s) => s.trim().length > 0)
        .slice(0, _MAX_CONTEXTS),
    [hits],
  );

  // mount / query·docId 변경 시 캐시만 조회. 자동 POST 없음 (S0 D4 비용 누수 fix).
  useEffect(() => {
    let cancelled = false;

    getSearchPrecision(query, docId)
      .then((res) => {
        if (cancelled) return;
        if (res.cached || res.skipped) {
          setData(res);
          setPhase('done');
          return;
        }
        // 캐시 미스 — 사용자 클릭 대기 (자동 POST X).
        setData(null);
        setPhase('idle');
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.detail : '캐시 조회 실패');
        setPhase('error');
      });

    return () => {
      cancelled = true;
    };
  }, [query, docId]);

  // 사용자 액션 — handler 안 동기 setState 는 React 19 lint OK.
  const handleMeasure = () => {
    if (phase === 'measuring' || contexts.length === 0) return;
    setError(null);
    setPhase('measuring');
    submitSearchPrecision({ query, doc_id: docId, contexts })
      .then((measured) => {
        setData(measured);
        setPhase('done');
      })
      .catch((err) => {
        setError(err instanceof ApiError ? err.detail : '측정 실패');
        setPhase('error');
      });
  };

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

  if (phase === 'idle') {
    const noContexts = contexts.length === 0;
    return (
      <div className="mb-4 rounded-lg border border-border bg-card px-4 py-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="flex items-center gap-2 text-sm font-medium text-foreground">
            <BarChart3 className="h-4 w-4 text-primary" />
            검색 적합도 (RAGAS Context Precision)
          </h3>
          <button
            type="button"
            onClick={handleMeasure}
            disabled={noContexts}
            aria-label="검색 적합도 측정 — LLM judge 호출 (~5초, 약 $0.003)"
            className={cn(
              'rounded border px-2 py-1 text-[11px] font-medium transition-colors',
              noContexts
                ? 'cursor-not-allowed border-border bg-muted text-muted-foreground'
                : 'border-primary/40 bg-primary/10 text-primary hover:bg-primary/20',
            )}
          >
            측정
          </button>
        </div>
        <p className="mt-1 text-[11px] text-muted-foreground">
          {noContexts
            ? '측정할 검색 결과 텍스트가 없어요.'
            : '아직 측정 전 — 클릭 시 LLM judge 1회 호출 (~5초, 약 $0.003).'}
        </p>
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
