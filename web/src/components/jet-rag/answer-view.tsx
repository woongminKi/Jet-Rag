'use client';

import { useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import {
  CheckCircle2,
  MessageCircle,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  ThumbsDown,
  ThumbsUp,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { ApiError, submitAnswerFeedback, type AnswerResponse, type AnswerSource } from '@/lib/api';
import { toast } from 'sonner';
import { RagasEvalCard } from './ragas-eval-card';
import { RouterSignalsBadge } from './router-signals-badge';

/** W25 D14 — `/ask` 답변 품질 가시화 (B + E + C 통합).
 *
 *  - E: 신뢰도 휴리스틱 배지 (높음/보통/낮음) — has_search_results + sources.length
 *  - B: 답변의 인라인 [N] → 클릭 시 해당 source 카드 highlight + scroll
 *  - C: 사용자 피드백 (👍/👎 + 옵션 코멘트) — POST /answer/feedback
 *
 *  - graceful: 마이그 011 미적용 시 백엔드가 skipped=true 응답 → 토스트로 안내
 */

type Confidence = 'high' | 'medium' | 'low';

interface AnswerViewProps {
  query: string;
  response: AnswerResponse;
  docId: string | null;
}

function classifyConfidence(response: AnswerResponse): Confidence {
  if (!response.has_search_results) return 'low';
  if (response.sources.length === 0) return 'low';
  if (response.sources.length <= 2) return 'medium';
  if (response.answer.trim().length < 50) return 'medium';
  return 'high';
}

const CONFIDENCE_META: Record<
  Confidence,
  { label: string; tone: string; icon: typeof ShieldCheck; description: string }
> = {
  high: {
    label: '신뢰도 높음',
    tone: 'border-success/40 bg-success/10 text-success-foreground',
    icon: ShieldCheck,
    description: '여러 출처 (3개 이상) 와 풍부한 답변',
  },
  medium: {
    label: '신뢰도 보통',
    tone: 'border-warning/40 bg-warning/10 text-warning-foreground',
    icon: ShieldQuestion,
    description: '출처가 적거나 답변이 짧음 — 직접 검증 권장',
  },
  low: {
    label: '신뢰도 낮음',
    tone: 'border-destructive/40 bg-destructive/10 text-destructive',
    icon: ShieldAlert,
    description: '관련 자료를 찾지 못했거나 출처 0개',
  },
};

export function AnswerView({ query, response, docId }: AnswerViewProps) {
  const confidence = useMemo(() => classifyConfidence(response), [response]);
  const meta = CONFIDENCE_META[confidence];

  // S5-A (2026-05-10) — backend `meta.low_confidence` + `meta.router_signals`
  // 시각화. RouterSignalsBadge 가 신뢰도 배지 아래에 추가 안내 표시.
  // 명시 액션 패턴 (Q-S5-2) — autoplay X, 안내만 노출.

  // 출처 highlight — [N] 클릭 시
  const sourceRefs = useRef<Map<number, HTMLLIElement | null>>(new Map());
  const [highlightedIdx, setHighlightedIdx] = useState<number | null>(null);

  const handleCitationClick = (idx: number) => {
    const el = sourceRefs.current.get(idx);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      setHighlightedIdx(idx);
      window.setTimeout(() => setHighlightedIdx(null), 2000);
    }
  };

  // 답변 본문 [N] 패턴 → 클릭 가능 button 으로 변환
  const renderedAnswer = useMemo(() => {
    const parts: Array<string | { idx: number; raw: string }> = [];
    const re = /\[(\d+)\]/g;
    let lastIdx = 0;
    let match: RegExpExecArray | null;
    while ((match = re.exec(response.answer)) !== null) {
      if (match.index > lastIdx) parts.push(response.answer.slice(lastIdx, match.index));
      parts.push({ idx: Number(match[1]), raw: match[0] });
      lastIdx = match.index + match[0].length;
    }
    if (lastIdx < response.answer.length) parts.push(response.answer.slice(lastIdx));
    return parts;
  }, [response.answer]);

  // 피드백
  const [submittedHelpful, setSubmittedHelpful] = useState<boolean | null>(null);
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  const [showCommentBox, setShowCommentBox] = useState(false);
  const [comment, setComment] = useState('');

  const submitFeedback = async (helpful: boolean, withComment = false) => {
    setFeedbackLoading(true);
    try {
      const res = await submitAnswerFeedback({
        query,
        answer_text: response.answer,
        helpful,
        comment: withComment && comment.trim() ? comment.trim() : null,
        doc_id: docId,
        sources_count: response.sources.length,
        model: response.model,
      });
      setSubmittedHelpful(helpful);
      if (res.skipped) {
        toast.info('피드백 저장 비활성', {
          description: res.note ?? '관리자에게 마이그 011 적용 부탁',
        });
      } else {
        toast.success(helpful ? '도움이 됐다고 평가했어요' : '개선 요청을 받았어요', {
          description: '소중한 피드백 감사합니다',
        });
      }
      setShowCommentBox(false);
      setComment('');
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : '알 수 없는 오류';
      toast.error('피드백 전송 실패', { description: detail });
    } finally {
      setFeedbackLoading(false);
    }
  };

  return (
    <section className="mx-auto max-w-3xl space-y-6">
      {/* 신뢰도 배지 */}
      <div
        className={cn(
          'flex items-center gap-2 rounded-md border px-3 py-2 text-xs',
          meta.tone,
        )}
      >
        <meta.icon className="h-4 w-4" aria-hidden />
        <span className="font-medium">{meta.label}</span>
        <span className="text-muted-foreground/80">· {meta.description}</span>
      </div>

      {/* S5-A — backend meta 기반 의도 안내 (low_confidence + router_signals) */}
      <RouterSignalsBadge meta={response.meta} />

      {/* 답변 본문 */}
      <article className="rounded-lg border border-border bg-card px-5 py-5 shadow-sm">
        <p className="whitespace-pre-line text-[15px] leading-relaxed text-foreground">
          {renderedAnswer.map((part, i) => {
            if (typeof part === 'string') return <span key={i}>{part}</span>;
            return (
              <button
                key={i}
                type="button"
                onClick={() => handleCitationClick(part.idx)}
                className="mx-0.5 inline-flex items-center rounded bg-primary/15 px-1 font-mono text-[12px] text-primary hover:bg-primary/25"
                aria-label={`출처 ${part.idx} 강조`}
              >
                {part.raw}
              </button>
            );
          })}
        </p>
        {!response.has_search_results && (
          <p className="mt-3 text-xs text-muted-foreground">
            관련 자료를 찾지 못했어요. 다른 키워드로{' '}
            <Link
              href={`/search?q=${encodeURIComponent(query)}`}
              className="text-primary hover:underline"
            >
              검색
            </Link>
            해 보세요.
          </p>
        )}

        {/* 피드백 버튼 (C) */}
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <span className="text-xs text-muted-foreground">이 답변이 도움이 됐나요?</span>
          <Button
            type="button"
            size="sm"
            variant={submittedHelpful === true ? 'default' : 'outline'}
            disabled={feedbackLoading || submittedHelpful !== null}
            onClick={() => submitFeedback(true)}
            className="h-7 gap-1 px-2 text-xs"
          >
            <ThumbsUp className="h-3 w-3" /> 네
          </Button>
          <Button
            type="button"
            size="sm"
            variant={submittedHelpful === false ? 'default' : 'outline'}
            disabled={feedbackLoading || submittedHelpful !== null}
            onClick={() => {
              setShowCommentBox(true);
            }}
            className="h-7 gap-1 px-2 text-xs"
          >
            <ThumbsDown className="h-3 w-3" /> 아니오
          </Button>
          {submittedHelpful !== null && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-success">
              <CheckCircle2 className="h-3 w-3" /> 피드백 감사합니다
            </span>
          )}
        </div>

        {showCommentBox && submittedHelpful === null && (
          <div className="mt-3 space-y-2">
            <label className="flex items-center gap-1 text-xs text-muted-foreground">
              <MessageCircle className="h-3 w-3" /> 어떤 점이 아쉬웠나요? (선택)
            </label>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={2}
              maxLength={500}
              placeholder="답변에 대한 의견을 남겨주세요"
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-xs"
            />
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={feedbackLoading}
                onClick={() => {
                  setShowCommentBox(false);
                  setComment('');
                }}
                className="h-7 px-2 text-xs"
              >
                취소
              </Button>
              <Button
                type="button"
                size="sm"
                disabled={feedbackLoading}
                onClick={() => submitFeedback(false, true)}
                className="h-7 px-2 text-xs"
              >
                보내기
              </Button>
            </div>
          </div>
        )}
      </article>

      {/* RAGAS 정량 평가 카드 (W25 D14) */}
      <RagasEvalCard
        query={query}
        answer={response.answer}
        docId={docId}
        sources={response.sources}
      />

      {/* 출처 카드 (B — 클릭 highlight) */}
      {response.sources.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-medium text-muted-foreground">
            출처 ({response.sources.length})
          </h2>
          <ol className="space-y-3">
            {response.sources.map((src, i) => (
              <SourceCard
                key={src.chunk_id}
                ref={(el) => {
                  sourceRefs.current.set(i + 1, el);
                }}
                index={i + 1}
                source={src}
                highlighted={highlightedIdx === i + 1}
              />
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}

interface SourceCardProps {
  index: number;
  source: AnswerSource;
  highlighted: boolean;
  ref: (el: HTMLLIElement | null) => void;
}

function SourceCard({ index, source, highlighted, ref }: SourceCardProps) {
  const docTitle = source.doc_title || '(제목 없음)';
  const docHref = `/doc/${source.doc_id}`;
  return (
    <li
      ref={ref}
      className={cn(
        'rounded-md border bg-card px-4 py-3 transition-all duration-300',
        highlighted ? 'border-primary ring-2 ring-primary/40' : 'border-border',
      )}
    >
      <div className="flex flex-wrap items-baseline gap-2">
        <Badge variant="outline" className="font-mono text-[11px]">
          [{index}]
        </Badge>
        <Link
          href={docHref}
          className="text-sm font-medium text-foreground hover:underline"
        >
          {docTitle}
        </Link>
        {source.page !== null && source.page > 0 && (
          <span className="text-xs text-muted-foreground">p.{source.page}</span>
        )}
        {source.section_title && (
          <span className="truncate text-xs text-muted-foreground">
            · {source.section_title}
          </span>
        )}
        <span className="ml-auto text-[11px] font-mono text-muted-foreground/70">
          chunk #{source.chunk_idx}
        </span>
      </div>
      <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-muted-foreground">
        {source.snippet}
      </p>
    </li>
  );
}
