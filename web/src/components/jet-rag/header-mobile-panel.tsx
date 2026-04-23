'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Search, Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface HeaderMobilePanelProps {
  onClose: () => void;
}

export function HeaderMobilePanel({ onClose }: HeaderMobilePanelProps) {
  const router = useRouter();
  const [query, setQuery] = useState('');

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;
    router.push(`/search?q=${encodeURIComponent(trimmed)}`);
    onClose();
  };

  return (
    <div
      id="mobile-menu-panel"
      className="border-t border-border bg-card/95 backdrop-blur md:hidden"
    >
      <div className="container mx-auto space-y-3 px-4 py-3">
        <form onSubmit={handleSubmit} className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            name="q"
            autoFocus
            placeholder="검색어를 입력하세요"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="h-10 pl-9"
          />
        </form>
        <Button asChild size="sm" className="w-full gap-2" onClick={onClose}>
          <Link href="/ingest">
            <Upload className="h-4 w-4" />
            파일 업로드
          </Link>
        </Button>
      </div>
    </div>
  );
}
