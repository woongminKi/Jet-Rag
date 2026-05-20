import Link from 'next/link';
import { Zap } from 'lucide-react';
import { SignupForm } from './signup-form';

/**
 * D1 Phase B — 가입 페이지 (공개 경로). 초대 코드 게이트.
 */
export default function SignupPage() {
  return (
    <main className="flex flex-1 items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm space-y-6 rounded-xl border border-border bg-card p-6 shadow-sm">
        <div className="flex flex-col items-center gap-2 text-center">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <Zap className="h-5 w-5" />
          </div>
          <h1 className="text-lg font-semibold text-foreground">가입하기</h1>
          <p className="text-sm text-muted-foreground">
            초대 코드로 Jet-Rag 에 가입하세요.
          </p>
        </div>

        <SignupForm />

        <p className="text-center text-sm text-muted-foreground">
          이미 계정이 있으신가요?{' '}
          <Link
            href="/login"
            className="font-medium text-primary underline-offset-4 hover:underline"
          >
            로그인
          </Link>
        </p>
      </div>
    </main>
  );
}
