/**
 * `/admin/queries` 라우트 — 실 query 로그 시각화 대시보드 (S1 D3 ship, master plan §6).
 *
 * - Server Component 가 default range='7d' 로 첫 fetch → SSR HTML 즉시 표시.
 * - Client Component (`QueriesDashboard`) 가 range 토글 시 refetch.
 * - graceful fallback — `.catch(() => null)` 백엔드 미기동 시 전용 카드.
 * - DoD: 1주 누적 후 실 query 분포 확인 → S1 D5 모델 회귀 측정의 사전 자료.
 *
 * 권한 — single-user MVP 라 별도 인증 없음 (다른 페이지 패턴과 동일).
 * production 진입 시 admin 보호는 별도 sprint.
 */

import { getAdminQueriesStats } from '@/lib/api';
import { QueriesDashboard } from '@/components/jet-rag/admin/queries-dashboard';

const INITIAL_RANGE = '7d' as const;

export default async function AdminQueriesPage() {
  const initialStats = await getAdminQueriesStats(INITIAL_RANGE).catch(() => null);

  if (!initialStats) {
    return (
      <main className="flex-1">
        <section className="container mx-auto px-4 py-8 md:px-6 md:py-12">
          <div className="rounded-lg border border-dashed border-border bg-muted/20 px-6 py-16 text-center">
            <p className="text-base font-medium text-foreground">
              실 query 통계를 불러오지 못했어요
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
        <QueriesDashboard initialStats={initialStats} />
      </section>
    </main>
  );
}
