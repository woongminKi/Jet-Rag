import type { Document, Stats, TrendResponse } from '@/lib/api';
import { ChunksStatsCard } from './cards/chunks-stats-card';
import { MyDocStatsCard } from './cards/my-doc-stats-card';
import { NewArrivalsCard } from './cards/new-arrivals-card';
import { PopularTagsCard } from './cards/popular-tags-card';
import { RecentlyViewedCard } from './cards/recently-viewed-card';
import { SearchTipsCard } from './cards/search-tips-card';
// W26 v3 — SLO/Trend/Vision 카드 4종 제거 (메인 페이지 노이즈 축소).
// 필요 시 git history 에서 복원 가능. 컴포넌트 파일 자체는 그대로 유지.
// import { MetricsTrendCard } from './cards/metrics-trend-card';
// import { SearchSloCard } from './cards/search-slo-card';
// import { VisionUsageCard } from './cards/vision-usage-card';

interface HomeGridProps {
  stats: Stats;
  recentDocuments: Document[];
  // W26 v3 — Trend 카드 제거로 미사용 prop. page.tsx 도 동시 정리.
  searchTrend?: TrendResponse | null;
  visionTrend?: TrendResponse | null;
}

export function HomeGrid({
  stats,
  recentDocuments,
}: HomeGridProps) {
  return (
    <section className="container mx-auto px-4 py-6 md:px-6 md:py-12">
      {/* W26 — mobile gap 축소 (gap-4) / desktop 은 기존 gap-8 유지 */}
      <div className="grid gap-4 md:gap-6 lg:grid-cols-3 lg:gap-8">
        <div className="space-y-4 md:space-y-6 lg:col-span-2">
          <NewArrivalsCard documents={recentDocuments} />
          <RecentlyViewedCard />
        </div>
        <div className="space-y-4 md:space-y-6">
          <PopularTagsCard tags={stats.popular_tags} />
          <MyDocStatsCard stats={stats} />
          <ChunksStatsCard stats={stats} />
          <SearchTipsCard />
        </div>
      </div>
    </section>
  );
}
