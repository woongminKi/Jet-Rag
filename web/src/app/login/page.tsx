import Link from 'next/link';
import { Zap } from 'lucide-react';
import { LoginForm } from './login-form';

/**
 * D1 Phase B — 로그인 페이지 (공개 경로). returnTo 보존.
 */
interface LoginPageProps {
  searchParams: Promise<{ returnTo?: string }>;
}

function sanitizeReturnTo(raw: string | undefined): string {
  if (raw && raw.startsWith('/') && !raw.startsWith('//')) return raw;
  return '/';
}

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const { returnTo } = await searchParams;

  return (
    <main className="flex flex-1 items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm space-y-6 rounded-xl border border-border bg-card p-6 shadow-sm">
        <div className="flex flex-col items-center gap-2 text-center">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <Zap className="h-5 w-5" />
          </div>
          <h1 className="text-lg font-semibold text-foreground">로그인</h1>
          <p className="text-sm text-muted-foreground">
            Jet-Rag 에 접속하려면 로그인하세요.
          </p>
        </div>

        <LoginForm returnTo={sanitizeReturnTo(returnTo)} />

        <p className="text-center text-sm text-muted-foreground">
          초대 코드가 있으신가요?{' '}
          <Link
            href="/signup"
            className="font-medium text-primary underline-offset-4 hover:underline"
          >
            가입하기
          </Link>
        </p>
      </div>
    </main>
  );
}
