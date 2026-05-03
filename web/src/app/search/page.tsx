import Link from 'next/link';
import { redirect } from 'next/navigation';
import { Search as SearchIcon } from 'lucide-react';
import { getStats, searchDocuments } from '@/lib/api';
import { SearchSubheader } from '@/components/jet-rag/search-subheader';
import { FilterSidebar } from '@/components/jet-rag/filter-sidebar';
import { ResultCard } from '@/components/jet-rag/result-card';
import { Badge } from '@/components/ui/badge';

interface SearchPageProps {
  searchParams: Promise<{
    q?: string;
    debug?: string;
    doc_id?: string;
    mode?: string;
  }>;
}

type ValidMode = 'hybrid' | 'dense' | 'sparse';

function parseMode(raw: string | undefined): ValidMode {
  if (raw === 'dense' || raw === 'sparse') return raw;
  return 'hybrid';
}

export default async function SearchPage({ searchParams }: SearchPageProps) {
  const {
    q,
    debug: debugParam,
    doc_id: docIdParam,
    mode: modeParam,
  } = await searchParams;
  const query = (q ?? '').trim();
  const debug = debugParam === '1';
  const docId = docIdParam?.trim() || null;
  const mode = parseMode(modeParam);

  if (!query) {
    redirect('/');
  }

  const [response, stats] = await Promise.all([
    searchDocuments(query, 10, 0, docId, mode),
    getStats().catch(() => null),
  ]);

  return (
    <main className="flex-1">
      <SearchSubheader
        initialQuery={query}
        total={response.total}
        tookMs={response.took_ms}
        queryParsed={response.query_parsed}
        debug={debug}
        docId={docId}
        mode={mode}
      />
      <div className="container mx-auto px-4 py-6 md:px-6">
        <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
          <FilterSidebar />
          <section>
            {response.items.length === 0 ? (
              <NoResults query={query} popularTags={stats?.popular_tags ?? []} />
            ) : (
              <div className="space-y-4">
                {response.items.map((hit) => (
                  <ResultCard key={hit.doc_id} hit={hit} debug={debug} />
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}

function NoResults({
  query,
  popularTags,
}: {
  query: string;
  popularTags: Array<{ tag: string; count: number }>;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-dashed border-border bg-muted/20 px-6 py-16 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-muted">
        <SearchIcon className="h-6 w-6 text-muted-foreground" />
      </div>
      <p className="text-base font-medium text-foreground">
        &lsquo;{query}&rsquo; 에 대한 결과가 없어요
      </p>
      <p className="text-sm text-muted-foreground">
        다른 키워드를 시도하거나 아래 인기 태그를 눌러 검색해 보세요.
      </p>
      {popularTags.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
          {popularTags.slice(0, 8).map((t) => (
            <Link key={t.tag} href={`/search?q=${encodeURIComponent(t.tag)}`}>
              <Badge variant="secondary" className="cursor-pointer hover:bg-secondary/80">
                #{t.tag}
              </Badge>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
