'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Search } from 'lucide-react';
import { Input } from '@/components/ui/input';

export function HeaderSearch() {
  const router = useRouter();
  const [query, setQuery] = useState('');

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;
    router.push(`/search?q=${encodeURIComponent(trimmed)}`);
  };

  return (
    <form onSubmit={handleSubmit} className="hidden min-w-0 max-w-xl flex-1 md:flex">
      <div className="relative w-full">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          type="search"
          name="q"
          placeholder="자연어로 검색하세요... (예: 지난달 기재부 가이드라인)"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="w-full border-transparent bg-secondary/50 pl-10 pr-4 focus:border-primary focus:bg-background"
        />
      </div>
    </form>
  );
}
