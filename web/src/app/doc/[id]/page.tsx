'use client';

import { useEffect, useState } from 'react';
import { use } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  Eye,
  Info,
  Loader2,
  Search,
  Shield,
  Sparkles,
  Tag,
  XCircle,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  ApiError,
  getDocument,
  reingestMissingVision,
  searchDocuments,
  type DocumentDetailResponse,
  type MatchedChunk,
  type SearchResponse,
} from '@/lib/api';
import { VisionPageCapExceededCard } from '@/components/jet-rag/cards/vision-page-cap-exceeded-card';
import { docTypeLabel } from '@/lib/doc-type-label';
import { buildDocsUrl } from '@/lib/docs-filter';
import { formatBytes } from '@/lib/format';
import { Highlighted } from '@/components/jet-rag/highlighted';

const POLL_INTERVAL_MS = 1500;
const MAX_POLL_DURATION_MS = 5 * 60 * 1000;
// W25 D5 — `?q=` 의 hits cap. doc 스코프 검색은 단일 doc hit 1개만 매칭하므로 작은 값으로 충분.
// matched_chunks 는 백엔드 cap 200 (doc 스코프 시 우회) 으로 처리되며 본 limit 와 무관.
// 백엔드 검증: Query(10, ge=1, le=50) — 50 초과 시 422.
const DOC_SCOPE_FETCH_LIMIT = 10;

interface PageProps {
  params: Promise<{ id: string }>;
}

export default function DocPage({ params }: PageProps) {
  const { id } = use(params);
  return <DocDetail docId={id} />;
}

function DocDetail({ docId }: { docId: string }) {
  const searchParams = useSearchParams();
  const justUploaded = searchParams.get('uploaded') === '1';
  const justDuplicated = searchParams.get('duplicated') === '1';
  // W25 D5 — `?q=...` 매칭 청크 표시 (사용자가 검색 결과의 `+N개 더 매칭` 클릭 진입 경로).
  const q = (searchParams.get('q') ?? '').trim();

  const [doc, setDoc] = useState<DocumentDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  // 매칭 청크 — null 은 fetch 미완료 / 미요청, 빈 배열은 fetch 완료 + 0 건.
  const [matchedChunks, setMatchedChunks] = useState<MatchedChunk[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const start = Date.now();

    const tick = async () => {
      try {
        const data = await getDocument(docId);
        if (cancelled) return;
        setDoc(data);
        setError(null);
        const status = data.latest_job?.status;
        const stillRunning = status === 'queued' || status === 'running';
        const expired = Date.now() - start > MAX_POLL_DURATION_MS;
        if (stillRunning && !expired) {
          timer = setTimeout(tick, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
          return;
        }
        const message =
          err instanceof ApiError ? err.detail : '문서를 불러오지 못했습니다.';
        setError(message);
      }
    };

    tick();

    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [docId]);

  // W25 D5 — `?q=` 가 있을 때만 doc 스코프 검색 (매칭 청크 모두 가져오기).
  // 백엔드: doc_id 스코프 시 `_MAX_MATCHED_CHUNKS_PER_DOC` cap 우회 → 응답에 모든 unique 청크 본문 포함.
  // 정렬: rrf_score 내림차순 (백엔드가 정렬해서 줌).
  // graceful: 실패 시 빈 배열 → MatchedChunksSection 의 0건 안내.
  // React 19 패턴 (AGENTS.md 패턴 2): useEffect 안 동기 setState 금지 →
  //   `.then/.catch` 콜백 안 setState 는 비동기라 OK. q 가 falsy 면 컴포넌트 자체가
  //   conditional render 돼서 stale state 노출 X — useEffect 안 reset 동기 setState 불필요.
  useEffect(() => {
    if (!q) return;
    let cancelled = false;
    searchDocuments(q, DOC_SCOPE_FETCH_LIMIT, 0, docId, 'hybrid')
      .then((resp: SearchResponse) => {
        if (cancelled) return;
        // doc_id 스코프 시 응답 items 는 0 또는 1건. items[0].matched_chunks 가 모든 매칭.
        const hit = resp.items[0];
        setMatchedChunks(hit ? hit.matched_chunks : []);
      })
      .catch(() => {
        if (cancelled) return;
        setMatchedChunks([]);
      });
    return () => {
      cancelled = true;
    };
  }, [docId, q]);

  if (notFound) {
    return (
      <main className="container mx-auto flex-1 px-4 py-16 md:px-6">
        <Card className="mx-auto max-w-md p-8 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-muted">
            <AlertCircle className="h-6 w-6 text-muted-foreground" />
          </div>
          <h1 className="text-lg font-semibold text-foreground">문서를 찾을 수 없어요</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            삭제되었거나 잘못된 주소일 수 있습니다.
          </p>
          <div className="mt-6 flex justify-center gap-2">
            <Button asChild variant="outline">
              <Link href="/">홈으로</Link>
            </Button>
            <Button asChild>
              <Link href="/ingest">새 문서 업로드</Link>
            </Button>
          </div>
        </Card>
      </main>
    );
  }

  return (
    <main className="container mx-auto flex-1 px-4 py-8 md:px-6 md:py-12">
      <div className="mx-auto max-w-3xl space-y-6">
        {justDuplicated && (
          <DuplicatedBanner />
        )}
        {justUploaded && doc?.latest_job?.status === 'completed' && (
          <UploadedBanner />
        )}

        <HeroSearch docId={docId} />

        {doc ? (
          <>
            <DocSummaryHeader doc={doc} />
            <DocStatusSection doc={doc} />
            {q && (
              <MatchedChunksSection
                query={q}
                chunks={matchedChunks}
                docId={docId}
              />
            )}
            {doc.summary && <SummarySection summary={doc.summary} />}
            {doc.tags.length > 0 && <TagsSection tags={doc.tags} />}
            <FlagsSection doc={doc} />
          </>
        ) : error ? (
          <ErrorCard message={error} />
        ) : (
          <DocSkeleton />
        )}
      </div>
    </main>
  );
}

// =====================================================
// Hero 검색 (W12 Day 1 — docId 자동 주입으로 단일 문서 스코프 자연어 QA, US-08)
// =====================================================
function HeroSearch({ docId }: { docId: string }) {
  const router = useRouter();
  const [q, setQ] = useState('');
  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const trimmed = q.trim();
    if (!trimmed) return;
    // doc 페이지에서 검색은 그 문서 내 자연어 QA — doc_id 자동 주입
    const params = new URLSearchParams({ q: trimmed, doc_id: docId });
    router.push(`/search?${params.toString()}`);
  };
  return (
    <form onSubmit={handleSubmit} className="relative">
      <Search className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground" />
      <Input
        type="search"
        placeholder="이 문서 내 자연어 검색"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        className="h-12 rounded-xl border-2 border-border bg-card pl-12 pr-24 text-sm shadow-sm focus:border-primary"
      />
      <Button
        type="submit"
        size="sm"
        className="absolute right-2 top-1/2 h-9 -translate-y-1/2 px-4"
      >
        검색
      </Button>
    </form>
  );
}

// =====================================================
// 헤더 — 제목 + 메타
// =====================================================
function DocSummaryHeader({ doc }: { doc: DocumentDetailResponse }) {
  const createdAt = new Date(doc.created_at).toLocaleDateString('ko-KR', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
  return (
    <header className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{docTypeLabel(doc.doc_type)}</Badge>
        <Badge variant="outline" className="text-muted-foreground">
          {doc.source_channel}
        </Badge>
        {doc.flags?.scan === true && (
          <Badge variant="secondary" className="gap-1">
            <Eye className="h-3 w-3" />
            스캔본
          </Badge>
        )}
        {doc.flags?.failed === true && (
          <Badge variant="destructive" className="gap-1">
            <XCircle className="h-3 w-3" /> 실패
          </Badge>
        )}
      </div>
      <h1 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">
        {doc.title}
      </h1>
      <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
        <span>{formatBytes(doc.size_bytes)}</span>
        <span>·</span>
        <span>{createdAt}</span>
        <span>·</span>
        <span>청크 {doc.chunks_count}개</span>
        {doc.received_ms != null && (
          <>
            <span>·</span>
            <span>수신 {doc.received_ms}ms</span>
          </>
        )}
      </div>
      {doc.source_url && (
        <a
          href={doc.source_url}
          target="_blank"
          rel="noreferrer noopener"
          className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          원본 페이지 열기
        </a>
      )}
    </header>
  );
}

// =====================================================
// 상태 (인제스트 진행 / 완료 / 실패)
// =====================================================
function DocStatusSection({ doc }: { doc: DocumentDetailResponse }) {
  const job = doc.latest_job;
  if (!job) return null;
  if (job.status === 'completed') {
    return (
      <Card className="flex items-center gap-3 border-success/30 bg-success/5 p-4">
        <CheckCircle2 className="h-5 w-5 text-success" />
        <p className="text-sm text-foreground">
          처리 완료 — 검색에서 이 문서를 찾을 수 있어요.
        </p>
      </Card>
    );
  }
  if (job.status === 'failed') {
    return (
      <Card className="space-y-2 border-destructive/30 bg-destructive/5 p-4">
        <div className="flex items-center gap-2">
          <XCircle className="h-5 w-5 text-destructive" />
          <p className="text-sm font-medium text-destructive">
            처리 실패 ({job.current_stage ?? '?'} 단계)
          </p>
        </div>
        {job.error_msg && (
          <p className="break-words rounded-md border border-destructive/20 bg-card px-3 py-2 text-xs text-destructive">
            {job.error_msg}
          </p>
        )}
      </Card>
    );
  }
  // queued / running
  return (
    <Card className="flex items-center gap-3 border-primary/30 bg-primary/5 p-4">
      <Loader2 className="h-5 w-5 animate-spin text-primary" />
      <div className="text-sm text-foreground">
        <p className="font-medium">처리 중</p>
        <p className="text-muted-foreground">
          현재 단계: {job.current_stage ?? '대기 중'}
        </p>
      </div>
    </Card>
  );
}

// =====================================================
// W25 D5 — 매칭 청크 섹션 (`?q=` 진입 시).
// 사용자 검색 결과 카드의 `+N개 더 매칭 (이 문서에서 모두 보기 →)` 진입 경로.
// 백엔드가 doc_id 스코프 시 모든 unique 청크 본문 + score 내림차순으로 응답 → 그대로 표시.
// =====================================================
function MatchedChunksSection({
  query,
  chunks,
  docId,
}: {
  query: string;
  chunks: MatchedChunk[] | null;
  docId: string;
}) {
  // fetch 미완료 — 작은 스켈레톤. 검색 자체가 ~1초 안이라 zero-flash 회피 우선.
  if (chunks === null) {
    return (
      <Card className="p-5">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
          <Search className="h-4 w-4 text-primary" />
          매칭 청크 불러오는 중…
        </div>
        <div className="space-y-2">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      </Card>
    );
  }

  if (chunks.length === 0) {
    // 사용자가 q 와 함께 진입했지만 매칭 0 — fallback 안내 + 검색 페이지 가이드.
    return (
      <Card className="space-y-2 border-warning/30 bg-warning/5 p-5">
        <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Search className="h-4 w-4 text-warning" />
          이 문서에 ‘{query}’ 매칭이 없어요
        </div>
        <p className="text-xs text-muted-foreground">
          전체 검색에서 다시 찾아보세요.
        </p>
        <div className="pt-1">
          <Button asChild size="sm" variant="outline">
            <Link href={`/search?q=${encodeURIComponent(query)}`}>
              전체 검색으로 보기
            </Link>
          </Button>
        </div>
      </Card>
    );
  }

  // W25 D6 fix — raw RRF score (`rrf 0.0161`) → 상대 매칭 강도 % 표시.
  // 정규화: 청크 단위 max 기준. 백엔드가 rrf_score 내림차순으로 정렬해서 주므로
  //   chunks[0].rrf_score 가 max 보장 → 별도 reduce 불필요.
  // 멘탈 모델: 검색 결과 카드의 `매칭 강도 100%` (doc 단위) vs 본 섹션의 `매칭 강도 99%` (chunk 단위) —
  //   라벨 동일, 단위 차이는 헤더 ⓘ 툴팁으로 안내.
  const maxRrf =
    typeof chunks[0]?.rrf_score === 'number' && chunks[0].rrf_score > 0
      ? chunks[0].rrf_score
      : null;

  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
        <Search className="h-4 w-4 text-primary" />
        ‘{query}’ 와 관련된 청크 {chunks.length}개
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className="ml-1 inline-flex cursor-help items-center gap-0.5 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Badge variant="outline" className="h-5 gap-0.5 px-1.5 text-[10px] font-normal">
                매칭 강도순
                <Info className="h-2.5 w-2.5" aria-hidden />
              </Badge>
              <span className="sr-only">매칭 강도순 안내</span>
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            이 문서 안 청크들 중 가장 강한 매칭 대비 상대 강도예요. 정답 신뢰도와는 다릅니다.
          </TooltipContent>
        </Tooltip>
      </div>
      <ul className="space-y-2">
        {chunks.map((chunk) => {
          // % 계산 — maxRrf 가 null 이거나 chunk score 가 없으면 표시 생략 (graceful).
          const matchPct =
            maxRrf !== null && typeof chunk.rrf_score === 'number'
              ? Math.max(1, Math.round((chunk.rrf_score / maxRrf) * 100))
              : null;
          return (
            <li
              key={chunk.chunk_id}
              className="rounded-md border border-border bg-muted/30 p-3 text-sm"
            >
              <div className="mb-1 flex items-center justify-between gap-2 text-[10px] uppercase tracking-wide text-muted-foreground">
                <div className="flex min-w-0 items-center gap-2">
                  {chunk.page !== null && <span>p.{chunk.page}</span>}
                  {chunk.section_title && (
                    <>
                      <span className="text-border">·</span>
                      <span className="truncate">{chunk.section_title}</span>
                    </>
                  )}
                  <span className="text-border">·</span>
                  <span>idx {chunk.chunk_idx}</span>
                </div>
                {matchPct !== null && (
                  <span className="shrink-0 normal-case tracking-normal tabular-nums text-muted-foreground/80">
                    매칭 강도 {matchPct}%
                  </span>
                )}
              </div>
              <p className="leading-relaxed text-foreground/90">
                <Highlighted text={chunk.text} ranges={chunk.highlight} />
              </p>
            </li>
          );
        })}
      </ul>
      {/* doc 스코프에서 0 건이 아닐 때만 — 보조 navigation 으로 전체 검색 안내 */}
      <div className="mt-3 text-right">
        <Link
          href={`/search?q=${encodeURIComponent(query)}&doc_id=${encodeURIComponent(docId)}`}
          className="text-xs text-muted-foreground hover:text-foreground hover:underline"
        >
          이 문서 검색 페이지로 →
        </Link>
      </div>
    </Card>
  );
}

// =====================================================
// 요약·태그·플래그 섹션
// =====================================================
function SummarySection({ summary }: { summary: string }) {
  return (
    <Card className="p-5">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-foreground">
        <Sparkles className="h-4 w-4 text-primary" />
        요약
      </div>
      <p className="whitespace-pre-line text-sm leading-relaxed text-foreground/90">
        {summary}
      </p>
    </Card>
  );
}

function TagsSection({ tags }: { tags: string[] }) {
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
        <Tag className="h-4 w-4 text-primary" />
        태그
      </div>
      <div className="flex flex-wrap gap-2">
        {tags.map((t) => (
          <Link
            key={t}
            href={buildDocsUrl({ tag: t })}
            aria-label={`${t} 태그로 좁혀 문서 보기`}
            title="이 태그로 좁히기"
          >
            <Badge variant="secondary" className="cursor-pointer hover:bg-secondary/80">
              #{t}
            </Badge>
          </Link>
        ))}
      </div>
    </Card>
  );
}

function FlagsSection({ doc }: { doc: DocumentDetailResponse }) {
  const f = doc.flags || {};
  const items: { label: string; tone: 'warn' | 'info' }[] = [];
  if (f.has_pii === true) items.push({ label: '개인정보 포함', tone: 'warn' });
  if (f.has_watermark === true) {
    const hits = Array.isArray(f.watermark_hits) ? (f.watermark_hits as string[]) : [];
    items.push({
      label: hits.length > 0 ? `워터마크: ${hits.join(', ')}` : '워터마크 감지',
      tone: 'warn',
    });
  }
  if (f.third_party === true) items.push({ label: '제3자 대화 감지', tone: 'warn' });
  if (f.scan === true) items.push({ label: '스캔본 (Vision OCR 사용)', tone: 'info' });

  // S0 D4 — vision_budget_exceeded 별도 카드로 분리 (재처리 버튼 포함).
  const budgetExceeded = f.vision_budget_exceeded === true;
  // S2 D3 — vision_page_cap_exceeded 별도 카드 (cost cap 과 직교, 동시 노출 가능).
  const pageCapExceeded = f.vision_page_cap_exceeded === true;

  if (items.length === 0 && !budgetExceeded && !pageCapExceeded) return null;
  return (
    <>
      {items.length > 0 && (
        <Card className="p-5">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
            <Shield className="h-4 w-4 text-warning" />
            주의사항
          </div>
          <ul className="space-y-2">
            {items.map((it, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-foreground/90">
                <Info
                  className={`mt-0.5 h-4 w-4 flex-shrink-0 ${
                    it.tone === 'warn' ? 'text-warning' : 'text-muted-foreground'
                  }`}
                />
                {it.label}
              </li>
            ))}
          </ul>
        </Card>
      )}
      {/* S2 D3 — 동시 노출 시 cost cap 카드를 위에. 사용자가 한도 정보를 위에서 아래로 읽음. */}
      {budgetExceeded && doc.doc_type === 'pdf' && (
        <VisionBudgetExceededCard doc={doc} />
      )}
      {pageCapExceeded && doc.doc_type === 'pdf' && (
        <VisionPageCapExceededCard doc={doc} />
      )}
    </>
  );
}

/** S0 D4 — vision 비용 cap 도달 시 안내 + 재처리 버튼.
 *  master plan §11.5 정합 — "약속 회피 + 사용자 통제권".
 *  - 사용자에게 어떤 한도(scope) 가 도달했는지 / 얼마나 사용했는지 표시
 *  - 재처리는 incremental vision reingest (chunks 보존 + 누락 페이지만)
 *  - 재처리 시점에도 cap 이 풀려있지 않으면 백엔드가 graceful skip (유저 surprise 0)
 */
function VisionBudgetExceededCard({ doc }: { doc: DocumentDetailResponse }) {
  const router = useRouter();
  const f = doc.flags || {};
  const budget = (f.vision_budget && typeof f.vision_budget === 'object'
    ? (f.vision_budget as Record<string, unknown>)
    : {}) as { scope?: string; used_usd?: number; cap_usd?: number; reason?: string };
  const scopeLabel =
    budget.scope === 'daily' ? '일일 한도' : '문서당 한도';
  const usedStr =
    typeof budget.used_usd === 'number' ? `$${budget.used_usd.toFixed(4)}` : '-';
  const capStr =
    typeof budget.cap_usd === 'number' ? `$${budget.cap_usd.toFixed(4)}` : '-';

  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const [retryQueued, setRetryQueued] = useState(false);

  const handleRetry = async () => {
    setRetrying(true);
    setRetryError(null);
    try {
      await reingestMissingVision(doc.id);
      setRetryQueued(true);
      // 인제스트 진행 상태 폴링은 기존 useEffect 의 latest_job 폴링이 자동 picks up.
      // 즉시 새 status 를 가져오도록 router.refresh() — 같은 페이지에서 재폴링 시작.
      router.refresh();
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : '재처리 요청에 실패했습니다.';
      setRetryError(msg);
    } finally {
      setRetrying(false);
    }
  };

  return (
    <Card className="space-y-3 border-warning/40 bg-warning/5 p-5">
      <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
        <AlertCircle className="h-4 w-4 text-warning" />
        시각 보강 일부 생략 ({scopeLabel} 도달)
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        Vision 비용 한도에 도달해 표/그림 보강 일부가 생략되었어요. 검색은 정상
        동작하지만 다이어그램·이미지 정확도가 낮을 수 있어요. 한도가 풀리면
        ‘재처리’ 로 누락 페이지만 다시 처리할 수 있어요.
      </p>
      <div className="grid grid-cols-2 gap-2 rounded-md border border-warning/30 bg-card p-3 text-xs">
        <div>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
            누적 사용
          </div>
          <div className="mt-0.5 font-mono text-foreground">{usedStr}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
            한도
          </div>
          <div className="mt-0.5 font-mono text-foreground">{capStr}</div>
        </div>
      </div>
      {retryError && (
        <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {retryError}
        </p>
      )}
      {retryQueued && !retryError && (
        <p className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-xs text-primary">
          재처리가 시작되었어요. 완료까지 잠시 걸릴 수 있어요.
        </p>
      )}
      <div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleRetry}
          disabled={retrying || retryQueued}
          className="gap-1"
        >
          {retrying ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Sparkles className="h-3 w-3" />
          )}
          {retrying ? '재처리 요청 중...' : '재처리'}
        </Button>
      </div>
    </Card>
  );
}

// =====================================================
// 배너 (중복 / 업로드 완료) + 스켈레톤 + 에러
// =====================================================
function DuplicatedBanner() {
  return (
    <Card className="flex items-start gap-3 border-warning/40 bg-warning/10 p-4">
      <Info className="mt-0.5 h-4 w-4 flex-shrink-0 text-warning" />
      <p className="text-sm text-foreground">
        이미 같은 내용이 등록되어 있어요. 기존 문서로 이동했습니다.
      </p>
    </Card>
  );
}

function UploadedBanner() {
  return (
    <Card className="flex items-start gap-3 border-success/40 bg-success/10 p-4">
      <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-success" />
      <p className="text-sm text-foreground">
        업로드가 완료되어 이 문서로 이동했어요. 이제 검색에서 찾을 수 있습니다.
      </p>
    </Card>
  );
}

function ErrorCard({ message }: { message: string }) {
  return (
    <Card className="space-y-2 border-destructive/30 bg-destructive/5 p-4">
      <div className="flex items-center gap-2">
        <XCircle className="h-5 w-5 text-destructive" />
        <p className="text-sm font-medium text-destructive">
          문서를 불러오지 못했어요
        </p>
      </div>
      <p className="text-xs text-muted-foreground">{message}</p>
    </Card>
  );
}

function DocSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <div className="flex gap-2">
          <Skeleton className="h-5 w-12" />
          <Skeleton className="h-5 w-16" />
        </div>
        <Skeleton className="h-9 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
      </div>
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-24 w-full" />
    </div>
  );
}
