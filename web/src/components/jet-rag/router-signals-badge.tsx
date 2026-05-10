'use client';

import {
  AlertCircle,
  Compass,
  Clock,
  HelpCircle,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { AnswerMeta } from '@/lib/api';

/** S5-A (2026-05-10) — `meta.low_confidence` + `meta.router_signals` 시각화.
 *
 *  backend `intent_router.route` (`api/app/services/intent_router.py`) 의 룰
 *  기반 진단을 사용자에게 노출. 명시 액션 패턴 — autoplay X, 안내만 표시
 *  (Q-S5-2 결정 default 권고).
 *
 *  표시 조건 (둘 중 하나라도 참):
 *  - `meta.low_confidence === true`
 *  - `meta.router_signals` 가 알려진 신호 1개 이상 포함
 *
 *  signals 매핑 (`triggered_signals` 의 string literal):
 *  - `cross_doc` → 여러 문서 비교 의도
 *  - `temporal`  → 시간 기준 질문
 *  - `ambiguous` → 의도 불명확
 *  - `numeric`   → 수치/통계 의도 (참고용 — low confidence 아니라도 표시 가능)
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
  cross_doc: {
    label: '여러 문서를 비교하는 의도로 인식했어요',
    icon: Compass,
  },
  temporal: {
    label: '시간 기준 질문으로 인식했어요',
    icon: Clock,
  },
  ambiguous: {
    label: '질문 의도가 명확하지 않을 수 있어요',
    icon: HelpCircle,
  },
};

export function RouterSignalsBadge({ meta }: RouterSignalsBadgeProps) {
  if (!meta) return null;

  const signals = (meta.router_signals ?? []).filter(
    (sig): sig is keyof typeof SIGNAL_META => sig in SIGNAL_META,
  );
  const lowConfidence = meta.low_confidence === true;

  if (!lowConfidence && signals.length === 0) return null;

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
    </div>
  );
}
