'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
// PORTFOLIO MODE C+ — 업로드 버튼 비활성. 복원 시 Upload import 복구.
import { FileText, Search, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

// PORTFOLIO MODE C+ — owner 인덱싱 12 docs 기반 추천 query (다양성: 경제/기업공시/법률/정책/본인 이력서).
// 채용 담당자가 0클릭 검색 시연 가능. 복원 시 본 배열 + 칩 렌더 블록 삭제 가능.
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
      <div className="container mx-auto px-4 py-16 md:px-6 md:py-24">
        <div className="mx-auto max-w-3xl text-center">
          <div className="mb-6 inline-flex items-center gap-2 rounded-full bg-primary/10 px-4 py-1.5 text-sm font-medium text-primary">
            <Sparkles className="h-4 w-4" />
            정리하지 않아도, 기억의 단편으로 꺼내 쓰는
          </div>

          <h1 className="mb-4 text-balance text-3xl font-bold tracking-tight text-foreground md:text-4xl lg:text-5xl">
            무엇을 찾고 계신가요?
          </h1>
          <p className="mb-8 text-balance text-lg text-muted-foreground">
            자연어로 검색하면 과거에 받았던 문서를 빠르게 찾아드려요.
          </p>

          <form onSubmit={handleSubmit} className="mx-auto max-w-2xl">
            <div className="relative">
              <Search className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground" />
              <Input
                type="search"
                name="q"
                placeholder='예: "지난달 기재부 가이드라인 변경점"'
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="h-14 rounded-xl border-2 border-border bg-card pl-12 pr-32 text-base shadow-sm focus:border-primary"
              />
              <Button
                type="submit"
                className="absolute right-2 top-1/2 h-10 -translate-y-1/2 px-6"
              >
                검색
              </Button>
            </div>
          </form>

          {/* PORTFOLIO MODE C+ — 0클릭 시연 가능한 추천 query 칩.
              복원 시 SAMPLE_QUERIES 상수 + 본 블록 삭제. */}
          <div className="mt-6">
            <div className="flex flex-wrap items-center justify-center gap-2">
              {SAMPLE_QUERIES.map((s) => (
                <button
                  key={s.query}
                  type="button"
                  onClick={() => handleSampleClick(s.query)}
                  className="rounded-full border border-border bg-card px-3 py-1.5 text-xs text-foreground transition hover:border-primary hover:bg-primary/5 hover:text-primary"
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            {/* PORTFOLIO MODE C+ — 파일 업로드 버튼 비활성. 복원 시 아래 블록 주석 해제. */}
            {/* <Button asChild size="lg" className="gap-2">
              <Link href="/ingest">
                <Upload className="h-5 w-5" />
                파일 업로드
              </Link>
            </Button> */}
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
