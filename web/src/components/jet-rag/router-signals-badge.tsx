'use client';

import Link from 'next/link';
import {
  AlertCircle,
  Compass,
  Layers,
  Search,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { AnswerMeta } from '@/lib/api';

/** S5-A (2026-05-10, 2026-05-14 CTA 강화) — backend `meta` 시각화.
 *
 *  backend `intent_router.route` + `query_decomposer` 결과를 사용자에게 노출.
 *  명시 액션 패턴 — autoplay X, 안내·링크만 표시 (Q-S5-2 결정 default 권고).
 *
 *  표시 조건 (셋 중 하나라도 참):
 *  - `meta.low_confidence === true`
 *  - `meta.router_signals` 가 알려진 신호 1개 이상 포함
 *  - `meta.decomposed_subqueries` 가 비어있지 않음 (2026-05-14 W-9 D3 CTA)
 *
 *  signals 매핑 (backend `intent_router.IntentRouterDecision.triggered_signals` —
 *  `T1_cross_doc` ~ `T7_multi_target` 7종. 2026-05-14 정정 — 사용자 가치 큰
 *  2종 (T1, T7) 만 노출하고 나머지는 graceful skip):
 *  - `T1_cross_doc`   → 여러 문서 비교 의도
 *  - `T7_multi_target`→ 여러 대상 동시 비교 의도
 *  - 그 외 (T2~T6)    → 사용자 노출 가치 낮음, badge 표시 안 함 (graceful filter).
 *
 *  `T6_low_confidence` 는 별도 `meta.low_confidence` boolean 으로 안내문 노출
 *  (signal 라벨 중복 회피).
 *
 *  decomposed_subqueries CTA (W-9 D3):
 *  - paid LLM 이 분해한 sub-query 를 `/search?q=...` 링크로 노출
 *  - 사용자가 각 sub-query 로 직접 재검색 가능 (명시 액션)
 *
 *  알 수 없는 signal 은 graceful skip (백엔드 신규 추가 시 안전).
 */

interface RouterSignalsBadgeProps {
  meta?: AnswerMeta | null;
}

interface SignalDescriptor {
  label: string;
  icon: typeof Compass;
}

const SIGNAL_META: Record<string, SignalDescriptor> = {
  T1_cross_doc: {
    label: '여러 문서를 비교하는 의도로 인식했어요',
    icon: Compass,
  },
  T7_multi_target: {
    label: '여러 대상을 동시에 묻는 질문이에요',
    icon: Layers,
  },
};

export function RouterSignalsBadge({ meta }: RouterSignalsBadgeProps) {
  if (!meta) return null;

  const signals = (meta.router_signals ?? []).filter(
    (sig): sig is keyof typeof SIGNAL_META => sig in SIGNAL_META,
  );
  const lowConfidence = meta.low_confidence === true;
  const subqueries = (meta.decomposed_subqueries ?? []).filter(
    (s) => typeof s === 'string' && s.trim().length > 0,
  );

  if (!lowConfidence && signals.length === 0 && subqueries.length === 0) {
    return null;
  }

  return (
    <div
      className={cn(
        'space-y-1.5 rounded-md border px-3 py-2 text-xs',
        lowConfidence
          ? 'border-warning/40 bg-warning/5 text-warning-foreground'
          : 'border-border bg-muted/40 text-muted-foreground',
      )}
      role="note"
      aria-label="답변 의도 분석"
    >
      {lowConfidence && (
        <div className="flex items-start gap-2">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <p className="leading-snug">
            이 질문의 의도를 명확히 파악하지 못했어요 —{' '}
            <span className="font-medium">출처를 직접 확인</span>하시기를
            권장해요.
          </p>
        </div>
      )}
      {signals.length > 0 && (
        <ul className="space-y-1 pl-5 first:pl-0">
          {signals.map((sig) => {
            const desc = SIGNAL_META[sig];
            const Icon = desc.icon;
            return (
              <li
                key={sig}
                className="flex items-start gap-2 text-muted-foreground"
              >
                <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                <span className="leading-snug">{desc.label}</span>
              </li>
            );
          })}
        </ul>
      )}
      {subqueries.length > 0 && (
        <div className="space-y-1.5 border-t border-border/40 pt-1.5">
          <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground/90">
            <Search className="h-3 w-3" aria-hidden />
            분해된 sub-query 로 검색했어요 — 직접 재검색해 보세요:
          </p>
          <ul className="flex flex-wrap gap-1.5">
            {subqueries.map((sq) => (
              <li key={sq}>
                <Link
                  href={`/search?q=${encodeURIComponent(sq)}`}
                  className="inline-flex items-center rounded bg-primary/10 px-2 py-0.5 text-[11px] text-primary hover:bg-primary/20"
                >
                  {sq}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
