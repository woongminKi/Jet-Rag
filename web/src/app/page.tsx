import { getStats, listDocuments } from '@/lib/api';
import { HeroSection } from '@/components/jet-rag/hero-section';
import { HomeGrid } from '@/components/jet-rag/home-grid';

// W26 v3 — SLO/Trend 카드 4종 제거 따라 getStatsTrend fetch 도 제거.
// 복원 시 git history 참조.
export default async function HomePage() {
  const [stats, documents] = await Promise.all([
    getStats(),
    listDocuments(5),
  ]);

  return (
    <main className="flex-1">
      <HeroSection />
      <HomeGrid stats={stats} recentDocuments={documents.items} />
    </main>
  );
}
