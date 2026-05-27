'use client';

import { useState, useTransition } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowLeft, Bug, FileText, Search, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';

interface SearchSubheaderProps {
  initialQuery: string;
  total: number;
  tookMs: number;
  /** W7 Day 1 — 검색 경로 진단 (선택, backward compat). dense/sparse hits + fallback 표시. */
  queryParsed?: {
    has_dense: boolean;
    has_sparse: boolean;
    dense_hits: number;
    sparse_hits: number;
    fused: number;
    fallback_reason?: string | null;
  };
  /** W7 Day 4 — debug 모드 ON 여부. 토글 시 ?debug=1 URL 갱신. */
  debug?: boolean;
  /** W12 Day 1 — 단일 문서 스코프 (US-08). 지정 시 "이 문서 내 검색" 라벨 + 전체 검색 링크 노출. */
  docId?: string | null;
  /** W14 Day 1 — ablation mode (hybrid | dense | sparse). KPI '하이브리드 +5pp' 비교 인프라. */
  mode?: 'hybrid' | 'dense' | 'sparse';
}

/** W26 Toss-언어 모바일 리팩토링.
 *
 *  Mobile (<md):
 *    row 1: [← back] [검색 input ...........................]
 *    row 2: [hybrid|dense|sparse] [✨ AI 답변] [이 문서 내 검색?] [fallback?]
 *    (debug · query_parsed dense/sparse 진단 badge 는 mobile 숨김 — 운영 도구는 desktop only)
 *
 *  Desktop (≥md): 단일 row 로 모두 노출 (기존 동작 유지).
 *
 *  가로 스크롤 완전 제거 — row 1 input flex-1 min-w-0, row 2 flex-wrap.
 */
export function SearchSubheader({
  initialQuery,
  total,
  tookMs,
  queryParsed,
  debug = false,
  docId = null,
  mode = 'hybrid',
}: SearchSubheaderProps) {
  const router = useRouter();
  const [query, setQuery] = useState(initialQuery);
  const [isPending, startTransition] = useTransition();

  const buildUrl = (overrides: { q?: string; mode?: string } = {}) => {
    const next = new URLSearchParams();
    next.set('q', overrides.q ?? initialQuery);
    if (debug) next.set('debug', '1');
    if (docId) next.set('doc_id', docId);
    const m = overrides.mode ?? mode;
    if (m && m !== 'hybrid') next.set('mode', m);
    return `/search?${next.toString()}`;
  };

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;
    router.push(buildUrl({ q: trimmed }));
  };

  const toggleDebug = () => {
    const next = new URLSearchParams();
    next.set('q', initialQuery);
    if (!debug) next.set('debug', '1');
    if (docId) next.set('doc_id', docId);
    if (mode !== 'hybrid') next.set('mode', mode);
    router.push(`/search?${next.toString()}`);
  };

  const switchMode = (next: 'hybrid' | 'dense' | 'sparse') => {
    if (next === mode) return;
    startTransition(() => {
      router.push(buildUrl({ mode: next }));
    });
  };

  return (
    <div className="sticky top-16 z-40 border-b border-border bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
      <div className="container mx-auto flex flex-col gap-2 px-4 py-3 md:flex-row md:items-center md:gap-3 md:py-3 md:px-6">
        {/* row 1 — back + 검색 input (mobile + desktop 공통) */}
        <div className="flex items-center gap-2 md:contents">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => router.push('/')}
            aria-label="홈으로"
            className="h-10 w-10 shrink-0 md:h-9 md:w-9"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>

          <form onSubmit={handleSubmit} className="relative min-w-0 flex-1 md:max-w-2xl">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="search"
              name="q"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="검색어를 입력하세요"
              className="h-11 w-full pl-9 text-base md:h-10 md:text-sm"
            />
          </form>
        </div>

        {/* row 2 (mobile 전용 stack) / desktop 에선 row 1 옆에 inline */}
        <div className="flex flex-wrap items-center gap-2 md:flex-nowrap md:gap-2">
          {/* W14 Day 1·4 — ablation mode 3-state 토글
              모바일에서도 노출하되 h-9 로 터치 타깃 확보. */}
          <div
            className="inline-flex h-9 items-center rounded-md border border-border bg-card md:h-8"
            title="ablation 모드 — hybrid (기본) / dense / sparse 비교"
            aria-busy={isPending}
          >
            {(['hybrid', 'dense', 'sparse'] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => switchMode(m)}
                disabled={isPending}
                className={
                  mode === m
                    ? 'h-full rounded-sm bg-primary px-2.5 text-xs font-semibold text-primary-foreground transition-opacity disabled:opacity-50 md:px-2 md:text-[11px]'
                    : 'h-full px-2.5 text-xs text-muted-foreground transition-opacity hover:text-foreground disabled:opacity-50 md:px-2 md:text-[11px]'
                }
                aria-pressed={mode === m}
              >
                {m}
              </button>
            ))}
          </div>

          {/* W25 D14 — AI 답변 진입점 (모바일에서도 노출). */}
          <Button
            asChild
            size="sm"
            variant="outline"
            className="h-9 gap-1.5 whitespace-nowrap px-3 text-sm md:h-8 md:px-2.5 md:text-xs"
            title="현재 검색어로 AI 답변 보기 (Gemini + 출처 인용)"
          >
            <Link
              href={`/ask?q=${encodeURIComponent(initialQuery)}${docId ? `&doc_id=${docId}` : ''}`}
            >
              <Sparkles className="h-4 w-4 text-primary md:h-3.5 md:w-3.5" />
              <span>AI 답변</span>
            </Link>
          </Button>

          {/* doc 스코프 라벨 — 노출 조건 발생 시만. */}
          {docId && (
            <Link
              href={`/search?q=${encodeURIComponent(initialQuery)}${debug ? '&debug=1' : ''}`}
              title="이 문서 스코프 해제 — 전체 검색으로 전환"
              className="inline-flex"
            >
              <Badge
                variant="outline"
                className="h-6 cursor-pointer gap-1 whitespace-nowrap px-2 text-[11px] hover:bg-muted"
              >
                <FileText className="h-3 w-3" />이 문서 내 검색
              </Badge>
            </Link>
          )}

          {/* fallback badge — 사용자 알람 성격이라 모바일도 유지. */}
          {queryParsed?.fallback_reason && (
            <Badge
              variant="destructive"
              className="h-6 whitespace-nowrap px-2 text-[11px]"
              title={`fallback: ${queryParsed.fallback_reason}`}
            >
              {queryParsed.fallback_reason}
            </Badge>
          )}

          {/* 결과 수 / 응답 시간 — 모바일은 좌측 정렬, desktop 은 우측 자동 push. */}
          <Badge
            variant="secondary"
            className="ml-auto hidden h-6 whitespace-nowrap px-2 text-[11px] sm:inline-flex"
          >
            {total}개 결과 · {(tookMs / 1000).toFixed(2)}초
          </Badge>

          {/* dense/sparse 진단 badge — 좁은 폭 보호로 desktop 만. */}
          {queryParsed && (
            <div
              className="hidden items-center gap-1 md:inline-flex"
              title={`dense ${queryParsed.dense_hits} · sparse ${queryParsed.sparse_hits} → fused ${queryParsed.fused}`}
            >
              <Badge
                variant={queryParsed.has_dense ? 'outline' : 'destructive'}
                className="h-5 px-1.5 text-[10px]"
              >
                dense {queryParsed.dense_hits}
              </Badge>
              <Badge
                variant={queryParsed.has_sparse ? 'outline' : 'secondary'}
                className="h-5 px-1.5 text-[10px]"
              >
                sparse {queryParsed.sparse_hits}
              </Badge>
            </div>
          )}

          {/* debug 토글 — 운영 도구라 mobile 숨김. */}
          <Button
            type="button"
            variant={debug ? 'default' : 'ghost'}
            size="icon"
            onClick={toggleDebug}
            aria-label={debug ? '디버그 끄기' : '디버그 켜기'}
            title={
              debug
                ? '디버그 모드 ON — 클릭 시 OFF'
                : '디버그 모드 OFF — chunk 메타·rrf·overlap 펼쳐 보기'
            }
            className="hidden h-9 w-9 md:inline-flex md:h-9 md:w-9"
          >
            <Bug className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* 모바일에서만 별도 row — 결과 수 안내 (sm:hidden). */}
      <div className="container mx-auto block px-4 pb-2 text-xs text-muted-foreground sm:hidden">
        {total}개 결과 · {(tookMs / 1000).toFixed(2)}초
      </div>
    </div>
  );
}
