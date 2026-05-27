'use client';

import { useEffect, useState } from 'react';
import { BarChart3, Loader2, RotateCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import {
  ApiError,
  getRagasEval,
  submitRagasEval,
  type AnswerSource,
  type RagasEvalResponse,
  type RagasMetrics,
} from '@/lib/api';
import { toast } from 'sonner';

/** W25 D14 — RAGAS 정량 평가 카드.
 *
 *  - mount 시 캐시 (GET) 조회 → 있으면 즉시 표시 (LLM judge 호출 0)
 *  - 캐시 없을 시 "정량 평가" 버튼 → POST (Gemini judge, ~5~10초 대기)
 *  - 결과 카드: Faithfulness + AnswerRelevancy 점수 (% + bar)
 *  - graceful: 마이그 012 미적용 또는 GEMINI_API_KEY 미설정 시 skipped 안내
 */

interface RagasEvalCardProps {
  query: string;
  answer: string;
  docId: string | null;
  sources: AnswerSource[];
}

const METRIC_LABELS: Record<keyof RagasMetrics, string> = {
  context_precision: '검색 적합도 (Context Precision)',
  faithfulness: '충실도 (Faithfulness)',
  answer_relevancy: '관련성 (Relevancy)',
  context_recall: 'Context Recall',
  answer_correctness: '정답 일치도',
};

const METRIC_DESCRIPTIONS: Record<keyof RagasMetrics, string> = {
  context_precision: '검색된 출처 chunks 가 질문에 얼마나 잘 맞는가 — 가장 관련된 chunk 가 상위에 있는지 LLM judge 평가',
  faithfulness: '답변이 출처에 충실한가 — 출처에 없는 내용 (환각) 비율',
  answer_relevancy: '답변이 질문에 적합한가 — 질문 의도 일치도',
  context_recall: '출처가 정답 정보 cover (ground truth 필요)',
  answer_correctness: '정답과 답변의 일치도 (ground truth 필요)',
};

function scoreToTone(score: number | null): string {
  if (score === null) return 'bg-muted text-muted-foreground';
  if (score >= 0.8) return 'bg-success text-success-foreground';
  if (score >= 0.6) return 'bg-warning text-warning-foreground';
  return 'bg-destructive text-destructive-foreground';
}

function scoreLabel(score: number | null): string {
  if (score === null) return '—';
  return `${Math.round(score * 100)}점`;
}

export function RagasEvalCard({ query, answer, docId, sources }: RagasEvalCardProps) {
  const [data, setData] = useState<RagasEvalResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [evaluating, setEvaluating] = useState(false);

  // mount 시 캐시 조회 (LLM judge 호출 0)
  useEffect(() => {
    let cancelled = false;
    getRagasEval(query, docId)
      .then((res) => {
        if (cancelled) return;
        // 캐시 hit (cached=true) 또는 metrics 모두 null 인 빈 응답
        setData(res);
      })
      .catch(() => {
        // graceful — 백엔드 미기동 등
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [query, docId]);

  const runEvaluation = async () => {
    setEvaluating(true);
    try {
      const contexts = sources.map((s) => s.snippet).filter((c) => c.trim().length > 0);
      if (contexts.length === 0) {
        toast.warning('출처 본문이 없어 평가할 수 없어요');
        return;
      }
      const res = await submitRagasEval({
        query,
        answer_text: answer,
        doc_id: docId,
        contexts,
      });
      setData(res);
      if (res.skipped) {
        toast.info('RAGAS 평가 비활성', {
          description: res.note ?? '관리자에게 마이그 012 적용 부탁',
        });
      } else {
        toast.success('정량 평가 완료', {
          description: `Gemini judge · ${res.took_ms}ms`,
        });
      }
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : '알 수 없는 오류';
      toast.error('RAGAS 평가 실패', { description: detail });
    } finally {
      setEvaluating(false);
    }
  };

  const hasMetrics =
    data !== null &&
    (data.metrics.faithfulness !== null || data.metrics.answer_relevancy !== null);

  if (loading) {
    return (
      <div className="overflow-hidden rounded-2xl border border-border bg-card px-4 py-3 text-xs text-muted-foreground">
        <Loader2 className="mr-1.5 inline-block h-3 w-3 animate-spin" /> 정량 평가 캐시 확인 중…
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-card p-4 md:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex min-w-0 items-center gap-2 text-sm font-medium text-foreground">
          <BarChart3 className="h-4 w-4 shrink-0 text-primary" />
          <span className="break-words">정량 평가 (RAGAS)</span>
        </h3>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={runEvaluation}
          disabled={evaluating}
          className="h-9 shrink-0 gap-1 px-3 text-xs sm:h-8 sm:px-2.5"
          title={
            hasMetrics
              ? '같은 질문 다시 평가 (LLM judge 호출, ~5~10초 + 비용)'
              : 'Gemini judge 로 정량 평가 (LLM 호출, ~5~10초)'
          }
        >
          {evaluating ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <RotateCw className="h-3 w-3" />
          )}
          {hasMetrics ? '다시 평가' : '정량 평가 실행'}
        </Button>
      </div>

      {data?.skipped && (
        <p className="mt-2 text-xs text-muted-foreground">
          {data.note ?? '평가 비활성'}
        </p>
      )}

      {hasMetrics && data && (
        <div className="mt-3 space-y-3">
          {/* "검색 적합도" — 사용자 needs (chunks 가 잘 불러와졌는지) */}
          <div>
            <h4 className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground/80">
              검색 품질
            </h4>
            <MetricRow
              label={METRIC_LABELS.context_precision}
              description={METRIC_DESCRIPTIONS.context_precision}
              score={data.metrics.context_precision}
            />
          </div>

          {/* 답변 품질 — 환각·적합 */}
          <div>
            <h4 className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground/80">
              답변 품질
            </h4>
            <div className="space-y-2">
              {(['faithfulness', 'answer_relevancy'] as const).map((key) => {
                const score = data.metrics[key];
                return (
                  <MetricRow
                    key={key}
                    label={METRIC_LABELS[key]}
                    description={METRIC_DESCRIPTIONS[key]}
                    score={score}
                  />
                );
              })}
            </div>
          </div>

          <p className="pt-1 text-[11px] text-muted-foreground/70">
            judge: {data.judge_model ?? '?'} · {data.cached ? '캐시' : `측정 ${data.took_ms ?? '?'}ms`}
            {data.created_at && ` · ${new Date(data.created_at).toLocaleString('ko')}`}
          </p>
        </div>
      )}

      {!hasMetrics && !data?.skipped && (
        <p className="mt-2 text-xs text-muted-foreground">
          아직 정량 평가가 없습니다. &lsquo;정량 평가 실행&rsquo; 클릭 시 Gemini judge 가
          충실도·관련성 점수를 매깁니다 (~5~10초).
        </p>
      )}
    </div>
  );
}

function MetricRow({
  label,
  description,
  score,
}: {
  label: string;
  description: string;
  score: number | null;
}) {
  const pct = score !== null ? Math.round(score * 100) : 0;
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
        <span className="min-w-0 break-words font-medium text-foreground" title={description}>
          {label}
        </span>
        <span className={cn('shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px]', scoreToTone(score))}>
          {scoreLabel(score)}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-sm bg-muted">
        <div
          className={cn('h-full transition-all', score !== null ? 'bg-primary' : 'bg-muted')}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
