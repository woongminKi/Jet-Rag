'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowLeft, Bug, Search } from 'lucide-react';
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
}

export function SearchSubheader({
  initialQuery,
  total,
  tookMs,
  queryParsed,
  debug = false,
}: SearchSubheaderProps) {
  const router = useRouter();
  const [query, setQuery] = useState(initialQuery);

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;
    const next = new URLSearchParams();
    next.set('q', trimmed);
    if (debug) next.set('debug', '1');
    router.push(`/search?${next.toString()}`);
  };

  const toggleDebug = () => {
    const next = new URLSearchParams();
    next.set('q', initialQuery);
    if (!debug) next.set('debug', '1');
    router.push(`/search?${next.toString()}`);
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

        <Badge variant="secondary" className="hidden whitespace-nowrap sm:inline-flex">
          {total}개 결과 · {(tookMs / 1000).toFixed(2)}초
        </Badge>
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
