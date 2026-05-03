import { getStats, getStatsTrend, listDocuments } from '@/lib/api';
import type { TrendResponse } from '@/lib/api';
import { HeroSection } from '@/components/jet-rag/hero-section';
import { HomeGrid } from '@/components/jet-rag/home-grid';

export default async function HomePage() {
  const [stats, documents, searchTrend, visionTrend] = await Promise.all([
    getStats(),
    listDocuments(5),
    // /stats/trend 가 graceful — 마이그레이션 미적용 시 error_code='migrations_pending'.
    // 그래도 fetch 자체 실패 (API 미기동 등) 시 카드 숨김 — 안전 fallback.
    getStatsTrend('7d', 'search', 'all').catch<TrendResponse | null>(() => null),
    getStatsTrend('7d', 'vision').catch<TrendResponse | null>(() => null),
  ]);

  return (
    <main className="flex-1">
      <HeroSection />
      <HomeGrid
        stats={stats}
        recentDocuments={documents.items}
        searchTrend={searchTrend}
        visionTrend={visionTrend}
      />
    </main>
  );
}
