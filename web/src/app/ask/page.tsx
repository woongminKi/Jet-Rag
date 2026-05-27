import Link from 'next/link';
import { Search as SearchIcon, Sparkles } from 'lucide-react';
import { getAnswer } from '@/lib/api';
import { AnswerView } from '@/components/jet-rag/answer-view';

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
 * W25 D14 — 답변 품질 가시화 (B+E+C): 신뢰도 배지 + [N] 클릭 highlight + 사용자 피드백.
 *
 * - Server Component (RSC) — 질문 도착 시 백엔드 `/answer` 1회 호출 (Gemini 2.5 Flash)
 * - AnswerView (client) — 인라인 [N] 클릭 / 신뢰도 휴리스틱 / 👍/👎 피드백
 * - 검색 0건 → has_search_results=false / answer="제공된 자료에서 해당 정보를 찾지 못했습니다."
 */
export default async function AskPage({ searchParams }: AskPageProps) {
  const { q, doc_id: docIdParam, top_k: topKParam } = await searchParams;
  const query = (q ?? '').trim();
  const docId = docIdParam?.trim() || null;
  const topK = parseTopK(topKParam);

  // W25 D14 — 빈 query 시 홈 redirect 대신 안내 페이지 (사용자가 /ask 직접 진입 시 안내 부족 fix).
  if (!query) {
    return (
      <main className="flex-1">
        <div className="container mx-auto max-w-2xl px-4 py-16 text-center md:px-6">
          <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-full bg-primary/15">
            <Sparkles className="h-7 w-7 text-primary" />
          </div>
          <h1 className="text-2xl font-bold text-foreground">AI 답변</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            자연어 질문을 입력하면 Gemini 가 적재된 문서를 근거로 답변하고, 출처와 정량 평가
            (Faithfulness · Relevancy · Context Precision) 를 함께 보여줍니다.
          </p>
          <div className="mt-8 space-y-3">
            <Link
              href="/"
              className="inline-flex h-10 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              <SearchIcon className="h-4 w-4" />홈에서 질문 입력
            </Link>
            <p className="text-xs text-muted-foreground">
              또는 검색 결과 페이지의 &lsquo;AI 답변 보기&rsquo; 카드 클릭
            </p>
          </div>
        </div>
      </main>
    );
  }

  const response = await getAnswer(query, topK, docId);

  return (
    <main className="flex-1">
      <div className="border-b border-border bg-card/95 shadow-sm backdrop-blur">
        <div className="container mx-auto flex flex-col gap-2 px-4 py-4 md:flex-row md:items-center md:justify-between md:px-6">
          <div className="flex min-w-0 items-center gap-2">
            <Sparkles className="h-5 w-5 shrink-0 text-primary" />
            <h1 className="min-w-0 break-words text-base font-semibold text-foreground md:text-lg">
              {query}
            </h1>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span className="break-all">{response.model}</span>
            <span>·</span>
            <span className="tabular-nums">{response.took_ms}ms</span>
            <span>·</span>
            <Link
              href={`/search?q=${encodeURIComponent(query)}${docId ? `&doc_id=${docId}` : ''}`}
              className="inline-flex items-center gap-1 whitespace-nowrap text-primary hover:underline"
            >
              <SearchIcon className="h-3.5 w-3.5" />
              검색 결과
            </Link>
          </div>
        </div>
      </div>

      <div className="container mx-auto px-4 py-6 md:px-6">
        <AnswerView query={query} response={response} docId={docId} />
      </div>
    </main>
  );
}
