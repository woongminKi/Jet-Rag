/**
 * `/admin/feedback` 라우트 — answer_feedback 통합 분석 대시보드 (S1 D4 ship, master plan §6).
 *
 * - Server Component 가 default range='7d' 로 첫 fetch → SSR HTML 즉시 표시.
 * - Client Component (`FeedbackDashboard`) 가 range 토글 시 refetch.
 * - graceful fallback — `.catch(() => null)` 백엔드 미기동 시 전용 카드.
 * - DoD: 1주 누적 후 사용자 평가 누적 신호 확인.
 *
 * 권한 — single-user MVP 라 별도 인증 없음 (`/admin/queries` 와 동일 패턴).
 */

import { getAdminFeedbackStats } from '@/lib/api';
import { FeedbackDashboard } from '@/components/jet-rag/admin/feedback-dashboard';

const INITIAL_RANGE = '7d' as const;

export default async function AdminFeedbackPage() {
  const initialStats = await getAdminFeedbackStats(INITIAL_RANGE).catch(() => null);

  if (!initialStats) {
    return (
      <main className="flex-1">
        <section className="container mx-auto px-4 py-8 md:px-6 md:py-12">
          <div className="rounded-lg border border-dashed border-border bg-muted/20 px-6 py-16 text-center">
            <p className="text-base font-medium text-foreground">
              사용자 피드백 통계를 불러오지 못했어요
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              잠시 후 다시 시도해 주세요. (백엔드 미기동 시 발생)
            </p>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="flex-1">
      <section className="container mx-auto px-4 py-8 md:px-6 md:py-12">
        <FeedbackDashboard initialStats={initialStats} />
      </section>
    </main>
  );
}
