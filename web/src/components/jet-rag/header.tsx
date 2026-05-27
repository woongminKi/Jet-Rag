'use client';

import { useState } from 'react';
import Link from 'next/link';
// PORTFOLIO MODE C+ — 업로드/로그인 비활성 (Upload, LogIn, LogOut 미사용 + Button 미사용).
import { Zap } from 'lucide-react';
// import { Button } from '@/components/ui/button';
// import { useAuth } from '@/lib/auth/auth-context';
// import { signOut } from '@/lib/auth/actions';
import { ActiveDocsIndicator } from './active-docs-indicator';
import { HeaderSearch } from './header-search';
import { HeaderMobileToggle } from './header-mobile-toggle';
import { HeaderMobilePanel } from './header-mobile-panel';

export function Header() {
  const [mobileOpen, setMobileOpen] = useState(false);
  // PORTFOLIO MODE — Auth context 미주입(layout 에서 AuthProvider 주석). 복원 시 useAuth() 재활성.
  // const { user } = useAuth();

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
      <div className="container mx-auto flex h-16 items-center justify-between gap-4 px-4 md:px-6">
        <Link
          href="/"
          className="flex items-center gap-2"
          onClick={() => setMobileOpen(false)}
        >
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <Zap className="h-5 w-5" />
          </div>
          <span className="hidden font-semibold text-foreground sm:inline-block">
            Jet-Rag
          </span>
        </Link>

        <HeaderSearch />

        <div className="flex items-center gap-2">
          <ActiveDocsIndicator />

          {/* PORTFOLIO MODE C+ — 업로드 버튼 비활성. 데모는 owner 인덱싱 12 docs read-only.
              복원 시 아래 블록 주석 해제 + 위 Upload import 복구. */}
          {/* <Button asChild size="sm" className="hidden gap-2 sm:flex">
            <Link href="/ingest">
              <Upload className="h-4 w-4" />
              <span className="hidden lg:inline">업로드</span>
            </Link>
          </Button> */}

          {/* PORTFOLIO MODE — 로그인/로그아웃 버튼 비활성. 복원 시 아래 블록 주석 해제. */}
          {/* {user ? (
            <form action={signOut} className="hidden sm:block">
              <Button
                type="submit"
                size="sm"
                variant="ghost"
                className="gap-2"
                title={user.email ?? '로그아웃'}
              >
                <LogOut className="h-4 w-4" />
                <span className="hidden lg:inline">로그아웃</span>
              </Button>
            </form>
          ) : (
            <Button
              asChild
              size="sm"
              variant="ghost"
              className="hidden gap-2 sm:flex"
            >
              <Link href="/login">
                <LogIn className="h-4 w-4" />
                <span className="hidden lg:inline">로그인</span>
              </Link>
            </Button>
          )} */}

          <HeaderMobileToggle
            open={mobileOpen}
            onToggle={() => setMobileOpen((prev) => !prev)}
          />
        </div>
      </div>

      {mobileOpen && (
        <HeaderMobilePanel onClose={() => setMobileOpen(false)} />
      )}
    </header>
  );
}
