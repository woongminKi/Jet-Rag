import Link from 'next/link';
import { redirect } from 'next/navigation';
import { Search as SearchIcon, Sparkles } from 'lucide-react';
import { getAnswer } from '@/lib/api';
import { Badge } from '@/components/ui/badge';

interface AskPageProps {
  searchParams: Promise<{
    q?: string;
    doc_id?: string;
    top_k?: string;
  }>;
}

const DEFAULT_TOP_K = 5;
const MAX_TOP_K = 10;

function parseTopK(raw: string | undefined): number {
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 1) return DEFAULT_TOP_K;
  return Math.min(MAX_TOP_K, Math.floor(n));
}

/**
 * W25 D12 — `/ask` LLM RAG 답변 PoC.
 *
 * - Server Component (RSC) — 질문 도착 시 백엔드 `/answer` 1회 호출 (Gemini 2.5 Flash)
 * - faithfulness: 답변에 인라인 [N] 으로 sources[] 인용 명시 (백엔드 prompt 강제)
 * - 검색 0건 → has_search_results=false / answer="제공된 자료에서 해당 정보를 찾지 못했습니다."
 * - PoC v1 — 인용 [N] 클릭 인터랙션 / streaming 은 v2
 *
 * AGENTS.md §1 패턴 — server fetch 첫 SSR / interactivity 필요 없으므로 'use client' 없음.
 */
export default async function AskPage({ searchParams }: AskPageProps) {
  const { q, doc_id: docIdParam, top_k: topKParam } = await searchParams;
  const query = (q ?? '').trim();
  const docId = docIdParam?.trim() || null;
  const topK = parseTopK(topKParam);

  if (!query) {
    redirect('/');
  }

  const response = await getAnswer(query, topK, docId);

  return (
    <main className="flex-1">
      <div className="border-b border-border bg-card/95 backdrop-blur">
        <div className="container mx-auto flex flex-col gap-2 px-4 py-4 md:px-6 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-primary" />
            <h1 className="text-base font-semibold text-foreground md:text-lg">
              {query}
            </h1>
          </div>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span>{response.model}</span>
            <span>·</span>
            <span>{response.took_ms}ms</span>
            <span>·</span>
            <Link
              href={`/search?q=${encodeURIComponent(query)}${docId ? `&doc_id=${docId}` : ''}`}
              className="inline-flex items-center gap-1 text-primary hover:underline"
            >
              <SearchIcon className="h-3.5 w-3.5" />
              검색 결과
            </Link>
          </div>
        </div>
      </div>

      <div className="container mx-auto px-4 py-6 md:px-6">
        <section className="mx-auto max-w-3xl space-y-6">
          <article className="rounded-lg border border-border bg-card px-5 py-5 shadow-sm">
            <p className="whitespace-pre-line text-[15px] leading-relaxed text-foreground">
              {response.answer}
            </p>
            {!response.has_search_results && (
              <p className="mt-3 text-xs text-muted-foreground">
                관련 자료를 찾지 못했어요. 다른 키워드로{' '}
                <Link
                  href={`/search?q=${encodeURIComponent(query)}`}
                  className="text-primary hover:underline"
                >
                  검색
                </Link>
                해 보세요.
              </p>
            )}
          </article>

          {response.sources.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-sm font-medium text-muted-foreground">출처 ({response.sources.length})</h2>
              <ol className="space-y-3">
                {response.sources.map((src, i) => (
                  <SourceCard key={src.chunk_id} index={i + 1} source={src} />
                ))}
              </ol>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

function SourceCard({
  index,
  source,
}: {
  index: number;
  source: import('@/lib/api').AnswerSource;
}) {
  const docTitle = source.doc_title || '(제목 없음)';
  const docHref = `/doc/${source.doc_id}`;
  return (
    <li className="rounded-md border border-border bg-card px-4 py-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <Badge variant="outline" className="font-mono text-[11px]">
          [{index}]
        </Badge>
        <Link
          href={docHref}
          className="text-sm font-medium text-foreground hover:underline"
        >
          {docTitle}
        </Link>
        {/* page 0 은 metadata 미상 의미 — 표시 회피 (W25 D12 PoC 가드). */}
        {source.page !== null && source.page > 0 && (
          <span className="text-xs text-muted-foreground">p.{source.page}</span>
        )}
        {source.section_title && (
          <span className="truncate text-xs text-muted-foreground">
            · {source.section_title}
          </span>
        )}
        <span className="ml-auto text-[11px] font-mono text-muted-foreground/70">
          chunk #{source.chunk_idx}
        </span>
      </div>
      <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-muted-foreground">
        {source.snippet}
      </p>
    </li>
  );
}
