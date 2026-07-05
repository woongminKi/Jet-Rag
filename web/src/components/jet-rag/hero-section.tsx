'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { FileText, Search, Sparkles, Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

// 익명 데모용 추천 query — owner 인덱싱 12 docs 기반 (다양성: 경제/기업공시/법률/정책/본인 이력서).
// 수익화 W1 결정: 로그인 병행 후에도 익명 데모(0클릭 검색 시연)를 위해 유지.
const SAMPLE_QUERIES: ReadonlyArray<{ label: string; query: string }> = [
  { label: '2026 한국 경제 성장률', query: '2026 한국 경제 성장률 전망' },
  { label: 'SK 사업보고서 매출', query: 'SK 사업보고서 매출 흐름' },
  { label: '하도급 직접지급 묵시적 해지', query: '하도급 직접지급합의 묵시적 해지 요건' },
  { label: '데이터센터 지원사업', query: '데이터센터 산업 활성화 지원 사업 내용' },
  { label: '기웅민 이력서', query: '기웅민 이력서 프로젝트 경험' },
];

export function HeroSection() {
  const router = useRouter();
  const [query, setQuery] = useState('');

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;
    router.push(`/search?q=${encodeURIComponent(trimmed)}`);
  };

  const handleSampleClick = (q: string) => {
    router.push(`/search?q=${encodeURIComponent(q)}`);
  };

  return (
    <section className="relative overflow-hidden bg-gradient-to-b from-primary/5 via-background to-background">
      {/* W26 — mobile py-10 (기존 py-16 은 모바일 viewport 기준 과함). */}
      <div className="container mx-auto px-4 py-10 md:px-6 md:py-24">
        <div className="mx-auto max-w-3xl text-center">
          <div className="mb-5 inline-flex items-center gap-2 rounded-full bg-primary/10 px-3.5 py-1.5 text-xs font-medium text-primary sm:text-sm">
            <Sparkles className="h-4 w-4 shrink-0" />
            <span className="break-keep">정리하지 않아도, 기억의 단편으로 꺼내 쓰는</span>
          </div>

          <h1 className="mb-3 text-balance text-2xl font-bold tracking-tight text-foreground sm:text-3xl md:mb-4 md:text-4xl lg:text-5xl">
            무엇을 찾고 계신가요?
          </h1>
          <p className="mb-6 text-balance text-base text-muted-foreground md:mb-8 md:text-lg">
            자연어로 검색하면 과거에 받았던 문서를 빠르게 찾아드려요.
          </p>

          {/* W26 — mobile hero search: 검색 input + button 동일 row 가 좁은 폰에서 잘림.
              해결: input pr 축소 + button 작게 + 작은 폰에서 button 별도 line 옵션은 미적용 (Toss 풍 한 줄 유지).
              h-12 (48px) 로 모바일 터치 타깃 충족, 폰트 base. */}
          <form onSubmit={handleSubmit} className="mx-auto max-w-2xl">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3.5 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground sm:left-4" />
              <Input
                type="search"
                name="q"
                placeholder='예: "지난달 기재부 가이드라인 변경점"'
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="h-12 rounded-2xl border-2 border-border bg-card pl-11 pr-24 text-base shadow-sm focus:border-primary sm:h-14 sm:rounded-xl sm:pl-12 sm:pr-32"
              />
              <Button
                type="submit"
                className="absolute right-1.5 top-1/2 h-9 -translate-y-1/2 px-4 text-sm sm:right-2 sm:h-10 sm:px-6"
              >
                검색
              </Button>
            </div>
          </form>

          {/* W26 — 추천 query 칩: mobile 에서 가로 scroll-snap + scrollbar-hide.
              개수가 5개라 좁은 폰에서 wrap 시 3줄까지 차지 → 부담스러움.
              chip row 만 의도된 horizontal scroll 허용 (CLAUDE.md 의 예외). */}
          <div className="mt-5 md:mt-6">
            <div className="scrollbar-hide -mx-4 flex snap-x snap-mandatory gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:items-center sm:justify-center sm:overflow-visible sm:px-0">
              {SAMPLE_QUERIES.map((s) => (
                <button
                  key={s.query}
                  type="button"
                  onClick={() => handleSampleClick(s.query)}
                  className="shrink-0 snap-start whitespace-nowrap rounded-full border border-border bg-card px-3.5 py-1.5 text-xs text-foreground transition hover:border-primary hover:bg-primary/5 hover:text-primary sm:shrink"
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-6 flex flex-wrap items-center justify-center gap-3 md:mt-8">
            <Button asChild size="lg" className="gap-2">
              <Link href="/ingest">
                <Upload className="h-5 w-5" />
                파일 업로드
              </Link>
            </Button>
            <Button asChild variant="outline" size="lg" className="gap-2">
              <Link href="/docs">
                <FileText className="h-5 w-5" />
                전체 문서 보기
              </Link>
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
