import type { Document, Stats } from '@/lib/api';
import { ChunksStatsCard } from './cards/chunks-stats-card';
import { MyDocStatsCard } from './cards/my-doc-stats-card';
import { NewArrivalsCard } from './cards/new-arrivals-card';
import { PopularTagsCard } from './cards/popular-tags-card';
import { RecentlyViewedCard } from './cards/recently-viewed-card';
import { SearchTipsCard } from './cards/search-tips-card';

interface HomeGridProps {
  stats: Stats;
  recentDocuments: Document[];
}

export function HomeGrid({ stats, recentDocuments }: HomeGridProps) {
  return (
    <section className="container mx-auto px-4 py-8 md:px-6 md:py-12">
      <div className="grid gap-8 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <NewArrivalsCard documents={recentDocuments} />
          <RecentlyViewedCard />
        </div>
        <div className="space-y-6">
          <PopularTagsCard tags={stats.popular_tags} />
          <MyDocStatsCard stats={stats} />
          <ChunksStatsCard stats={stats} />
          <SearchTipsCard />
        </div>
      </div>
    </section>
  );
}
