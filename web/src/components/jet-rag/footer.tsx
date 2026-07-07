import Link from 'next/link';

export function Footer() {
  return (
    <footer className="mt-auto border-t border-border py-6 text-sm text-muted-foreground">
      <div className="container mx-auto flex flex-wrap items-center justify-between gap-3 px-4 md:px-6">
        <span>© 2026 Jet-Rag</span>
        <nav className="flex gap-4">
          <Link href="/terms" className="hover:text-foreground">
            이용약관
          </Link>
          <Link href="/privacy" className="hover:text-foreground">
            개인정보처리방침
          </Link>
        </nav>
      </div>
    </footer>
  );
}
