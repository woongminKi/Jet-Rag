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
  // W19 Day 1 한계 #79 — mode 토글 빠른 클릭 race 방지.
  // startTransition 으로 navigation 감싸 React 가 자동으로 stale transition 차단.
  // isPending 으로 진행 중 button disabled (사용자 시각 피드백).
  const [isPending, startTransition] = useTransition();

  // W14 Day 1 — URL 빌더 (모든 상태 보존). mode 만 새 값으로 override 가능.
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
    <div className="sticky top-16 z-40 border-b border-border bg-card/95 backdrop-blur">
      <div className="container mx-auto flex items-center gap-3 px-4 py-3 md:px-6">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => router.push('/')}
          aria-label="홈으로"
        >
          <ArrowLeft className="h-4 w-4" />
        </Button>

        <form onSubmit={handleSubmit} className="relative flex-1 max-w-2xl">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            name="q"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="검색어를 입력하세요"
            className="h-10 pl-9"
          />
        </form>

        {/* W12 Day 1 — doc 스코프 라벨 (US-08). 클릭 시 전체 검색으로 전환. */}
        {docId && (
          <Link
            href={`/search?q=${encodeURIComponent(initialQuery)}${debug ? '&debug=1' : ''}`}
            title="이 문서 스코프 해제 — 전체 검색으로 전환"
            className="inline-flex"
          >
            <Badge
              variant="outline"
              className="h-5 cursor-pointer gap-1 whitespace-nowrap px-1.5 text-[10px] hover:bg-muted"
            >
              <FileText className="h-3 w-3" />이 문서 내 검색
            </Badge>
          </Link>
        )}
        <Badge variant="secondary" className="hidden whitespace-nowrap sm:inline-flex">
          {total}개 결과 · {(tookMs / 1000).toFixed(2)}초
        </Badge>

        {/* W25 D14 — AI 답변 진입점. 검색 결과 페이지 → /ask (LLM RAG + 신뢰도 + 출처). */}
        <Button
          asChild
          size="sm"
          variant="outline"
          className="h-8 gap-1.5 whitespace-nowrap px-2.5 text-xs"
          title="현재 검색어로 AI 답변 보기 (Gemini + 출처 인용)"
        >
          <Link
            href={`/ask?q=${encodeURIComponent(initialQuery)}${docId ? `&doc_id=${docId}` : ''}`}
          >
            <Sparkles className="h-3.5 w-3.5 text-primary" />
            <span>AI 답변</span>
          </Link>
        </Button>
        {/* W9 Day 8 — fallback badge 는 사용자 알람 성격이라 mobile 도 노출 (한계 #33).
            dense/sparse 진단 badge 는 좁은 폭 보호로 md+ 만 유지. */}
        {queryParsed?.fallback_reason && (
          <Badge
            variant="destructive"
            className="h-5 whitespace-nowrap px-1.5 text-[10px]"
            title={`fallback: ${queryParsed.fallback_reason}`}
          >
            {queryParsed.fallback_reason}
          </Badge>
        )}
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
        {/* W14 Day 1·4 — ablation mode 3-state 토글
            Day 1: md+ 만 노출
            Day 4 (한계 #78): mobile 도 노출 — 좁은 폭은 폰트/패딩 축소로 대응
            W19 Day 4 (#78 follow-up): mobile 폰트 9→10.5 + 패딩 미세 조정으로 가독성↑ */}
        <div
          className="inline-flex h-7 items-center rounded-md border border-border bg-card"
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
                  ? 'h-full rounded-sm bg-primary px-2 text-[10.5px] font-semibold text-primary-foreground transition-opacity disabled:opacity-50 md:text-[11px]'
                  : 'h-full px-2 text-[10.5px] text-muted-foreground transition-opacity hover:text-foreground disabled:opacity-50 md:text-[11px]'
              }
              aria-pressed={mode === m}
            >
              {m}
            </button>
          ))}
        </div>
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
        >
          <Bug className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
