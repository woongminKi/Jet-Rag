import Link from 'next/link';
import { redirect } from 'next/navigation';
import { ArrowRight, Search as SearchIcon, Sparkles } from 'lucide-react';
import { getStats, searchDocuments } from '@/lib/api';
import { SearchSubheader } from '@/components/jet-rag/search-subheader';
import { FilterSidebar } from '@/components/jet-rag/filter-sidebar';
import { ResultCard } from '@/components/jet-rag/result-card';
import { SearchPrecisionCard } from '@/components/jet-rag/search-precision-card';
import { Badge } from '@/components/ui/badge';
import { buildDocsUrl } from '@/lib/docs-filter';

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
            {/* W25 D14 — 검색 적합도 자동 측정 (mount 시 캐시 → 미스 시 LLM judge ~5초). */}
            {response.items.length > 0 && (
              <SearchPrecisionCard
                query={query}
                docId={docId}
                hits={response.items}
              />
            )}

            {/* W25 D14 — 검색 결과 위 prominent AI 답변 진입 카드.
                sub-header 의 작은 버튼만으로 진입점이 약하다는 사용자 피드백 반영. */}
            <Link
              href={`/ask?q=${encodeURIComponent(query)}${docId ? `&doc_id=${docId}` : ''}`}
              className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-primary/30 bg-primary/5 px-4 py-3 transition-colors hover:bg-primary/10"
            >
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/15">
                  <Sparkles className="h-5 w-5 text-primary" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-foreground">
                    AI 답변 보기
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Gemini 가 검색 결과를 정리해 답변 + 출처 인용 + RAGAS 정량 평가
                  </p>
                </div>
              </div>
              <ArrowRight className="h-4 w-4 text-primary" aria-hidden />
            </Link>

            {response.items.length === 0 ? (
              <NoResults
                query={query}
                popularTags={stats?.popular_tags ?? []}
                docId={docId}
              />
            ) : (
              <div className="space-y-4">
                {response.items.map((hit) => (
                  <ResultCard
                    key={hit.doc_id}
                    hit={hit}
                    debug={debug}
                    query={query}
                  />
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
  docId,
}: {
  query: string;
  popularTags: Array<{ tag: string; count: number }>;
  docId: string | null;
}) {
  // W14 Day 4 (한계 #68) — doc 스코프 검색에서 0건 시 전체 검색 fallback 안내
  const isDocScope = docId !== null;
  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-dashed border-border bg-muted/20 px-6 py-16 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-muted">
        <SearchIcon className="h-6 w-6 text-muted-foreground" />
      </div>
      <p className="text-base font-medium text-foreground">
        {isDocScope
          ? `이 문서 안에서 '${query}' 결과가 없어요`
          : `'${query}' 에 대한 결과가 없어요`}
      </p>
      <p className="text-sm text-muted-foreground">
        {isDocScope
          ? '문서 스코프를 해제하고 전체에서 다시 찾아보세요.'
          : '다른 키워드를 시도하거나 아래 인기 태그를 눌러 검색해 보세요.'}
      </p>
      {isDocScope && (
        <Link
          href={`/search?q=${encodeURIComponent(query)}`}
          className="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          전체 검색으로 보기
        </Link>
      )}
      {!isDocScope && popularTags.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
          {popularTags.slice(0, 8).map((t) => (
            <Link
              key={t.tag}
              href={buildDocsUrl({ tag: t.tag })}
              aria-label={`${t.tag} 태그로 좁혀 문서 보기`}
              title="이 태그로 좁히기"
            >
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
